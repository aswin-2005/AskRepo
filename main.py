"""
main.py
-------
CLI entry point for AskRepo — chat with any codebase.

Commands:
  askrepo index <path>                     Index a local file or directory
  askrepo index-repo <owner/repo>          Clone and index a public GitHub repo
  askrepo list                             Show everything currently in the index
  askrepo query "<question>"               Ask a question about indexed code
  askrepo clear                            Wipe the index
  askrepo count                            Show total chunk count

Examples:
  askrepo index ./myproject
  askrepo index auth.py
  askrepo index-repo fastapi/fastapi
  askrepo index-repo https://github.com/psf/requests
  askrepo index-repo django/django --branch stable/4.2.x
  askrepo query "what does hash_password do?"
  askrepo count
"""

import sys
import os
import config
import parser as code_parser
import chunker
import describer
import store
import query as query_module
import github_fetcher

# All file-discovery settings live in config.py
SUPPORTED_EXTENSIONS = config.SUPPORTED_EXTENSIONS
SUPPORTED_FILENAMES  = config.SUPPORTED_FILENAMES
SKIP_DIRS            = config.SKIP_DIRS
SKIP_FILE_PREFIXES   = config.SKIP_FILE_PREFIXES
SKIP_FILE_SUFFIXES   = config.SKIP_FILE_SUFFIXES


def index_path(target_path: str) -> None:
    """Index a single file or all supported files in a directory."""
    if os.path.isfile(target_path):
        files = [target_path]
    elif os.path.isdir(target_path):
        files = []
        for root, dirs, filenames in os.walk(target_path):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in SKIP_DIRS
            ]
            for fname in filenames:
                # Skip test / spec files
                if fname.startswith(SKIP_FILE_PREFIXES):
                    continue
                if any(fname.endswith(s) for s in SKIP_FILE_SUFFIXES):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in SUPPORTED_EXTENSIONS or fname in SUPPORTED_FILENAMES:
                    files.append(os.path.join(root, fname))
    else:
        print(f"Error: '{target_path}' is not a valid file or directory.")
        sys.exit(1)

    if not files:
        print("No supported files found.")
        return

    print(f"Found {len(files)} file(s) to index.\n")

    for file_path in files:
        print(f"Parsing: {file_path}")
        parsed = code_parser.parse_file(file_path)
        if parsed is None:
            print(f"  Skipped (unsupported language).")
            continue

        n_funcs = len(parsed["functions"])
        n_classes = len(parsed["classes"])
        print(f"  -> {n_funcs} function(s), {n_classes} class(es) found.")

        chunks = chunker.build_chunks(parsed)
        print(f"  -> {len(chunks)} chunk(s) to describe.")

        chunks = describer.describe_all(chunks, verbose=True)
        store.add_chunks(chunks)
        print()

    total = store.collection_count()
    print(f"Done. Total chunks in index: {total}")


def _parse_path(raw_path: str) -> tuple[str, str]:
    """
    Normalize a chunk's raw path into (source_label, relative_path).

    GitHub repos (./repos/owner_reponame/...)  -> source = 'owner/reponame'
    Local paths  (./sample_app/frontend/...)   -> source = 'sample_app'
    """
    p = raw_path.replace("\\", "/").strip()

    if "/repos/" in p:
        after = p.split("/repos/", 1)[1]
        parts = after.split("/", 1)
        repo_dir = parts[0]
        rel = parts[1] if len(parts) > 1 else ""
        source = repo_dir.replace("_", "/", 1)
        return source, rel

    p = p.lstrip("./")
    parts = [x for x in p.split("/") if x]
    if len(parts) > 1:
        return parts[0], "/".join(parts[1:])
    return "local", p


def list_index() -> None:
    """Print a structured breakdown of everything in the current index."""
    from collections import defaultdict

    all_meta = store.get_all_metadata()
    if not all_meta:
        print("Index is empty. Run `python main.py index <path>` first.")
        return

    by_path = defaultdict(list)
    for meta in all_meta:
        source, rel = _parse_path(meta.get("path", "unknown"))
        by_path[(source, rel)].append(meta)

    by_source = defaultdict(list)
    for (source, rel) in by_path:
        by_source[source].append(rel)

    total_chunks  = len(all_meta)
    total_files   = len(by_path)
    total_sources = len(by_source)

    print(f"\n{'='*62}")
    print(f"  INDEX BREAKDOWN")
    print(f"{'='*62}")
    print(f"  Sources : {total_sources}")
    print(f"  Files   : {total_files}")
    print(f"  Chunks  : {total_chunks}")

    for source in sorted(by_source.keys()):
        rels = sorted(by_source[source])
        source_chunks = sum(len(by_path[(source, r)]) for r in rels)
        print(f"\n{'-'*62}")
        print(f"  Source : {source}   ({len(rels)} files | {source_chunks} chunks)")
        print(f"{'-'*62}")

        for rel in rels:
            chunks = by_path[(source, rel)]
            lang = chunks[0].get("language", "?")
            n = len(chunks)

            type_counts = {}
            for c in chunks:
                t = c.get("type", "?")
                type_counts[t] = type_counts.get(t, 0) + 1
            type_summary = ", ".join(
                f"{cnt}x {t}" if cnt > 1 else t
                for t, cnt in type_counts.items()
            )

            names = [c["name"] for c in chunks if c.get("name") and c.get("type") != "file"]
            names_str = ""
            if names:
                preview = names[:4]
                extra = len(names) - 4
                names_str = "  ->  " + ", ".join(preview)
                if extra > 0:
                    names_str += f" (+{extra} more)"

            print(f"  {rel:<38} {lang:<12} {n:>3} chunk{'s' if n!=1 else ' '}  [{type_summary}]{names_str}")

    print(f"\n{'='*62}\n")


def run_query(user_query: str) -> None:
    """Run a natural language query against the indexed codebase."""
    if store.collection_count() == 0:
        print("Index is empty. Run `python main.py index <path>` first.")
        return

    print(f"\nQuery: {user_query}\n")
    answer = query_module.answer(user_query, verbose=True)
    print("\n--- Answer ---")
    print(answer)
    print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1].lower()

    if command == "index":
        if len(sys.argv) < 3:
            print("Usage: python main.py index <file_or_directory>")
            sys.exit(1)
        index_path(sys.argv[2])

    elif command == "index-repo":
        if len(sys.argv) < 3:
            print("Usage: python main.py index-repo <owner/repo or URL> [--branch <branch>]")
            sys.exit(1)
        repo_input = sys.argv[2]
        branch = None
        if "--branch" in sys.argv:
            idx = sys.argv.index("--branch")
            if idx + 1 < len(sys.argv):
                branch = sys.argv[idx + 1]
        try:
            clone_dir, repo_name = github_fetcher.clone_repo(repo_input, branch=branch)
            print(f"\nIndexing repo: {repo_name}\n")
            index_path(clone_dir)
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif command == "list":
        list_index()

    elif command == "query":
        if len(sys.argv) < 3:
            print('Usage: askrepo query "<your question>"')
            sys.exit(1)
        run_query(" ".join(sys.argv[2:]))

    elif command == "clear":
        store.clear_collection()

    elif command == "count":
        n = store.collection_count()
        print(f"Chunks in index: {n}")

    else:
        print(f"Unknown command: '{command}'")
        print("Run 'askrepo' with no arguments to see available commands.")
        sys.exit(1)


if __name__ == "__main__":
    main()
