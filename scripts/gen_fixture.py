"""Deterministically generate the demo dbt fixture (manifest.json + schema.yml).

Run:  python scripts/gen_fixture.py
Produces fixtures/dbt_project/manifest.json (schema v9) and schema.yml.

The fixture encodes exactly:
  - 24 nodes: 3 sources + 21 models
  - 31 lineage edges
  - 2 isolated orphan models: orders_v1, users_archive
  - 8 PII columns across 3 tables: stg_customers, stg_payments, stg_users
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT = "demo_shop"
SCHEMA_V9 = "https://schemas.getdbt.com/dbt/manifest/v9/manifest.json"

# (name, layer) for the 21 models. Sources handled separately.
MODELS = [
    # 6 staging
    ("stg_customers", "staging"),
    ("stg_orders", "staging"),
    ("stg_products", "staging"),
    ("stg_payments", "staging"),
    ("stg_users", "staging"),
    ("stg_order_items", "staging"),
    # 7 intermediate
    ("int_customer_orders", "intermediate"),
    ("int_order_items_joined", "intermediate"),
    ("int_payments_enriched", "intermediate"),
    ("int_product_sales", "intermediate"),
    ("int_user_activity", "intermediate"),
    ("int_order_fulfillment", "intermediate"),
    ("int_customer_lifetime", "intermediate"),
    # 6 marts
    ("fct_orders", "mart"),
    ("dim_customers", "mart"),
    ("dim_products", "mart"),
    ("rpt_revenue", "mart"),
    ("rpt_fulfillment", "mart"),
    ("ml_churn_features", "mart"),
    # 2 orphans (isolated: reference tables not in the project, no consumers)
    ("orders_v1", "other"),
    ("users_archive", "other"),
]

SOURCES = ["raw_orders", "raw_customers", "raw_products"]

# downstream model -> list of upstream table names (31 edges total)
REFS: dict[str, list[str]] = {
    "stg_customers": ["raw_customers"],
    "stg_orders": ["raw_orders"],
    "stg_products": ["raw_products"],
    "stg_payments": ["raw_orders"],
    "stg_users": ["raw_customers"],
    "stg_order_items": ["raw_orders"],
    "int_customer_orders": ["stg_customers", "stg_orders"],
    "int_order_items_joined": ["stg_orders", "stg_order_items"],
    "int_payments_enriched": ["stg_payments", "stg_orders"],
    "int_product_sales": ["stg_products", "stg_order_items"],
    "int_user_activity": ["stg_users"],
    "int_order_fulfillment": ["stg_orders", "stg_order_items"],
    "int_customer_lifetime": ["int_customer_orders", "int_payments_enriched"],
    "fct_orders": ["int_order_items_joined", "int_payments_enriched"],
    "dim_customers": ["stg_customers", "int_customer_lifetime"],
    "dim_products": ["stg_products", "int_product_sales"],
    "rpt_revenue": ["fct_orders"],
    "rpt_fulfillment": ["fct_orders", "int_order_fulfillment"],
    "ml_churn_features": ["fct_orders", "dim_customers", "int_user_activity"],
    # orphans reference legacy tables not present in the project => no edges
    "orders_v1": ["raw_orders_legacy"],
    "users_archive": ["raw_users_legacy"],
}

# PII columns, by table.
PII_COLUMNS = {
    "stg_customers": [
        ("email", "STRING"),
        ("phone", "STRING"),
        ("date_of_birth", "DATE"),
    ],
    "stg_payments": [
        ("card_last4", "STRING"),
        ("billing_address", "STRING"),
    ],
    "stg_users": [
        ("email", "STRING"),
        ("first_name", "STRING"),
        ("last_name", "STRING"),
    ],
}

# A couple of decoy columns that look like PII words but are NOT PII.
DECOY_COLUMNS = {
    "stg_customers": [("is_email_verified", "BOOLEAN"), ("email_domain", "STRING")],
    "int_user_activity": [("session_count", "INTEGER")],
}

OWNERS = {
    "fct_orders": "@alice",
    "dim_customers": "@alice",
    "rpt_revenue": "@bob",
}


def _columns_for(name: str) -> dict:
    cols: dict[str, dict] = {
        "id": {"name": "id", "data_type": "STRING", "description": ""},
        "created_at": {"name": "created_at", "data_type": "TIMESTAMP", "description": ""},
    }
    for cname, ctype in PII_COLUMNS.get(name, []):
        cols[cname] = {"name": cname, "data_type": ctype, "description": ""}
    for cname, ctype in DECOY_COLUMNS.get(name, []):
        cols[cname] = {"name": cname, "data_type": ctype, "description": ""}
    return cols


def _compiled_sql(name: str) -> str:
    refs = REFS.get(name, [])
    if not refs:
        return f"SELECT * FROM {name}_raw"
    selects = ", ".join(f"{r}.*" for r in refs[:1]) or "*"
    base = refs[0]
    joins = "\n".join(f"JOIN {r} ON {base}.id = {r}.id" for r in refs[1:])
    return f"SELECT {selects}\nFROM {base}\n{joins}".strip()


def build_manifest() -> dict:
    nodes: dict[str, dict] = {}
    sources: dict[str, dict] = {}

    for src in SOURCES:
        uid = f"source.{PROJECT}.{src}.{src}"
        sources[uid] = {
            "unique_id": uid,
            "name": src,
            "resource_type": "source",
            "relation_name": f"`raw`.`{src}`",
            "original_file_path": "models/staging/_sources.yml",
            "columns": {
                "id": {"name": "id", "data_type": "STRING", "description": ""},
            },
        }

    for name, layer in MODELS:
        uid = f"model.{PROJECT}.{name}"
        node = {
            "unique_id": uid,
            "name": name,
            "resource_type": "model",
            "compiled_code": _compiled_sql(name),
            "relation_name": f"`analytics`.`{layer}`.`{name}`",
            "original_file_path": f"models/{layer}/{name}.sql",
            "columns": _columns_for(name),
            "config": {"meta": {}},
            "meta": {},
        }
        if name in OWNERS:
            node["config"]["meta"]["owner"] = OWNERS[name]
            node["meta"]["owner"] = OWNERS[name]
        nodes[uid] = node

    return {
        "metadata": {"dbt_schema_version": SCHEMA_V9, "project_name": PROJECT},
        "nodes": nodes,
        "sources": sources,
    }


def build_schema_yml() -> str:
    lines = ["version: 2", "", "models:"]
    for name, _ in MODELS:
        if name in OWNERS:
            lines += [f"  - name: {name}", "    meta:", f"      owner: \"{OWNERS[name]}\""]
    return "\n".join(lines) + "\n"


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "fixtures" / "dbt_project"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    (out_dir / "schema.yml").write_text(build_schema_yml())

    n_sources = len(manifest["sources"])
    n_models = len(manifest["nodes"])
    n_edges = sum(
        1
        for m, refs in REFS.items()
        for r in refs
        if r in {x[0] for x in MODELS} or r in SOURCES
    )
    print(f"Wrote {out_dir/'manifest.json'}: {n_sources} sources, {n_models} models, {n_edges} edges")


if __name__ == "__main__":
    main()
