from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python as tspython

# 1. Setup
PY_LANGUAGE = Language(tspython.language())
parser = Parser(PY_LANGUAGE)

code = """ 
import hashlib

def hash_password(password: str) -> str:
    \"\"\"Returns a SHA256 hash of the password.\"\"\"
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    hashed = hash_password(password)
    return {"user": username, "status": "success"}
"""

# 2. Query Definition
# We use parentheses to group and tags (@) to capture
query_text = """
(function_definition
  name: (identifier) @name
  body: (block (expression_statement (string) @docstring)?) @body)

(call
  function: (identifier) @call.name)
"""

# 3. Execution
tree = parser.parse(bytes(code, "utf8"))
query = Query(PY_LANGUAGE, query_text)

# 4. Execute via QueryCursor (required in tree-sitter >= 0.25)
cursor = QueryCursor(query)
# captures() returns dict[str, list[Node]] keyed by capture name
captures = cursor.captures(tree.root_node)

# 5. Printing
print("--- Results ---")
for tag, nodes in captures.items():
    for node in nodes:
        print(f"Tag: {tag} | Text: {node.text.decode('utf8')}")