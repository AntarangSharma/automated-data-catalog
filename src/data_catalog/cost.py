"""Token-cost estimation (no tiktoken -- that is OpenAI's tokenizer, not Claude's)."""

from __future__ import annotations

from .models import TableMeta

# claude-sonnet-4-6 pricing (USD per 1M tokens)
INPUT_PRICE_PER_M = 3.0
OUTPUT_PRICE_PER_M = 15.0
TOKENS_PER_TABLE_OUTPUT = 200


def estimate_tokens(text: str) -> int:
    # Conservative estimate: ~1 token per 3.5 characters.
    return int(len(text) / 3.5)


def estimate_input_tokens(tables: list[TableMeta], prompt_template_tokens: int = 300) -> int:
    return sum(
        estimate_tokens(t.compiled_sql or "") + prompt_template_tokens for t in tables
    )


def estimate_cost(tables: list[TableMeta], prompt_template_tokens: int = 300) -> float:
    input_tokens = estimate_input_tokens(tables, prompt_template_tokens)
    output_tokens = len(tables) * TOKENS_PER_TABLE_OUTPUT
    return (input_tokens / 1e6 * INPUT_PRICE_PER_M) + (
        output_tokens / 1e6 * OUTPUT_PRICE_PER_M
    )
