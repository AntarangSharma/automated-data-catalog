"""Pydantic domain types for the data catalog."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

Layer = Literal["source", "staging", "intermediate", "mart", "other"]
Sensitivity = Literal["public", "internal", "confidential", "restricted"]


class NodeType(str, Enum):
    SOURCE = "source"
    MODEL = "model"


class PIIType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    DOB = "dob"
    NAME = "name"
    ADDRESS = "address"
    FINANCIAL = "financial"
    OTHER = "other"


class ColumnMeta(BaseModel):
    name: str
    data_type: str
    description: str = ""
    pii: bool = False
    pii_type: PIIType | None = None


class TableMeta(BaseModel):
    unique_id: str  # dbt unique_id or path hash for raw SQL
    name: str
    node_type: NodeType
    compiled_sql: str | None  # None for sources
    columns: list[ColumnMeta] = Field(default_factory=list)
    file_path: str
    layer: Layer
    owner: str = "Unknown"
    description: str = ""
    sensitivity: Sensitivity = "internal"
    pii_columns: list[str] = Field(default_factory=list)


class LineageEdge(BaseModel):
    upstream: str  # unique_id
    downstream: str  # unique_id


class CatalogReport(BaseModel):
    tables: list[TableMeta]
    edges: list[LineageEdge]
    orphaned: list[str]  # unique_ids with no downstream dbt consumers
    pii_summary: dict[PIIType, list[str]]  # pii_type -> [table.column]
