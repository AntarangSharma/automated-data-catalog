"""Two-stage PII column classifier: regex heuristics first, Claude only for ambiguous columns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import ColumnMeta, PIIType, TableMeta

# (compiled pattern, PIIType) -- a column matching one of these is a PII candidate.
_PII_SPECS: list[tuple[str, PIIType]] = [
    (r"\b(email|e_mail)\b", PIIType.EMAIL),
    (r"\b(phone|mobile|cell|telephone)\b", PIIType.PHONE),
    (r"\b(ssn|social_security)\b", PIIType.SSN),
    (r"\b(dob|date_of_birth|birth_date|birthdate)\b", PIIType.DOB),
    (r"\b(first_name|last_name|full_name|given_name|surname)\b", PIIType.NAME),
    (r"\b(address|street|postal|zip_code|postcode|billing_address)\b", PIIType.ADDRESS),
    (r"\b(credit_card|card_number|card_last4|pan|cvv)\b", PIIType.FINANCIAL),
    (r"\b(passport|national_id|tax_id|ein|tin)\b", PIIType.OTHER),
]
PII_PATTERNS = [re.compile(p, re.IGNORECASE) for p, _ in _PII_SPECS]

# These match PII words but are NOT PII (flags, aggregates, derived metadata).
EXCLUSION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^is_",
        r"^has_",
        r"^valid_",
        r"_flag$",
        r"_count$",
        r"_domain$",
        r"_format$",
        r"_verified$",
        r"_hash$",
    )
]


@dataclass
class HeuristicResult:
    definite: list[tuple[ColumnMeta, PIIType]]
    ambiguous: list[ColumnMeta]


def _excluded(name: str) -> bool:
    return any(p.search(name) for p in EXCLUSION_PATTERNS)


def _match_pii_type(name: str) -> PIIType | None:
    for pat, ptype in zip(PII_PATTERNS, [t for _, t in _PII_SPECS]):
        if pat.search(name):
            return ptype
    return None


def classify_column_heuristic(col: ColumnMeta) -> tuple[bool, PIIType | None, bool]:
    """Return (is_definite_pii, pii_type, is_ambiguous)."""
    name = col.name
    if _excluded(name):
        return False, None, False
    ptype = _match_pii_type(name)
    if ptype is not None:
        return True, ptype, False
    # Partial / weak signal: a PII word appears as a substring but not as a whole token.
    if re.search(r"(email|phone|name|address|card|ssn|birth|passport)", name, re.IGNORECASE):
        return False, None, True
    return False, None, False


def scan_table_heuristic(table: TableMeta) -> HeuristicResult:
    definite: list[tuple[ColumnMeta, PIIType]] = []
    ambiguous: list[ColumnMeta] = []
    for col in table.columns:
        is_pii, ptype, amb = classify_column_heuristic(col)
        if is_pii and ptype is not None:
            definite.append((col, ptype))
        elif amb:
            ambiguous.append(col)
    return HeuristicResult(definite=definite, ambiguous=ambiguous)


def apply_heuristics(tables: list[TableMeta]) -> None:
    """Mutate tables in place: set ColumnMeta.pii/pii_type and TableMeta.pii_columns
    for definitely-PII columns. Ambiguous columns are left for stage 2."""
    for t in tables:
        result = scan_table_heuristic(t)
        pii_cols: list[str] = []
        for col, ptype in result.definite:
            col.pii = True
            col.pii_type = ptype
            pii_cols.append(col.name)
        t.pii_columns = pii_cols


def collect_ambiguous(tables: list[TableMeta]) -> list[tuple[TableMeta, ColumnMeta]]:
    out: list[tuple[TableMeta, ColumnMeta]] = []
    for t in tables:
        for col in scan_table_heuristic(t).ambiguous:
            out.append((t, col))
    return out


# ---- Stage 2: Claude classification of ambiguous columns -------------------

STAGE2_PROMPT = (
    "Is column `{name}` (type: {type}) in table `{table_desc}` likely to contain PII? "
    'Answer JSON: {{"is_pii": bool, "pii_type": str|null, "reasoning": str}}'
)


def classify_ambiguous_llm(client, tables: list[TableMeta]) -> None:
    """Call Claude for each ambiguous column and apply results. No-op if none."""
    pending = collect_ambiguous(tables)
    for table, col in pending:
        prompt = STAGE2_PROMPT.format(
            name=col.name,
            type=col.data_type,
            table_desc=table.description or table.name,
        )
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
            data = json.loads(text)
        except Exception:
            continue
        if data.get("is_pii"):
            col.pii = True
            try:
                col.pii_type = PIIType(data.get("pii_type") or "other")
            except ValueError:
                col.pii_type = PIIType.OTHER
            if col.name not in table.pii_columns:
                table.pii_columns.append(col.name)


def pii_summary(tables: list[TableMeta]) -> dict[PIIType, list[str]]:
    summary: dict[PIIType, list[str]] = {}
    for t in tables:
        for col in t.columns:
            if col.pii and col.pii_type is not None:
                summary.setdefault(col.pii_type, []).append(f"{t.name}.{col.name}")
    return summary
