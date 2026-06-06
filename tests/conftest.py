from __future__ import annotations

from pathlib import Path

import pytest

from data_catalog.models import ColumnMeta, NodeType, TableMeta
from data_catalog.scanner import scan_manifest

FIXTURE_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "dbt_project" / "manifest.json"


@pytest.fixture
def manifest_path() -> Path:
    return FIXTURE_MANIFEST


@pytest.fixture
def tables():
    return scan_manifest(FIXTURE_MANIFEST)


def make_table(name, layer, sql=None, node_type=NodeType.MODEL, columns=None):
    return TableMeta(
        unique_id=f"model.t.{name}",
        name=name,
        node_type=node_type,
        compiled_sql=sql,
        columns=columns or [],
        file_path=f"models/{layer}/{name}.sql",
        layer=layer,
    )


def col(name, dtype="STRING"):
    return ColumnMeta(name=name, data_type=dtype)
