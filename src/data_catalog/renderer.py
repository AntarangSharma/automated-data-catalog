"""Render the catalog to markdown table pages, index, PII report, and a Mermaid diagram."""

from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import TableMeta
from .parser import downstream_names, upstream_names
from .pii import pii_summary
from .resources import templates_dir

LAYER_ORDER = ["source", "staging", "intermediate", "mart", "other"]
_ID_SANITIZE = re.compile(r"[^A-Za-z0-9_]")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir())),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )


def _sanitize(name: str) -> str:
    return _ID_SANITIZE.sub("_", name)


def _by_layer(tables: list[TableMeta]) -> dict[str, list[TableMeta]]:
    groups: dict[str, list[TableMeta]] = {}
    for layer in LAYER_ORDER:
        members = [t for t in tables if t.layer == layer]
        if members:
            groups[layer] = sorted(members, key=lambda t: t.name)
    return groups


def render_table_page(env: Environment, table: TableMeta, g: nx.DiGraph) -> str:
    tmpl = env.get_template("table.md.j2")
    return tmpl.render(
        table=table,
        upstream=sorted(upstream_names(g, table.unique_id)),
        downstream=sorted(downstream_names(g, table.unique_id)),
    )


def render_index(env: Environment, tables: list[TableMeta], g: nx.DiGraph, orphaned: list[str]) -> str:
    id_to_name = {t.unique_id: t.name for t in tables}
    tmpl = env.get_template("index.md.j2")
    return tmpl.render(
        tables=tables,
        edge_count=g.number_of_edges(),
        pii_table_count=sum(1 for t in tables if t.pii_columns),
        orphaned=sorted(id_to_name.get(o, o) for o in orphaned),
        by_layer=_by_layer(tables),
    )


def render_pii_report(env: Environment, tables: list[TableMeta]) -> str:
    pii_tables = sorted((t for t in tables if t.pii_columns), key=lambda t: t.name)
    summary = pii_summary(tables)
    total = sum(len(t.pii_columns) for t in pii_tables)
    tmpl = env.get_template("pii_report.md.j2")
    return tmpl.render(
        pii_tables=pii_tables,
        pii_summary=summary,
        total_pii_columns=total,
    )


def render_lineage(env: Environment, tables: list[TableMeta], g: nx.DiGraph) -> str:
    id_to_name = {t.unique_id: t.name for t in tables}
    subgraphs: dict[str, list[str]] = {}
    for layer in LAYER_ORDER:
        names = sorted(_sanitize(t.name) for t in tables if t.layer == layer)
        subgraphs[layer] = names
    edges = sorted(
        (_sanitize(id_to_name[u]), _sanitize(id_to_name[v])) for u, v in g.edges
    )
    tmpl = env.get_template("lineage.mmd.j2")
    return tmpl.render(subgraphs=subgraphs, edges=edges)


def render_catalog(
    tables: list[TableMeta],
    g: nx.DiGraph,
    orphaned: list[str],
    output_dir: Path | str,
) -> dict[str, int]:
    """Write all catalog files. Returns counts of artifacts written."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    env = _env()

    for t in tables:
        (out / f"{t.name}.md").write_text(render_table_page(env, t, g))
    (out / "index.md").write_text(render_index(env, tables, g, orphaned))
    (out / "pii_report.md").write_text(render_pii_report(env, tables))
    (out / "lineage.mmd").write_text(render_lineage(env, tables, g))

    return {
        "table_pages": len(tables),
        "index": 1,
        "pii_report": 1,
        "lineage": 1,
    }
