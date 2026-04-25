"""
chunker.py
----------
Converts parser output into the final chunk dicts.

Two modes based on parsed["mode"]:
  - structured: 1 file chunk + N function chunks + N class chunks + N method chunks
  - simple:     1 document chunk (markdown, json, toml, yaml, txt, etc.)

The `verbal` field is left empty here — describer.py fills it in.
"""

import os


def _import_names(imports: list[dict]) -> list[str]:
    """Flatten imports into a simple list of module name strings."""
    names = []
    for imp in imports:
        names.append(imp["module"])
    return names


def _build_file_chunk(parsed: dict) -> dict:
    return {
        "type": "file",
        "path": parsed["path"],
        "language": parsed["language"],
        "verbal": "",                                     # filled by describer
        "imports": _import_names(parsed["imports"]),
        "functions": [f["name"] for f in parsed["functions"]],
        "classes": [c["name"] for c in parsed["classes"]],
        "globals": [g["name"] for g in parsed["globals"]],
        "raw_code": parsed["raw_code"],
    }


def _build_function_chunk(func: dict, file_path: str, language: str,
                          class_name: str | None = None) -> dict:
    chunk_type = "method" if class_name else "function"
    return {
        "type": chunk_type,
        "path": file_path,
        "language": language,
        "class_name": class_name,
        "name": func["name"],
        "verbal": "",                                     # filled by describer
        "params": func["params"],
        "returns": func["returns"],
        "decorators": func["decorators"],
        "docstring": func["docstring"],
        "calls": func["calls"],
        "raw_code": func["raw_code"],
    }


def _build_class_chunk(cls: dict, file_path: str, language: str) -> dict:
    return {
        "type": "class",
        "path": file_path,
        "language": language,
        "name": cls["name"],
        "verbal": "",                                     # filled by describer
        "inherits": cls["inherits"],
        "decorators": cls["decorators"],
        "docstring": cls["docstring"],
        "constructor": cls["constructor"],
        "variables": cls["variables"],
        "methods": [m["name"] for m in cls["methods"]],
        "raw_code": cls["raw_code"],
    }


def _build_document_chunk(parsed: dict) -> dict:
    """Single chunk for simple text/config files — no structural breakdown."""
    return {
        "type": "document",
        "path": parsed["path"],
        "language": parsed["language"],
        "verbal": "",                     # filled by describer
        "raw_code": parsed["raw_code"],
    }


def build_chunks(parsed: dict) -> list[dict]:
    """
    Given parser output, return a list of chunk dicts.

    Simple files  → [document chunk]
    Structured    → [file chunk] + function chunks + class chunks + method chunks
    """
    # Simple file mode (markdown, json, toml, yaml, txt, etc.)
    if parsed.get("mode") == "simple":
        return [_build_document_chunk(parsed)]

    # Structured mode (Python, JS, TS)
    chunks = []
    path = parsed["path"]
    lang = parsed["language"]

    # 1. File chunk
    chunks.append(_build_file_chunk(parsed))

    # 2. Top-level functions
    for func in parsed["functions"]:
        chunks.append(_build_function_chunk(func, path, lang))

    # 3. Classes + their methods
    for cls in parsed["classes"]:
        chunks.append(_build_class_chunk(cls, path, lang))

        # Constructor as its own method chunk
        if cls["constructor"]:
            chunks.append(_build_function_chunk(
                cls["constructor"], path, lang, class_name=cls["name"]
            ))

        # Each method
        for method in cls["methods"]:
            chunks.append(_build_function_chunk(
                method, path, lang, class_name=cls["name"]
            ))

    return chunks
