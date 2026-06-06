from __future__ import annotations

from data_catalog.parser import _extract_table_refs, build_lineage
from tests.conftest import make_table


def test_extract_refs_from_joins():
    sql = "SELECT a.* FROM stg_orders a JOIN stg_items b ON a.id = b.id"
    refs = _extract_table_refs(sql)
    assert set(refs) == {"stg_orders", "stg_items"}


def test_extract_refs_ignores_ctes():
    sql = "WITH t AS (SELECT * FROM raw_orders) SELECT * FROM t"
    refs = _extract_table_refs(sql)
    assert "raw_orders" in refs
    assert "t" not in refs


def test_fixture_edge_count(tables):
    g, _ = build_lineage(tables)
    assert g.number_of_nodes() == 24
    assert g.number_of_edges() == 31


def test_orphans_are_isolated_models_only(tables):
    g, orphans = build_lineage(tables)
    id_to_name = {t.unique_id: t.name for t in tables}
    orphan_names = {id_to_name[o] for o in orphans}
    assert orphan_names == {"orders_v1", "users_archive"}


def test_sources_not_orphans(tables):
    g, orphans = build_lineage(tables)
    by_id = {t.unique_id: t for t in tables}
    for o in orphans:
        assert by_id[o].node_type.value == "model"


def test_terminal_mart_not_orphan(tables):
    # rpt_revenue has out_degree 0 but is consumed-from-upstream => NOT an orphan.
    g, orphans = build_lineage(tables)
    by_name = {t.name: t for t in tables}
    rpt = by_name["rpt_revenue"]
    assert g.out_degree(rpt.unique_id) == 0
    assert rpt.unique_id not in orphans


def test_no_self_edges():
    # A model selecting from a table with its own name must not create a self-edge.
    t = make_table("loop", "staging", sql="SELECT * FROM loop")
    g, _ = build_lineage([t])
    assert g.number_of_edges() == 0


def test_edge_direction(tables):
    g, _ = build_lineage(tables)
    name_to_id = {t.name: t.unique_id for t in tables}
    assert g.has_edge(name_to_id["raw_orders"], name_to_id["stg_orders"])
    assert not g.has_edge(name_to_id["stg_orders"], name_to_id["raw_orders"])
