from __future__ import annotations

from data_catalog.cache import EnrichmentCache, cache_key


def test_hit_and_miss(tmp_path):
    c = EnrichmentCache(tmp_path / "c.sqlite")
    key = cache_key("SELECT 1")
    assert c.get(key) is None
    c.put(key, {"table_description": "x"})
    assert c.get(key) == {"table_description": "x"}
    c.close()


def test_different_sql_different_key():
    assert cache_key("SELECT 1") != cache_key("SELECT 2")


def test_same_sql_same_key():
    assert cache_key("SELECT a FROM t") == cache_key("SELECT a FROM t")


def test_none_sql_is_stable():
    assert cache_key(None) == cache_key("")


def test_has_and_replace(tmp_path):
    c = EnrichmentCache(tmp_path / "c.sqlite")
    key = cache_key("x")
    assert not c.has(key)
    c.put(key, {"v": 1})
    assert c.has(key)
    c.put(key, {"v": 2})
    assert c.get(key) == {"v": 2}
    c.close()
