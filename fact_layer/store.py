"""
JSON-backed fact store: within-document dedup, clustering, comparability/
adjudication, incremental ingest.

Pipeline stage: parse -> triage -> extract -> verify -> normalize -> resolve
-> DEDUPE -> CLUSTER -> GATE -> ADJUDICATE -> serve (this file covers the
capitalized stages).

Clustering uses Fact.cluster_key() on CANONICAL subject/measure (post
resolve.py), not raw strings — two facts about "Revenue from operations" and
"Total income from operations" only land in the same cluster because
resolve.py already canonicalized both to "revenue".

ingest() is incremental by construction: a brand-new cluster gets the full
(protected, reused) adjudicate_cluster() treatment since every pair in it is
new by definition, but an EXISTING cluster that gains new facts only computes
gate+adjudicate for pairs touching at least one new fact — old-vs-old pairs
in that cluster are never re-touched, not even to recompute and discard.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional, Union

from .adjudicate import adjudicate, adjudicate_cluster
from .extract import _REJECTED_PATH as _DEFAULT_REJECTED_PATH
from .extract import extract_document, extract_document_defaults
from .models import Evidence, Fact, Qualifiers, Quantity, Relation, RelationType
from .parse import parse_pdf
from .resolve import Resolver, resolve_facts, write_resolution_log
from .storage import FactStore, JsonFactStore, StoreSnapshot
# Re-exported for backward compatibility: these JSON (de)serialization
# helpers used to live in this module; they now live in storage.py
# alongside JsonFactStore, which is their only real caller, but
# tests/test_evidence_regions.py imports _evidence_to_dict/_evidence_from_dict
# from here directly, so the names stay available at their original path.
from .storage import (  # noqa: F401
    _evidence_from_dict,
    _evidence_to_dict,
    _fact_from_dict,
    _json_safe,
    _period_from_dict,
    _qualifiers_from_dict,
    _relation_from_dict,
    _relation_to_dict,
)
from .triage import _DEMO_BUDGETS, default_budget, select_pages

logger = logging.getLogger("fact_layer.store")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")
_STORE_PATH = os.path.join(_DATA_DIR, "store.json")


# --------------------------------------------------------------------------
# STEP 2 (specification section 11): within-document dedup
# --------------------------------------------------------------------------

def _qualifiers_key(q: Qualifiers) -> tuple:
    period_key = None
    if q.period:
        period_key = (
            q.period.kind.value,
            q.period.start.isoformat() if q.period.start else None,
            q.period.end.isoformat() if q.period.end else None,
        )
    as_of_key = q.as_of.isoformat() if q.as_of else None
    extra_key = tuple(sorted(q.extra.items())) if q.extra else ()
    return (period_key, as_of_key, q.scope.value, q.basis, q.segment, q.geography, q.issuer, extra_key)


def _value_key(fact: Fact) -> tuple:
    if isinstance(fact.value, Quantity):
        return ("quantity", str(fact.value.value), fact.value.unit, fact.value.currency)
    if isinstance(fact.value, date):
        return ("date", fact.value.isoformat())
    return ("other", str(fact.value))


def _dedup_key(fact: Fact) -> tuple:
    doc_id = fact.evidence.doc_id if fact.evidence else None
    return (doc_id, fact.subject, fact.measure, _qualifiers_key(fact.qualifiers), _value_key(fact))


def dedupe_within_document(facts: list[Fact]) -> tuple[list[Fact], dict[str, list[Evidence]]]:
    """Merge facts sharing doc_id + subject + measure + qualifiers +
    normalised value into one Fact, keeping the highest-confidence instance.

    models.py's Fact.evidence is a single Evidence, not a list — extending
    its cardinality is not an additive change (it would ripple through every
    existing consumer of fact.evidence.doc_id/.page/etc.) and models.py is
    protected. The additional evidence spans a merge collects therefore live
    in the returned `extra_evidence` map (fact_id -> the spans beyond the
    primary one) rather than on the Fact object itself; Store persists both.
    """
    groups: dict[tuple, list[Fact]] = {}
    for f in facts:
        groups.setdefault(_dedup_key(f), []).append(f)

    deduped: list[Fact] = []
    extra_evidence: dict[str, list[Evidence]] = {}
    for group in groups.values():
        primary = max(group, key=lambda f: f.confidence)
        if len(group) > 1:
            primary.confidence = max(f.confidence for f in group)   # specification 11: take max confidence
            others = [f.evidence for f in group if f is not primary and f.evidence]
            if others:
                extra_evidence[primary.fact_id] = others
        deduped.append(primary)
    return deduped, extra_evidence


# --------------------------------------------------------------------------
# STEP 3: incremental relation computation
# --------------------------------------------------------------------------

def _drop_same_document_corroboration(relations: list[Relation], facts_by_id: dict[str, Fact]) -> list[Relation]:
    """Specification section 11: 'Corroboration is only meaningful across
    doc_id boundaries — weight same-document agreement at zero.'
    adjudicate_cluster() (reused as-is, protected) only skips exact
    same-PAGE repeats; dedup already collapses most same-document identical
    claims before this stage, but two facts from the same document with
    slightly different qualifiers can still land in a cluster together and
    resolve to CORROBORATES. This closes that gap without touching
    adjudicate.py — it filters its output, it doesn't change its logic."""
    kept = []
    for rel in relations:
        if rel.relation == RelationType.CORROBORATES:
            fa = facts_by_id.get(rel.source_fact_id)
            fb = facts_by_id.get(rel.target_fact_id)
            if fa and fb and fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id:
                continue
        kept.append(rel)
    return kept


def _incremental_pairwise_relations(new_facts: list[Fact], existing_facts: list[Fact]) -> list[Relation]:
    """Same pairing rules as adjudicate_cluster() (same-page skip, UNRELATED
    dropped) but restricted to pairs touching at least one new fact — this
    is what makes growing an existing cluster incremental: old-vs-old pairs
    are never recomputed."""
    pairs = [(new_facts[i], new_facts[j]) for i in range(len(new_facts)) for j in range(i + 1, len(new_facts))]
    pairs += [(nf, ef) for nf in new_facts for ef in existing_facts]

    out: list[Relation] = []
    for fa, fb in pairs:
        if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                and fa.evidence.page == fb.evidence.page:
            continue
        rel = adjudicate(fa, fb)
        if rel.relation != RelationType.UNRELATED:
            out.append(rel)
    return out


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    facts_extracted: int = 0
    facts_verified: int = 0
    new_facts: list[Fact] = field(default_factory=list)
    new_relations: list[Relation] = field(default_factory=list)
    touched_clusters: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    pages: int = 0                 # api.py STEP 1: total pages in the source PDF
    pages_selected: int = 0        # pages the triage budget actually sent to the LLM
    facts_rejected: int = 0        # proposed - verified; span-verification/parse failures


class Store:
    """JSON-backed, in-memory fact store. Clusters by canonical
    (subject, measure) via Fact.cluster_key(); relations are computed
    incrementally as documents are ingested, not recomputed from scratch."""

    def __init__(self, backend: Optional[FactStore] = None) -> None:
        self.facts: dict[str, Fact] = {}
        self.extra_evidence: dict[str, list[Evidence]] = {}
        self.relations: list[Relation] = []
        self.clusters: dict[str, list[str]] = {}
        self.resolver = Resolver()
        self.ingested_docs: dict[str, str] = {}   # doc_id -> filename
        # `backend` is the FactStore this Store was loaded from (or defaults
        # to a plain JsonFactStore) — api.py uses it for the two read-only
        # diagnostic queries (list_rejected_facts, read_resolution_summary)
        # that used to open() data/rejected_facts.jsonl / resolution_log.json
        # directly. It is independent of whatever path save()/load() are
        # called with (see their docstrings) — in real usage both point at
        # the same files, so this only matters for tests that redirect paths.
        self.backend: FactStore = backend or JsonFactStore()

    # ---- ingest -----------------------------------------------------------

    def ingest(self, path: str, budget: Optional[int] = None,
               rejected_path: Optional[str] = None,
               on_stage: Optional[Callable[[str], None]] = None) -> IngestResult:
        """`rejected_path` defaults to extract.py's real data/rejected_facts.jsonl
        (unchanged behaviour for real usage — api.py, run_full_ingest()). It
        exists as a parameter so tests that ingest the real corpus can redirect
        it to a temp file instead of appending to the actual graded deliverable
        every time the suite runs.

        `on_stage` (optional, additive — every existing caller passes
        nothing and is unaffected) is invoked at each real, observable
        boundary inside this method: "parsing", "extracting", "resolving",
        "adjudicating". There is deliberately no separate "verifying"
        callback: span verification and deterministic value verification
        happen fact-by-fact, inline inside extract_document() as each raw
        candidate is produced, not as a discrete pass afterward — reporting
        a "verifying" stage transition here would assert a boundary that
        doesn't actually exist in this architecture. Callers that want a job
        model with a "storing" stage add it themselves around the JSON
        persistence step (Store.save()), which happens outside this method.
        """
        def _stage(name: str) -> None:
            if on_stage is not None:
                on_stage(name)

        _stage("parsing")
        doc = parse_pdf(path)
        if doc.error:
            return IngestResult(doc_id=doc.doc_id, filename=doc.filename, skipped_reason=f"parse error: {doc.error}")
        if doc.doc_id in self.ingested_docs:
            return IngestResult(doc_id=doc.doc_id, filename=doc.filename,
                                skipped_reason="already ingested")

        filename = doc.filename
        _stage("extracting")
        defaults = extract_document_defaults(doc)
        eff_budget = budget if budget is not None else (_DEMO_BUDGETS.get(filename) or default_budget(doc.n_pages))
        selected = select_pages(doc, eff_budget)
        raw_facts, stats = extract_document(doc, selected, defaults,
                                             rejected_path=rejected_path or _DEFAULT_REJECTED_PATH)

        _stage("resolving")
        deduped, extra = dedupe_within_document(raw_facts)
        resolved, self.resolver = resolve_facts(deduped, self.resolver)

        new_fact_ids: set[str] = set()
        touched_clusters: set[str] = set()
        for f in resolved:
            self.facts[f.fact_id] = f
            new_fact_ids.add(f.fact_id)
            if f.fact_id in extra:
                self.extra_evidence[f.fact_id] = extra[f.fact_id]
            ck = f.cluster_key()
            bucket = self.clusters.setdefault(ck, [])
            if f.fact_id not in bucket:
                bucket.append(f.fact_id)
            touched_clusters.add(ck)

        _stage("adjudicating")
        new_relations: list[Relation] = []
        for ck in touched_clusters:
            fact_ids = self.clusters[ck]
            if len(fact_ids) < 2:
                continue
            new_ids_here = [fid for fid in fact_ids if fid in new_fact_ids]
            existing_ids_here = [fid for fid in fact_ids if fid not in new_fact_ids]

            if not existing_ids_here:
                # Brand-new cluster: every pair is new by definition, so the
                # full reused adjudicate_cluster() IS the incremental set.
                cluster_facts = [self.facts[fid] for fid in fact_ids]
                relations = adjudicate_cluster(cluster_facts)
            else:
                # Existing cluster gaining facts: only new-vs-* pairs.
                new_facts_here = [self.facts[fid] for fid in new_ids_here]
                existing_facts_here = [self.facts[fid] for fid in existing_ids_here]
                relations = _incremental_pairwise_relations(new_facts_here, existing_facts_here)

            relations = _drop_same_document_corroboration(relations, self.facts)
            new_relations.extend(relations)

        self.relations.extend(new_relations)
        self.ingested_docs[doc.doc_id] = filename

        return IngestResult(
            doc_id=doc.doc_id, filename=filename,
            facts_extracted=stats.proposed, facts_verified=stats.verified,
            new_facts=resolved, new_relations=new_relations,
            touched_clusters=sorted(touched_clusters),
            pages=doc.n_pages, pages_selected=len(selected),
            facts_rejected=max(0, stats.proposed - stats.verified),
        )

    # ---- evidence access ----------------------------------------------

    def get_evidence(self, fact_id: str) -> list[Evidence]:
        """THE sanctioned way to read a fact's full evidence. Use this, never
        `fact.evidence` directly.

        `Fact.evidence` (models.py, protected) is a SINGLE Evidence, so a fact
        that deduplication merged from several spans can only carry one of
        them on the object itself; the rest live in `Store.extra_evidence`.
        Reading `fact.evidence` alone therefore silently under-reports — you
        get one span and no indication that others exist. This method merges
        both sources and de-duplicates, so callers (api.py, the frontend, any
        evidence panel) see every span backing the claim.

        Returns [] for an unknown fact_id. Order is stable: the fact's own
        primary span first, then merged spans in the order they were
        collected.
        """
        fact = self.facts.get(fact_id)
        spans: list[Evidence] = []
        seen: set[tuple] = set()

        def _add(e: Optional[Evidence]) -> None:
            if e is None:
                return
            key = (e.doc_id, e.page, e.char_start, e.char_end, e.verbatim_quote)
            if key in seen:
                return
            seen.add(key)
            spans.append(e)

        if fact is not None:
            _add(fact.evidence)
        for e in self.extra_evidence.get(fact_id, []):
            _add(e)
        return spans

    # ---- reporting ----------------------------------------------------

    def clusters_with_multiple_facts(self) -> dict[str, list[Fact]]:
        return {ck: [self.facts[fid] for fid in fids] for ck, fids in self.clusters.items() if len(fids) >= 2}

    def relation_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.relations:
            counts[r.relation.value] = counts.get(r.relation.value, 0) + 1
        return counts

    def canonical_summary(self) -> dict:
        return {
            "canonical_subjects": len({f.subject for f in self.facts.values()}),
            "canonical_measures": len({f.measure for f in self.facts.values()}),
            "canonical_issuers": len({f.qualifiers.issuer for f in self.facts.values() if f.qualifiers.issuer}),
            "total_facts": len(self.facts),
            "clusters_total": len(self.clusters),
            "clusters_with_2plus_facts": len(self.clusters_with_multiple_facts()),
            "total_relations": len(self.relations),
            "relation_counts": self.relation_counts(),
        }

    # ---- persistence --------------------------------------------------

    def save(self, path: Union[str, FactStore] = _STORE_PATH) -> None:
        """`path` may be a plain path string (wrapped in a JsonFactStore, the
        pre-refactor behavior — every existing caller does this) or a
        FactStore instance directly, e.g. a future PostgresFactStore. Either
        way the actual bytes-on-disk (or bytes-in-database) work happens
        inside that backend, not here."""
        backend = path if isinstance(path, FactStore) else JsonFactStore(path)
        backend.save(StoreSnapshot(
            facts=self.facts, extra_evidence=self.extra_evidence,
            relations=self.relations, clusters=self.clusters,
            ingested_docs=self.ingested_docs,
        ))

    @classmethod
    def load(cls, path: Union[str, FactStore] = _STORE_PATH) -> "Store":
        """Same `path`-or-backend flexibility as save() (see its docstring).
        The returned Store's `.backend` is the backend it was loaded from —
        api.py's diagnostic reads (list_rejected_facts, read_resolution_summary)
        go through it."""
        backend = path if isinstance(path, FactStore) else JsonFactStore(path)
        store = cls(backend=backend)
        snapshot = backend.load()
        store.facts = snapshot.facts
        store.extra_evidence = snapshot.extra_evidence
        store.relations = snapshot.relations
        store.clusters = snapshot.clusters
        store.ingested_docs = snapshot.ingested_docs
        return store


# --------------------------------------------------------------------------
# CLI: full 6-document ingest
# --------------------------------------------------------------------------

def run_full_ingest(pdf_paths: Optional[list[str]] = None) -> Store:
    import glob
    if pdf_paths is None:
        pdf_paths = sorted(glob.glob(os.path.join(_REPO_ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))

    store = Store()
    for path in pdf_paths:
        result = store.ingest(path)
        if result.skipped_reason:
            print(f">>> {result.filename}: skipped ({result.skipped_reason})")
            continue
        print(f">>> {result.filename}: {len(result.new_facts)} facts, "
              f"{len(result.new_relations)} new relations, "
              f"{len(result.touched_clusters)} clusters touched")

    store.save()
    write_resolution_log(store.resolver)
    return store


def main() -> None:
    store = run_full_ingest()
    summary = store.canonical_summary()
    print()
    print("=" * 70)
    print("STORE SUMMARY")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
