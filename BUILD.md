# Automated Data Catalog + Lineage Builder — Build Instructions

CLI that reads a dbt project (via manifest.json) or a directory of raw SQL files, uses Claude
to generate business descriptions and PII tags, parses table-level lineage into a DAG, and
outputs browsable markdown + a Mermaid lineage diagram.

---

## Constraints (non-negotiable)

- Parse `manifest.json` only — no dbt runtime dependency, no subprocess calls to dbt.
- Target manifest schema v9 (dbt 1.5+) and v10 (dbt 1.6+). Detect version from `metadata.dbt_schema_version`.
- Column-level lineage: `--experimental` flag only. Document as best-effort. Never the default.
- LLM cache: skip enrichment for tables whose `compiled_code` SHA256 is already in cache.
- Print estimated token cost before any LLM calls and require explicit `--yes` or `-y` to proceed.
- Demo runs fully offline from bundled fixtures — no credentials required.
- `gitpython` is optional — fall back gracefully when not in a git repo.

---

## Stack

```toml
[project.scripts]
catalog = "data_catalog.cli:app"

[project.dependencies]
anthropic = ">=0.50"
sqlglot = { extras = ["rs"] }
networkx = "*"
jinja2 = "*"
pydantic = ">=2.0"
typer = "*"
httpx = "*"    # for future DataHub push, no other HTTP use

[project.optional-dependencies]
git = ["gitpython"]
dev = ["pytest", "pytest-cov", "ruff", "pyright", "pytest-mock"]
```

---

## Project Layout

```
src/data_catalog/
  cli.py          # typer: build / diff / validate / demo
  models.py       # Pydantic types
  scanner.py      # manifest.json + raw SQL → list[TableMeta]
  parser.py       # SQLGlot → networkx DAG
  enricher.py     # Claude batched enrichment
  pii.py          # heuristic + LLM PII column classifier
  cache.py        # SQLite: sha256(compiled_code) → enrichment
  cost.py         # token count estimator (no tiktoken)
  renderer.py     # Jinja2 → markdown + Mermaid
  owner.py        # git blame owner resolution with fallback
  models_datahub.py  # DataHub YAML serialization (only used with --format datahub)
fixtures/
  dbt_project/
    manifest.json   # 24 models, schema v9, deterministic
    schema.yml      # includes meta.owner on some nodes
templates/
  table.md.j2
  index.md.j2
  pii_report.md.j2
  lineage.mmd.j2
tests/
  unit/
  integration/     # @pytest.mark.integration, needs real manifest
```

---

## Build Order

**1. `models.py`**

```python
class NodeType(str, Enum):
    SOURCE = "source"
    MODEL = "model"

class PIIType(str, Enum):
    EMAIL = "email"; PHONE = "phone"; SSN = "ssn"; DOB = "dob"
    NAME = "name"; ADDRESS = "address"; FINANCIAL = "financial"; OTHER = "other"

class ColumnMeta(BaseModel):
    name: str
    data_type: str
    description: str = ""
    pii: bool = False
    pii_type: PIIType | None = None

class TableMeta(BaseModel):
    unique_id: str          # dbt unique_id or path hash for raw SQL
    name: str
    node_type: NodeType
    compiled_sql: str | None   # None for sources
    columns: list[ColumnMeta]
    file_path: str
    layer: Literal["source","staging","intermediate","mart","other"]
    owner: str = "Unknown"
    description: str = ""
    sensitivity: Literal["public","internal","confidential","restricted"] = "internal"
    pii_columns: list[str] = []

class LineageEdge(BaseModel):
    upstream: str    # unique_id
    downstream: str  # unique_id

class CatalogReport(BaseModel):
    tables: list[TableMeta]
    edges: list[LineageEdge]
    orphaned: list[str]   # unique_ids with no downstream dbt consumers
    pii_summary: dict[PIIType, list[str]]  # pii_type → [table.column]
```

**2. `fixtures/dbt_project/manifest.json`**

Build this before any other step — everything depends on it.

Requirements:
- Schema version: `{"metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v9/manifest.json"}}`
- 24 nodes total: 3 sources + 21 models
- Sources: `raw_orders`, `raw_customers`, `raw_products`
- Layers: 3 sources → 6 staging → 8 intermediate → 6 marts → 2 orphaned models (orders_v1, users_archive)
- PII columns (8 total, across 3 tables): customer email, phone, dob; payments card_last4, billing_address
- Sources have `node_type: "source"`, no `compiled_code` key
- Models have `compiled_code` (standard SQL, no Jinja), `unique_id`, `relation_name`, `columns` dict
- `schema.yml` must have `meta: {owner: "@alice"}` on at least 3 models

**3. `scanner.py`**

```python
def scan_manifest(path: Path) -> list[TableMeta]:
    data = json.loads(path.read_text())
    version = _detect_schema_version(data)  # v9 or v10
    nodes = {**data.get("nodes", {}), **data.get("sources", {})}
    result = []
    for uid, node in nodes.items():
        if node.get("resource_type") not in ("model", "source"):
            continue
        result.append(_parse_node(uid, node, version))
    return result

def scan_sql_dir(path: Path) -> list[TableMeta]:
    # Walk *.sql files, use filename as table name, file path hash as unique_id
    ...
```

`_detect_schema_version`: parse `metadata.dbt_schema_version` URL, extract integer (v9/v10).
For v10, `compiled_code` is in the same location. Schema difference is minor; handle both.

**4. `parser.py`**

```python
def build_lineage(tables: list[TableMeta]) -> tuple[nx.DiGraph, list[str]]:
    g = nx.DiGraph()
    name_to_id = {t.name: t.unique_id for t in tables}
    for t in tables:
        g.add_node(t.unique_id, meta=t)
    for t in tables:
        if not t.compiled_sql:
            continue  # sources have no SQL — skip, they are leaf nodes
        refs = _extract_table_refs(t.compiled_sql)
        for ref in refs:
            upstream_id = name_to_id.get(ref)
            if upstream_id:
                g.add_edge(upstream_id, t.unique_id)
    orphans = [n for n in g.nodes if g.out_degree(n) == 0
               and tables_by_id[n].node_type == NodeType.MODEL]
    # Note: sources with out_degree=0 are NOT orphans — they're unconnected sources (expected)
    return g, orphans

def _extract_table_refs(sql: str) -> list[str]:
    # Use SQLGlot to find all table references in FROM and JOIN clauses
    # Return bare table names (no project/dataset prefix)
    ...
```

**5. `pii.py`**

Two-stage classifier. Run stage 1 first; only call Claude (stage 2) for ambiguous columns.

Stage 1 — regex heuristics (no LLM):
```python
PII_PATTERNS = [
    r"\b(email|e_mail)\b",
    r"\b(phone|mobile|cell|telephone)\b",
    r"\b(ssn|social_security)\b",
    r"\b(dob|date_of_birth|birth_date|birthdate)\b",
    r"\b(first_name|last_name|full_name|given_name|surname)\b",
    r"\b(address|street|postal|zip_code|postcode)\b",
    r"\b(credit_card|card_number|pan|cvv)\b",
    r"\b(passport|national_id|tax_id|ein|tin)\b",
]
# Exclusion patterns — these match PII words but are NOT PII:
EXCLUSION_PATTERNS = [
    r"^is_", r"^has_", r"^valid_", r"_flag$", r"_count$",
    r"_domain$", r"_format$", r"_verified$", r"_hash$",
]
```
A column is definitively PII if it matches a PII pattern and does NOT match any exclusion.
A column is ambiguous if it partially matches. Only ambiguous columns go to Claude.

Stage 2 prompt: "Is column `{name}` (type: {type}) in table `{table_desc}` likely to contain PII? Answer JSON: {is_pii: bool, pii_type: str|null, reasoning: str}"

**6. `cache.py`**

```python
# Cache key: SHA256(compiled_sql or "") — changes when model SQL changes
# Schema: CREATE TABLE enrichment (cache_key TEXT PRIMARY KEY, data JSON, cached_at TEXT)
def get(cache_key: str) -> dict | None: ...
def put(cache_key: str, data: dict) -> None: ...
```

The `diff` subcommand logic: compare each table's `SHA256(compiled_sql)` to cache.
Tables not in cache → re-enrich. This is correct because manifest.json is the source of truth,
not git file diffs.

**7. `cost.py`**

Do NOT use tiktoken (it is OpenAI's tokenizer, not Claude's).

```python
def estimate_tokens(text: str) -> int:
    # Conservative estimate: 1 token per 3.5 characters
    return int(len(text) / 3.5)

def estimate_cost(tables: list[TableMeta], prompt_template_tokens: int = 300) -> float:
    # claude-sonnet-4-6: $3/M input tokens, $15/M output tokens
    input_tokens = sum(estimate_tokens(t.compiled_sql or "") + prompt_template_tokens
                       for t in tables)
    output_tokens = len(tables) * 200  # ~200 tokens per table response
    return (input_tokens / 1e6 * 3.0) + (output_tokens / 1e6 * 15.0)
```

**8. `enricher.py`**

Batch by token budget, not table count:
```python
MAX_BATCH_INPUT_TOKENS = 4000

def make_batches(tables: list[TableMeta]) -> list[list[TableMeta]]:
    batches, current, current_tokens = [], [], 0
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
```

Prompt per batch (return JSON array, one entry per table):
```
For each table below, provide:
- table_description: 1-2 sentence business-friendly description (not technical)
- column_descriptions: {col_name: description} for each column
- sensitivity_level: "public" | "internal" | "confidential" | "restricted"
- suggested_owner_hint: infer from table name/layer if possible, else null

Position context: {layer} layer table. Upstream: {upstream_names}. Downstream: {downstream_names}.

Tables:
[{name, compiled_sql, columns}]
```

On Claude JSON parse failure for a batch: log warning, skip enrichment for that batch, continue.
Do not retry automatically.

**9. `owner.py`**

Resolution priority:
1. `meta.owner` from dbt schema.yml (parsed from manifest `config.meta` or `node.meta`)
2. Most recent git committer for the model file (`gitpython` git blame on `file_path`)
3. Fallback: `"Unknown"`

`gitpython` import must be inside the function — don't fail at import if not installed.

**10. `renderer.py`**

Mermaid diagram safety: if DAG has > 25 nodes, split into subgraphs by layer:
```
graph LR
  subgraph Sources
    raw_orders; raw_customers
  end
  subgraph Staging
    stg_orders; stg_customers
  end
  ...
```
Always emit subgraph structure regardless of size — it renders better in GitHub.

Table markdown output must include (see sample below):
- Description, sensitivity, owner, PII status
- Upstream → table → downstream (text, not Mermaid — this is in the per-table file)
- Column table with PII column rows highlighted

**11. `cli.py`**

```
catalog build  --source <manifest.json or dir> --output <dir> [--format markdown|datahub] [-y]
catalog diff   --source <manifest.json or dir> --output <dir> [-y]
catalog validate --catalog-dir <dir>
catalog demo
```

`validate` subcommand checks:
- Every table file in output dir has a corresponding table in the index
- All lineage upstream/downstream names in each table page exist in the index
- PII report column references exist in their table files
- Mermaid file has no duplicate node IDs
- Exit 0 if clean, exit 1 with error list if not

`diff`: same as `build` but skips tables whose cache_key is already in cache.
Prints: "N tables unchanged (cached), M tables re-enriched."

---

## Demo Output (exact format required)

```
$ uv run catalog demo

Scanning fixtures/dbt_project/manifest.json...
  ✓ 24 nodes found (3 sources, 21 models)
  ✓ Manifest schema: v9

Building lineage DAG...
  ✓ 24 nodes, 31 edges
  ⚠ Orphaned models (no dbt downstream): orders_v1, users_archive

PII scan (heuristic)...
  ⚠ 8 PII columns across 3 tables:
      customers: email, phone, date_of_birth
      payments: card_last4, billing_address
      users: email, first_name, last_name

Estimated enrichment cost: ~$0.04 (24 tables, ~14,200 tokens)
Proceed? [y/N]: y   ← (auto-answered in demo mode)

Enriching with Claude... (4 batches)
  ✓ Batch 1/4 (7 tables)
  ✓ Batch 2/4 (7 tables)
  ✓ Batch 3/4 (6 tables)
  ✓ Batch 4/4 (4 tables)
  → 12 tables from cache, 12 freshly enriched

Writing catalog to ./demo_catalog/ ...
  ✓ 24 table pages
  ✓ index.md
  ✓ pii_report.md
  ✓ lineage.mmd
  ✓ datahub.yaml (--format datahub not set, skipped)

Done. Open demo_catalog/index.md to browse.
```

---

## Sample Table Page (renderer must produce this quality)

```markdown
## fct_orders
**Description:** Core transactional fact table representing one completed order per row.
Used by Finance for revenue reporting and by Operations for fulfillment tracking.

| | |
|---|---|
| **Sensitivity** | Internal |
| **Owner** | @alice |
| **PII** | None |
| **Layer** | Mart |

**Lineage:** `stg_orders`, `dim_customers`, `dim_products` → **`fct_orders`** → `rpt_revenue`, `rpt_fulfillment`, `ml_churn_features`

| Column | Type | Description | PII |
|--------|------|-------------|-----|
| order_id | STRING | Unique order identifier (UUID v4) | — |
| customer_id | STRING | FK to dim_customers | — |
| order_total_usd | FLOAT64 | Pre-tax order value in USD | — |
| created_at | TIMESTAMP | UTC timestamp when order was placed | — |
```

---

## Testing Requirements

Unit tests required for:
- `scanner`: correct TableMeta from v9 manifest, sources have `compiled_sql=None`, layer detection
- `parser`: edges correct, orphan = model with out_degree=0 (not sources), no self-edges
- `pii`: heuristic correctly flags email/phone/dob, correctly excludes is_email_verified/email_domain/email_count
- `cache`: hit returns cached value, miss returns None, different SQL → different key
- `cost.estimate_tokens`: within 20% of known-token strings
- `renderer`: Mermaid subgraph structure when nodes > 25, table page includes all required sections
- `enricher.make_batches`: no batch exceeds MAX_BATCH_INPUT_TOKENS, all tables appear exactly once
- `cli.validate`: catches missing table file, catches broken lineage reference

All unit tests use `fixtures/dbt_project/manifest.json`. Never call network or Claude.
Integration tests: `@pytest.mark.integration`, skipped without `ANTHROPIC_API_KEY` env var.

Coverage target: 80%+ excluding `cli.py`.

---

## Out of Scope (v1)

- Python / Spark / Airflow ETL parsing
- Real DataHub/OpenMetadata API push (YAML export only)
- Column-level lineage as non-experimental
- Web UI or hosted catalog
- Automated CI/CD scheduling
- Cross-system lineage (BigQuery + dbt combined)
- Slack/email notifications on catalog changes
