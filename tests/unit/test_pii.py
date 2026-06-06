from __future__ import annotations

from data_catalog.models import PIIType
from data_catalog.pii import (
    apply_heuristics,
    classify_column_heuristic,
    collect_ambiguous,
    pii_summary,
)
from tests.conftest import col, make_table


def _is_pii(name):
    is_pii, ptype, _ = classify_column_heuristic(col(name))
    return is_pii, ptype


def test_flags_core_pii():
    assert _is_pii("email") == (True, PIIType.EMAIL)
    assert _is_pii("phone") == (True, PIIType.PHONE)
    assert _is_pii("date_of_birth") == (True, PIIType.DOB)
    assert _is_pii("ssn") == (True, PIIType.SSN)
    assert _is_pii("first_name") == (True, PIIType.NAME)
    assert _is_pii("billing_address") == (True, PIIType.ADDRESS)
    assert _is_pii("card_last4") == (True, PIIType.FINANCIAL)


def test_excludes_derived_columns():
    for name in ("is_email_verified", "email_domain", "email_count", "has_phone", "name_hash"):
        is_pii, _, _ = classify_column_heuristic(col(name))
        assert is_pii is False, name


def test_fixture_pii_counts(tables):
    apply_heuristics(tables)
    pii_tables = [t for t in tables if t.pii_columns]
    total = sum(len(t.pii_columns) for t in pii_tables)
    assert len(pii_tables) == 3
    assert total == 8


def test_pii_summary_groups_by_type(tables):
    apply_heuristics(tables)
    summary = pii_summary(tables)
    assert "stg_customers.email" in summary[PIIType.EMAIL]
    assert "stg_users.email" in summary[PIIType.EMAIL]


def test_ambiguous_collected_not_definite():
    # "nickname" contains 'name' but not as a whole token -> ambiguous, not definite.
    t = make_table("u", "staging", columns=[col("nickname")])
    apply_heuristics([t])
    assert t.pii_columns == []
    amb = collect_ambiguous([t])
    assert any(c.name == "nickname" for _, c in amb)
