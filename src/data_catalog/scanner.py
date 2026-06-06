"""Scan a dbt manifest.json or a directory of raw SQL files into TableMeta."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import ColumnMeta, Layer, NodeType, TableMeta

_VERSION_RE = re.compile(r"/manifest/v(\d+)/")


def _detect_schema_version(data: dict) -> int:
    url = data.get("metadata", {}).get("dbt_schema_version", "")
    m = _VERSION_RE.search(url)
    if not m:
        raise ValueError(f"Unrecognized dbt_schema_version: {url!r}")
    return int(m.group(1))


def _detect_layer(name: str, node_type: NodeType, fsrc: str = "") -> Layer:
    if node_type is NodeType.SOURCE:
        return "source"
    lowered = name.lower()
    path = fsrc.lower()
    if lowered.startswith("stg_") or "/staging/" in path:
        return "staging"
    if lowered.startswith("int_") or "/intermediate/" in path:
        return "intermediate"
    if lowered.startswith(("fct_", "dim_", "rpt_", "ml_", "mart_")) or "/marts/" in path:
        return "mart"
    return "other"


def _parse_columns(node: dict) -> list[ColumnMeta]:
    cols = []
    for cname, c in (node.get("columns") or {}).items():
        cols.append(
            ColumnMeta(
                name=c.get("name", cname),
                data_type=(c.get("data_type") or "UNKNOWN"),
                description=c.get("description", "") or "",
            )
        )
    return cols


def _parse_node(uid: str, node: dict, version: int) -> TableMeta:
    resource_type = node.get("resource_type")
    node_type = NodeType.SOURCE if resource_type == "source" else NodeType.MODEL
    # compiled_code lives in the same place for v9 and v10.
    compiled = node.get("compiled_code") if node_type is NodeType.MODEL else None
    fsrc = node.get("original_file_path") or node.get("path") or ""
    return TableMeta(
        unique_id=uid,
        name=node.get("name", uid.split(".")[-1]),
        node_type=node_type,
        compiled_sql=compiled,
        columns=_parse_columns(node),
        file_path=fsrc,
        layer=_detect_layer(node.get("name", ""), node_type, fsrc),
    )


def manifest_schema_version(path: Path) -> int:
    return _detect_schema_version(json.loads(Path(path).read_text()))


def manifest_nodes(path: Path) -> dict[str, dict]:
    """Return {unique_id: raw node dict} for models + sources (for owner resolution)."""
    data = json.loads(Path(path).read_text())
    return {**data.get("nodes", {}), **data.get("sources", {})}


def scan_manifest(path: Path) -> list[TableMeta]:
    data = json.loads(Path(path).read_text())
    version = _detect_schema_version(data)
    nodes = {**data.get("nodes", {}), **data.get("sources", {})}
    result: list[TableMeta] = []
    for uid, node in nodes.items():
        if node.get("resource_type") not in ("model", "source"):
            continue
        result.append(_parse_node(uid, node, version))
    return result


def scan_sql_dir(path: Path) -> list[TableMeta]:
    path = Path(path)
    result: list[TableMeta] = []
    for sql_file in sorted(path.rglob("*.sql")):
        text = sql_file.read_text()
        name = sql_file.stem
        uid = "sql." + hashlib.sha256(str(sql_file).encode()).hexdigest()[:16]
        result.append(
            TableMeta(
                unique_id=uid,
                name=name,
                node_type=NodeType.MODEL,
                compiled_sql=text,
                columns=[],
                file_path=str(sql_file),
                layer=_detect_layer(name, NodeType.MODEL, str(sql_file)),
            )
        )
    return result
