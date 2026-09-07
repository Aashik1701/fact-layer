"""
Storage contract: the one place the fact/relation/document graph touches
bytes on disk. Everything else (Store's in-memory dedup/clustering/adjudication,
resolve.py's canonicalization, api.py's HTTP shaping) depends on this
contract, never on JSON files directly.

    FactStore (abstract)
        |
        +-- JsonFactStore   -- current: local/demo, zero external dependency
        +-- PostgresFactStore -- future: skeleton only, not wired up (see below)

Why this shape, not a bigger repository framework
--------------------------------------------------
The real write pattern in this codebase is: mutate Store's in-memory
facts/clusters/relations dicts across many ingest() calls, then flush ONE
snapshot at a time. Nothing here ever persists a single fact or relation in
isolation. So the contract's core is `load()`/`save()` over a whole
`StoreSnapshot`, not save_fact()/save_relation() per-entity methods that
nothing in the pipeline would actually call. The two extra read methods
(`list_rejected_facts`, `read_resolution_summary`) exist because api.py's
/stats and /rejected-facts endpoints used to `open()` those files directly —
that's the literal "core domain contains open(data/store.json)" problem this
module fixes.

What is deliberately NOT here
------------------------------
- The parsed-PDF cache (parse.py, cache/parsed/) and the LLM replay cache
  (llm.py, cache/llm/) are re-derivable performance caches, not domain data.
  Routing them through FactStore would conflate "speed up a deterministic
  recomputation" with "persist the knowledge graph" — different lifetimes,
  different failure modes (losing a cache costs time; losing the store loses
  the graph).
- extract.py's real-time rejected-fact append (`_append_rejected`, called
  once per rejected candidate, inline inside extraction) is left untouched.
  It already takes an injectable `path` parameter, which is enough for tests
  to redirect it; rerouting the actual per-row *write* through this
  contract as well would mean the extraction hot path taking a FactStore
  dependency for a single `open(path, "a")` call, i.e. more indirection for
  no behavior change. Only the READ side (api.py aggregating the file back
  into JSON) is unified here.
- resolve.py's write_resolution_log() builds its dict FROM live Resolver
  state (decisions, tiers, llm_calls_used) — that computation is domain
  logic, not persistence, so it stays in resolve.py. Only the path it writes
  to, and reading it back, go through this module.

Transactions (Phase 6) and concurrency (Phase 7) are discussed in the
README's "Persistence" section, not faked here: JsonFactStore's save() is
atomic at the file level (temp file + os.replace, same pattern used
elsewhere in this codebase for the LLM/parse caches) but a single JSON file
cannot give you the specific atomicity the pipeline would actually cash in
on (e.g. "either both the new facts AND the new relations they produced land,
or neither does" is not something a bare `open()` can express) — the file
write itself is all-or-nothing, but there is no multi-document transaction
underneath it, so this module claims exactly one guarantee (a save() call
either fully succeeds or leaves the previous file untouched) and no more.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from .extract import _REJECTED_PATH as _DEFAULT_REJECTED_PATH
from .models import (
    Evidence,
    Fact,
    Modality,
    Period,
    PeriodKind,
    Qualifiers,
    Quantity,
    Relation,
    RelationType,
    Scope,
    ValueKind,
)
from .resolve import _RESOLUTION_LOG_PATH as _DEFAULT_RESOLUTION_LOG_PATH

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")
_STORE_PATH = os.path.join(_DATA_DIR, "store.json")


@dataclass
class StoreSnapshot:
    """The whole persisted state of a Store, as one value. Mirrors exactly
    the five fields Store.save()/Store.load() have always serialized —
    nothing added, nothing renamed, so the on-disk JSON shape written by
    JsonFactStore is byte-for-byte what Store.save() wrote before this
    refactor."""

    facts: dict[str, Fact] = field(default_factory=dict)
    extra_evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    clusters: dict[str, list[str]] = field(default_factory=dict)
    ingested_docs: dict[str, str] = field(default_factory=dict)


class FactStore(ABC):
    """Everything Store and api.py need from persistence. A future
    PostgresFactStore implements the same five methods; nothing in
    domain code (Store, resolve.py, adjudicate.py, extract.py's actual
    extraction logic) would need to change to use it — see
    PostgresFactStore below for exactly what's missing to wire one up for
    real."""

    @abstractmethod
    def load(self) -> StoreSnapshot:
        """Full current state. Returns an empty StoreSnapshot if nothing
        has been persisted yet (matches Store.load()'s prior behavior of
        returning a fresh Store when the file doesn't exist)."""

    @abstractmethod
    def save(self, snapshot: StoreSnapshot) -> None:
        """Persist the full current state, replacing whatever was there.
        Must be atomic at the file/row level: a failed save leaves the
        previously persisted snapshot intact rather than a half-written one."""

    @abstractmethod
    def list_rejected_facts(self) -> list[dict]:
        """Every rejected-fact record persisted so far, in append order.
        Filtering by reason and pagination are API concerns (api.py), not
        storage concerns — this returns the full list every time, the same
        way the file-backed version always did."""

    @abstractmethod
    def read_resolution_summary(self) -> Optional[dict]:
        """The resolver decision-log summary (by_tier counts, llm_calls_used,
        canonical_measures), or None if nothing has been written yet."""


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _evidence_to_dict(e: Evidence) -> dict:
    return {
        "doc_id": e.doc_id, "page": e.page, "char_start": e.char_start, "char_end": e.char_end,
        "verbatim_quote": e.verbatim_quote,
        "bbox": list(e.bbox) if e.bbox else None,
        "extractor": e.extractor, "verified": e.verified,
        "table_id": e.table_id, "row_index": e.row_index, "column_index": e.column_index,
        "cell_bbox": list(e.cell_bbox) if e.cell_bbox else None,
        "row_label": e.row_label, "column_header": e.column_header,
        "unit_context": e.unit_context,
    }


def _evidence_from_dict(d: dict) -> Evidence:
    return Evidence(
        doc_id=d["doc_id"], page=d["page"], char_start=d["char_start"], char_end=d["char_end"],
        verbatim_quote=d["verbatim_quote"],
        bbox=tuple(d["bbox"]) if d.get("bbox") else None,
        extractor=d.get("extractor", "llm"), verified=d.get("verified", False),
        table_id=d.get("table_id"), row_index=d.get("row_index"), column_index=d.get("column_index"),
        cell_bbox=tuple(d["cell_bbox"]) if d.get("cell_bbox") else None,
        row_label=d.get("row_label"), column_header=d.get("column_header"),
        unit_context=d.get("unit_context", ""),
    )


def _period_from_dict(d: Optional[dict]) -> Optional[Period]:
    if not d:
        return None
    from datetime import date as _date
    start = _date.fromisoformat(d["start"]) if d.get("start") else None
    end = _date.fromisoformat(d["end"]) if d.get("end") else None
    return Period(kind=PeriodKind(d["kind"]), start=start, end=end, label=d.get("label", ""))


def _qualifiers_from_dict(d: dict) -> Qualifiers:
    from datetime import date as _date
    as_of = _date.fromisoformat(d["as_of"]) if d.get("as_of") else None
    return Qualifiers(
        period=_period_from_dict(d.get("period")),
        as_of=as_of,
        scope=Scope(d.get("scope", Scope.UNKNOWN.value)),
        basis=d.get("basis"), segment=d.get("segment"), geography=d.get("geography"),
        issuer=d.get("issuer"), extra=d.get("extra", {}),
    )


def _fact_from_dict(d: dict) -> Fact:
    value_kind = ValueKind(d["value_kind"])
    raw_value = d["value"]
    if value_kind == ValueKind.QUANTITY and isinstance(raw_value, dict):
        value: Any = Quantity(
            value=Decimal(raw_value["value"]), unit=raw_value.get("unit", "count"),
            currency=raw_value.get("currency"), sig_figs=raw_value.get("sig_figs", 15),
            raw=raw_value.get("raw", ""),
        )
    elif value_kind == ValueKind.DATE and isinstance(raw_value, str):
        from datetime import date as _date
        value = _date.fromisoformat(raw_value)
    else:
        value = raw_value

    evidence = _evidence_from_dict(d["evidence"]) if d.get("evidence") else None

    return Fact(
        subject=d["subject"], measure=d["measure"], value_kind=value_kind, value=value,
        qualifiers=_qualifiers_from_dict(d.get("qualifiers", {})),
        modality=Modality(d.get("modality", Modality.ASSERTED.value)),
        evidence=evidence, confidence=d.get("confidence", 1.0),
        subject_raw=d.get("subject_raw", ""), measure_raw=d.get("measure_raw", ""),
        value_verification=d.get("value_verification", ""),
        value_verification_reason=d.get("value_verification_reason", ""),
        fact_id=d.get("fact_id", ""),
    )


def _relation_to_dict(r: Relation) -> dict:
    return {
        "source_fact_id": r.source_fact_id, "target_fact_id": r.target_fact_id,
        "relation": r.relation.value, "confidence": r.confidence,
        "reason_code": r.reason_code, "explanation": r.explanation,
        "qualifier_diff": _json_safe(r.qualifier_diff), "decided_by": r.decided_by,
    }


def _relation_from_dict(d: dict) -> Relation:
    return Relation(
        source_fact_id=d["source_fact_id"], target_fact_id=d["target_fact_id"],
        relation=RelationType(d["relation"]), confidence=d["confidence"],
        reason_code=d["reason_code"], explanation=d["explanation"],
        qualifier_diff=d.get("qualifier_diff", {}), decided_by=d.get("decided_by", "rule"),
    )


class JsonFactStore(FactStore):
    """Current implementation: one JSON file for the fact/relation graph
    (identical on-disk shape to the pre-refactor Store.save()/load()), plus
    read access to the two adjacent JSONL/JSON diagnostic files that already
    existed (rejected_facts.jsonl, resolution_log.json).

    Why JSON, not a database, for this assignment: it is simple, fully
    reproducible from `scripts/build_demo_store.py`, needs zero external
    services for a reviewer to run, and every existing consumer already
    round-trips through it. It is NOT horizontally scalable and does not
    provide multi-writer transactional guarantees — see the module
    docstring and the README's Persistence section for what that means in
    practice and what a PostgresFactStore would need to add.
    """

    def __init__(
        self,
        store_path: str = _STORE_PATH,
        rejected_path: str = _DEFAULT_REJECTED_PATH,
        resolution_log_path: str = _DEFAULT_RESOLUTION_LOG_PATH,
    ) -> None:
        self.store_path = store_path
        self.rejected_path = rejected_path
        self.resolution_log_path = resolution_log_path

    # ---- fact/relation/document graph --------------------------------

    def load(self) -> StoreSnapshot:
        if not os.path.exists(self.store_path):
            return StoreSnapshot()
        with open(self.store_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        facts = {fid: _fact_from_dict(fd) for fid, fd in data.get("facts", {}).items()}
        extra_evidence = {
            fid: [_evidence_from_dict(s) for s in spans]
            for fid, spans in data.get("extra_evidence", {}).items()
        }
        relations = [_relation_from_dict(rd) for rd in data.get("relations", [])]
        return StoreSnapshot(
            facts=facts, extra_evidence=extra_evidence, relations=relations,
            clusters=data.get("clusters", {}), ingested_docs=data.get("ingested_docs", {}),
        )

    def save(self, snapshot: StoreSnapshot) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        data = {
            "facts": {fid: _json_safe(f.to_dict()) for fid, f in snapshot.facts.items()},
            "extra_evidence": {
                fid: [_evidence_to_dict(e) for e in spans]
                for fid, spans in snapshot.extra_evidence.items()
            },
            "relations": [_relation_to_dict(r) for r in snapshot.relations],
            "clusters": snapshot.clusters,
            "ingested_docs": snapshot.ingested_docs,
        }
        tmp = f"{self.store_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.store_path)

    # ---- diagnostic logs (read side only — see module docstring) ------

    def list_rejected_facts(self) -> list[dict]:
        rows: list[dict] = []
        if not os.path.exists(self.rejected_path):
            return rows
        with open(self.rejected_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows

    def read_resolution_summary(self) -> Optional[dict]:
        if not os.path.exists(self.resolution_log_path):
            return None
        with open(self.resolution_log_path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("summary")


def backend_from_env(**overrides: str) -> FactStore:
    """The one configuration knob this module introduces: STORAGE_BACKEND
    selects which FactStore api.py's module-level STORE (or any other real
    entry point) constructs. Defaults to "json" — the only backend that
    actually works locally, with zero external services or credentials.
    `**overrides` are passed straight through to JsonFactStore's
    constructor (e.g. store_path=...) so callers can still redirect
    individual paths exactly as before.

    "postgres" is accepted as a value (the seam is real, not aspirational)
    but raises immediately with a clear message rather than silently
    falling back to JSON or pretending to connect to a database — see
    PostgresFactStore's docstring for what is actually missing."""
    backend = os.environ.get("STORAGE_BACKEND", "json").strip().lower()
    if backend == "json":
        return JsonFactStore(**overrides)
    if backend == "postgres":
        raise NotImplementedError(
            "STORAGE_BACKEND=postgres is not implemented — PostgresFactStore "
            "is a documented skeleton, not a working backend (see its class "
            "docstring in fact_layer/storage.py for what finishing it would "
            "require). Use STORAGE_BACKEND=json (the default) for local "
            "development and for this assignment."
        )
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r} — supported: 'json'")


class PostgresFactStore(FactStore):
    """Skeleton only — NOT wired up, NOT importable-and-usable without
    finishing it, and NOT required for local development or the test suite
    (nothing constructs this class today; STORAGE_BACKEND=json is the only
    functional option). It exists to make the "persistence is an
    implementation detail" claim concrete rather than aspirational: this is
    the shape a real cloud backend would take, using the exact same
    StoreSnapshot contract Store and api.py already depend on.

    What finishing this would actually require (deliberately not done here
    per the brief — this is a skeleton, not a justified production need):
      - A `facts` table (fact_id PK, subject, measure, value_kind, value
        JSONB, qualifiers JSONB, evidence JSONB, ...), a `relations` table
        (source_fact_id, target_fact_id, relation, ...), a `documents`
        table (doc_id, filename), a `rejected_facts` table, and a
        `resolution_log` table (or a single JSONB summary row) — five
        tables mirroring StoreSnapshot's five fields plus the two
        diagnostic logs.
      - `load()` becomes five SELECTs assembled back into a StoreSnapshot;
        `save()` becomes a single transaction (BEGIN; upsert facts; upsert
        relations; upsert clusters/ingested_docs; COMMIT) — this is
        precisely the multi-table atomicity JSON cannot give you (see the
        module docstring's Transactions note), and the actual motivating
        reason to ever make this move for real.
      - A connection pool (e.g. psycopg2/asyncpg) — deliberately not
        imported at module load time below, so importing fact_layer.storage
        never requires psycopg2 to be installed for JSON-only local use.
      - Concurrency then comes from the database's own row/transaction
        locking instead of the process-local threading.Lock api.py uses
        today (see README's Persistence/Concurrency section) — that lock
        would be removed once a real multi-writer backend exists, not
        stacked underneath one.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _unavailable(self) -> None:
        raise NotImplementedError(
            "PostgresFactStore is a documented skeleton, not a working backend — "
            "see fact_layer/storage.py's PostgresFactStore docstring for what "
            "implementing it for real requires. Local/test usage must use "
            "JsonFactStore (the default)."
        )

    def load(self) -> StoreSnapshot:
        self._unavailable()

    def save(self, snapshot: StoreSnapshot) -> None:
        self._unavailable()

    def list_rejected_facts(self) -> list[dict]:
        self._unavailable()

    def read_resolution_summary(self) -> Optional[dict]:
        self._unavailable()
