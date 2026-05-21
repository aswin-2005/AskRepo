"""
config.py
---------
Single source of truth for all runtime settings.
Tweak values here — no need to touch any other file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# User-level data directory — works correctly regardless of install location
_DATA_DIR = Path.home() / ".askrepo"

# ---------------------------------------------------------------------------
# HuggingFace — work fully offline after first model download
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # hide "Loading weights" bar
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ---------------------------------------------------------------------------
# Describer backend  (used during `index` / `index-repo`)
# ---------------------------------------------------------------------------
# Which LLM generates verbal descriptions for each chunk.
#   "ollama" — local, unlimited, no API key needed (default)
#   "groq"   — cloud, fast, but free tier is 100k tokens/day
DESCRIBER_BACKEND = "ollama"

# ---------------------------------------------------------------------------
# Query backend  (used during `query`)
# ---------------------------------------------------------------------------
# Which LLM synthesises the final answer from retrieved chunks.
#   "groq"   — cloud, fast, much better answer quality (default)
#   "ollama" — local, unlimited, slower / weaker for long reasoning
QUERY_BACKEND = "groq"

# --- Groq settings ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"

# Delay (seconds) between Groq calls to stay within free-tier rate limits
GROQ_CALL_DELAY = 0.5

# --- Ollama settings ---
OLLAMA_BASE_URL = "http://localhost:11434"   # default Ollama address
OLLAMA_MODEL    = "gemma:2b"

# ---------------------------------------------------------------------------
# Vector store (ChromaDB — fully local)
# ---------------------------------------------------------------------------
CHROMA_DB_PATH  = str(_DATA_DIR / "chroma_db")
COLLECTION_NAME = "codebase"

# Number of chunks returned per semantic search
TOP_K = 5

# Embedding model (loaded from local HF cache — no internet after first download)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ---------------------------------------------------------------------------
# GitHub repo cache
# ---------------------------------------------------------------------------
REPOS_CACHE_DIR = str(_DATA_DIR / "repos")

# ---------------------------------------------------------------------------
# File discovery — what to index
# ---------------------------------------------------------------------------

# File extensions to include. Structured languages get AST parsing;
# everything else falls through to simple (raw content) mode.
SUPPORTED_EXTENSIONS = {
    # Structured (AST parsed via tree-sitter)
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    # Simple text / config files
    ".md", ".markdown", ".txt", ".rst",
    ".json", ".toml", ".yaml", ".yml",
    ".env", ".ini", ".cfg", ".conf",
}

# Exact filenames (no extension) to include regardless of extension rules
SUPPORTED_FILENAMES = {"Dockerfile", "Makefile", ".gitignore", ".dockerignore"}

# ---------------------------------------------------------------------------
# File discovery — what to skip
# ---------------------------------------------------------------------------

# Directories to never descend into
SKIP_DIRS = {
    "venv", ".venv", "__pycache__", "node_modules",
    "dist", "build", ".git", "vendor", "third_party",
    "site-packages",
    # Documentation / example folders (common in large repos, not source code)
    "docs", "doc", "documentation",
    "examples", "example", "benchmarks", "bench",
}

# Filenames starting with these prefixes are skipped (test files)
SKIP_FILE_PREFIXES = ("test_", "spec_")

# Filenames ending with these suffixes are skipped (test files)
SKIP_FILE_SUFFIXES = (
    "_test.py", "_test.js", "_test.ts",
    ".test.js", ".test.ts",
    ".spec.js", ".spec.ts",
    "_spec.rb",
)

# ---------------------------------------------------------------------------
# Simple file type mapping (extension/filename → language label)
# Used by parser.py to tag document-mode chunks
# ---------------------------------------------------------------------------

SIMPLE_EXTENSIONS = {
    ".md":       "markdown",
    ".markdown": "markdown",
    ".txt":      "text",
    ".rst":      "restructuredtext",
    ".json":     "json",
    ".toml":     "toml",
    ".yaml":     "yaml",
    ".yml":      "yaml",
    ".env":      "env",
    ".ini":      "config",
    ".cfg":      "config",
    ".conf":     "config",
}

# Exact filenames → language label
SIMPLE_FILENAMES = {
    "Dockerfile":    "dockerfile",
    "Makefile":      "makefile",
    ".gitignore":    "gitignore",
    ".dockerignore": "gitignore",
}
