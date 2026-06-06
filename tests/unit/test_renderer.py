from __future__ import annotations

from data_catalog.models import PIIType
from data_catalog.parser import build_lineage
from data_catalog.renderer import (
    _env,
    render_catalog,
    render_lineage,
    render_table_page,
)
from tests.conftest import col, make_table


def test_table_page_has_required_sections():
    c1 = col("order_id")
    c2 = col("email")
    c2.pii = True
    c2.pii_type = PIIType.EMAIL
    t = make_table("fct_orders", "mart", sql="SELECT * FROM stg_orders", columns=[c1, c2])
    t.description = "Core orders fact table."
    t.owner = "@alice"
    t.pii_columns = ["email"]
    g, _ = build_lineage([t])
    page = render_table_page(_env(), t, g)
    assert "## fct_orders" in page
    assert "**Description:** Core orders fact table." in page
    assert "**Sensitivity**" in page
    assert "**Owner** | @alice" in page
    assert "**Layer** | Mart" in page
    assert "**Lineage:**" in page
    assert "| Column | Type | Description | PII |" in page
    assert "| email | STRING |" in page
    assert "email" in page  # pii column present


def test_mermaid_subgraphs_emitted_over_25_nodes():
    tables = []
    for i in range(10):
        tables.append(make_table(f"stg_{i}", "staging", sql="SELECT 1"))
    for i in range(10):
        tables.append(make_table(f"int_{i}", "intermediate", sql="SELECT 1"))
    for i in range(10):
        tables.append(make_table(f"fct_{i}", "mart", sql="SELECT 1"))
    assert len(tables) > 25
    g, _ = build_lineage(tables)
    mmd = render_lineage(_env(), tables, g)
    assert "graph LR" in mmd
    assert "subgraph Staging" in mmd
    assert "subgraph Intermediate" in mmd
    assert "subgraph Mart" in mmd


def test_render_catalog_writes_all_files(tmp_path):
    t1 = make_table("stg_orders", "staging", sql="SELECT * FROM raw_orders")
    t2 = make_table("raw_orders", "source", sql=None)
    t2.node_type = t2.node_type.SOURCE
    from data_catalog.models import NodeType

    t2.node_type = NodeType.SOURCE
    g, orphans = build_lineage([t1, t2])
    counts = render_catalog([t1, t2], g, orphans, tmp_path)
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "pii_report.md").exists()
    assert (tmp_path / "lineage.mmd").exists()
    assert (tmp_path / "stg_orders.md").exists()
    assert counts["table_pages"] == 2
