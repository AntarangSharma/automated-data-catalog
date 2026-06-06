"""Resolve table owner: dbt meta.owner -> git blame -> 'Unknown'."""

from __future__ import annotations

from pathlib import Path

from .models import TableMeta


def owner_from_meta(node: dict) -> str | None:
    """Extract meta.owner from a manifest node (config.meta.owner or node.meta.owner)."""
    config_meta = (node.get("config") or {}).get("meta") or {}
    if config_meta.get("owner"):
        return config_meta["owner"]
    node_meta = node.get("meta") or {}
    if node_meta.get("owner"):
        return node_meta["owner"]
    return None


def owner_from_git(file_path: str, repo_root: Path | None = None) -> str | None:
    """Most recent committer of file_path via git blame. Returns None if unavailable.

    gitpython is imported inside the function so a missing install never breaks import.
    """
    try:
        import git  # type: ignore
    except ImportError:
        return None
    try:
        search_from = str(repo_root) if repo_root else str(Path(file_path).resolve().parent)
        repo = git.Repo(search_from, search_parent_directories=True)
        commits = list(repo.iter_commits(paths=file_path, max_count=1))
        if not commits:
            return None
        author = commits[0].author
        return author.name or author.email
    except Exception:
        return None


def resolve_owner(
    table: TableMeta,
    node: dict | None = None,
    repo_root: Path | None = None,
) -> str:
    if node is not None:
        meta_owner = owner_from_meta(node)
        if meta_owner:
            return meta_owner
    if table.file_path:
        git_owner = owner_from_git(table.file_path, repo_root)
        if git_owner:
            return git_owner
    return "Unknown"
