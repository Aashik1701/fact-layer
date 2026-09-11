"""
Storage backend benchmark: JSON vs SQLite, same workload, same corpus.

Extends the README's existing 100-synthetic-document / 12,000-fact stress
test (README §10 "Multi-Document Store Scaling") rather than replacing it —
reuses scripts/retrieval_benchmark.py's generate_corpus() verbatim (same
seed, same 100-doc/120-facts-per-doc shape), so this is the SAME workload
the original 0.03s -> 7.26s / 122MB numbers were measured against, not a
friendlier one invented to make SQLite look better.

The architectural question this measures is not "which backend is faster
in general" — it's the specific bottleneck README §10 already documents:
JsonFactStore.save() is O(total store size) because a JSON file has no way
to append, so adding one more document to an already-large store costs
roughly the same as writing the whole store from scratch. The headline
metric below is therefore "incremental_add_one_doc_s" at each scale, not
raw save() time — that IS the architectural problem being solved.

Run it directly:
    .venv/bin/python scripts/benchmark_storage.py

Writes data/storage_benchmark_report.json (a diagnostic report artifact,
same convention as data/retrieval_benchmark_report.json — not corpus data:
deleting and re-running this script never changes what the application
knows, only what this benchmark measured last).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))   # repo root, for `fact_layer`
sys.path.insert(0, _HERE)                     # scripts/, for retrieval_benchmark

from retrieval_benchmark import FACTS_PER_DOC, N_DOCS, SEED, generate_corpus  # noqa: E402

from fact_layer.sqlite_storage import SQLiteFactStore  # noqa: E402
from fact_layer.storage import JsonFactStore, StoreSnapshot  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPORT_PATH = os.path.join(_REPO_ROOT, "data", "storage_benchmark_report.json")

SCALES = [1, 10, 25, 50, 100]   # documents persisted so far, per Phase 11's requested points


def _facts_by_doc(facts: list) -> dict[str, list]:
    by_doc: dict[str, list] = {}
    for f in facts:
        by_doc.setdefault(f.evidence.doc_id, []).append(f)
    return by_doc


def _clusters_for(facts: dict) -> dict[str, list[str]]:
    clusters: dict[str, list[str]] = {}
    for fid, f in facts.items():
        clusters.setdefault(f.cluster_key(), []).append(fid)
    return clusters


def _file_size_mb(path: str) -> float:
    return round(os.path.getsize(path) / 1_000_000, 3) if os.path.exists(path) else 0.0


def _sqlite_size_mb(db_path: str) -> float:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            total += os.path.getsize(p)
    return round(total / 1_000_000, 3)


def run() -> dict:
    print(f"Generating synthetic corpus: {N_DOCS} docs x {FACTS_PER_DOC} facts/doc "
          f"(seed={SEED}, same as scripts/retrieval_benchmark.py)...")
    all_facts = generate_corpus()
    by_doc = _facts_by_doc(all_facts)
    doc_ids = sorted(by_doc.keys())
    assert len(doc_ids) == N_DOCS

    tmpdir = tempfile.mkdtemp(prefix="fkl_storage_benchmark_")
    json_path = os.path.join(tmpdir, "store.json")
    sqlite_path = os.path.join(tmpdir, "store.sqlite3")
    json_backend = JsonFactStore(store_path=json_path)
    sqlite_backend = SQLiteFactStore(sqlite_path)

    accumulated_facts: dict = {}
    accumulated_docs: dict = {}
    results = []

    for n in SCALES:
        target_docs = doc_ids[:n]
        new_docs = [d for d in target_docs if d not in accumulated_docs]

        # ---- JSON: full-snapshot save() at this scale (its only mode) ----
        for d in new_docs:
            for f in by_doc[d]:
                accumulated_facts[f.fact_id] = f
            accumulated_docs[d] = f"{d}.pdf"
        snapshot = StoreSnapshot(
            facts=accumulated_facts, extra_evidence={}, relations=[],
            clusters=_clusters_for(accumulated_facts), ingested_docs=accumulated_docs,
        )
        t0 = time.perf_counter()
        json_backend.save(snapshot)
        json_full_save_s = time.perf_counter() - t0

        # "adding one more document" cost, isolated: re-save with just one
        # additional doc's facts merged in, timing ONLY that last save().
        if n < N_DOCS:
            one_more_doc = doc_ids[n]
            facts_plus_one = dict(accumulated_facts)
            for f in by_doc[one_more_doc]:
                facts_plus_one[f.fact_id] = f
            docs_plus_one = dict(accumulated_docs)
            docs_plus_one[one_more_doc] = f"{one_more_doc}.pdf"
            snap_plus_one = StoreSnapshot(
                facts=facts_plus_one, extra_evidence={}, relations=[],
                clusters=_clusters_for(facts_plus_one), ingested_docs=docs_plus_one,
            )
            t0 = time.perf_counter()
            json_backend.save(snap_plus_one)   # this becomes the persisted state for scale n
            json_incremental_s = time.perf_counter() - t0
            # Roll the persisted JSON file back to exactly `n` docs (not n+1)
            # so the NEXT scale point's "new_docs" delta is computed correctly.
            json_backend.save(snapshot)
        else:
            json_incremental_s = None

        t0 = time.perf_counter()
        json_backend.load()
        json_load_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        json_backend.load()   # a representative read: full load, then filter — matches how api.py's STORE actually reads (everything is in-memory after one load())
        json_query_s = time.perf_counter() - t0

        # ---- SQLite: incremental save_new() per new document ----
        t0 = time.perf_counter()
        for d in new_docs:
            new_facts = {f.fact_id: f for f in by_doc[d]}
            sqlite_backend.save_new(
                new_doc=(d, f"{d}.pdf"), new_facts=new_facts,
                new_extra_evidence={}, new_relations=[],
            )
        sqlite_batch_s = time.perf_counter() - t0

        if n < N_DOCS:
            one_more_doc = doc_ids[n]
            new_facts = {f.fact_id: f for f in by_doc[one_more_doc]}
            t0 = time.perf_counter()
            sqlite_backend.save_new(
                new_doc=(one_more_doc, f"{one_more_doc}.pdf"), new_facts=new_facts,
                new_extra_evidence={}, new_relations=[],
            )
            sqlite_incremental_s = time.perf_counter() - t0
            # Undo: delete that document's rows so the next scale point
            # starts from exactly `n` persisted documents, matching JSON's
            # rollback above.
            with sqlite_backend._connect() as conn:
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (one_more_doc,))
                conn.execute("DELETE FROM facts WHERE doc_id = ?", (one_more_doc,))
                conn.commit()
        else:
            sqlite_incremental_s = None

        t0 = time.perf_counter()
        sqlite_backend.load()
        sqlite_load_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        sqlite_backend.load()
        sqlite_query_s = time.perf_counter() - t0

        row = {
            "docs_persisted": n,
            "facts_persisted": len(accumulated_facts),
            "json_full_save_s": round(json_full_save_s, 4),
            "json_incremental_add_one_doc_s": round(json_incremental_s, 4) if json_incremental_s is not None else None,
            "json_load_s": round(json_load_s, 4),
            "json_file_size_mb": _file_size_mb(json_path),
            "sqlite_batch_insert_s": round(sqlite_batch_s, 4),
            "sqlite_incremental_add_one_doc_s": round(sqlite_incremental_s, 4) if sqlite_incremental_s is not None else None,
            "sqlite_load_s": round(sqlite_load_s, 4),
            "sqlite_db_size_mb": _sqlite_size_mb(sqlite_path),
        }
        results.append(row)
        print(f"  n={n:>3} docs ({row['facts_persisted']:>5} facts): "
              f"json save={row['json_full_save_s']:.4f}s "
              f"(+1 doc={row['json_incremental_add_one_doc_s']}) "
              f"[{row['json_file_size_mb']}MB]  |  "
              f"sqlite +1 doc={row['sqlite_incremental_add_one_doc_s']} "
              f"[{row['sqlite_db_size_mb']}MB]")

    report = {
        "workload": {"n_docs": N_DOCS, "facts_per_doc": FACTS_PER_DOC, "seed": SEED,
                    "total_facts": len(all_facts)},
        "scales": results,
        "note": (
            "json_incremental_add_one_doc_s and sqlite_incremental_add_one_doc_s are "
            "the same operation on each backend: persist ONE additional document on "
            "top of an already-persisted store of size docs_persisted. This is the "
            "metric that demonstrates (or doesn't) whether a backend avoids rewriting "
            "the whole historical knowledge layer per new document."
        ),
    }
    os.makedirs(os.path.dirname(_REPORT_PATH), exist_ok=True)
    with open(_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {_REPORT_PATH}")
    return report


if __name__ == "__main__":
    run()
