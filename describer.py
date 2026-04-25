"""
describer.py
------------
Generates verbal descriptions for each chunk using an LLM.
The `verbal` field is what gets embedded in the vector DB.

Backends (set DESCRIBER_BACKEND in config.py):
  "groq"   — Groq cloud API   (fast, free tier limited to 100k tokens/day)
  "ollama" — Local Ollama      (unlimited, no internet, model must be pulled)
"""

import time
import re
import json
import config


# ---------------------------------------------------------------------------
# Groq backend
# ---------------------------------------------------------------------------
_groq_client = None

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        if not config.GROQ_API_KEY:
            raise ValueError(
                "GROQ_API_KEY is not set. "
                "Add it to your .env file or set DESCRIBER_BACKEND = 'ollama' in config.py."
            )
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


def _call_groq(prompt: str) -> str:
    from groq import RateLimitError, APIError
    client = _get_groq_client()
    try:
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except RateLimitError as e:
        wait = 60
        m = re.search(r'try again in (\d+)m', str(e))
        if m:
            wait = int(m.group(1)) * 60 + 10
        print(f"\n  [Rate limit] Waiting {wait}s before retrying...")
        time.sleep(wait)
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except APIError as e:
        print(f"  [Groq API error] {e} — skipping description.")
        return ""


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------
def _call_ollama(prompt: str) -> str:
    """
    Calls the local Ollama HTTP API (/api/generate, non-streaming).
    Requires Ollama to be running and the model to be pulled:
        ollama pull gemma:2b
    """
    import urllib.request

    payload = json.dumps({
        "model":  config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 300,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{config.OLLAMA_BASE_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("response", "").strip()
    except Exception as e:
        print(f"  [Ollama error] {e} — skipping description.")
        return ""


# ---------------------------------------------------------------------------
# Unified caller — dispatches based on config.DESCRIBER_BACKEND
# ---------------------------------------------------------------------------
def _call_llm(prompt: str) -> str:
    backend = config.DESCRIBER_BACKEND.lower()
    if backend == "groq":
        result = _call_groq(prompt)
        time.sleep(config.GROQ_CALL_DELAY)   # rate-limit pause only for Groq
        return result
    elif backend == "ollama":
        return _call_ollama(prompt)
    else:
        raise ValueError(
            f"Unknown DESCRIBER_BACKEND: {config.DESCRIBER_BACKEND!r}. "
            "Valid options: 'groq', 'ollama'"
        )


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _prompt_for_file(chunk: dict) -> str:
    return f"""You are a code documentation assistant.
Given a source file's structure, write 2-4 sentences describing:
- What this file is for (its overall purpose)
- What the root-scope code does (globals, setup, config — anything outside functions/classes)
- What functions and classes it provides (just name them, don't explain each)
- Its key dependencies

Be specific and concise. Do not use bullet points — write plain prose.

File: {chunk['path']}
Language: {chunk['language']}
Imports: {', '.join(chunk['imports']) or 'none'}
Functions defined: {', '.join(chunk['functions']) or 'none'}
Classes defined: {', '.join(chunk['classes']) or 'none'}
Global variables: {', '.join(chunk['globals']) or 'none'}

Source code:
{chunk['raw_code'][:3000]}
"""


def _prompt_for_function(chunk: dict) -> str:
    params_str = ", ".join(
        f"{p['name']}: {p['type']}" if p['type'] else p['name']
        for p in chunk['params']
    )
    calls_str = ", ".join(c['name'] for c in chunk['calls']) or "none"
    class_ctx = f"Method of class `{chunk['class_name']}`." if chunk['class_name'] else "Top-level function."
    docstring_ctx = f"Docstring: {chunk['docstring']}" if chunk['docstring'] else ""

    return f"""You are a code documentation assistant.
Write 2-4 sentences describing this function: what it does, what its parameters mean,
what it returns, and any notable logic or dependencies. Be specific. Plain prose only.

{class_ctx}
Name: {chunk['name']}
Parameters: ({params_str})
Returns: {chunk['returns'] or 'not annotated'}
Calls: {calls_str}
{docstring_ctx}

Source code:
{chunk['raw_code'][:2000]}
"""


def _prompt_for_class(chunk: dict) -> str:
    inherits_str = ", ".join(chunk['inherits']) or "nothing"
    methods_str  = ", ".join(chunk['methods'])  or "none"
    vars_str     = ", ".join(chunk['variables']) or "none"

    return f"""You are a code documentation assistant.
Write 2-4 sentences describing this class: its purpose, what it inherits from,
its key attributes and what its methods collectively provide. Plain prose only.

Name: {chunk['name']}
Inherits from: {inherits_str}
Instance variables: {vars_str}
Methods: {methods_str}

Source code:
{chunk['raw_code'][:2000]}
"""


def _prompt_for_document(chunk: dict) -> str:
    return f"""You are a code documentation assistant.
Given the contents of a {chunk['language']} file, write 2-4 sentences describing:
- What this file is for (its purpose or role in the project)
- Key values, dependencies, settings, or instructions it contains
- Anything a developer reading the codebase should know about it
Be specific. Plain prose only. Do not use bullet points.

File: {chunk['path']}
Type: {chunk['language']}

Contents:
{chunk['raw_code'][:3000]}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def describe_chunk(chunk: dict) -> dict:
    """Fill in the `verbal` field for a single chunk. Returns the chunk."""
    chunk_type = chunk["type"]

    if chunk_type == "file":
        prompt = _prompt_for_file(chunk)
    elif chunk_type in ("function", "method"):
        prompt = _prompt_for_function(chunk)
    elif chunk_type == "class":
        prompt = _prompt_for_class(chunk)
    elif chunk_type == "document":
        prompt = _prompt_for_document(chunk)
    else:
        chunk["verbal"] = ""
        return chunk

    chunk["verbal"] = _call_llm(prompt)
    return chunk


def describe_all(chunks: list[dict], verbose: bool = True) -> list[dict]:
    """Fill verbal descriptions for all chunks using the configured backend."""
    backend = config.DESCRIBER_BACKEND.lower()
    if verbose and chunks:
        print(f"  [describer] backend = {backend}"
              f"  model = {config.OLLAMA_MODEL if backend == 'ollama' else config.GROQ_MODEL}")

    described = []
    for i, chunk in enumerate(chunks):
        if verbose:
            label = chunk.get("name") or chunk.get("path", "?")
            print(f"  [{i+1}/{len(chunks)}] Describing {chunk['type']}: {label}")
        described.append(describe_chunk(chunk))
    return described
