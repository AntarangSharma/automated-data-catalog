from __future__ import annotations

import json
from types import SimpleNamespace

from data_catalog.enricher import enrich_batch_llm
from data_catalog.models import PIIType
from data_catalog.models_datahub import to_datahub_yaml, write_datahub_yaml
from data_catalog.owner import owner_from_git, owner_from_meta, resolve_owner
from data_catalog.pii import apply_heuristics, classify_ambiguous_llm
from data_catalog.scanner import scan_sql_dir
from tests.conftest import col, make_table


# ---- fake Anthropic client -------------------------------------------------

class _FakeMessages:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


# ---- owner -----------------------------------------------------------------

def test_owner_from_meta_config():
    node = {"config": {"meta": {"owner": "@team"}}, "meta": {}}
    assert owner_from_meta(node) == "@team"


def test_owner_from_meta_node_level():
    node = {"config": {"meta": {}}, "meta": {"owner": "@x"}}
    assert owner_from_meta(node) == "@x"


def test_owner_from_meta_absent():
    assert owner_from_meta({"config": {}, "meta": {}}) is None


def test_owner_from_git_not_a_repo(tmp_path):
    # gitpython installed but path is not a repo -> None (graceful).
    assert owner_from_git(str(tmp_path / "x.sql"), repo_root=tmp_path) is None


def test_resolve_owner_prefers_meta():
    t = make_table("fct", "mart")
    assert resolve_owner(t, {"config": {"meta": {"owner": "@a"}}}) == "@a"


def test_resolve_owner_fallback_unknown():
    t = make_table("fct", "mart")
    t.file_path = ""
    assert resolve_owner(t, None) == "Unknown"


# ---- datahub ---------------------------------------------------------------

def test_datahub_yaml_structure():
    c = col("email")
    c.pii = True
    c.pii_type = PIIType.EMAIL
    t = make_table("stg_users", "staging", sql="SELECT 1", columns=[c])
    t.pii_columns = ["email"]
    t.description = "Users."
    yaml = to_datahub_yaml([t])
    assert "datasets:" in yaml
    assert "urn:li:dataset" in yaml
    assert "contains-pii" in yaml
    assert "piiType: \"email\"" in yaml


def test_write_datahub_yaml(tmp_path):
    t = make_table("t", "staging", sql="SELECT 1", columns=[col("a")])
    out = write_datahub_yaml([t], tmp_path)
    assert out.exists()
    assert "fieldPath" in out.read_text()


# ---- scanner: raw SQL dir --------------------------------------------------

def test_scan_sql_dir(tmp_path):
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "stg_a.sql").write_text("SELECT * FROM raw_a")
    (tmp_path / "fct_b.sql").write_text("SELECT * FROM stg_a")
    tables = scan_sql_dir(tmp_path)
    names = {t.name for t in tables}
    assert names == {"stg_a", "fct_b"}
    by_name = {t.name: t for t in tables}
    assert by_name["stg_a"].layer == "staging"
    assert by_name["fct_b"].layer == "mart"
    assert by_name["stg_a"].unique_id != by_name["fct_b"].unique_id


# ---- LLM-backed paths (mocked) ---------------------------------------------

def test_enrich_batch_llm_parses_json():
    payload = json.dumps(
        [{"name": "t", "table_description": "d", "column_descriptions": {}, "sensitivity_level": "internal"}]
    )
    client = _FakeClient(payload)
    t = make_table("t", "staging", sql="SELECT 1")
    results = enrich_batch_llm(client, [t], context={})
    assert results[0]["name"] == "t"


def test_classify_ambiguous_llm_flags_pii():
    t = make_table("u", "staging", columns=[col("nickname")])
    apply_heuristics([t])  # nickname is ambiguous, not yet flagged
    assert t.pii_columns == []
    client = _FakeClient(json.dumps({"is_pii": True, "pii_type": "name", "reasoning": "is a name"}))
    classify_ambiguous_llm(client, [t])
    assert "nickname" in t.pii_columns
    assert t.columns[0].pii_type == PIIType.NAME


def test_classify_ambiguous_llm_handles_bad_json():
    t = make_table("u", "staging", columns=[col("nickname")])
    apply_heuristics([t])
    client = _FakeClient("not json")
    classify_ambiguous_llm(client, [t])  # must not raise
    assert t.pii_columns == []
