from __future__ import annotations

from data_catalog.enricher import (
    MAX_BATCH_INPUT_TOKENS,
    apply_enrichment,
    make_batches,
    offline_enrichment,
)
from data_catalog.cost import estimate_tokens
from tests.conftest import col, make_table


def _big_sql(n):
    return "SELECT " + ", ".join(f"c{i}" for i in range(n)) + " FROM t"


def test_no_batch_exceeds_budget():
    tables = [make_table(f"t{i}", "staging", sql=_big_sql(300)) for i in range(20)]
    batches = make_batches(tables)
    for batch in batches:
        total = sum(estimate_tokens(t.compiled_sql or "") + 300 for t in batch)
        # A batch may exceed only if it is a single oversized table.
        if len(batch) > 1:
            assert total <= MAX_BATCH_INPUT_TOKENS


def test_all_tables_appear_exactly_once():
    tables = [make_table(f"t{i}", "staging", sql=_big_sql(100)) for i in range(15)]
    batches = make_batches(tables)
    flat = [t.name for b in batches for t in b]
    assert sorted(flat) == sorted(t.name for t in tables)
    assert len(flat) == len(set(flat))


def test_single_oversized_table_gets_own_batch():
    tables = [make_table("huge", "staging", sql=_big_sql(5000))]
    batches = make_batches(tables)
    assert len(batches) == 1


def test_apply_enrichment_sets_fields():
    t = make_table("t", "staging", sql="SELECT 1", columns=[col("amount", "FLOAT")])
    apply_enrichment(
        t,
        {
            "table_description": "An orders table.",
            "sensitivity_level": "confidential",
            "column_descriptions": {"amount": "Order amount."},
        },
    )
    assert t.description == "An orders table."
    assert t.sensitivity == "confidential"
    assert t.columns[0].description == "Order amount."


def test_offline_enrichment_pii_bumps_sensitivity():
    c = col("email")
    c.pii = True
    from data_catalog.models import PIIType

    c.pii_type = PIIType.EMAIL
    t = make_table("stg_users", "staging", sql="SELECT 1", columns=[c])
    t.pii_columns = ["email"]
    data = offline_enrichment(t)
    assert data["sensitivity_level"] == "confidential"
