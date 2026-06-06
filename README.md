# Automated Data Catalog + Lineage Builder

A CLI that turns a dbt project (or a directory of raw SQL) into a browsable data
catalog. It parses table-level **lineage** into a DAG, uses Claude to generate
**business descriptions**, flags **PII columns**, and renders **markdown pages**
plus a **Mermaid lineage diagram**.

No dbt runtime required — it reads `manifest.json` directly.

```
catalog demo          # fully offline walkthrough from bundled fixtures
catalog build  --source manifest.json --output ./catalog -y
catalog diff   --source manifest.json --output ./catalog -y
catalog validate --catalog-dir ./catalog
```

## Features

- **Lineage** — table-level DAG from compiled SQL via [SQLGlot](https://github.com/tobymao/sqlglot) + [networkx]; CTE-aware, no self-edges.
- **LLM enrichment** — batched Claude calls produce business descriptions, column docs, and a sensitivity level per table.
- **PII detection** — two-stage: regex heuristics first, Claude only for ambiguous columns. Exclusion patterns avoid false positives like `is_email_verified` or `email_count`.
- **Cost control** — prints an estimated token cost and requires explicit `-y`/`--yes` before any LLM call.
- **Caching** — enrichment is cached on `SHA256(compiled_sql)`; `diff` re-enriches only changed tables.
- **Owner resolution** — `meta.owner` from dbt → git blame → `Unknown`.
- **Exports** — markdown (default) or DataHub-style YAML (`--format datahub`).
- **Offline demo** — `catalog demo` runs end-to-end with no credentials.

## Install

```bash
uv venv && uv pip install -e ".[dev,git]"
# or: pip install -e ".[dev,git]"
```

`git` extra enables git-blame owner resolution (optional). Set `ANTHROPIC_API_KEY`
for real enrichment (`build` / `diff`); `demo` needs no key.

## Quickstart

```bash
catalog demo
```

```
Scanning fixtures/dbt_project/manifest.json...
  ✓ 24 nodes found (3 sources, 21 models)
  ✓ Manifest schema: v9

Building lineage DAG...
  ✓ 24 nodes, 31 edges
  ⚠ Orphaned models (no dbt lineage): orders_v1, users_archive

PII scan (heuristic)...
  ⚠ 8 PII columns across 3 tables:
      stg_customers: date_of_birth, email, phone
      stg_payments: billing_address, card_last4
      stg_users: email, first_name, last_name

Estimated enrichment cost: ~$0.09 (24 tables, ~12,452 tokens)
...
Done. Open demo_catalog/index.md to browse.
```

Open `demo_catalog/index.md` to browse the result.

## Commands

| Command | Purpose |
|---------|---------|
| `build` | Scan, enrich every table with Claude, and render the catalog. |
| `diff` | Same as `build`, but skips tables whose compiled-SQL hash is already cached. |
| `validate` | Check internal consistency of a built catalog (index links, lineage refs, PII refs, Mermaid node IDs). Exit 1 on errors. |
| `demo` | Fully offline run from bundled fixtures — no credentials. |

Common options: `--source` (a `manifest.json` or a SQL directory), `--output`
(target directory), `--format markdown|datahub`, `-y`/`--yes` (skip the cost prompt).

## Output

A built catalog directory contains:

- `index.md` — overview grouped by layer (source / staging / intermediate / mart / other)
- `<table>.md` — one page per table: description, sensitivity, owner, PII status, lineage, and a column table
- `pii_report.md` — PII columns by type and by table
- `lineage.mmd` — Mermaid diagram with per-layer subgraphs
- `datahub.yaml` — only with `--format datahub`

## Development

```bash
pytest                      # unit tests (integration tests are deselected by default)
pytest -m integration       # requires ANTHROPIC_API_KEY
ruff check src/
python scripts/gen_fixture.py   # regenerate the demo fixture
```

See [`BUILD.md`](BUILD.md) for the full design spec and module-by-module build notes.

## Scope (v1)

In: dbt `manifest.json` (schema v9/v10) and raw SQL directories; table-level
lineage; markdown + DataHub YAML export.

Out: non-dbt ETL parsing (Spark/Airflow), live DataHub/OpenMetadata API push,
non-experimental column-level lineage, a hosted web UI, and cross-system lineage.

[networkx]: https://networkx.org/
