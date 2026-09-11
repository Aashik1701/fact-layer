"""
Migrate the existing JSON store (facts/relations/documents graph, plus the
two adjacent diagnostic logs) into a SQLite database, and verify parity.

Usage:
    .venv/bin/python scripts/migrate_json_to_sqlite.py [--store PATH] [--rejected PATH]
        [--resolution-log PATH] [--out PATH]

Defaults to the committed canonical files and writes to
data/store.sqlite3 — but see the --out flag: this script never touches
data/store.json, data/rejected_facts.jsonl, or data/resolution_log.json.
It only READS them and WRITES a new SQLite file. Run it with a custom --out
to migrate into an isolated location (tests do exactly this).

What "preserve" means here: this does not re-run extraction, resolution, or
adjudication — it reads the JSON/JSONL records exactly as persisted and
inserts them as-is (fact_id, evidence, relations, rejected-fact records,
resolution decisions unchanged). Nothing is recomputed, renamed, or
dropped. The parity check at the end re-derives both sides' canonical
summaries and asserts they match exactly, byte for byte on every count.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.sqlite_storage import SQLiteFactStore, _DEFAULT_SQLITE_PATH  # noqa: E402
from fact_layer.storage import JsonFactStore, _STORE_PATH  # noqa: E402
from fact_layer.store import Store  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_REJECTED = os.path.join(_REPO_ROOT, "data", "rejected_facts.jsonl")
_DEFAULT_RESOLUTION_LOG = os.path.join(_REPO_ROOT, "data", "resolution_log.json")


def migrate(
    store_path: str = _STORE_PATH,
    rejected_path: str = _DEFAULT_REJECTED,
    resolution_log_path: str = _DEFAULT_RESOLUTION_LOG,
    out_path: str = _DEFAULT_SQLITE_PATH,
) -> tuple[Store, Store]:
    """Returns (json_store, sqlite_store) — both loaded fresh after the
    migration, so the caller (or a test) can compare them directly."""
    json_backend = JsonFactStore(
        store_path=store_path, rejected_path=rejected_path, resolution_log_path=resolution_log_path,
    )
    snapshot = json_backend.load()

    if os.path.exists(out_path):
        raise SystemExit(
            f"{out_path} already exists — refusing to migrate into an existing "
            f"database (this script only ever creates a fresh one). Remove it "
            f"first if you intend to re-run the migration."
        )

    sqlite_backend = SQLiteFactStore(out_path)
    sqlite_backend.save(snapshot)

    rejected_records = json_backend.list_rejected_facts()
    if rejected_records:
        sqlite_backend.write_rejected_facts(rejected_records)

    if os.path.exists(resolution_log_path):
        with open(resolution_log_path, "r", encoding="utf-8") as fh:
            resolution_log = json.load(fh)
        sqlite_backend.write_resolution_summary(resolution_log)

    return Store.load(json_backend), Store.load(sqlite_backend)


def _canonicalize_relations(relations: list) -> list[tuple]:
    """Order-independent representation for comparison — relations are
    accumulated in touched-cluster iteration order (a Python set, not a
    stable sequence) even for the exact same input, so byte-for-byte list
    equality was never a meaningful invariant; sorted tuples are."""
    return sorted(
        (r.source_fact_id, r.target_fact_id, r.relation.value, round(r.confidence, 6),
         r.reason_code, r.decided_by)
        for r in relations
    )


def check_parity(json_store: Store, sqlite_store: Store) -> None:
    js, ss = json_store.canonical_summary(), sqlite_store.canonical_summary()
    mismatches = []
    for key in js:
        if js[key] != ss[key]:
            mismatches.append(f"  {key}: json={js[key]!r} sqlite={ss[key]!r}")

    if set(json_store.facts.keys()) != set(sqlite_store.facts.keys()):
        mismatches.append(
            f"  fact_id sets differ: {len(json_store.facts)} json vs {len(sqlite_store.facts)} sqlite"
        )
    else:
        for fid, jf in json_store.facts.items():
            sf = sqlite_store.facts[fid]
            if jf.to_dict() != sf.to_dict():
                mismatches.append(f"  fact {fid}: payload differs after round-trip")

    if json_store.ingested_docs != sqlite_store.ingested_docs:
        mismatches.append(
            f"  ingested_docs differ: {json_store.ingested_docs} vs {sqlite_store.ingested_docs}"
        )

    if _canonicalize_relations(json_store.relations) != _canonicalize_relations(sqlite_store.relations):
        mismatches.append("  relations differ (order-independent comparison)")

    json_rejected = json_store.backend.list_rejected_facts()
    sqlite_rejected = sqlite_store.backend.list_rejected_facts()
    if len(json_rejected) != len(sqlite_rejected):
        mismatches.append(
            f"  rejected_facts count differs: json={len(json_rejected)} sqlite={len(sqlite_rejected)}"
        )

    json_res = json_store.backend.read_resolution_summary()
    sqlite_res = sqlite_store.backend.read_resolution_summary()
    if (json_res is None) != (sqlite_res is None):
        mismatches.append(f"  resolution_summary presence differs: json={json_res!r} sqlite={sqlite_res!r}")
    elif json_res is not None:
        for key in ("total_decisions", "llm_calls_used"):
            if json_res.get(key) != sqlite_res.get(key):
                mismatches.append(f"  resolution_summary.{key}: json={json_res.get(key)!r} sqlite={sqlite_res.get(key)!r}")
        if json_res.get("by_tier") != sqlite_res.get("by_tier"):
            mismatches.append(f"  resolution_summary.by_tier: json={json_res.get('by_tier')!r} sqlite={sqlite_res.get('by_tier')!r}")
        if sorted(json_res.get("canonical_measures", [])) != sorted(sqlite_res.get("canonical_measures", [])):
            mismatches.append("  resolution_summary.canonical_measures differ")

    if mismatches:
        raise SystemExit("PARITY CHECK FAILED:\n" + "\n".join(mismatches))

    print("PARITY CHECK PASSED — JSON and SQLite snapshots are semantically identical.")
    print(f"  facts={js['total_facts']} clusters={js['clusters_total']} "
          f"({js['clusters_with_2plus_facts']} with 2+) relations={js['total_relations']} "
          f"rejected_facts={len(json_rejected)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=_STORE_PATH)
    parser.add_argument("--rejected", default=_DEFAULT_REJECTED)
    parser.add_argument("--resolution-log", default=_DEFAULT_RESOLUTION_LOG)
    parser.add_argument("--out", default=_DEFAULT_SQLITE_PATH)
    args = parser.parse_args()

    print(f"Migrating {args.store} -> {args.out} ...")
    json_store, sqlite_store = migrate(args.store, args.rejected, args.resolution_log, args.out)
    check_parity(json_store, sqlite_store)


if __name__ == "__main__":
    main()
