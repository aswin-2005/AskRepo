"""
main.py
-------
CLI entry point for AskRepo — chat with any codebase.
Run with no arguments to see usage.
"""

import sys
import os
from askrepo import config
from askrepo import parser as code_parser
from askrepo import chunker
from askrepo import describer
from askrepo import store
from askrepo import query as query_module
from askrepo import github_fetcher
from askrepo import ui

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
                if fname.startswith(SKIP_FILE_PREFIXES):
                    continue
                if any(fname.endswith(s) for s in SKIP_FILE_SUFFIXES):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in SUPPORTED_EXTENSIONS or fname in SUPPORTED_FILENAMES:
                    files.append(os.path.join(root, fname))
    else:
        ui.print_error(f"'{target_path}' is not a valid file or directory.")
        sys.exit(1)

    if not files:
        ui.print_warning("No supported files found.")
        return

    ui.print_info(f"Found {len(files)} file(s) to index.")
    ui.console.print()

    # Build the live file table + overall file-level progress bar
    table = ui.index_file_table()
    overall_prog, overall_task = ui.make_overall_progress(len(files))

    from rich.live import Live
    from rich.console import Group

    total_chunks_indexed = 0

    with Live(
        Group(table, overall_prog),
        console=ui.console,
        refresh_per_second=12,
        vertical_overflow="visible",
    ) as live:
        for file_path in files:
            parsed = code_parser.parse_file(file_path)

            if parsed is None:
                ui.index_add_row(table, file_path, "?", 0, 0, 0, skipped=True)
                overall_prog.advance(overall_task)
                continue

            n_funcs   = len(parsed["functions"])
            n_classes = len(parsed["classes"])
            lang      = parsed.get("language", "?")

            chunks = chunker.build_chunks(parsed)

            # Temporarily stop the live display so describer spinners can render cleanly
            live.stop()
            chunks = describer.describe_all(chunks, verbose=True)
            live.start()

            store.add_chunks(chunks)
            total_chunks_indexed += len(chunks)

            ui.index_add_row(
                table, file_path, lang, n_funcs, n_classes, len(chunks)
            )
            overall_prog.advance(overall_task)

    ui.console.print()
    total = store.collection_count()
    ui.print_success(f"Done — {total_chunks_indexed} new chunks indexed  "
                     f"(index total: {total})")
    ui.console.print()


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
        ui.print_warning("Index is empty. Run `askrepo index <path>` first.")
        return

    by_path: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for meta in all_meta:
        source, rel = _parse_path(meta.get("path", "unknown"))
        by_path[(source, rel)].append(meta)

    by_source: dict[str, list[str]] = defaultdict(list)
    for (source, rel) in by_path:
        by_source[source].append(rel)

    ui.print_list_index(by_source, by_path, len(all_meta))


def run_query(user_query: str, verbose: bool = False) -> None:
    """Run a natural language query against the indexed codebase."""
    if store.collection_count() == 0:
        ui.print_warning("Index is empty. Run `askrepo index <path>` first.")
        return

    answer, chunks, backend, model = query_module.answer(
        user_query, verbose=verbose
    )
    ui.print_query_result(user_query, chunks, answer, verbose, backend, model)


def main():
    if len(sys.argv) < 2:
        ui.print_help()
        sys.exit(0)

    command = sys.argv[1].lower()

    if command == "index":
        if len(sys.argv) < 3:
            ui.print_error("Usage: askrepo index <file_or_directory>")
            sys.exit(1)
        index_path(sys.argv[2])

    elif command == "index-repo":
        if len(sys.argv) < 3:
            ui.print_error(
                "Usage: askrepo index-repo <owner/repo or URL> [--branch <branch>]"
            )
            sys.exit(1)
        repo_input = sys.argv[2]
        branch = None
        if "--branch" in sys.argv:
            idx = sys.argv.index("--branch")
            if idx + 1 < len(sys.argv):
                branch = sys.argv[idx + 1]
        try:
            clone_dir, repo_name = github_fetcher.clone_repo(repo_input, branch=branch)
            ui.print_info(f"Indexing repo: [bold white]{repo_name}[/]")
            ui.console.print()
            index_path(clone_dir)
        except RuntimeError as e:
            ui.print_error(str(e))
            sys.exit(1)

    elif command == "list":
        list_index()

    elif command == "query":
        if len(sys.argv) < 3:
            ui.print_error('Usage: askrepo query "<your question>" [--verbose]')
            sys.exit(1)
        verbose = "--verbose" in sys.argv
        args = [a for a in sys.argv[2:] if a != "--verbose"]
        run_query(" ".join(args), verbose=verbose)

    elif command == "clear":
        store.clear_collection()
        ui.print_clear()

    elif command == "count":
        n = store.collection_count()
        ui.print_count(n)

    else:
        ui.print_error(f"Unknown command: '{command}'")
        ui.print_info("Run 'askrepo' with no arguments to see available commands.")
        sys.exit(1)


if __name__ == "__main__":
    main()
