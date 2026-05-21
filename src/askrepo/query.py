"""
query.py
--------
Query pipeline: retrieve relevant chunks → build prompt → call LLM → return answer.

The LLM backend (Groq or Ollama) is controlled by DESCRIBER_BACKEND in config.py —
the same setting used for indexing descriptions. No separate config needed.

Query flow:
  1. Semantic search: top-k chunks via ChromaDB
  2. Build context prompt from retrieved chunks
  3. Call LLM (Groq or Ollama) → return answer
"""

import json
from askrepo import config
from askrepo import store as store_module


# ---------------------------------------------------------------------------
# LLM callers — mirrors the backends in describer.py
# ---------------------------------------------------------------------------
_groq_client = None

def _call_groq(prompt: str) -> str:
    global _groq_client
    from groq import Groq, RateLimitError, APIError
    import re, time
    if _groq_client is None:
        if not config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not set.")
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    try:
        response = _groq_client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1024,
        )
        return response.choices[0].message.content.strip()
    except RateLimitError as e:
        msg = str(e)
        m = re.search(r'try again in (\d+)m', msg)
        wait_hint = f"~{m.group(1)} min" if m else "a while"
        print(f"\n  [Groq rate limit] Daily token cap reached. Try again in {wait_hint}.")
        print(  "  Tip: set QUERY_BACKEND = \"ollama\" in config.py to use local model instead.")
        return ""
    except APIError as e:
        print(f"  [Groq API error] {e}")
        return ""


def _call_ollama(prompt: str) -> str:
    import urllib.request
    payload = json.dumps({
        "model":  config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 1024},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_BASE_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body.get("response", "").strip()


def _call_llm(prompt: str) -> str:
    backend = config.QUERY_BACKEND.lower()
    if backend == "groq":
        return _call_groq(prompt)
    elif backend == "ollama":
        return _call_ollama(prompt)
    else:
        raise ValueError(
            f"Unknown QUERY_BACKEND: {config.QUERY_BACKEND!r}. "
            "Valid options: 'groq', 'ollama'"
        )


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
def _format_chunk_for_context(chunk: dict) -> str:
    """Format a single retrieved chunk into a readable context block."""
    lines = []
    chunk_type = chunk.get("type", "?")
    path = chunk.get("path", "?")
    name = chunk.get("name", "")

    header = f"[{chunk_type.upper()}]"
    if name:
        header += f" {name}"
    header += f"  ({path})"
    lines.append(header)

    verbal = chunk.get("verbal", "")
    if verbal:
        lines.append(f"Description: {verbal}")

    if chunk_type == "file":
        if chunk.get("imports"):
            lines.append(f"Imports: {', '.join(chunk['imports'])}")
        if chunk.get("functions"):
            lines.append(f"Functions: {', '.join(chunk['functions'])}")
        if chunk.get("classes"):
            lines.append(f"Classes: {', '.join(chunk['classes'])}")
        if chunk.get("globals"):
            lines.append(f"Globals: {', '.join(chunk['globals'])}")

    elif chunk_type in ("function", "method"):
        params = chunk.get("params") or []
        if params:
            param_str = ", ".join(
                f"{p['name']}: {p['type']}" if p.get('type') else p['name']
                for p in params
            )
            lines.append(f"Params: ({param_str})")
        if chunk.get("returns"):
            lines.append(f"Returns: {chunk['returns']}")
        calls = chunk.get("calls") or []
        if calls:
            lines.append(f"Calls: {', '.join(c['name'] for c in calls)}")
        if chunk.get("class_name"):
            lines.append(f"Class: {chunk['class_name']}")

    elif chunk_type == "class":
        if chunk.get("inherits"):
            lines.append(f"Inherits: {', '.join(chunk['inherits'])}")
        if chunk.get("methods"):
            lines.append(f"Methods: {', '.join(chunk['methods'])}")
        if chunk.get("variables"):
            lines.append(f"Variables: {', '.join(chunk['variables'])}")

    raw = chunk.get("raw_code") or ""
    raw = raw if isinstance(raw, str) else str(raw)
    if raw:
        preview = raw[:1500] + ("\n... [truncated]" if len(raw) > 1500 else "")
        lines.append(f"Source:\n{preview}")

    return "\n".join(lines)


def _build_prompt(user_query: str, chunks: list[dict]) -> str:
    context_blocks = "\n\n---\n\n".join(
        _format_chunk_for_context(c) for c in chunks
    )
    return f"""You are a code intelligence assistant. You have been given parsed \
documentation from a codebase. Answer the user's question using ONLY the context \
provided below. If the answer is not present in the context, say so clearly.
Do not make up information about code that is not shown.

Formatting rules:
- Write in clear, plain prose. Do NOT use markdown headers (##, ###) or bold/italic markers.
- You MAY use fenced code blocks (```language ... ```) for code snippets.
- Keep the answer concise and directly useful.

=== CONTEXT ===

{context_blocks}

=== END CONTEXT ===

Question: {user_query}

Answer:"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def answer(
    user_query: str,
    top_k: int = None,
    verbose: bool = False,
) -> tuple[str, list[dict], str, str]:
    """
    Full query pipeline: retrieve -> build prompt -> call LLM -> return result.

    Returns
    -------
    (answer_text, retrieved_chunks, backend_name, model_name)
    The caller (main.py) decides how to display them.
    """
    k = top_k or config.TOP_K
    backend = config.QUERY_BACKEND.lower()
    model   = config.OLLAMA_MODEL if backend == "ollama" else config.GROQ_MODEL

    chunks = store_module.search(user_query, top_k=k)
    if not chunks:
        return "No indexed code found. Please run `index` first.", [], backend, model

    prompt = _build_prompt(user_query, chunks)
    result = _call_llm(prompt)
    return result, chunks, backend, model

