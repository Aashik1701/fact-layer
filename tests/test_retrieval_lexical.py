"""Tests for fact_layer/retrieval/lexical.py (sqlite3 FTS5 + bm25)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.retrieval.lexical import LexicalIndex


def _mkindex(tmp_path):
    return LexicalIndex(str(tmp_path / "lexical.sqlite3"))


def test_upsert_and_search_finds_shared_tokens(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert("f1", "subject: delhivery\nmeasure: revenue\nperiod: FY2023-24")
    idx.upsert("f2", "subject: delhivery\nmeasure: revenue\nperiod: FY2024-25")
    idx.upsert("f3", "subject: rbi\nmeasure: reserve_money_growth\nperiod: FY2023-24")

    results = idx.search("subject: delhivery\nmeasure: revenue\nperiod: FY2023-24", top_k=10)
    ids = [r.fact_id for r in results]
    assert "f1" in ids
    assert "f2" in ids   # shares subject/measure tokens even though period differs
    assert set(ids) == {"f1", "f2", "f3"}   # "period" token alone still links f3 loosely


def test_higher_score_for_more_overlapping_terms(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert("close", "subject: delhivery\nmeasure: revenue\nperiod: FY2023-24\nscope: standalone")
    idx.upsert("far", "subject: rbi\nmeasure: reserve_money_growth\nperiod: FY2019-20\nscope: unknown")
    results = idx.search("subject: delhivery\nmeasure: revenue\nperiod: FY2023-24\nscope: standalone", top_k=10)
    by_id = {r.fact_id: r.score for r in results}
    assert by_id["close"] > by_id["far"]


def test_upsert_many_replaces_existing_row(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert("f1", "subject: alpha")
    idx.upsert("f1", "subject: beta")
    assert len(idx) == 1
    results = idx.search("subject: beta", top_k=10)
    assert [r.fact_id for r in results] == ["f1"]
    # "alpha" alone (not the shared "subject" token) must no longer match —
    # proves the old row content was replaced, not merely appended to.
    assert idx.search("alpha", top_k=10) == []


def test_delete_removes_row(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert("f1", "subject: delhivery measure: revenue")
    assert len(idx) == 1
    idx.delete("f1")
    assert len(idx) == 0
    assert idx.search("subject: delhivery", top_k=10) == []


def test_rebuild_clears_index(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert_many([("f1", "subject: a"), ("f2", "subject: b")])
    assert len(idx) == 2
    idx.rebuild()
    assert len(idx) == 0


def test_empty_query_returns_no_results(tmp_path):
    idx = _mkindex(tmp_path)
    idx.upsert("f1", "subject: delhivery")
    assert idx.search("   ", top_k=10) == []
