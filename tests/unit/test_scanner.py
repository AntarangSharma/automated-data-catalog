from __future__ import annotations

from data_catalog.models import NodeType
from data_catalog.scanner import (
    _detect_layer,
    manifest_schema_version,
    scan_manifest,
)


def test_scan_manifest_counts(tables):
    sources = [t for t in tables if t.node_type is NodeType.SOURCE]
    models = [t for t in tables if t.node_type is NodeType.MODEL]
    assert len(tables) == 24
    assert len(sources) == 3
    assert len(models) == 21


def test_sources_have_no_compiled_sql(tables):
    for t in tables:
        if t.node_type is NodeType.SOURCE:
            assert t.compiled_sql is None
        else:
            assert t.compiled_sql  # models carry compiled SQL


def test_schema_version_v9(manifest_path):
    assert manifest_schema_version(manifest_path) == 9


def test_layer_detection(tables):
    by_name = {t.name: t for t in tables}
    assert by_name["raw_orders"].layer == "source"
    assert by_name["stg_orders"].layer == "staging"
    assert by_name["int_customer_orders"].layer == "intermediate"
    assert by_name["fct_orders"].layer == "mart"
    assert by_name["dim_customers"].layer == "mart"
    assert by_name["orders_v1"].layer == "other"


def test_detect_layer_by_prefix():
    assert _detect_layer("stg_x", NodeType.MODEL) == "staging"
    assert _detect_layer("int_x", NodeType.MODEL) == "intermediate"
    assert _detect_layer("rpt_x", NodeType.MODEL) == "mart"
    assert _detect_layer("ml_x", NodeType.MODEL) == "mart"
    assert _detect_layer("whatever", NodeType.MODEL) == "other"


def test_columns_parsed(tables):
    cust = next(t for t in tables if t.name == "stg_customers")
    names = {c.name for c in cust.columns}
    assert {"email", "phone", "date_of_birth"} <= names
