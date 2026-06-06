from __future__ import annotations

import os

import pytest

from data_catalog.enricher import enrich_batch_llm
from data_catalog.scanner import scan_manifest
from tests.conftest import FIXTURE_MANIFEST

pytestmark = pytest.mark.integration

_NO_KEY = not os.environ.get("ANTHROPIC_API_KEY")


@pytest.mark.skipif(_NO_KEY, reason="requires ANTHROPIC_API_KEY")
def test_enrich_one_batch_against_real_api():
    import anthropic

    client = anthropic.Anthropic()
    tables = [t for t in scan_manifest(FIXTURE_MANIFEST) if t.compiled_sql][:2]
    results = enrich_batch_llm(client, tables, context={})
    assert isinstance(results, list)
    assert {r.get("name") for r in results} <= {t.name for t in tables}
