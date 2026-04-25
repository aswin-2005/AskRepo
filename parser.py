"""
parser.py
---------
Reads a source file and extracts its components.

Supports two modes:
  - structured: AST-based extraction via tree-sitter (Python, JS, TS, TSX)
  - simple:     raw content read for text/config files (md, json, toml, yaml, etc.)

To add a new structured language: register it in LANGUAGE_REGISTRY with its
loader function and ensure JS-style or Python-style extractor handles it.
To add a new simple file type: add the extension to SIMPLE_EXTENSIONS.
"""

import config
import os
from tree_sitter import Language, Parser

# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------
def _load_python():
    import tree_sitter_python as tspython
    return Language(tspython.language())

def _load_javascript():
    import tree_sitter_javascript as tsjs
    return Language(tsjs.language())

def _load_typescript():
    import tree_sitter_typescript as tsts
    return Language(tsts.language_typescript())

def _load_tsx():
    import tree_sitter_typescript as tsts
    return Language(tsts.language_tsx())



# Maps file extension → (language_name, loader_fn)
LANGUAGE_REGISTRY = {
    ".py":  ("python",     _load_python),
    ".js":  ("javascript", _load_javascript),
    ".mjs": ("javascript", _load_javascript),
    ".cjs": ("javascript", _load_javascript),
    ".ts":  ("typescript", _load_typescript),
    ".tsx": ("tsx",        _load_tsx),
}


# ---------------------------------------------------------------------------
# Shared AST helpers (used by all structured extractors)
# ---------------------------------------------------------------------------
def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_child_by_type(node, type_name: str):
    for child in node.children:
        if child.type == type_name:
            return child
    return None


# ===========================================================================
# PYTHON EXTRACTOR
# ===========================================================================
def _py_get_docstring(body_node, source: bytes) -> str | None:
    if body_node is None:
        return None
    for child in body_node.children:
        if child.type == "expression_statement":
            inner = child.children[0] if child.children else None
            if inner and inner.type == "string":
                raw = _node_text(inner, source)
                return raw.strip('"""').strip("'''").strip('"').strip("'").strip()
    return None


def _py_extract_params(parameters_node, source: bytes) -> list[dict]:
    params = []
    if parameters_node is None:
        return params
    for child in parameters_node.children:
        if child.type == "identifier":
            name = _node_text(child, source)
            if name == "self":
                continue
            params.append({"name": name, "type": None})
        elif child.type in ("typed_parameter", "typed_default_parameter"):
            name_node = _get_child_by_type(child, "identifier")
            type_node = _get_child_by_type(child, "type")
            name = _node_text(name_node, source) if name_node else "?"
            if name == "self":
                continue
            params.append({"name": name, "type": _node_text(type_node, source) if type_node else None})
        elif child.type == "default_parameter":
            name_node = _get_child_by_type(child, "identifier")
            name = _node_text(name_node, source) if name_node else "?"
            if name == "self":
                continue
            params.append({"name": name, "type": None})
    return params


def _py_extract_calls(body_node, source: bytes, imports: list[dict]) -> list[dict]:
    calls = []
    if body_node is None:
        return calls
    import_map = {}
    for imp in imports:
        if imp["kind"] == "from":
            for item in imp["items"]:
                import_map[item] = imp["module"]
        else:
            alias = imp.get("alias") or imp["module"].split(".")[0]
            import_map[alias] = imp["module"]

    def walk(node):
        if node.type == "call":
            func_node = _get_child_by_type(node, "identifier")
            if func_node is None:
                attr = _get_child_by_type(node, "attribute")
                if attr:
                    obj_node = attr.children[0] if attr.children else None
                    method_node = attr.children[-1] if attr.children else None
                    if obj_node and method_node:
                        obj = _node_text(obj_node, source)
                        method = _node_text(method_node, source)
                        calls.append({"name": f"{obj}.{method}", "resolved_path": import_map.get(obj)})
            else:
                name = _node_text(func_node, source)
                calls.append({"name": name, "resolved_path": import_map.get(name)})
        for child in node.children:
            walk(child)
    walk(body_node)
    return calls


def _py_extract_imports(root, source: bytes) -> list[dict]:
    imports = []
    for node in root.children:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append({"kind": "import", "module": _node_text(child, source), "alias": None, "items": []})
                elif child.type == "aliased_import":
                    name_node = child.children[0]
                    alias_node = child.children[-1]
                    imports.append({"kind": "import", "module": _node_text(name_node, source),
                                    "alias": _node_text(alias_node, source), "items": []})
        elif node.type == "import_from_statement":
            mod_node = _get_child_by_type(node, "dotted_name") or _get_child_by_type(node, "relative_import")
            module = _node_text(mod_node, source) if mod_node else "?"
            items = []
            for child in node.children:
                if child.type == "import_list":
                    for item in child.children:
                        if item.type in ("identifier", "dotted_name"):
                            items.append(_node_text(item, source))
            imports.append({"kind": "from", "module": module, "alias": None, "items": items})
    return imports


def _py_extract_function(node, source: bytes, imports: list[dict]) -> dict:
    name_node = _get_child_by_type(node, "identifier")
    params_node = _get_child_by_type(node, "parameters")
    body_node = _get_child_by_type(node, "block")
    decorators = []
    for child in node.children:
        if child.type == "decorator":
            decorators.append(_node_text(child, source).lstrip("@").strip())
    return_type = None
    for child in node.children:
        if child.type == "type":
            return_type = _node_text(child, source)
    return {
        "name": _node_text(name_node, source) if name_node else "?",
        "params": _py_extract_params(params_node, source),
        "returns": return_type,
        "decorators": decorators,
        "docstring": _py_get_docstring(body_node, source),
        "calls": _py_extract_calls(body_node, source, imports),
        "raw_code": _node_text(node, source),
    }


def _py_extract_class(node, source: bytes, imports: list[dict]) -> dict:
    name_node = _get_child_by_type(node, "identifier")
    body_node = _get_child_by_type(node, "block")
    inherits = []
    arg_list = _get_child_by_type(node, "argument_list")
    if arg_list:
        for child in arg_list.children:
            if child.type == "identifier":
                inherits.append(_node_text(child, source))
    methods, constructor, class_vars = [], None, []
    if body_node:
        for child in body_node.children:
            if child.type == "function_definition":
                m = _py_extract_function(child, source, imports)
                if m["name"] == "__init__":
                    constructor = m
                else:
                    methods.append(m)
            elif child.type == "expression_statement":
                inner = child.children[0] if child.children else None
                if inner and inner.type == "assignment":
                    class_vars.append(_node_text(inner.children[0], source))
    decorators = []
    for child in node.children:
        if child.type == "decorator":
            decorators.append(_node_text(child, source).lstrip("@").strip())
    return {
        "name": _node_text(name_node, source) if name_node else "?",
        "inherits": inherits,
        "decorators": decorators,
        "docstring": _py_get_docstring(body_node, source),
        "constructor": constructor,
        "variables": class_vars,
        "methods": methods,
        "raw_code": _node_text(node, source),
    }


def _py_extract_globals(root, source: bytes) -> list[dict]:
    globals_ = []
    for node in root.children:
        if node.type == "expression_statement":
            inner = node.children[0] if node.children else None
            if inner and inner.type == "assignment":
                left = inner.children[0]
                right_nodes = [c for c in inner.children if c != left and c.type != "="]
                value = _node_text(right_nodes[0], source) if right_nodes else None
                globals_.append({"name": _node_text(left, source), "value": value})
    return globals_


def _parse_python(file_path: str, source: bytes, language) -> dict:
    parser = Parser(language)
    root = parser.parse(source).root_node
    imports = _py_extract_imports(root, source)
    functions = [_py_extract_function(n, source, imports) for n in root.children if n.type == "function_definition"]
    classes = [_py_extract_class(n, source, imports) for n in root.children if n.type == "class_definition"]
    globals_ = _py_extract_globals(root, source)
    return {
        "path": file_path, "language": "python", "mode": "structured",
        "imports": imports, "functions": functions, "classes": classes,
        "globals": globals_, "raw_code": source.decode("utf-8", errors="replace"),
    }


# ===========================================================================
# JAVASCRIPT / TYPESCRIPT EXTRACTOR
# ===========================================================================
def _js_extract_params(params_node, source: bytes) -> list[dict]:
    params = []
    if params_node is None:
        return params
    for child in params_node.children:
        t = child.type
        if t == "identifier":
            params.append({"name": _node_text(child, source), "type": None})
        elif t == "assignment_pattern":
            left = child.children[0] if child.children else None
            params.append({"name": _node_text(left, source) if left else "?", "type": None})
        elif t == "rest_pattern":
            inner = child.children[-1] if child.children else None
            params.append({"name": "..." + (_node_text(inner, source) if inner else "?"), "type": None})
        elif t in ("required_parameter", "optional_parameter"):
            # TypeScript typed / optional params
            name_node = child.children[0] if child.children else None
            name = _node_text(name_node, source) if name_node else "?"
            if t == "optional_parameter":
                name += "?"
            type_str = None
            for c in child.children:
                if c.type == "type_annotation":
                    for tc in c.children:
                        if tc.type != ":":
                            type_str = _node_text(tc, source)
                            break
            params.append({"name": name, "type": type_str})
    return params


def _js_extract_calls(body_node, source: bytes, imports: list[dict]) -> list[dict]:
    calls = []
    if body_node is None:
        return calls
    import_map = {}
    for imp in imports:
        for item in imp.get("items", []):
            import_map[item] = imp["module"]

    def walk(node):
        if node.type == "call_expression":
            func = node.children[0] if node.children else None
            if func:
                if func.type == "identifier":
                    name = _node_text(func, source)
                    calls.append({"name": name, "resolved_path": import_map.get(name)})
                elif func.type == "member_expression":
                    parts = [c for c in func.children if c.type not in (".",)]
                    if len(parts) >= 2:
                        obj = _node_text(parts[0], source)
                        prop = _node_text(parts[-1], source)
                        calls.append({"name": f"{obj}.{prop}", "resolved_path": import_map.get(obj)})
        for child in node.children:
            walk(child)
    walk(body_node)
    return calls


def _js_extract_imports(root, source: bytes) -> list[dict]:
    imports = []
    for node in root.children:
        if node.type == "import_declaration":
            module, items = None, []
            for child in node.children:
                if child.type == "string":
                    module = _node_text(child, source).strip("'\"`")
                elif child.type == "import_clause":
                    for ic in child.children:
                        if ic.type == "identifier":
                            items.append(_node_text(ic, source))
                        elif ic.type == "named_imports":
                            for spec in ic.children:
                                if spec.type == "import_specifier":
                                    n = spec.children[0] if spec.children else None
                                    if n:
                                        items.append(_node_text(n, source))
                        elif ic.type == "namespace_import":
                            n = ic.children[-1] if ic.children else None
                            if n:
                                items.append("* as " + _node_text(n, source))
            if module:
                imports.append({"kind": "from", "module": module, "alias": None, "items": items})
    return imports


def _js_fn_data(name: str, fn_node, source: bytes, imports: list[dict]) -> dict:
    """Extract a function dict from any function-like node."""
    params_node, body_node, return_type = None, None, None
    for child in fn_node.children:
        if child.type == "formal_parameters":
            params_node = child
        elif child.type == "statement_block":
            body_node = child
        elif child.type == "type_annotation" and body_node is None:
            for tc in child.children:
                if tc.type != ":":
                    return_type = _node_text(tc, source)
                    break
    return {
        "name": name,
        "params": _js_extract_params(params_node, source),
        "returns": return_type,
        "decorators": [],
        "docstring": None,
        "calls": _js_extract_calls(body_node, source, imports),
        "raw_code": _node_text(fn_node, source),
    }


def _is_fn_node(node) -> bool:
    return node.type in ("arrow_function", "function_expression", "function")


def _js_extract_functions(root, source: bytes, imports: list[dict]) -> list[dict]:
    functions = []

    def from_lexical(decl_node):
        for declarator in decl_node.children:
            if declarator.type == "variable_declarator":
                name_node = declarator.children[0] if declarator.children else None
                val_node = declarator.children[-1] if len(declarator.children) > 1 else None
                if name_node and val_node and _is_fn_node(val_node):
                    fn = _js_fn_data(_node_text(name_node, source), val_node, source, imports)
                    fn["raw_code"] = _node_text(decl_node, source)
                    functions.append(fn)

    for node in root.children:
        if node.type == "function_declaration":
            n = _get_child_by_type(node, "identifier")
            functions.append(_js_fn_data(_node_text(n, source) if n else "?", node, source, imports))
        elif node.type in ("lexical_declaration", "variable_declaration"):
            from_lexical(node)
        elif node.type in ("export_statement", "export_declaration"):
            for child in node.children:
                if child.type == "function_declaration":
                    n = _get_child_by_type(child, "identifier")
                    functions.append(_js_fn_data(_node_text(n, source) if n else "?", child, source, imports))
                elif child.type in ("lexical_declaration", "variable_declaration"):
                    from_lexical(child)
    return functions


def _js_extract_classes(root, source: bytes, imports: list[dict]) -> list[dict]:
    classes = []

    def extract_class(node):
        name_node = _get_child_by_type(node, "identifier") or _get_child_by_type(node, "type_identifier")
        name = _node_text(name_node, source) if name_node else "?"
        inherits = []
        for child in node.children:
            if child.type in ("class_heritage", "extends_clause"):
                for ic in child.children:
                    if ic.type in ("identifier", "type_identifier"):
                        inherits.append(_node_text(ic, source))
        body_node = _get_child_by_type(node, "class_body")
        methods, constructor = [], None
        if body_node:
            for child in body_node.children:
                if child.type == "method_definition":
                    prop = child.children[0] if child.children else None
                    method_name = _node_text(prop, source) if prop else "?"
                    params_node = _get_child_by_type(child, "formal_parameters")
                    body = _get_child_by_type(child, "statement_block")
                    method = {
                        "name": method_name,
                        "params": _js_extract_params(params_node, source),
                        "returns": None,
                        "decorators": [],
                        "docstring": None,
                        "calls": _js_extract_calls(body, source, imports),
                        "raw_code": _node_text(child, source),
                    }
                    if method_name == "constructor":
                        constructor = method
                    else:
                        methods.append(method)
        return {
            "name": name, "inherits": inherits, "decorators": [],
            "docstring": None, "constructor": constructor,
            "variables": [], "methods": methods,
            "raw_code": _node_text(node, source),
        }

    for node in root.children:
        if node.type == "class_declaration":
            classes.append(extract_class(node))
        elif node.type in ("export_statement", "export_declaration"):
            for child in node.children:
                if child.type == "class_declaration":
                    classes.append(extract_class(child))
    return classes


def _js_extract_globals(root, source: bytes, fn_names: set) -> list[dict]:
    globals_ = []
    for node in root.children:
        if node.type in ("lexical_declaration", "variable_declaration"):
            for declarator in node.children:
                if declarator.type == "variable_declarator":
                    name_node = declarator.children[0] if declarator.children else None
                    val_node = declarator.children[-1] if len(declarator.children) > 1 else None
                    if name_node:
                        name = _node_text(name_node, source)
                        if name in fn_names:
                            continue
                        if val_node and _is_fn_node(val_node):
                            continue
                        value = _node_text(val_node, source) if val_node and val_node != name_node else None
                        globals_.append({"name": name, "value": value})
    return globals_


def _parse_js_ts(file_path: str, source: bytes, lang_name: str, language) -> dict:
    parser = Parser(language)
    root = parser.parse(source).root_node
    imports = _js_extract_imports(root, source)
    functions = _js_extract_functions(root, source, imports)
    classes = _js_extract_classes(root, source, imports)
    globals_ = _js_extract_globals(root, source, {f["name"] for f in functions})
    return {
        "path": file_path, "language": lang_name, "mode": "structured",
        "imports": imports, "functions": functions, "classes": classes,
        "globals": globals_, "raw_code": source.decode("utf-8", errors="replace"),
    }


# ===========================================================================
# SIMPLE FILE READER (markdown, json, toml, yaml, txt, etc.)
# ===========================================================================
def _parse_simple(file_path: str, language: str) -> dict | None:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return None
    return {
        "path": file_path,
        "language": language,
        "mode": "simple",
        "imports": [],
        "functions": [],
        "classes": [],
        "globals": [],
        "raw_code": content,
    }


# ===========================================================================
# PUBLIC API
# ===========================================================================
def parse_file(file_path: str) -> dict | None:
    """
    Parse a source file and return extracted components, or None if unsupported.

    Dispatches to:
      - structured parser (tree-sitter) for .py / .js / .ts / .tsx
      - simple reader for .md / .json / .toml / .yaml / .txt / etc.
    """
    ext = os.path.splitext(file_path)[1].lower()
    filename = os.path.basename(file_path)

    # --- Structured languages ---
    if ext in LANGUAGE_REGISTRY:
        lang_name, lang_loader = LANGUAGE_REGISTRY[ext]
        language = lang_loader()
        with open(file_path, "rb") as f:
            source = f.read()
        if lang_name == "python":
            return _parse_python(file_path, source, language)
        else:
            return _parse_js_ts(file_path, source, lang_name, language)

    # --- Simple files by extension ---
    if ext in config.SIMPLE_EXTENSIONS:
        return _parse_simple(file_path, config.SIMPLE_EXTENSIONS[ext])

    # --- Simple files by exact filename ---
    if filename in config.SIMPLE_FILENAMES:
        return _parse_simple(file_path, config.SIMPLE_FILENAMES[filename])

    return None
