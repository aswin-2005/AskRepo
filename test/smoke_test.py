import sys
sys.path.insert(0, 'f:/codes/PROJECTS/rag')
import parser as p, chunker

files = [
    'f:/codes/PROJECTS/rag/sample_app/frontend/api.js',
    'f:/codes/PROJECTS/rag/sample_app/README.md',
    'f:/codes/PROJECTS/rag/sample_app/frontend/package.json',
]

for fp in files:
    result = p.parse_file(fp)
    chunks = chunker.build_chunks(result)
    mode = result["mode"]
    lang = result["language"]
    fname = fp.split("/")[-1]
    print(f"[{mode}] {lang} | {fname}")
    for c in chunks:
        name = c.get("name") or fname
        ctype = c["type"]
        print(f"   {ctype:10} {name}")
    print()
