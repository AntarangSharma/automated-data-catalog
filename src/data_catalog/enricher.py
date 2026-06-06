"""Claude batched enrichment of table/column descriptions, sensitivity, owner hints."""

from __future__ import annotations

import json

from .cost import estimate_tokens
from .models import Sensitivity, TableMeta

MAX_BATCH_INPUT_TOKENS = 4000

BATCH_PROMPT = """For each table below, provide:
- table_description: 1-2 sentence business-friendly description (not technical)
- column_descriptions: {{col_name: description}} for each column
- sensitivity_level: "public" | "internal" | "confidential" | "restricted"
- suggested_owner_hint: infer from table name/layer if possible, else null

Return a JSON array, one object per table, in the same order. Each object:
{{"name": str, "table_description": str, "column_descriptions": {{}},
  "sensitivity_level": str, "suggested_owner_hint": str|null}}

Tables:
{tables_json}
"""


def make_batches(tables: list[TableMeta]) -> list[list[TableMeta]]:
    batches: list[list[TableMeta]] = []
    current: list[TableMeta] = []
    current_tokens = 0
    for t in tables:
        t_tokens = estimate_tokens(t.compiled_sql or "") + 300
        if current and current_tokens + t_tokens > MAX_BATCH_INPUT_TOKENS:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(t)
        current_tokens += t_tokens
    if current:
        batches.append(current)
    return batches


def _batch_payload(batch: list[TableMeta], context: dict[str, dict]) -> str:
    rows = []
    for t in batch:
        ctx = context.get(t.unique_id, {})
        rows.append(
            {
                "name": t.name,
                "layer": t.layer,
                "upstream": ctx.get("upstream", []),
                "downstream": ctx.get("downstream", []),
                "compiled_sql": (t.compiled_sql or "")[:2000],
                "columns": [c.name for c in t.columns],
            }
        )
    return json.dumps(rows, indent=2)


def apply_enrichment(table: TableMeta, data: dict) -> None:
    """Apply one enrichment dict to a TableMeta (in place)."""
    if data.get("table_description"):
        table.description = data["table_description"]
    sens = data.get("sensitivity_level")
    if sens in ("public", "internal", "confidential", "restricted"):
        table.sensitivity = sens  # type: ignore[assignment]
    col_desc = data.get("column_descriptions") or {}
    for col in table.columns:
        if col.name in col_desc:
            col.description = col_desc[col.name]


def enrich_batch_llm(client, batch: list[TableMeta], context: dict[str, dict]) -> list[dict]:
    """Call Claude for one batch. On JSON parse failure: log warning, return [] (skip)."""
    prompt = BATCH_PROMPT.format(tables_json=_batch_payload(batch, context))
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    return json.loads(text)


# ---- Offline deterministic enrichment (used by `demo`, no network) ---------

_LAYER_BLURB = {
    "source": "Raw source data ingested from an upstream system.",
    "staging": "Lightly cleaned and standardized model close to its source.",
    "intermediate": "Reusable intermediate transformation joining staged models.",
    "mart": "Business-facing model serving analytics and reporting.",
    "other": "Standalone or legacy model.",
}

_LAYER_SENSITIVITY: dict[str, Sensitivity] = {
    "source": "confidential",
    "staging": "internal",
    "intermediate": "internal",
    "mart": "internal",
    "other": "internal",
}


def offline_enrichment(table: TableMeta) -> dict:
    """Deterministic enrichment for offline/demo use -- no LLM call."""
    sensitivity: Sensitivity = _LAYER_SENSITIVITY.get(table.layer, "internal")
    if table.pii_columns:
        sensitivity = "confidential"
    desc = (
        f"{table.name.replace('_', ' ').title()} -- "
        f"{_LAYER_BLURB.get(table.layer, _LAYER_BLURB['other'])}"
    )
    col_desc = {
        c.name: (
            f"PII ({c.pii_type.value}) field." if c.pii else f"{c.name.replace('_', ' ').capitalize()}."
        )
        for c in table.columns
    }
    return {
        "name": table.name,
        "table_description": desc,
        "column_descriptions": col_desc,
        "sensitivity_level": sensitivity,
        "suggested_owner_hint": None,
    }
