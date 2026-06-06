"""DataHub YAML serialization (only used with --format datahub).

Emits a minimal DataHub-style dataset YAML. This is an export format only --
no API push is performed (see BUILD.md "Out of Scope").
"""

from __future__ import annotations

from pathlib import Path

from .models import TableMeta

_PLATFORM = "dbt"


def _q(value: str) -> str:
    """Quote a scalar for YAML safely."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dataset_block(table: TableMeta) -> list[str]:
    urn = f"urn:li:dataset:(urn:li:dataPlatform:{_PLATFORM},{table.name},PROD)"
    lines = [
        f"  - urn: {_q(urn)}",
        f"    name: {_q(table.name)}",
        f"    platform: {_q(_PLATFORM)}",
        f"    description: {_q(table.description)}",
        f"    owner: {_q(table.owner)}",
        f"    sensitivity: {_q(table.sensitivity)}",
        f"    layer: {_q(table.layer)}",
        "    tags:",
        f"      - {_q('layer:' + table.layer)}",
        f"      - {_q('sensitivity:' + table.sensitivity)}",
    ]
    if table.pii_columns:
        lines.append(f"      - {_q('contains-pii')}")
    lines.append("    schema:")
    if not table.columns:
        lines.append("      fields: []")
    else:
        lines.append("      fields:")
        for col in table.columns:
            lines.append(f"        - fieldPath: {_q(col.name)}")
            lines.append(f"          type: {_q(col.data_type)}")
            lines.append(f"          description: {_q(col.description)}")
            lines.append("          isPartOfKey: false")
            lines.append(f"          isPII: {str(col.pii).lower()}")
            if col.pii and col.pii_type is not None:
                lines.append(f"          piiType: {_q(col.pii_type.value)}")
    return lines


def to_datahub_yaml(tables: list[TableMeta]) -> str:
    lines = ["version: 1", "source: data-catalog", "datasets:"]
    for t in sorted(tables, key=lambda x: x.name):
        lines.extend(_dataset_block(t))
    return "\n".join(lines) + "\n"


def write_datahub_yaml(tables: list[TableMeta], output_dir: Path | str) -> Path:
    out = Path(output_dir) / "datahub.yaml"
    out.write_text(to_datahub_yaml(tables))
    return out
