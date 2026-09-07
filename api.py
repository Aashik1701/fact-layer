"""
FastAPI layer over the fact_layer pipeline.

Milestone 5, STEP 1 (project specification build-order table). This file is deliberately
thin: it holds HTTP plumbing (routing, request parsing, JSON shaping) and
zero domain logic. Every decision about what a fact IS, whether two facts are
comparable, or how they relate was already made by fact_layer/{comparability,
adjudicate,resolve,store}.py — this module only serves what those modules
already computed, plus one exception: it calls the already-pure, side-effect
-free comparability.gate() again on read to expose the gate verdict on
GET /relations/{id}, since Relation (models.py, protected) does not persist
the Verdict enum on itself.

Evidence is always read through Store.get_evidence(), never fact.evidence
directly, per the project's binding evidence-access contract.

Startup loads the existing store from data/store.json rather than
re-ingesting, so the API is usable within a couple of seconds even though a
full 6-document ingest takes minutes.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from datetime import date
from typing import Optional

import pdfplumber
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from fact_layer import llm as _llm
from fact_layer.comparability import gate
from fact_layer.jobs import Job, JobStage, JobStatus, JobStore
from fact_layer.models import Fact, Qualifiers, Quantity, Relation
from fact_layer.parse import _doc_id
from fact_layer.resolve import write_resolution_log
from fact_layer.store import Store, _STORE_PATH

logger = logging.getLogger("fact_layer.api")

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_UPLOADS_DIR = os.path.join(_REPO_ROOT, "data", "uploads")
_PAGE_IMAGE_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "pages")
_DIST_DIR = os.path.join(_REPO_ROOT, "frontend", "dist")
_FRONTEND_DIR = _DIST_DIR if os.path.isdir(_DIST_DIR) else os.path.join(_REPO_ROOT, "frontend")
_RESOLUTION_LOG_PATH = os.path.join(_REPO_ROOT, "data", "resolution_log.json")
_REJECTED_PATH = os.path.join(_REPO_ROOT, "data", "rejected_facts.jsonl")
_PAGE_IMAGE_RESOLUTION = 110

# --------------------------------------------------------------------------
# Upload security (B1)
#
# PDF content — and the client-supplied filename that arrives alongside it —
# is untrusted input. Two independent hardenings, both enforced before any
# bytes are written to disk:
#   1. the on-disk path is built from a SANITIZED basename, never the raw
#      client filename, and is verified to still resolve inside
#      _UPLOADS_DIR before it is ever opened for writing (belt + suspenders:
#      the sanitizer alone already can't produce a traversal, the
#      containment check catches it anyway if that ever stops being true);
#   2. the body is read incrementally with a running size cap, so an
#      oversized upload is rejected without ever materializing the whole
#      thing in memory first.
# --------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB — generous for a text-native
                                        # financial-report PDF (this corpus's
                                        # largest real file is ~4.3 MB), well
                                        # short of what would indicate abuse
                                        # for a local/demo deployment.
_UPLOAD_READ_CHUNK = 1024 * 1024       # 1 MB per read() call
_PDF_MAGIC = b"%PDF-"
_UNSAFE_FILENAME_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_upload_filename(original: str) -> str:
    """Never let a client-supplied filename control where a file is written.
    Takes only the final path component (after normalising both '/' and
    Windows-style '\\' separators), strips any residual '..' tokens and
    unsafe characters, and guarantees a '.pdf' suffix. This alone defeats
    every traversal shape this project's threat model calls out
    ("../../evil.pdf", "/tmp/evil.pdf", "..\\..\\evil.pdf",
    "foo/../../evil.pdf") because only the last segment survives; the
    containment check in `_safe_upload_dest` is the independent second
    layer, not a substitute for this one."""
    base = (original or "upload").replace("\\", "/").split("/")[-1]
    base = base.replace("..", "")
    base = _UNSAFE_FILENAME_CHARS_RE.sub("_", base).strip("._") or "upload"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def _safe_upload_dest(original_filename: str) -> str:
    """Resolve a sanitized, collision-free destination path inside
    _UPLOADS_DIR, verifying containment before returning it. Raises
    HTTPException rather than ever handing back a path outside the
    upload directory."""
    os.makedirs(_UPLOADS_DIR, exist_ok=True)
    base = _sanitize_upload_filename(original_filename)
    dest = os.path.join(_UPLOADS_DIR, base)

    real_uploads = os.path.realpath(_UPLOADS_DIR)
    real_dest = os.path.realpath(dest)
    if os.path.commonpath([real_uploads, real_dest]) != real_uploads:
        # Should be unreachable given the sanitizer above; fail closed if
        # it ever isn't, rather than trust the computed path.
        raise HTTPException(400, "invalid filename")

    if os.path.exists(dest):
        # A different file with the same sanitized name — never silently
        # overwrite an existing upload; give this one a distinct name.
        stem, ext = os.path.splitext(base)
        dest = os.path.join(_UPLOADS_DIR, f"{stem}_{uuid.uuid4().hex[:8]}{ext}")
    return dest


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile incrementally, aborting as soon as the running
    total exceeds `max_bytes` — never calls file.read() with no size limit,
    so an oversized upload never gets fully materialized in memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                413, f"file exceeds maximum upload size of {max_bytes // (1024 * 1024)} MB"
            )
        chunks.append(chunk)
    return b"".join(chunks)

app = FastAPI(title="Fact Knowledge Layer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Loaded once at import time — the store already reflects every prior
# `python -m fact_layer.store` / POST /ingest run; startup must not re-ingest.
STORE = Store.load(_STORE_PATH)

# --------------------------------------------------------------------------
# Ingestion jobs (single-process, in-memory — see fact_layer/jobs.py and
# README's "Ingestion Job Model" section for the honest scope of what this
# does and doesn't guarantee across restarts / multiple instances).
#
# _INGEST_LOCK serializes actual pipeline processing: STORE is one shared,
# JSON-persisted, in-memory object, and FastAPI's BackgroundTasks runs sync
# callables in a worker thread, so two uploads landing close together could
# otherwise race on Store.facts/Store.clusters/Store.relations or interleave
# writes to data/store.json. Holding this lock for the whole of
# process_document() means jobs process one at a time — simple, correct,
# and honest about not providing any cross-process/distributed guarantee.
# --------------------------------------------------------------------------

JOBS = JobStore()
_INGEST_LOCK = threading.Lock()


def _sanitize_job_error(exc: Exception) -> str:
    """Never let an internal exception reach a client verbatim — no stack
    traces, no local filesystem paths, no provider response bodies beyond a
    short, already-user-facing prefix. LLMError/parse-error messages in this
    codebase are already written to be human-readable (see llm.py), so they
    are kept, just capped and stripped of the local repo path; anything
    else collapses to a generic message (the real exception is still
    logged server-side via logger.exception)."""
    msg = str(exc).replace(_REPO_ROOT, "<repo>")
    return msg if len(msg) <= 300 else msg[:300] + "..."


def process_document(job_id: str, dest: str, filename: str) -> None:
    """The actual ingestion pipeline, reusable and independently testable —
    called from a background task in production, and directly (synchronously)
    in tests that want to assert on pipeline behavior without going through
    HTTP at all. Never duplicates Store.ingest()'s logic: this function is
    orchestration (job bookkeeping, error sanitization, persistence) around
    one call to it, exactly as api.py's original inline /ingest body was,
    just extracted so it has a name and can run off the request thread.
    """
    with _INGEST_LOCK:
        try:
            calls_before = _llm._stats["calls"]
            t0 = time.time()
            result = STORE.ingest(
                dest, rejected_path=_REJECTED_PATH,
                on_stage=lambda s: JOBS.set_stage(job_id, JobStage(s)),
            )
        except _llm.LLMError as e:
            # A genuinely new document that LLM_MODE=replay has no cached
            # response for (or a live-mode failure). The file itself parsed
            # fine (it's a real, valid PDF); it stays in data/uploads/ so a
            # retry with live API access can pick it back up.
            JOBS.fail(job_id, f"extraction failed: {_sanitize_job_error(e)}")
            return
        except Exception:
            logger.exception("unexpected error processing job %s (file=%s)", job_id, filename)
            JOBS.fail(job_id, "internal processing error")
            return

        if result.skipped_reason and result.skipped_reason.startswith("parse error"):
            # Not a usable document — clean up rather than leave a dead file
            # behind (B1.4 cleanup behavior, preserved unchanged). Distinct
            # from the LLMError case above: this file never actually parsed.
            try:
                os.remove(dest)
            except OSError:
                logger.warning("failed to remove unparseable upload %s", dest)
            JOBS.fail(job_id, f"could not parse uploaded PDF: {result.skipped_reason}")
            return

        elapsed = time.time() - t0
        llm_calls_made = _llm._stats["calls"] - calls_before

        response = {
            "doc_id": result.doc_id,
            "filename": result.filename,
            "already_ingested": result.skipped_reason == "already ingested",
            "skipped_reason": result.skipped_reason,
            "counts": {
                "pages": result.pages,
                "pages_selected": result.pages_selected,
                "facts_proposed": result.facts_extracted,
                "facts_verified": result.facts_verified,
                "facts_rejected": result.facts_rejected,
            },
            "new_facts": [_fact_summary(f) for f in result.new_facts],
            "new_relations": [_relation_summary(r) for r in result.new_relations],
            "clusters_touched": len(result.touched_clusters),
            "llm_calls_made": llm_calls_made,
            "elapsed_seconds": round(elapsed, 3),
        }

        if result.skipped_reason is None:
            JOBS.set_stage(job_id, JobStage.STORING)
            STORE.save(_STORE_PATH)
            write_resolution_log(STORE.resolver, _RESOLUTION_LOG_PATH)

        JOBS.complete(job_id, result.doc_id, response,
                      already_ingested=(result.skipped_reason == "already ingested"))


# --------------------------------------------------------------------------
# doc_id -> source PDF path
#
# parse._doc_id() is a pure hash of an absolute path (not file content), so
# a doc_id can be resolved back to its file by hashing every known PDF's path
# and matching. This costs nothing (string hashing, no I/O beyond a glob) and
# needs no persisted mapping of its own — corpus PDFs live under
# starter-datasets/, uploaded ones under data/uploads/ (already gitignored).
# --------------------------------------------------------------------------

def _known_pdf_paths() -> list[str]:
    paths = glob.glob(os.path.join(_REPO_ROOT, "starter-datasets", "**", "*.pdf"), recursive=True)
    if os.path.isdir(_UPLOADS_DIR):
        paths += glob.glob(os.path.join(_UPLOADS_DIR, "*.pdf"))
    return paths


def _resolve_doc_path(doc_id: str) -> Optional[str]:
    for path in _known_pdf_paths():
        if _doc_id(path) == doc_id:
            return path
    # Fallback: if data/store.json was ingested on a machine with a different absolute path,
    # match against the filename recorded in STORE.ingested_docs
    expected_filename = STORE.ingested_docs.get(doc_id)
    if expected_filename:
        for path in _known_pdf_paths():
            if os.path.basename(path) == expected_filename:
                return path
    return None


def _find_doc_id_by_content(content_hash: str) -> Optional[str]:
    """doc_id (models.py/parse.py, protected) is a hash of the file's
    ABSOLUTE PATH, not its bytes — re-uploading a PDF that is byte-identical
    to an already-ingested one (the realistic 'oops, uploaded the same file
    twice' / 're-uploaded one of the 6 corpus PDFs' case) would otherwise get
    a fresh doc_id and be fully re-extracted as if it were new. Checking
    content hash against every already-ingested doc's source file (a handful
    of sha256 reads) catches this without touching parse.py's identity rule."""
    for doc_id in STORE.ingested_docs:
        path = _resolve_doc_path(doc_id)
        if path is None or not os.path.exists(path):
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() == content_hash:
            return doc_id
    return None


# --------------------------------------------------------------------------
# JSON shaping — plain dicts, no pydantic response models (thin layer, small
# surface; adding a parallel schema for every fact_layer dataclass would be
# the kind of abstraction the project specification (section 8) warns against).
# --------------------------------------------------------------------------

def _json_safe(v):
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def _qualifiers_out(q: Qualifiers) -> dict:
    period = None
    if q.period:
        period = {
            "kind": q.period.kind.value,
            "start": q.period.start.isoformat() if q.period.start else None,
            "end": q.period.end.isoformat() if q.period.end else None,
            "label": q.period.label,
        }
    return {
        "period": period,
        "as_of": q.as_of.isoformat() if q.as_of else None,
        "scope": q.scope.value,
        "basis": q.basis,
        "segment": q.segment,
        "geography": q.geography,
        "issuer": q.issuer,
        "extra": q.extra,
    }


def _value_out(fact: Fact) -> dict:
    if isinstance(fact.value, Quantity):
        return {
            "raw": fact.value.raw,
            "normalized": str(fact.value.value),
            "unit": fact.value.unit,
            "currency": fact.value.currency,
            "sig_figs": fact.value.sig_figs,
        }
    if isinstance(fact.value, date):
        return {"raw": fact.value.isoformat(), "normalized": fact.value.isoformat()}
    return {"raw": str(fact.value), "normalized": str(fact.value)}


_page_dims_cache: dict[tuple[str, int], Optional[tuple[float, float]]] = {}


def _page_dimensions(doc_id: str, page_no: int) -> Optional[tuple[float, float]]:
    """(width, height) in PDF points for one page — the frontend needs this
    to scale a bbox (also in PDF points) onto the /page-image PNG, which is
    rendered at a fixed DPI (_PAGE_IMAGE_RESOLUTION), not fixed pixel size.
    Cached per-process since the same evidence page is requested repeatedly."""
    key = (doc_id, page_no)
    if key in _page_dims_cache:
        return _page_dims_cache[key]
    path = _resolve_doc_path(doc_id)
    dims = None
    if path:
        try:
            with pdfplumber.open(path) as pdf:
                if 1 <= page_no <= len(pdf.pages):
                    pg = pdf.pages[page_no - 1]
                    dims = (pg.width, pg.height)
        except Exception:
            dims = None
    _page_dims_cache[key] = dims
    return dims


def _evidence_out(e) -> dict:
    dims = _page_dimensions(e.doc_id, e.page)
    return {
        "doc_id": e.doc_id, "page": e.page,
        "char_start": e.char_start, "char_end": e.char_end,
        "verbatim_quote": e.verbatim_quote,
        "bbox": list(e.bbox) if e.bbox else None,
        "page_width": dims[0] if dims else None,
        "page_height": dims[1] if dims else None,
        "page_image_resolution": _PAGE_IMAGE_RESOLUTION,
        "extractor": e.extractor, "verified": e.verified,
        # A8 — additive table-context fields; null when a fact's value
        # wasn't confidently attributed to exactly one table cell.
        "table_id": e.table_id,
        "row_index": e.row_index, "column_index": e.column_index,
        "cell_bbox": list(e.cell_bbox) if e.cell_bbox else None,
        "row_label": e.row_label, "column_header": e.column_header,
        "unit_context": e.unit_context or None,
    }


def _fact_summary(fact: Fact) -> dict:
    return {
        "fact_id": fact.fact_id,
        "subject": fact.subject, "subject_raw": fact.subject_raw,
        "measure": fact.measure, "measure_raw": fact.measure_raw,
        "value_kind": fact.value_kind.value,
        "value": _value_out(fact),
        "qualifiers": _qualifiers_out(fact.qualifiers),
        "modality": fact.modality.value,
        "confidence": fact.confidence,
        "doc_id": fact.evidence.doc_id if fact.evidence else None,
        "page": fact.evidence.page if fact.evidence else None,
        "evidence_count": len(STORE.get_evidence(fact.fact_id)),
        "value_verification": fact.value_verification or None,
        "value_verification_reason": fact.value_verification_reason or None,
    }


def _fact_full(fact: Fact) -> dict:
    out = _fact_summary(fact)
    out["evidence"] = [_evidence_out(e) for e in STORE.get_evidence(fact.fact_id)]
    return out


def _relation_id(rel: Relation) -> str:
    seed = f"{rel.source_fact_id}|{rel.target_fact_id}|{rel.relation.value}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


def _relation_index() -> dict[str, Relation]:
    return {_relation_id(r): r for r in STORE.relations}


def _relation_summary(rel: Relation) -> dict:
    a = STORE.facts.get(rel.source_fact_id)
    b = STORE.facts.get(rel.target_fact_id)
    return {
        "relation_id": _relation_id(rel),
        "source_fact_id": rel.source_fact_id,
        "target_fact_id": rel.target_fact_id,
        "source_summary": f"{a.subject}::{a.measure}" if a else None,
        "target_summary": f"{b.subject}::{b.measure}" if b else None,
        "relation": rel.relation.value,
        "confidence": rel.confidence,
        "reason_code": rel.reason_code,
        "decided_by": rel.decided_by,
    }


def _relation_full(rel: Relation) -> dict:
    a = STORE.facts.get(rel.source_fact_id)
    b = STORE.facts.get(rel.target_fact_id)
    out = _relation_summary(rel)
    out["explanation"] = rel.explanation
    out["qualifier_diff"] = _json_safe(rel.qualifier_diff)
    out["source_fact"] = _fact_full(a) if a else None
    out["target_fact"] = _fact_full(b) if b else None
    if a and b:
        g = gate(a, b)
        out["gate"] = {
            "verdict": g.verdict.value,
            "reason_code": g.reason_code,
            "explanation": g.explanation,
            "period_relation": g.period_relation.value,
            "cross_issuer": g.cross_issuer,
            "qualifier_diff": _json_safe(g.qualifier_diff),
        }
    return out


# --------------------------------------------------------------------------
# POST /ingest
# --------------------------------------------------------------------------

@app.post("/ingest", status_code=202)
async def ingest(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """Validates and safely stores the upload synchronously (fast, and the
    part that must reject a bad request before anything is trusted), then
    hands the actual pipeline run to a background task and returns
    immediately with a job to poll — see GET /jobs/{job_id}. The response
    shape is deliberately uniform for every accepted upload, including the
    already-ingested short-circuit (instant, but still job-shaped): a
    client only ever needs one code path — create, then poll until
    COMPLETED/FAILED."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only .pdf uploads are accepted")

    # Fast, cheap rejection when the client declares an oversized body up
    # front — Content-Length can be absent or wrong for chunked transfer,
    # so this is a courtesy, not the enforcement: _read_upload_capped()
    # below is what actually guarantees the cap regardless.
    declared_length = file.size
    if declared_length is not None and declared_length > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file exceeds maximum upload size of {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    body = await _read_upload_capped(file, _MAX_UPLOAD_BYTES)

    if not body.startswith(_PDF_MAGIC):
        # Extension alone is trivially spoofable; the magic bytes are the
        # real check. Nothing is written to disk for an invalid upload.
        raise HTTPException(400, "uploaded file is not a valid PDF")

    content_hash = hashlib.sha256(body).hexdigest()
    existing_doc_id = _find_doc_id_by_content(content_hash)
    if existing_doc_id is not None:
        # Identical content already in the store — nothing to process, so
        # the job is created already COMPLETED rather than faking an async
        # round-trip for work that doesn't need to happen.
        job = JOBS.create(file.filename)
        JOBS.complete(job.job_id, existing_doc_id, {
            "doc_id": existing_doc_id,
            "filename": STORE.ingested_docs.get(existing_doc_id, file.filename),
            "skipped_reason": "identical content already ingested",
            "counts": {"pages": None, "pages_selected": None,
                       "facts_proposed": 0, "facts_verified": 0, "facts_rejected": 0},
            "new_facts": [], "new_relations": [],
            "clusters_touched": 0,
            "llm_calls_made": 0, "elapsed_seconds": 0.0,
        }, already_ingested=True)
        return {"job_id": job.job_id, "status": job.status.value, "stage": job.stage.value}

    # The on-disk path is built from a SANITIZED, containment-verified
    # basename — never the raw client filename (B1.1). See
    # _sanitize_upload_filename()'s docstring for the exact traversal
    # shapes this defeats.
    dest = _safe_upload_dest(file.filename)
    with open(dest, "wb") as fh:
        fh.write(body)

    job = JOBS.create(file.filename)
    background_tasks.add_task(process_document, job.job_id, dest, file.filename)
    return {"job_id": job.job_id, "status": job.status.value, "stage": job.stage.value}


# --------------------------------------------------------------------------
# GET /jobs/{job_id}
# --------------------------------------------------------------------------

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job_id")
    return job.to_dict()


# --------------------------------------------------------------------------
# GET /documents
# --------------------------------------------------------------------------

def _diagnostics_out(d) -> dict:
    return {
        "total_pages": d.total_pages, "text_pages": d.text_pages,
        "image_only_pages": d.image_only_pages, "sparse_pages": d.sparse_pages,
        "tables_detected": d.tables_detected, "pages_with_tables": d.pages_with_tables,
        "repeated_header_candidates": d.repeated_header_candidates,
        "repeated_footer_candidates": d.repeated_footer_candidates,
        "warnings": d.warnings,
    }


@app.get("/documents")
def list_documents():
    out = []
    for doc_id, filename in STORE.ingested_docs.items():
        fact_count = sum(1 for f in STORE.facts.values() if f.evidence and f.evidence.doc_id == doc_id)
        path = _resolve_doc_path(doc_id)
        n_pages = None
        table_strategy = None
        diagnostics = None
        if path:
            from fact_layer.diagnostics import compute_document_diagnostics
            from fact_layer.parse import parse_pdf
            doc = parse_pdf(path)   # served from cache/parsed/ — no re-parse
            n_pages = doc.n_pages
            table_strategy = doc.table_strategy
            diagnostics = _diagnostics_out(compute_document_diagnostics(doc))
        out.append({
            "doc_id": doc_id, "filename": filename, "fact_count": fact_count,
            "n_pages": n_pages, "table_strategy": table_strategy,
            "diagnostics": diagnostics,
        })
    return {"total": len(out), "documents": out}


# --------------------------------------------------------------------------
# GET /facts, GET /facts/{id}
# --------------------------------------------------------------------------

@app.get("/facts")
def list_facts(
    doc_id: Optional[str] = None,
    subject: Optional[str] = None,
    measure: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    facts = list(STORE.facts.values())
    if doc_id:
        facts = [f for f in facts if f.evidence and f.evidence.doc_id == doc_id]
    if subject:
        facts = [f for f in facts if f.subject == subject]
    if measure:
        facts = [f for f in facts if f.measure == measure]
    if min_confidence is not None:
        facts = [f for f in facts if f.confidence >= min_confidence]

    total = len(facts)
    page = facts[offset:offset + limit]
    return {
        "total": total, "limit": limit, "offset": offset,
        "facts": [_fact_summary(f) for f in page],
    }


@app.get("/facts/{fact_id}")
def get_fact(fact_id: str):
    fact = STORE.facts.get(fact_id)
    if fact is None:
        raise HTTPException(404, "unknown fact_id")
    return _fact_full(fact)


# --------------------------------------------------------------------------
# GET /clusters
# --------------------------------------------------------------------------

@app.get("/clusters")
def list_clusters(min_size: int = Query(1, ge=1)):
    out = []
    for ck, fact_ids in STORE.clusters.items():
        if len(fact_ids) < min_size:
            continue
        fact_id_set = set(fact_ids)
        rels = [
            r for r in STORE.relations
            if r.source_fact_id in fact_id_set and r.target_fact_id in fact_id_set
        ]
        out.append({
            "cluster_key": ck,
            "size": len(fact_ids),
            "facts": [_fact_summary(STORE.facts[fid]) for fid in fact_ids if fid in STORE.facts],
            "relations": [_relation_summary(r) for r in rels],
        })
    out.sort(key=lambda c: c["size"], reverse=True)
    return {"total": len(out), "clusters": out}


# --------------------------------------------------------------------------
# GET /relations, GET /relations/{id}
# --------------------------------------------------------------------------

@app.get("/relations")
def list_relations(
    type: Optional[str] = None,
    min_confidence: Optional[float] = None,
    doc_id: Optional[str] = None,
    fact_id: Optional[str] = None,
):
    rels = list(STORE.relations)
    if type:
        rels = [r for r in rels if r.relation.value.lower() == type.lower()]
    if min_confidence is not None:
        rels = [r for r in rels if r.confidence >= min_confidence]
    if doc_id:
        def _touches(r: Relation) -> bool:
            a = STORE.facts.get(r.source_fact_id)
            b = STORE.facts.get(r.target_fact_id)
            return (a and a.evidence and a.evidence.doc_id == doc_id) or \
                   (b and b.evidence and b.evidence.doc_id == doc_id)
        rels = [r for r in rels if _touches(r)]
    if fact_id:
        # Additive filter for the "Compare this fact" UI action — every
        # relation this specific fact participates in, either side. Reuses
        # the already-computed Relation list; no new comparability logic.
        rels = [r for r in rels if r.source_fact_id == fact_id or r.target_fact_id == fact_id]

    rels.sort(key=lambda r: r.confidence, reverse=True)
    return {"total": len(rels), "relations": [_relation_summary(r) for r in rels]}


@app.get("/relations/{relation_id}")
def get_relation(relation_id: str):
    index = _relation_index()
    rel = index.get(relation_id)
    if rel is None:
        raise HTTPException(404, "unknown relation_id")
    return _relation_full(rel)


# --------------------------------------------------------------------------
# GET /stats
# --------------------------------------------------------------------------

@app.get("/stats")
def stats():
    summary = STORE.canonical_summary()

    rejected_count = 0
    rejected_by_reason: dict[str, int] = {}
    if os.path.exists(_REJECTED_PATH):
        import json
        with open(_REJECTED_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rejected_count += 1
                try:
                    reason = json.loads(line).get("reason", "unknown")
                except Exception:
                    reason = "unknown"
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1

    verified_count = len(STORE.facts)
    total_attempted = verified_count + rejected_count
    pass_rate = (verified_count / total_attempted) if total_attempted else None

    facts_by_doc: dict[str, int] = {}
    for f in STORE.facts.values():
        if f.evidence:
            facts_by_doc[f.evidence.doc_id] = facts_by_doc.get(f.evidence.doc_id, 0) + 1

    resolution_summary = None
    if os.path.exists(_RESOLUTION_LOG_PATH):
        import json
        with open(_RESOLUTION_LOG_PATH, "r", encoding="utf-8") as fh:
            resolution_summary = json.load(fh).get("summary")

    # Coverage: how much of the extracted corpus actually participates in a
    # cross-fact relationship, vs. sitting in a singleton cluster with
    # nothing to compare against. Deliberately surfaced rather than left for
    # someone to compute by hand — extraction volume alone overstates how
    # much cross-document reasoning is actually happening.
    facts_in_relation: set[str] = set()
    for r in STORE.relations:
        facts_in_relation.add(r.source_fact_id)
        facts_in_relation.add(r.target_fact_id)
    facts_in_multi_cluster = sum(len(fids) for fids in STORE.clusters.values() if len(fids) >= 2)
    coverage = {
        "facts_in_any_relation": len(facts_in_relation),
        "facts_in_any_relation_pct": round(len(facts_in_relation) / verified_count * 100, 1) if verified_count else None,
        "facts_in_multi_fact_cluster": facts_in_multi_cluster,
        "facts_in_multi_fact_cluster_pct": round(facts_in_multi_cluster / verified_count * 100, 1) if verified_count else None,
    }

    return {
        "documents": {"count": len(STORE.ingested_docs), "filenames": list(STORE.ingested_docs.values())},
        "facts": {"total": verified_count, "by_doc": facts_by_doc},
        "span_verification": {
            "verified": verified_count, "rejected": rejected_count,
            "rejected_by_reason": rejected_by_reason, "pass_rate": pass_rate,
        },
        "relations": {"total": summary["total_relations"], "by_type": summary["relation_counts"]},
        "coverage": coverage,
        "canonical": {
            "subjects": summary["canonical_subjects"],
            "measures": summary["canonical_measures"],
            "issuers": summary["canonical_issuers"],
        },
        "clusters": {"total": summary["clusters_total"], "with_2plus_facts": summary["clusters_with_2plus_facts"]},
        "resolution": resolution_summary,
        "llm_this_process": dict(_llm._stats),
    }


# --------------------------------------------------------------------------
# GET /page-image/{doc_id}/{page}
# --------------------------------------------------------------------------

@app.get("/page-image/{doc_id}/{page}")
def page_image(doc_id: str, page: int):
    if page < 1:
        raise HTTPException(400, "page is 1-indexed")

    os.makedirs(_PAGE_IMAGE_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_PAGE_IMAGE_CACHE_DIR, f"{doc_id}_{page}.png")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as fh:
            return Response(content=fh.read(), media_type="image/png")

    path = _resolve_doc_path(doc_id)
    if path is None:
        raise HTTPException(404, "unknown doc_id")

    try:
        with pdfplumber.open(path) as pdf:
            if page > len(pdf.pages):
                raise HTTPException(404, f"page {page} out of range (document has {len(pdf.pages)} pages)")
            pg = pdf.pages[page - 1]
            image = pg.to_image(resolution=_PAGE_IMAGE_RESOLUTION)
            image.save(cache_path, format="PNG")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"failed to render page: {e}")

    with open(cache_path, "rb") as fh:
        return Response(content=fh.read(), media_type="image/png")


# --------------------------------------------------------------------------
# GET /rejected-facts — the Four Cases view's "extraction failure" case.
# data/rejected_facts.jsonl is itself the deliverable (project specification
# section 3.1); this just reads it back as JSON instead of the frontend needing to
# fetch and parse a raw .jsonl file from the static mount.
# --------------------------------------------------------------------------

@app.get("/rejected-facts")
def rejected_facts(limit: int = Query(20, ge=1, le=500), reason: Optional[str] = None):
    import json as _json
    rows: list[dict] = []
    total = 0
    if os.path.exists(_REJECTED_PATH):
        with open(_REJECTED_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except Exception:
                    continue
                if reason and row.get("reason") != reason:
                    continue
                total += 1
                if len(rows) < limit:
                    rows.append(row)
    return {"total": total, "rejected_facts": rows}


# --------------------------------------------------------------------------
# Static frontend — single-file UI, no build step. Mounted only if present
# (so the API is fully usable on its own even before the frontend exists).
# Must be mounted LAST: Starlette matches explicit routes above it first,
# and this StaticFiles mount is the fallback for everything else ("/").
# --------------------------------------------------------------------------

if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
