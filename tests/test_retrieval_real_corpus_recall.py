"""
Section 13's explicit requirement: use the EXISTING known relation pairs
(the real, committed `data/store.json` corpus — 552 facts, 14 relations,
the 5-document pre-seeded scope, same numbers README §9's "Store &
Relation Inventory" documents) as a
retrieval evaluation set, not only the synthetic benchmark corpus
(`scripts/retrieval_benchmark.py`, README §10a). For every known relation,
verify the partner fact appears in the retrieved top-K and report the
actual measured Recall@10/25/50/100 for each channel.

Module-scoped fixtures: indexing 552 facts once and sharing it across
these tests (rather than once per test) keeps this fast — the embedding
cache absorbs any repeat cost regardless.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.retrieval.config import RetrievalConfig
from fact_layer.retrieval.index import RetrievalIndex
from fact_layer.retrieval.metrics import recall_at_k
from fact_layer.store import Store

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "embeddings", "models")


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted in EMBEDDING_MODE=replay")


@pytest.fixture(autouse=True)
def _no_http(monkeypatch):
    import requests.adapters
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _no_network)
    yield


@pytest.fixture(scope="module")
def real_store() -> Store:
    store = Store.load()
    assert len(store.relations) > 0, "data/store.json must hold the real ingested corpus for this test"
    return store


@pytest.fixture(scope="module")
def real_index(real_store, tmp_path_factory) -> RetrievalIndex:
    cfg = RetrievalConfig(
        enabled=True, top_k=100, index_dir=str(tmp_path_factory.mktemp("real_corpus_idx")),
        embedding_model_cache_dir=_MODEL_CACHE_DIR, embedding_mode="replay",
    )
    idx = RetrievalIndex(cfg)
    idx.upsert_facts(real_store.facts.values())
    yield idx
    idx.close()


@pytest.fixture(scope="module")
def known_pairs(real_store) -> list[tuple[str, str]]:
    return [(r.source_fact_id, r.target_fact_id) for r in real_store.relations]


def test_known_relation_pairs_match_readme_documented_count(known_pairs):
    # README §9 documents 14 relations over the committed 5-document
    # pre-seeded corpus (552 facts). This is a canary, not a hard
    # requirement of this test file: if the committed store.json is ever
    # regenerated with a different corpus, update this number deliberately
    # rather than let it silently drift.
    #
    # This was 15 until the comparability-gate fix for
    # PeriodRelation.UNKNOWN (Verdict.AMBIGUOUS instead of falling through
    # to COMPARABLE — see tests/test_comparability_ambiguous.py and README
    # §8 Case 2 / §9): one stale relation (reason_code
    # value_mismatch_period_unverified, a CONTRADICTS pair the old,
    # buggy gate produced from an unverified-period pair) can no longer be
    # produced by current code, and the committed store.json was rebuilt
    # accordingly. Verified via two independent, isolated, deterministic
    # rebuilds of the 5-document corpus that removed exactly this one
    # relation and nothing else (identical fact_id sets to the prior
    # commit).
    assert len(known_pairs) == 14


@pytest.mark.parametrize("channel", ["lexical", "semantic", "hybrid"])
def test_real_corpus_recall_at_k(real_store, real_index, known_pairs, channel):
    recall = recall_at_k(known_pairs, real_store.facts, real_index, [10, 25, 50, 100], channel=channel)
    # Measured result on the real corpus (see README §10a): every channel
    # reaches 1.0 by K=25, hybrid/semantic already at K=10. This asserts a
    # regression floor, not the exact measured ceiling — a future change
    # that pushes recall below this on the REAL corpus (as opposed to the
    # synthetic large-cluster stress benchmark, where incomplete recall is
    # an accepted, documented trade-off) is worth knowing about.
    assert recall[25] == 1.0, f"{channel} channel lost known-relation recall on the real corpus: {recall}"


def test_real_corpus_all_known_relations_recovered_via_full_pipeline(real_store, real_index, known_pairs):
    """End-to-end version of the recall check: not just 'is the partner in
    the top-K list', but 'does generate_candidate_pairs() + blocking
    actually keep this pair as a live candidate'."""
    from fact_layer.retrieval.integration import generate_candidate_pairs

    facts = list(real_store.facts.values())
    pairs = generate_candidate_pairs(facts, real_store.facts, real_index)
    surfaced = {tuple(sorted((a.fact_id, b.fact_id))) for a, b in pairs}

    recovered = sum(1 for a, b in known_pairs if tuple(sorted((a, b))) in surfaced)
    assert recovered == len(known_pairs), (
        f"only {recovered}/{len(known_pairs)} known relation pairs were surfaced as "
        f"candidates (post-blocking) on the real corpus"
    )
