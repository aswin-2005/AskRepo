"""
store.py
--------
ChromaDB wrapper for storing and retrieving chunks.

Two-tier collection access to avoid loading the embedding model unnecessarily:

  _get_collection()       — full collection with embedding function.
                            Used by: add_chunks(), search()
                            Triggers model load on first call.

  _get_meta_collection()  — lightweight collection without embedding.
                            Used by: collection_count(), get_all_metadata()
                            NEVER loads the embedding model.

This means `python main.py list` and `python main.py count` are instant —
the sentence-transformers model is only loaded when you actually index or query.
"""

import json
import chromadb
import config

# ---------------------------------------------------------------------------
# Lazy embedding function — only loaded when semantic operations are needed
# ---------------------------------------------------------------------------
_EMBEDDING_FN = None


def _get_embedding_fn():
    """Load SentenceTransformerEmbeddingFunction on first use only."""
    global _EMBEDDING_FN
    if _EMBEDDING_FN is None:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        _EMBEDDING_FN = SentenceTransformerEmbeddingFunction(model_name=config.EMBEDDING_MODEL)
    return _EMBEDDING_FN


# Fields that contain raw text — must NOT be auto-deserialized even if
# their content happens to start with { or [ (e.g. a JSON file's raw_code)
_RAW_TEXT_FIELDS = {"raw_code"}

# ---------------------------------------------------------------------------
# ChromaDB client + collection singletons
# ---------------------------------------------------------------------------
_client = None
_embed_collection = None   # has embedding function — for search & add
_meta_collection  = None   # no embedding function — for count & list


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    return _client


def _get_collection():
    """Full collection with embedding — triggers model load on first call."""
    global _embed_collection
    if _embed_collection is None:
        _embed_collection = _get_client().get_or_create_collection(
            name=config.COLLECTION_NAME,
            embedding_function=_get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )
    return _embed_collection


def _get_meta_collection():
    """
    Lightweight collection reference with NO embedding function.
    Safe to call for count() and get(include=["metadatas"]) — the
    embedding function is never invoked for those operations.
    Returns None if the collection doesn't exist yet.
    """
    global _meta_collection
    if _meta_collection is None:
        client = _get_client()
        try:
            _meta_collection = client.get_collection(name=config.COLLECTION_NAME)
        except Exception:
            return None
    return _meta_collection


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _chunk_to_chroma(chunk: dict, chunk_id: str) -> tuple[str, str, dict]:
    """
    Convert a chunk dict into (id, document, metadata) for ChromaDB.
    Lists/dicts get JSON-serialized (ChromaDB metadata must be scalar).
    """
    document = chunk.get("verbal") or ""
    meta = {}
    for key, value in chunk.items():
        if key == "verbal":
            continue
        if isinstance(value, (list, dict)) and value is not None:
            meta[key] = json.dumps(value)
        elif value is None:
            meta[key] = ""
        else:
            meta[key] = value
    return chunk_id, document, meta


def _make_id(chunk: dict, index: int) -> str:
    """Stable unique ID for a chunk."""
    path = chunk.get("path", "unknown").replace("\\", "/")
    name = chunk.get("name") or chunk.get("type", "chunk")
    class_name = chunk.get("class_name") or ""
    suffix = f"{class_name}.{name}" if class_name else name
    return f"{path}::{chunk['type']}::{suffix}::{index}"


def _deserialize_meta(meta: dict, verbal: str) -> dict:
    """
    Reconstruct a chunk dict from ChromaDB metadata.
    raw_code is always kept as a plain string (never JSON-parsed).
    """
    chunk = {"verbal": verbal}
    for key, value in meta.items():
        if key in _RAW_TEXT_FIELDS:
            chunk[key] = value if isinstance(value, str) else ""
        elif isinstance(value, str) and value.startswith(("[", "{")):
            try:
                chunk[key] = json.loads(value)
            except json.JSONDecodeError:
                chunk[key] = value
        else:
            chunk[key] = value
    return chunk


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def add_chunks(chunks: list[dict]) -> None:
    """Store a list of chunks in ChromaDB. Loads embedding model if needed."""
    global _meta_collection
    collection = _get_collection()   # loads embedding model here if first call

    ids, documents, metadatas = [], [], []
    for i, chunk in enumerate(chunks):
        cid, doc, meta = _chunk_to_chroma(chunk, _make_id(chunk, i))
        ids.append(cid)
        documents.append(doc)
        metadatas.append(meta)

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    _meta_collection = None   # invalidate meta cache after write
    print(f"  Stored {len(ids)} chunks in ChromaDB.")


def search(query: str, top_k: int = None, where: dict = None) -> list[dict]:
    """
    Semantic search. Loads embedding model on first call.

    Args:
        query:  natural language question
        top_k:  number of results (defaults to config.TOP_K)
        where:  optional ChromaDB metadata filter dict
    """
    collection = _get_collection()   # loads embedding model here if first call
    k = top_k or config.TOP_K

    kwargs = {
        "query_texts": [query],
        "n_results": min(k, collection.count() or 1),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunk = _deserialize_meta(meta, doc)
        chunk["score"] = round(1 - dist, 4)
        chunks.append(chunk)
    return chunks


def get_by_filter(where: dict) -> list[dict]:
    """
    Programmatic metadata lookup — no vector search, no embedding model needed.
    """
    col = _get_meta_collection()
    if col is None:
        return []
    results = col.get(where=where, include=["documents", "metadatas"])
    return [
        _deserialize_meta(meta, doc)
        for doc, meta in zip(results["documents"], results["metadatas"])
    ]


def get_all_metadata() -> list[dict]:
    """
    Return metadata for every chunk. No embedding model loaded.
    Used by the `list` command.
    """
    col = _get_meta_collection()
    if col is None:
        return []
    results = col.get(include=["metadatas"])
    return results.get("metadatas", [])


def collection_count() -> int:
    """Return total chunk count. No embedding model loaded."""
    col = _get_meta_collection()
    return col.count() if col else 0


def clear_collection() -> None:
    """Delete and recreate the collection (wipes all indexed data)."""
    global _embed_collection, _meta_collection
    client = _get_client()
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except Exception:
        pass
    _embed_collection = None
    _meta_collection  = None
    print("Collection cleared.")
