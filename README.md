# AskRepo

A local-first code intelligence system that indexes source code and documentation into a vector database and answers natural language questions about it using semantic search and an LLM.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![ChromaDB](https://img.shields.io/badge/vector_db-ChromaDB-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![LLM](https://img.shields.io/badge/LLM-Groq%20%7C%20Ollama-purple)

---

## Overview

AskRepo parses your codebase at the AST level, assigns each function, class, and file a natural language description using an LLM, stores everything in a local ChromaDB vector store, and lets you query it in plain English.

It supports any public GitHub repository via shallow Git clone, and handles both structured code (Python, JavaScript, TypeScript) and unstructured documents (Markdown, JSON, TOML, YAML, plain text, config files).

Both the indexing descriptions and the query answering use pluggable LLM backends — Groq (cloud) or Ollama (local) — configurable independently of each other.

---

## How It Works

```
Source Code / GitHub Repo
         │
         ▼
  ┌─────────────┐
  │   parser.py  │  AST extraction (tree-sitter) for .py / .js / .ts / .tsx
  │              │  Raw content read for .md / .json / .toml / .yaml / etc.
  └──────┬───────┘
         │  Structured data: functions, classes, imports, globals
         ▼
  ┌─────────────┐
  │  chunker.py  │  One chunk per function, class, file overview, or document
  └──────┬───────┘
         │  List of typed chunks with metadata
         ▼
  ┌──────────────┐
  │ describer.py  │  LLM generates a 2-4 sentence verbal description per chunk
  │               │  Backend: Ollama (local) or Groq (cloud) — set in config.py
  └──────┬────────┘
         │  Chunks with `verbal` field populated
         ▼
  ┌──────────────┐
  │   store.py    │  Embeds the verbal description via sentence-transformers
  │               │  Stores vectors + metadata in local ChromaDB
  └──────┬────────┘
         │
         ◆  Index complete
         │
  ┌──────┴────────────────────────────────────────────────────┐
  │                        query.py                            │
  │  1. Embed the user's question                              │
  │  2. Retrieve top-k chunks via cosine similarity            │
  │  3. Build a context prompt from the retrieved chunks       │
  │  4. Call LLM (Groq or Ollama) → synthesise answer         │
  └───────────────────────────────────────────────────────────┘
```

### Key design decisions

- **AST over full-file embedding** — Each function and class is indexed independently. This gives precise semantic hits instead of retrieving large, diluted file blobs.
- **Verbal descriptions as the embedding target** — Rather than embedding raw code (which encodes syntax, not intent), an LLM first writes a plain English description of each chunk. That description is what gets embedded. This dramatically improves retrieval relevance.
- **Fully local storage** — ChromaDB persists all vectors to `./chroma_db/` on disk. No cloud vector database, no data leaves the machine (unless you use the Groq backend).
- **Shallow Git clones** — GitHub repositories are fetched with `git clone --depth=1`, avoiding API rate limits and keeping clone sizes small.
- **Lazy model loading** — The embedding model is only loaded into memory when a command actually needs it (`query`, `index`). Commands like `list` and `count` run instantly without touching the model.

---

## Project Structure

```
askrepo/
├── main.py            CLI entry point — all commands route through here
├── askrepo.bat        Windows launcher (run `askrepo` from the project directory)
├── config.py          Single source of truth for all settings
├── parser.py          AST extraction (Python, JS, TS) + simple file reader
├── chunker.py         Splits parsed output into indexable chunks
├── describer.py       LLM description generation (Groq / Ollama)
├── store.py           ChromaDB wrapper — add, search, count, metadata
├── query.py           Query pipeline — retrieve → prompt → LLM → answer
├── github_fetcher.py  Git clone / pull for public GitHub repositories
├── requirements.txt
├── .env               GROQ_API_KEY goes here
├── chroma_db/         Local vector store (auto-created on first index)
└── repos/             Cached GitHub repository clones
```

---

## Requirements

- Python 3.10+
- [Git](https://git-scm.com/) (must be in PATH — used for `index-repo`)
- [Ollama](https://ollama.com/) with `gemma:2b` pulled (if using the Ollama backend)
- A [Groq API key](https://console.groq.com/) (if using the Groq backend — free tier available)

---

## Installation

```bash
git clone <this-repo>
cd askrepo

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

If you intend to use only Ollama, the `.env` file and Groq key are not required.

### CLI setup (Windows)

The project includes `askrepo.bat`. To use `askrepo` as a command from anywhere, add the project directory to your system PATH, or simply run it from within the project directory:

```
askrepo query "how does authentication work?"
```

Alternatively, you can always invoke it directly:

```
python main.py query "how does authentication work?"
```

---

## Configuration

All settings are in `config.py`. Edit this file directly — no CLI flags, no environment variable hunting.

```python
# config.py

# Which LLM generates verbal descriptions during indexing
# "ollama"  — local, unlimited, no API key needed  (default)
# "groq"    — cloud, faster, 100k token/day free tier
DESCRIBER_BACKEND = "ollama"

# Which LLM synthesises answers during queries
# "groq"    — cloud, better reasoning quality      (default)
# "ollama"  — local, unlimited
QUERY_BACKEND = "groq"

# Ollama
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "gemma:2b"

# Groq
GROQ_MODEL      = "llama-3.3-70b-versatile"
GROQ_CALL_DELAY = 0.5           # seconds between calls, respects free-tier limits

# Retrieval
TOP_K = 5                       # chunks returned per query

# Directories never descended into during indexing
SKIP_DIRS = {"venv", ".venv", "__pycache__", "node_modules", "docs", ...}
```

### Backend matrix

| Use case | DESCRIBER_BACKEND | QUERY_BACKEND |
|---|---|---|
| Default (local index, cloud query) | `"ollama"` | `"groq"` |
| Fully offline | `"ollama"` | `"ollama"` |
| Groq daily limit hit | `"ollama"` | `"ollama"` |
| Fastest indexing (burns tokens) | `"groq"` | `"groq"` |

---

## Usage

### Index a local path

```bash
askrepo index ./myproject
askrepo index ./src/auth.py
```

Walks the directory recursively. Skips test files, dependency directories (`node_modules`, `.venv`, etc.), and documentation folders (`docs/`). Accepts a single file or any directory.

### Index a GitHub repository

```bash
askrepo index-repo fastapi/fastapi
askrepo index-repo https://github.com/psf/requests
askrepo index-repo django/django --branch stable/4.2.x
```

Performs a shallow clone (`--depth=1`) into `./repos/<owner>_<repo>/`. If the repository is already cached, runs `git pull` to update it instead of re-cloning.

> **Note on large repositories** — Repos with large `docs/` folders (translations, tutorials) will generate hundreds of chunks and exhaust the Groq free-tier token budget quickly. The `docs/` directory is in `SKIP_DIRS` by default. Adjust `SKIP_DIRS` in `config.py` if needed.

### Query

```bash
askrepo query "how does authentication work?"
askrepo query "what does the Timers class track?"
askrepo query "what python version does this require?"
```

Runs the full pipeline: embed the question → retrieve top-k chunks → build a context prompt → call the LLM → print the answer.

### List the index

```bash
askrepo list
```

Prints a structured breakdown of everything currently indexed, grouped by source:

```
==============================================================
  INDEX BREAKDOWN
==============================================================
  Sources : 1
  Files   : 7
  Chunks  : 25

--------------------------------------------------------------
  Source : aswin-2005/MONOL-Server   (7 files | 25 chunks)
--------------------------------------------------------------
  auth.py          python    12 chunks  [file, 11x function]  ->  generate_challenge, ...
  crypt.py         python     4 chunks  [file, 3x function]   ->  encrypt_with_aesgcm, ...
  entries.py       python     5 chunks  [file, 4x function]   ->  add_entry, get_entries, ...
  requirements.txt text       1 chunk   [document]
  ...
==============================================================
```

### Count chunks

```bash
askrepo count
```

Prints the total number of indexed chunks. Does not load the embedding model.

### Clear the index

```bash
askrepo clear
```

Wipes the ChromaDB collection. Does not delete cached repository clones in `./repos/`.

---

## Supported File Types

### Structured (AST-parsed)

These files are parsed with [tree-sitter](https://tree-sitter.github.io/tree-sitter/). Each function, class, and method becomes its own chunk with extracted metadata (parameters, return type, calls, docstring).

| Extension | Language |
|---|---|
| `.py` | Python |
| `.js`, `.mjs`, `.cjs` | JavaScript |
| `.ts` | TypeScript |
| `.tsx` | TypeScript + JSX |

### Simple (raw content)

These files are read as plain text and stored as a single document chunk each.

| Extension / Filename | Label |
|---|---|
| `.md`, `.markdown` | markdown |
| `.txt` | text |
| `.rst` | restructuredtext |
| `.json` | json |
| `.toml` | toml |
| `.yaml`, `.yml` | yaml |
| `.env`, `.ini`, `.cfg`, `.conf` | env / config |
| `Dockerfile`, `Makefile` | dockerfile / makefile |
| `.gitignore`, `.dockerignore` | gitignore |

---

## Embedding Model

AskRepo uses `all-MiniLM-L6-v2` from [sentence-transformers](https://www.sbert.net/) for embedding verbal descriptions. The model is downloaded once on first use and cached locally at `~/.cache/huggingface/`. Subsequent runs load it from disk — no internet connection required.

The model is lazy-loaded: it is only initialised when a command actually needs embeddings (`index`, `query`). Commands like `list` and `count` are instant.

To change the embedding model, update `EMBEDDING_MODEL` in `config.py`.

---

## Ollama Setup

Install Ollama and pull the model:

```bash
# Install from https://ollama.com
ollama pull gemma:2b
ollama serve        # Ollama usually auto-starts; only needed if not running
```

Verify it is reachable:

```bash
curl http://localhost:11434/api/tags
```

The base URL and model name are configurable in `config.py` under `OLLAMA_BASE_URL` and `OLLAMA_MODEL`. Any Ollama-compatible model can be used.

---

## Skipped Files and Directories

The following are automatically excluded during indexing to avoid token waste and retrieval noise:

**Directories:** `venv`, `.venv`, `__pycache__`, `node_modules`, `dist`, `build`, `.git`, `vendor`, `third_party`, `site-packages`, `docs`, `doc`, `documentation`, `examples`, `example`, `benchmarks`, `bench`

**File name patterns:**
- Prefix: `test_`, `spec_`
- Suffix: `_test.py`, `_test.js`, `_test.ts`, `.test.js`, `.test.ts`, `.spec.js`, `.spec.ts`, `_spec.rb`

All of these are configurable via `SKIP_DIRS`, `SKIP_FILE_PREFIXES`, and `SKIP_FILE_SUFFIXES` in `config.py`.

---

## Limitations

- **Groq free tier** — 100,000 tokens per day. Indexing a large repository with many files can exhaust this quickly. Use Ollama for indexing (`DESCRIBER_BACKEND = "ollama"`) and reserve Groq tokens for queries.
- **Query quality with small models** — `gemma:2b` is capable but noticeably weaker than `llama-3.3-70b-versatile` on complex reasoning. For best answer quality, use Groq for queries.
- **No incremental re-indexing** — Re-running `index` or `index-repo` on an already-indexed path will upsert (overwrite) existing chunks. This is safe but re-runs all LLM description calls.
- **No cross-collection search** — All indexed sources share a single ChromaDB collection. Run `clear` if you want to start fresh.

---

## Dependencies

| Package | Purpose |
|---|---|
| `chromadb` | Local vector database |
| `sentence-transformers` | Embedding model (`all-MiniLM-L6-v2`) |
| `tree-sitter` | AST parsing core |
| `tree-sitter-python` | Python grammar |
| `tree-sitter-javascript` | JavaScript grammar |
| `tree-sitter-typescript` | TypeScript / TSX grammar |
| `groq` | Groq SDK for cloud LLM calls |
| `python-dotenv` | `.env` file loading |
