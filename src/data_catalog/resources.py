"""Locate bundled templates and fixtures whether running in-repo or installed."""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def _candidates(rel: str) -> list[Path]:
    # Installed layout: data_catalog/<rel>.  Repo layout: <repo_root>/<rel>.
    repo_root = _PKG_DIR.parents[1]  # src/data_catalog -> src -> repo
    return [_PKG_DIR / rel, repo_root / rel]


def resource_dir(rel: str) -> Path:
    for c in _candidates(rel):
        if c.exists():
            return c
    raise FileNotFoundError(f"Could not locate bundled resource directory: {rel}")


def templates_dir() -> Path:
    return resource_dir("templates")


def fixture_manifest() -> Path:
    return resource_dir("fixtures") / "dbt_project" / "manifest.json"
