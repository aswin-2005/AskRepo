"""
ui.py
-----
Centralized display layer for AskRepo.
All terminal output goes through here — no other module should import rich directly.

Public API
----------
  banner()                          Print the AskRepo header once
  print_help()                      Styled help/usage screen
  print_success(msg)                ✓ green message
  print_error(msg)                  ✗ red message
  print_warning(msg)                ⚠ yellow message
  print_info(msg)                   · dim message

  index_start(total)               → returns (progress, task_id, table, live) context
  index_add_row(table, ...)         Add a file row to the indexing live table
  index_finish(live, total)         Close live display, print summary

  describe_progress(chunks)        → context manager: spinner + overall bar while describing

  print_list_index(by_source, by_path, total_chunks)
  print_query_result(chunks, answer, verbose)
  print_count(n)
  print_clear()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Console — single instance, custom theme
# ---------------------------------------------------------------------------
_theme = Theme(
    {
        "brand":    "bold cyan",
        "success":  "bold green",
        "error":    "bold red",
        "warning":  "bold yellow",
        "info":     "dim white",
        "path":     "dim white",
        "lang.py":  "bold blue",
        "lang.js":  "bold yellow",
        "lang.ts":  "bold cyan",
        "lang.md":  "bold magenta",
        "lang.cfg": "dim white",
        "lang.doc": "dim white",
        "chunk":    "white",
        "score":    "dim cyan",
        "header":   "bold white",
    }
)

import sys as _sys
import io as _io
# Force UTF-8 output on Windows so rich symbols don't crash with cp1252
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(_sys.stderr, "reconfigure"):
    try:
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(theme=_theme, highlight=False, stderr=False)


# ---------------------------------------------------------------------------
# ASCII banner
# ---------------------------------------------------------------------------
_LOGO = r"""
   _         _    ____
  / \   ___ | | _|  _ \ ___ _ __   ___
 / _ \ / __|| |/ / |_) / _ \ '_ \ / _ \
/ ___ \\__ \|   <|  _ <  __/ |_) | (_) |
/_/   \_\___/|_|\_\_| \_\___| .__/ \___/
                              |_|
"""


def banner() -> None:
    """Print the AskRepo ASCII logo + subtitle."""
    logo_text = Text(_LOGO.rstrip("\n"), style="bold cyan", justify="left")
    subtitle = Text("  Code Intelligence CLI  -  chat with any codebase", style="dim white")
    console.print()
    console.print(logo_text)
    console.print(subtitle)
    console.print()


# ---------------------------------------------------------------------------
# Semantic message helpers
# ---------------------------------------------------------------------------
def print_success(msg: str) -> None:
    console.print(f"  [success]OK[/] {msg}")


def print_error(msg: str) -> None:
    console.print(f"  [error]ERR[/] {msg}")


def print_warning(msg: str) -> None:
    console.print(f"  [warning]![/]  {msg}")


def print_info(msg: str) -> None:
    console.print(f"  [info]{msg}[/]")


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------
def print_help() -> None:
    banner()

    cmd_table = Table(
        show_header=True,
        header_style="header",
        box=box.SIMPLE,
        padding=(0, 2),
        show_edge=False,
    )
    cmd_table.add_column("Command", style="brand", no_wrap=True)
    cmd_table.add_column("Description", style="white")

    commands = [
        ("index <path>",                    "Index a local file or directory"),
        ("index-repo <owner/repo>",         "Clone and index a public GitHub repo"),
        ("list",                            "Show everything currently in the index"),
        ("query \"<question>\" [--verbose]", "Ask a question about indexed code"),
        ("count",                           "Show total chunk count in the index"),
        ("clear",                           "Wipe the entire index"),
    ]
    for cmd, desc in commands:
        cmd_table.add_row(cmd, desc)

    ex_table = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        show_edge=False,
    )
    ex_table.add_column("Example", style="info")
    examples = [
        "askrepo index ./myproject",
        "askrepo index auth.py",
        "askrepo index-repo fastapi/fastapi",
        "askrepo index-repo django/django --branch stable/4.2.x",
        'askrepo query "what does hash_password do?"',
        'askrepo query "explain the auth flow" --verbose',
        "askrepo count",
    ]
    for ex in examples:
        ex_table.add_row(ex)

    console.print(
        Panel(cmd_table, title="[header]Commands[/]", border_style="cyan", padding=(1, 2))
    )
    console.print(
        Panel(ex_table, title="[header]Examples[/]", border_style="dim", padding=(0, 2))
    )
    console.print()


# ---------------------------------------------------------------------------
# Indexing — live table + overall progress
# ---------------------------------------------------------------------------
def _lang_style(lang: str) -> str:
    mapping = {"python": "lang.py", "javascript": "lang.js", "typescript": "lang.ts",
                "markdown": "lang.md"}
    return mapping.get(lang.lower(), "lang.doc")


def _lang_badge(lang: str) -> Text:
    abbreviations = {
        "python": "PY", "javascript": "JS", "typescript": "TS",
        "markdown": "MD", "json": "JSON", "yaml": "YAML",
        "text": "TXT", "config": "CFG", "env": "ENV",
        "dockerfile": "DOCK", "makefile": "MAKE", "gitignore": "GIT",
    }
    abbr = abbreviations.get(lang.lower(), lang[:4].upper())
    return Text(f"[{abbr}]", style=_lang_style(lang))


def index_file_table() -> Table:
    """Create the indexing live table (reused across rows)."""
    t = Table(
        show_header=True,
        header_style="header",
        box=box.SIMPLE,
        padding=(0, 1),
        show_edge=False,
        expand=False,
    )
    t.add_column("", width=2, no_wrap=True)           # status icon
    t.add_column("File", style="path", no_wrap=False, max_width=60)
    t.add_column("Lang", no_wrap=True, width=7)
    t.add_column("Funcs", justify="right", width=5)
    t.add_column("Classes", justify="right", width=7)
    t.add_column("Chunks", justify="right", width=6)
    return t


def index_add_row(
    table: Table,
    file_path: str,
    lang: str,
    n_funcs: int,
    n_classes: int,
    n_chunks: int,
    skipped: bool = False,
) -> None:
    """Append one result row to the indexing live table."""
    icon = Text("-", style="dim white") if skipped else Text("OK", style="success")
    lang_cell = Text("-", style="dim white") if skipped else _lang_badge(lang)
    style = "dim white" if skipped else "white"

    table.add_row(
        icon,
        Text(file_path, style="path" if not skipped else "dim white"),
        lang_cell,
        Text("-" if skipped else str(n_funcs), style=style, justify="right"),
        Text("-" if skipped else str(n_classes), style=style, justify="right"),
        Text("-" if skipped else str(n_chunks), style=style, justify="right"),
    )


def make_overall_progress(total_files: int) -> tuple[Progress, object]:
    """Create overall file-level progress bar. Returns (progress, task_id)."""
    prog = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]Indexing[/]  "),
        BarColumn(bar_width=32, style="cyan", complete_style="bold cyan"),
        MofNCompleteColumn(),
        TextColumn("files"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    task_id = prog.add_task("", total=total_files)
    return prog, task_id


# ---------------------------------------------------------------------------
# Describer — per-chunk spinner + overall bar
# ---------------------------------------------------------------------------
@contextmanager
def describe_progress(chunks: list[dict]) -> Generator[object, None, None]:
    """
    Context manager for describe_all().

    Usage inside describe_all():
        with ui.describe_progress(chunks) as tracker:
            for i, chunk in enumerate(chunks):
                tracker.update(chunk)
                described.append(describe_chunk(chunk))
    """
    total = len(chunks)

    overall = Progress(
        BarColumn(bar_width=30, style="dim cyan", complete_style="cyan"),
        MofNCompleteColumn(),
        TextColumn("[dim white]chunks described[/]"),
        console=console,
        transient=False,
    )
    overall_task = overall.add_task("", total=total)

    spinner = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[dim white]Describing[/] [white]{task.description}[/]"),
        console=console,
        transient=True,
    )
    spin_task = spinner.add_task("", total=1)

    class _Tracker:
        def update(self, chunk: dict) -> None:
            label = chunk.get("name") or chunk.get("path", "...")
            ctype = chunk.get("type", "?")
            spinner.update(spin_task, description=f"[dim white]{ctype}[/] [white]{label}[/]")
            overall.advance(overall_task)

    tracker = _Tracker()

    with Live(Group(spinner, overall), console=console, refresh_per_second=12):
        yield tracker


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------
def print_list_index(
    by_source: dict[str, list[str]],
    by_path: dict[tuple[str, str], list[dict]],
    total_chunks: int,
) -> None:
    total_files = sum(len(v) for v in by_source.values())
    total_sources = len(by_source)

    console.print()
    console.print(
        Rule(
            f"[header]Index Summary[/]  -  "
            f"[brand]{total_sources}[/] source{'s' if total_sources != 1 else ''}  -  "
            f"[brand]{total_files}[/] file{'s' if total_files != 1 else ''}  -  "
            f"[brand]{total_chunks}[/] chunks",
            style="cyan",
        )
    )
    console.print()

    for source in sorted(by_source.keys()):
        rels = sorted(by_source[source])
        source_chunks = sum(len(by_path[(source, r)]) for r in rels)

        t = Table(
            show_header=True,
            header_style="header",
            box=box.SIMPLE,
            padding=(0, 1),
            show_edge=False,
            expand=False,
            title=f"[bold white]{source}[/]  [dim white]({len(rels)} files - {source_chunks} chunks)[/]",
            title_justify="left",
        )
        t.add_column("File", style="path", no_wrap=False, max_width=55)
        t.add_column("Lang", no_wrap=True, width=7)
        t.add_column("Chunks", justify="right", width=6)
        t.add_column("Types", style="info", no_wrap=False)
        t.add_column("Names preview", style="info", no_wrap=False, max_width=40)

        for rel in rels:
            chunks = by_path[(source, rel)]
            lang = chunks[0].get("language", "?")
            n = len(chunks)

            type_counts: dict[str, int] = {}
            for c in chunks:
                tp = c.get("type", "?")
                type_counts[tp] = type_counts.get(tp, 0) + 1
            type_summary = "  ".join(
                f"{cnt}x {tp}" if cnt > 1 else tp
                for tp, cnt in type_counts.items()
            )

            names = [c["name"] for c in chunks if c.get("name") and c.get("type") != "file"]
            if names:
                preview = names[:4]
                extra = len(names) - 4
                names_str = ", ".join(preview)
                if extra > 0:
                    names_str += f"  [dim]+{extra} more[/]"
            else:
                names_str = ""

            t.add_row(rel, _lang_badge(lang), str(n), type_summary, names_str)

        console.print(t)
        console.print()

    console.print(Rule(style="dim"))
    console.print()


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------
def print_query_result(
    user_query: str,
    chunks: list[dict],
    answer: str,
    verbose: bool,
    backend: str,
    model: str,
) -> None:
    console.print()

    if verbose and chunks:
        chunk_table = Table(
            show_header=True,
            header_style="header",
            box=box.SIMPLE,
            padding=(0, 1),
            show_edge=False,
            expand=False,
        )
        chunk_table.add_column("Type",  width=9,  no_wrap=True)
        chunk_table.add_column("Name",  style="white", no_wrap=False, max_width=35)
        chunk_table.add_column("Path",  style="path",  no_wrap=False, max_width=45)
        chunk_table.add_column("Score", width=6,  justify="right")

        for c in chunks:
            ctype     = c.get("type", "?")
            name      = c.get("name") or "-"
            path      = c.get("path", "?")
            score     = c.get("score")
            score_str = f"{score:.3f}" if isinstance(score, float) else str(score or "-")

            chunk_table.add_row(
                Text(ctype, style="cyan"),
                name,
                path,
                Text(score_str, style="score"),
            )

        title = (
            f"[header]Retrieved {len(chunks)} chunk{'s' if len(chunks) != 1 else ''}[/]  "
            f"[info]model:[/] [brand]{model}[/]"
        )
        console.print(
            Panel(chunk_table, title=title, border_style="dim", padding=(1, 2))
        )
        console.print()

    # Answer — render as Markdown so code blocks, bold, lists all display correctly
    console.print(Rule("[header]Answer[/]", style="cyan"))
    console.print()
    if answer.strip():
        console.print(Markdown(answer.strip()), soft_wrap=True)
    else:
        console.print("[dim white]  (No answer returned)[/]")
    console.print()


# ---------------------------------------------------------------------------
# count / clear
# ---------------------------------------------------------------------------
def print_count(n: int) -> None:
    console.print()
    label = "chunk" if n == 1 else "chunks"
    console.print(f"  [brand]{n}[/] {label} in index")
    console.print()


def print_clear() -> None:
    print_success("Index cleared.")
