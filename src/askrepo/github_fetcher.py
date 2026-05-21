"""
github_fetcher.py
-----------------
Clones a public GitHub repo into a local cache directory for indexing.

Uses git clone --depth=1 (shallow clone) — fast, no rate limits, works for any repo size.
Repos are cached in ./repos/<owner>_<reponame>/. Re-running updates via git pull.

Usage:
    clone_dir, repo_name = clone_repo("fastapi/fastapi")
    clone_dir, repo_name = clone_repo("https://github.com/psf/requests")
    clone_dir, repo_name = clone_repo("django/django", branch="stable/4.2.x")
"""

import os
import subprocess
from askrepo import config

REPOS_CACHE_DIR = config.REPOS_CACHE_DIR


def parse_repo_input(input_str: str) -> tuple[str, str]:
    """
    Normalize various input formats to (clone_url, repo_name).

    Accepts:
      - "owner/repo"                        → github.com shorthand
      - "https://github.com/owner/repo"
      - "https://github.com/owner/repo.git"
    Returns:
      - clone_url: full HTTPS clone URL ending in .git
      - repo_name: human-readable "owner/repo"
    """
    inp = input_str.strip().rstrip("/")

    if inp.startswith(("https://", "http://", "git@")):
        # Full URL
        clean = inp.rstrip(".git") if inp.endswith(".git") else inp
        parts = clean.rstrip("/").split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"
        clone_url = f"https://github.com/{repo_name}.git"
        return clone_url, repo_name

    # owner/repo shorthand
    if "/" in inp and inp.count("/") == 1:
        repo_name = inp
        clone_url = f"https://github.com/{repo_name}.git"
        return clone_url, repo_name

    raise ValueError(
        f"Cannot parse repo input: {input_str!r}\n"
        "Expected formats: 'owner/repo' or 'https://github.com/owner/repo'"
    )


def clone_repo(repo_url_or_slug: str, branch: str = None) -> tuple[str, str]:
    """
    Clone or update a GitHub repo in the local cache.

    Args:
        repo_url_or_slug: "owner/repo" or full GitHub URL
        branch:           specific branch to clone (default: repo's default branch)

    Returns:
        (clone_dir, repo_name) where:
          clone_dir  — absolute path to the cloned/updated repo
          repo_name  — "owner/repo" string
    """
    clone_url, repo_name = parse_repo_input(repo_url_or_slug)
    safe_name = repo_name.replace("/", "_")
    clone_dir = os.path.abspath(os.path.join(REPOS_CACHE_DIR, safe_name))

    os.makedirs(REPOS_CACHE_DIR, exist_ok=True)

    if os.path.exists(os.path.join(clone_dir, ".git")):
        # Repo already cloned — pull latest
        print(f"Repo already cached at: {clone_dir}")
        print("Pulling latest changes...")
        result = subprocess.run(
            ["git", "-C", clone_dir, "pull", "--depth=1"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  Warning: git pull failed — using cached version.\n  {result.stderr.strip()}")
        else:
            print(f"  Updated: {result.stdout.strip()}")
    else:
        # Fresh clone
        print(f"Cloning {clone_url} ...")
        cmd = ["git", "clone", "--depth=1", "--single-branch"]
        if branch:
            cmd += ["-b", branch]
        cmd += [clone_url, clone_dir]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone failed for {clone_url}:\n{result.stderr.strip()}\n\n"
                "Make sure git is installed and the repo is public."
            )
        print(f"Cloned to: {clone_dir}")

    return clone_dir, repo_name
