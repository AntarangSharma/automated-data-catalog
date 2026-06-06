"""Typer CLI: build / diff / validate / demo."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from . import cost as cost_mod
from . import enricher, owner, pii
from .cache import EnrichmentCache, cache_key
from .models import TableMeta
from .parser import build_lineage, downstream_names, upstream_names
from .renderer import render_catalog
from .resources import fixture_manifest
from .scanner import (
    manifest_nodes,
    manifest_schema_version,
    scan_manifest,
    scan_sql_dir,
)

app = typer.Typer(add_completion=False, help="Automated Data Catalog + Lineage Builder")


# ---- shared helpers --------------------------------------------------------

def _load_tables(source: Path) -> tuple[list[TableMeta], dict[str, dict] | None, int | None]:
    source = Path(source)
    if source.is_dir():
        return scan_sql_dir(source), None, None
    version = manifest_schema_version(source)
    return scan_manifest(source), manifest_nodes(source), version


def _resolve_owners(tables: list[TableMeta], nodes: dict[str, dict] | None, repo_root: Path | None) -> None:
    for t in tables:
        node = nodes.get(t.unique_id) if nodes else None
        t.owner = owner.resolve_owner(t, node, repo_root)


def _build_context(tables: list[TableMeta], g) -> dict[str, dict]:
    return {
        t.unique_id: {
            "upstream": upstream_names(g, t.unique_id),
            "downstream": downstream_names(g, t.unique_id),
        }
        for t in tables
    }


def _anthropic_client():
    import os

    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic()


def _print_scan(source: Path, tables: list[TableMeta], version: int | None) -> None:
    sources = [t for t in tables if t.node_type.value == "source"]
    models = [t for t in tables if t.node_type.value == "model"]
    typer.echo(f"Scanning {source}...")
    typer.echo(f"  ✓ {len(tables)} nodes found ({len(sources)} sources, {len(models)} models)")
    if version is not None:
        typer.echo(f"  ✓ Manifest schema: v{version}")


def _print_lineage(g, orphaned: list[str], tables: list[TableMeta]) -> None:
    id_to_name = {t.unique_id: t.name for t in tables}
    typer.echo("\nBuilding lineage DAG...")
    typer.echo(f"  ✓ {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    if orphaned:
        names = ", ".join(sorted(id_to_name.get(o, o) for o in orphaned))
        typer.echo(f"  ⚠ Orphaned models (no dbt lineage): {names}")


def _print_pii(tables: list[TableMeta]) -> None:
    pii_tables = [t for t in tables if t.pii_columns]
    total = sum(len(t.pii_columns) for t in pii_tables)
    typer.echo("\nPII scan (heuristic)...")
    if not total:
        typer.echo("  ✓ No PII columns detected")
        return
    typer.echo(f"  ⚠ {total} PII columns across {len(pii_tables)} tables:")
    for t in sorted(pii_tables, key=lambda x: x.name):
        typer.echo(f"      {t.name}: {', '.join(t.pii_columns)}")


def _confirm_cost(tables: list[TableMeta], assume_yes: bool, demo: bool) -> bool:
    n_tok = cost_mod.estimate_input_tokens(tables) + len(tables) * cost_mod.TOKENS_PER_TABLE_OUTPUT
    usd = cost_mod.estimate_cost(tables)
    typer.echo(
        f"\nEstimated enrichment cost: ~${usd:.2f} ({len(tables)} tables, ~{n_tok:,} tokens)"
    )
    if demo:
        typer.echo("Proceed? [y/N]: y   ← (auto-answered in demo mode)")
        return True
    if assume_yes:
        typer.echo("Proceed? [y/N]: y   (--yes)")
        return True
    return typer.confirm("Proceed?", default=False)


def _enrich(
    tables: list[TableMeta],
    g,
    cache: EnrichmentCache,
    *,
    mode: str,
    client=None,
) -> tuple[int, int]:
    """Enrich tables. mode in {build, diff, demo}. Returns (n_cached, n_enriched)."""
    context = _build_context(tables, g)

    def _batches_label(n: int) -> str:
        return f"{n} batch" if n == 1 else f"{n} batches"

    # Partition into cached vs to-enrich.
    cached, to_enrich = [], []
    for t in tables:
        key = cache_key(t.compiled_sql)
        hit = cache.get(key)
        # `build` always re-enriches; `diff`/`demo` reuse cache hits.
        if mode in ("diff", "demo") and hit is not None:
            enricher.apply_enrichment(t, hit)
            cached.append(t)
        else:
            to_enrich.append(t)

    if not to_enrich:
        return len(cached), 0

    if mode == "demo":
        # Offline deterministic enrichment -- no network.
        batches = enricher.make_batches(to_enrich)
        typer.echo(f"\nEnriching with Claude... ({_batches_label(len(batches))})")
        for i, batch in enumerate(batches, 1):
            for t in batch:
                data = enricher.offline_enrichment(t)
                enricher.apply_enrichment(t, data)
                cache.put(cache_key(t.compiled_sql), data)
            typer.echo(f"  ✓ Batch {i}/{len(batches)} ({len(batch)} tables)")
    else:
        batches = enricher.make_batches(to_enrich)
        typer.echo(f"\nEnriching with Claude... ({_batches_label(len(batches))})")
        for i, batch in enumerate(batches, 1):
            try:
                results = enricher.enrich_batch_llm(client, batch, context)
            except Exception as exc:  # JSON parse / API error: skip batch, continue
                typer.echo(f"  ⚠ Batch {i}/{len(batches)} failed ({exc}); skipping enrichment")
                continue
            by_name = {r.get("name"): r for r in results if isinstance(r, dict)}
            for t in batch:
                data = by_name.get(t.name)
                if data:
                    enricher.apply_enrichment(t, data)
                    cache.put(cache_key(t.compiled_sql), data)
            typer.echo(f"  ✓ Batch {i}/{len(batches)} ({len(batch)} tables)")
        # Stage-2 PII for ambiguous columns.
        pii.classify_ambiguous_llm(client, to_enrich)

    return len(cached), len(to_enrich)


def _run(
    source: Path,
    output: Path,
    fmt: str,
    assume_yes: bool,
    *,
    mode: str,
    demo: bool = False,
) -> None:
    tables, nodes, version = _load_tables(source)
    repo_root = source.parent if source.is_file() else source
    _print_scan(source, tables, version)

    _resolve_owners(tables, nodes, repo_root)
    pii.apply_heuristics(tables)

    g, orphaned = build_lineage(tables)
    _print_lineage(g, orphaned, tables)
    _print_pii(tables)

    if not _confirm_cost(tables, assume_yes, demo):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)

    cache_path = Path(output) / ".catalog_cache.sqlite"
    cache = EnrichmentCache(cache_path)
    if demo:
        _seed_demo_cache(tables, cache)
    client = None if (demo or mode == "validate") else _anthropic_client()
    n_cached, n_enriched = _enrich(tables, g, cache, mode=mode, client=client)
    cache.close()
    if mode in ("diff", "demo"):
        typer.echo(f"  → {n_cached} tables from cache, {n_enriched} freshly enriched")
    elif mode == "build":
        typer.echo(f"{n_enriched} tables enriched.")

    typer.echo(f"\nWriting catalog to {output}/ ...")
    counts = render_catalog(tables, g, orphaned, output)
    typer.echo(f"  ✓ {counts['table_pages']} table pages")
    typer.echo("  ✓ index.md")
    typer.echo("  ✓ pii_report.md")
    typer.echo("  ✓ lineage.mmd")
    if fmt == "datahub":
        from .models_datahub import write_datahub_yaml

        write_datahub_yaml(tables, output)
        typer.echo("  ✓ datahub.yaml")
    else:
        typer.echo("  ✓ datahub.yaml (--format datahub not set, skipped)")

    typer.echo(f"\nDone. Open {Path(output).name}/index.md to browse.")


def _seed_demo_cache(tables: list[TableMeta], cache: EnrichmentCache) -> None:
    """Pre-populate the cache for half the tables so the demo shows a cache split."""
    half = len(tables) // 2
    for t in sorted(tables, key=lambda x: x.unique_id)[:half]:
        cache.put(cache_key(t.compiled_sql), enricher.offline_enrichment(t))


# ---- commands --------------------------------------------------------------

@app.command()
def build(
    source: Path = typer.Option(..., "--source", help="manifest.json or a directory of SQL"),
    output: Path = typer.Option(..., "--output", help="output directory"),
    fmt: str = typer.Option("markdown", "--format", help="markdown | datahub"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip cost confirmation"),
):
    """Build the catalog (enriches every table with Claude)."""
    _run(source, output, fmt, yes, mode="build")


@app.command()
def diff(
    source: Path = typer.Option(..., "--source", help="manifest.json or a directory of SQL"),
    output: Path = typer.Option(..., "--output", help="output directory"),
    fmt: str = typer.Option("markdown", "--format", help="markdown | datahub"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip cost confirmation"),
):
    """Like build, but skips tables whose compiled-SQL hash is already cached."""
    _run(source, output, fmt, yes, mode="diff")


@app.command()
def demo():
    """Run a fully offline demo from bundled fixtures (no credentials required)."""
    _run(fixture_manifest(), Path("./demo_catalog"), "markdown", True, mode="demo", demo=True)


@app.command()
def validate(
    catalog_dir: Path = typer.Option(..., "--catalog-dir", help="a built catalog directory"),
):
    """Validate internal consistency of a built catalog. Exit 1 with errors if not clean."""
    errors = _validate_catalog(Path(catalog_dir))
    if errors:
        typer.echo(f"✗ {len(errors)} validation error(s):")
        for e in errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)
    typer.echo("✓ Catalog is valid.")


def _validate_catalog(catalog_dir: Path) -> list[str]:
    errors: list[str] = []
    index = catalog_dir / "index.md"
    if not index.exists():
        return [f"missing index.md in {catalog_dir}"]
    index_text = index.read_text()

    # Table names linked from the index, e.g. [stg_orders](stg_orders.md)
    indexed = set(re.findall(r"\[([^\]]+)\]\(([^)]+)\.md\)", index_text))
    indexed_names = {name for name, _ in indexed}

    table_files = {p.stem for p in catalog_dir.glob("*.md")} - {"index", "pii_report"}

    # 1. Every table page must be linked in the index.
    for name in table_files:
        if name not in indexed_names:
            errors.append(f"table page '{name}.md' has no entry in index.md")

    # 2. Lineage references in each table page must resolve to known tables.
    lineage_re = re.compile(r"\*\*Lineage:\*\* (.+)")
    backtick_re = re.compile(r"`([^`]+)`")
    for name in table_files:
        text = (catalog_dir / f"{name}.md").read_text()
        m = lineage_re.search(text)
        if not m:
            continue
        for ref in backtick_re.findall(m.group(1)):
            if ref not in table_files:
                errors.append(f"{name}.md references unknown table '{ref}' in lineage")

    # 3. PII report column refs must exist in their table files.
    pii_report = catalog_dir / "pii_report.md"
    if pii_report.exists():
        for tname, _ in re.findall(r"\[([^\]]+)\]\(([^)]+)\.md\)", pii_report.read_text()):
            if tname not in table_files:
                errors.append(f"pii_report.md references missing table '{tname}'")

    # 4. Mermaid file must have no duplicate node IDs.
    mmd = catalog_dir / "lineage.mmd"
    if mmd.exists():
        node_ids = re.findall(r"^\s{4}(\w+)\[", mmd.read_text(), re.MULTILINE)
        dupes = {n for n in node_ids if node_ids.count(n) > 1}
        for d in sorted(dupes):
            errors.append(f"lineage.mmd has duplicate node id '{d}'")

    return errors


if __name__ == "__main__":
    app()
