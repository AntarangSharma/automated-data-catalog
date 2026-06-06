from __future__ import annotations

from data_catalog.cli import _validate_catalog


def _write_clean_catalog(d):
    (d / "index.md").write_text(
        "# Data Catalog\n\n| Table |\n|---|\n| [stg_orders](stg_orders.md) |\n"
    )
    (d / "stg_orders.md").write_text(
        "## stg_orders\n**Lineage:** **`stg_orders`**\n\n| Column | Type |\n"
    )
    (d / "pii_report.md").write_text("# PII Report\nNo PII.\n")
    (d / "lineage.mmd").write_text("graph LR\n    stg_orders[\"stg_orders\"]\n")


def test_clean_catalog_passes(tmp_path):
    _write_clean_catalog(tmp_path)
    assert _validate_catalog(tmp_path) == []


def test_missing_index_is_error(tmp_path):
    errors = _validate_catalog(tmp_path)
    assert errors and "index.md" in errors[0]


def test_catches_table_page_not_in_index(tmp_path):
    _write_clean_catalog(tmp_path)
    (tmp_path / "orphan_page.md").write_text("## orphan_page\n")
    errors = _validate_catalog(tmp_path)
    assert any("orphan_page" in e for e in errors)


def test_catches_broken_lineage_reference(tmp_path):
    _write_clean_catalog(tmp_path)
    (tmp_path / "stg_orders.md").write_text(
        "## stg_orders\n**Lineage:** `ghost_table` → **`stg_orders`**\n"
    )
    errors = _validate_catalog(tmp_path)
    assert any("ghost_table" in e for e in errors)


def test_catches_duplicate_mermaid_node(tmp_path):
    _write_clean_catalog(tmp_path)
    (tmp_path / "lineage.mmd").write_text(
        "graph LR\n    stg_orders[\"stg_orders\"]\n    stg_orders[\"stg_orders\"]\n"
    )
    errors = _validate_catalog(tmp_path)
    assert any("duplicate node id" in e for e in errors)
