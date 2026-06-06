"""Parse table-level lineage from compiled SQL into a networkx DAG."""

from __future__ import annotations

import networkx as nx
import sqlglot
from sqlglot import exp

from .models import NodeType, TableMeta


def _extract_table_refs(sql: str) -> list[str]:
    """Return bare table names referenced in FROM/JOIN clauses (no project/dataset prefix)."""
    refs: list[str] = []
    seen: set[str] = set()
    try:
        statements = sqlglot.parse(sql, error_level=sqlglot.ErrorLevel.IGNORE)
    except Exception:
        return refs
    for stmt in statements:
        if stmt is None:
            continue
        # Names introduced by CTEs are not real upstream tables.
        cte_names = {cte.alias_or_name for cte in stmt.find_all(exp.CTE)}
        for table in stmt.find_all(exp.Table):
            name = table.name  # bare table name, ignores db/catalog qualifiers
            if not name or name in cte_names or name in seen:
                continue
            seen.add(name)
            refs.append(name)
    return refs


def build_lineage(tables: list[TableMeta]) -> tuple[nx.DiGraph, list[str]]:
    g = nx.DiGraph()
    name_to_id = {t.name: t.unique_id for t in tables}
    tables_by_id = {t.unique_id: t for t in tables}

    for t in tables:
        g.add_node(t.unique_id, meta=t)

    for t in tables:
        if not t.compiled_sql:
            continue  # sources have no SQL -- leaf upstream nodes
        for ref in _extract_table_refs(t.compiled_sql):
            upstream_id = name_to_id.get(ref)
            if upstream_id and upstream_id != t.unique_id:  # no self-edges
                g.add_edge(upstream_id, t.unique_id)

    # Orphans: models disconnected from the graph entirely (no upstream AND no
    # downstream). Terminal marts (out_degree 0 but consumed-from-above) are NOT
    # orphans; unconnected sources are excluded by the node_type check.
    orphans = [
        n
        for n in g.nodes
        if g.in_degree(n) == 0
        and g.out_degree(n) == 0
        and tables_by_id[n].node_type is NodeType.MODEL
    ]
    return g, orphans


def upstream_names(g: nx.DiGraph, uid: str) -> list[str]:
    return [g.nodes[u]["meta"].name for u in g.predecessors(uid)]


def downstream_names(g: nx.DiGraph, uid: str) -> list[str]:
    return [g.nodes[d]["meta"].name for d in g.successors(uid)]
