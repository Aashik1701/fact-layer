"""
Rebuild data/store.json from FIVE of the six starter documents, holding the
IMF Article IV excerpt back so it can be ingested live through the UI
(POST /ingest) during the demo — the sixth document's LLM responses are
already in the committed cache/llm/ (from the milestone 3 run), so the live
ingest runs the full parse -> triage -> extract -> verify -> resolve ->
cluster -> gate -> adjudicate path entirely offline and deterministically.

This script is the reproducible alternative to hand-editing data/store.json:
run it and the demo state is exactly reproducible by a grader, every time.

Usage:
    LLM_MODE=replay python3 scripts/build_demo_store.py
"""

import glob
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.resolve import write_resolution_log  # noqa: E402
from fact_layer.store import Store, _STORE_PATH  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HELD_BACK = "03-imf-india-2025-article-iv-excerpt.pdf"

# data/rejected_facts.jsonl is the FULL 6-document corpus's extraction-
# failure deliverable (project specification section 3.1) — a standalone artifact,
# already verified at its true single-run count. Rebuilding only 5 of the 6
# documents here must not touch it (it would just show a partial, smaller
# count that isn't the number this repo reports); rejections from this build
# go to a throwaway path instead. IMF's own rejections still land in the
# real file when it's ingested live via POST /ingest, same as any real
# document a grader uploads.
_THROWAWAY_REJECTED_PATH = os.path.join(tempfile.mkdtemp(prefix="fact_layer_demo_build_"), "rejected_facts.jsonl")


def main() -> None:
    all_paths = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
    demo_paths = [p for p in all_paths if os.path.basename(p) != HELD_BACK]

    if len(demo_paths) != 5:
        raise SystemExit(
            f"expected 5 documents after excluding {HELD_BACK!r}, got {len(demo_paths)} "
            f"from {[os.path.basename(p) for p in all_paths]} — check the starter-datasets layout."
        )

    print(f"Building demo store from {len(demo_paths)} documents (holding back {HELD_BACK} for live ingest):")
    for p in demo_paths:
        print(f"  - {os.path.basename(p)}")
    print()

    store = Store()
    for path in demo_paths:
        result = store.ingest(path, rejected_path=_THROWAWAY_REJECTED_PATH)
        if result.skipped_reason:
            print(f">>> {result.filename}: skipped ({result.skipped_reason})")
            continue
        print(f">>> {result.filename}: {len(result.new_facts)} facts, "
              f"{len(result.new_relations)} new relations, "
              f"{len(result.touched_clusters)} clusters touched")

    store.save(_STORE_PATH)
    write_resolution_log(store.resolver)

    summary = store.canonical_summary()
    print()
    print("=" * 70)
    print("DEMO STORE SUMMARY (5 documents)")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print()
    print(f"data/store.json and data/resolution_log.json written. "
          f"{HELD_BACK} is NOT in this store — ingest it live via POST /ingest.")


if __name__ == "__main__":
    main()
