from __future__ import annotations

from data_catalog.cost import estimate_cost, estimate_tokens
from tests.conftest import make_table


def test_estimate_tokens_within_20pct():
    # ~125 char string; a real Claude tokenizer is ~30-35 tokens. 3.5 c/tok -> ~35.
    text = "SELECT customer_id, order_total, created_at FROM stg_orders WHERE status = 'complete' GROUP BY 1, 2, 3"
    known = len(text) / 4.0  # rough ground truth (~4 chars/token for English+SQL)
    est = estimate_tokens(text)
    assert abs(est - known) / known < 0.20


def test_estimate_tokens_monotonic():
    assert estimate_tokens("a" * 100) > estimate_tokens("a" * 10)


def test_estimate_cost_positive():
    tables = [make_table("t1", "staging", sql="SELECT * FROM a")]
    assert estimate_cost(tables) > 0


def test_estimate_cost_scales_with_table_count():
    one = [make_table("t1", "staging", sql="SELECT * FROM a")]
    many = [make_table(f"t{i}", "staging", sql="SELECT * FROM a") for i in range(10)]
    assert estimate_cost(many) > estimate_cost(one)
