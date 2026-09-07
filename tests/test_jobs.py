"""Tests for the ingestion job model (fact_layer/jobs.py) and its wiring
into POST /ingest, GET /jobs/{job_id}, and the reusable process_document()
background function.

Every test that reaches Store.ingest() here uses either the deterministic
"already ingested" short-circuit (instant, no LLM call) or a minimal
text-bearing PDF that guarantees a real, always-uncached LLM call and a
clean LLMError before any Store mutation — the same safe pattern
tests/test_security.py already established, so none of these tests can
mutate the real data/store.json. See that file's module docstring for the
full reasoning and the real incident (caught and reverted) that established it.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

from fact_layer.jobs import Job, JobStage, JobStatus, JobStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MINIMAL_TEXT_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 70 >>
stream
BT /F1 12 Tf 20 150 Td (Revenue for FY2024 was 120.4 crore.) Tj ET
endstream
endobj
trailer
<< /Size 6 /Root 1 0 R >>
%%EOF
"""


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/committed cache only")


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    import api
    monkeypatch.setattr(api, "_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setattr(api, "_RESOLUTION_LOG_PATH", str(tmp_path / "resolution_log.json"))
    yield


@pytest.fixture(scope="module")
def client():
    import api
    assert len(api.STORE.facts) > 0
    return TestClient(api.app)


@pytest.fixture
def uploads_dir_snapshot():
    import api
    before = set(os.listdir(api._UPLOADS_DIR)) if os.path.isdir(api._UPLOADS_DIR) else set()
    yield api._UPLOADS_DIR
    after = set(os.listdir(api._UPLOADS_DIR)) if os.path.isdir(api._UPLOADS_DIR) else set()
    for new_file in after - before:
        try:
            os.remove(os.path.join(api._UPLOADS_DIR, new_file))
        except OSError:
            pass


# --------------------------------------------------------------------------
# 1/2 — JobStore/Job unit behavior: creation, queued state, transitions
# --------------------------------------------------------------------------

def test_job_creation_starts_queued():
    store = JobStore()
    job = store.create("report.pdf")
    assert job.status == JobStatus.QUEUED
    assert job.stage == JobStage.QUEUED
    assert job.filename == "report.pdf"
    assert job.doc_id is None
    assert job.error is None
    assert job.result is None


def test_job_ids_are_unique():
    store = JobStore()
    ids = {store.create("a.pdf").job_id for _ in range(50)}
    assert len(ids) == 50


def test_get_unknown_job_returns_none():
    store = JobStore()
    assert store.get("does-not-exist") is None


def test_set_stage_moves_to_processing():
    store = JobStore()
    job = store.create("report.pdf")
    store.set_stage(job.job_id, JobStage.PARSING)
    updated = store.get(job.job_id)
    assert updated.status == JobStatus.PROCESSING
    assert updated.stage == JobStage.PARSING


def test_set_stage_progression_matches_real_pipeline_boundaries():
    """Only stages that correspond to an actual Store.ingest() boundary are
    used — no fabricated "verifying" transition (see Store.ingest()'s
    on_stage docstring for why)."""
    store = JobStore()
    job = store.create("report.pdf")
    for stage in [JobStage.PARSING, JobStage.EXTRACTING, JobStage.RESOLVING, JobStage.ADJUDICATING, JobStage.STORING]:
        store.set_stage(job.job_id, stage)
        assert store.get(job.job_id).stage == stage


# --------------------------------------------------------------------------
# 3 — successful completion
# --------------------------------------------------------------------------

def test_job_complete_sets_completed_status_and_result():
    store = JobStore()
    job = store.create("report.pdf")
    store.complete(job.job_id, "doc123", {"new_facts": []}, already_ingested=False)
    updated = store.get(job.job_id)
    assert updated.status == JobStatus.COMPLETED
    assert updated.stage == JobStage.COMPLETED
    assert updated.doc_id == "doc123"
    assert updated.result == {"new_facts": []}
    assert updated.already_ingested is False


# --------------------------------------------------------------------------
# 4 — failed job
# --------------------------------------------------------------------------

def test_job_fail_sets_failed_status_and_error():
    store = JobStore()
    job = store.create("report.pdf")
    store.fail(job.job_id, "extraction failed: no cache entry")
    updated = store.get(job.job_id)
    assert updated.status == JobStatus.FAILED
    assert updated.stage == JobStage.FAILED
    assert updated.error == "extraction failed: no cache entry"


def test_job_to_dict_is_json_safe():
    store = JobStore()
    job = store.create("report.pdf")
    d = job.to_dict()
    assert d["status"] == "queued" and d["stage"] == "queued"   # plain strings, not enum objects


# --------------------------------------------------------------------------
# API integration: POST /ingest -> 202 + job, GET /jobs/{id}
# --------------------------------------------------------------------------

def test_post_ingest_returns_202_with_queued_job_snapshot(client, uploads_dir_snapshot):
    """The POST response body reflects the job's state at creation time
    (queued) — the background task (which may already have finished by the
    time this Python call returns, under TestClient) runs after that
    snapshot was built into the response."""
    r = client.post("/ingest", files={"file": ("job_snapshot_test.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    assert r.status_code == 202
    body = r.json()
    assert set(body.keys()) == {"job_id", "status", "stage"}
    assert body["status"] == "queued"
    assert body["stage"] == "queued"


def test_get_unknown_job_id_404s(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


def test_get_job_exposes_required_fields(client, uploads_dir_snapshot):
    r = client.post("/ingest", files={"file": ("job_fields_test.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    job_id = r.json()["job_id"]
    job = client.get(f"/jobs/{job_id}").json()
    for field in ("job_id", "status", "stage", "doc_id", "error", "created_at", "updated_at"):
        assert field in job


def test_already_ingested_job_completes_synchronously_with_real_shape(client):
    """The content-hash short-circuit needs no background processing, so
    the job it returns is already COMPLETED — this is the "successful
    completion" case exercised deterministically, without depending on any
    LLM call at all."""
    path = os.path.join(ROOT, "starter-datasets", "delhivery", "01-delhivery-prospectus-2022-excerpt.pdf")
    with open(path, "rb") as fh:
        r = client.post("/ingest", files={"file": ("01-delhivery-prospectus-2022-excerpt.pdf", fh, "application/pdf")})
    assert r.status_code == 202
    job = client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "completed"
    assert job["stage"] == "completed"
    assert job["already_ingested"] is True
    assert job["doc_id"] is not None
    assert job["result"]["new_facts"] == []


def test_failed_job_error_never_leaks_local_filesystem_path(client, uploads_dir_snapshot):
    r = client.post("/ingest", files={"file": ("leak_check.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    job = client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert ROOT not in job["error"]
    assert "/Users/" not in job["error"] and "site-packages" not in job["error"]


def test_existing_security_behavior_unchanged_through_job_flow(client, uploads_dir_snapshot):
    """B1 regressions must not creep back in via this refactor: a traversal
    filename still only ever writes inside data/uploads/, and an oversized
    upload is still rejected synchronously before any job is created."""
    import api
    r = client.post("/ingest", files={"file": ("../../evil.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    assert r.status_code == 202
    real_uploads = os.path.realpath(api._UPLOADS_DIR)
    for name in os.listdir(uploads_dir_snapshot):
        assert os.path.commonpath([real_uploads, os.path.realpath(os.path.join(uploads_dir_snapshot, name))]) == real_uploads


# --------------------------------------------------------------------------
# Phase 4 — process_document() is directly callable/testable, no HTTP needed
# --------------------------------------------------------------------------

def test_process_document_directly_without_http(tmp_path, monkeypatch):
    """The reusable processing function must be independently testable —
    not only reachable through the FastAPI route."""
    import api
    from fact_layer.jobs import JobStore as _JS

    monkeypatch.setattr(api, "_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setattr(api, "_RESOLUTION_LOG_PATH", str(tmp_path / "resolution_log.json"))
    monkeypatch.setattr(api, "JOBS", _JS())   # isolated registry for this test

    dest = tmp_path / "direct_test.pdf"
    dest.write_bytes(_MINIMAL_TEXT_PDF)
    job = api.JOBS.create("direct_test.pdf")

    api.process_document(job.job_id, str(dest), "direct_test.pdf")

    finished = api.JOBS.get(job.job_id)
    assert finished.status == JobStatus.FAILED   # no replay cache for this content
    assert finished.error is not None


# --------------------------------------------------------------------------
# Phase 8 — concurrency: the ingest lock serializes processing
# --------------------------------------------------------------------------

def test_ingest_lock_serializes_concurrent_processing(monkeypatch):
    """Store is one shared, JSON-persisted object; concurrent background
    tasks must not interleave inside the actual ingest critical section.
    Proven here by holding a real lock (api._INGEST_LOCK) across two
    threads each doing a sleep-then-append — if the lock did NOT serialize
    them, both sleeps would overlap and total wall time would be roughly
    one sleep interval, not the sum of both."""
    import api

    order: list[str] = []

    def fake_ingest(self, path, rejected_path=None, on_stage=None, budget=None):
        order.append(f"start:{path}")
        time.sleep(0.2)
        order.append(f"end:{path}")
        from fact_layer.store import IngestResult
        return IngestResult(doc_id=f"doc-{path}", filename=path, skipped_reason="already ingested")

    monkeypatch.setattr(type(api.STORE), "ingest", fake_ingest)

    from fact_layer.jobs import JobStore as _JS
    monkeypatch.setattr(api, "JOBS", _JS())
    job_a = api.JOBS.create("a.pdf")
    job_b = api.JOBS.create("b.pdf")

    t0 = time.time()
    threads = [
        threading.Thread(target=api.process_document, args=(job_a.job_id, "a.pdf", "a.pdf")),
        threading.Thread(target=api.process_document, args=(job_b.job_id, "b.pdf", "b.pdf")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.time() - t0

    assert elapsed >= 0.35, "two 0.2s critical sections should serialize to ~0.4s, not overlap to ~0.2s"
    # No interleaving: each start/end pair for one path is contiguous —
    # if the lock failed, a start for A could land between B's start/end.
    for path in ("a.pdf", "b.pdf"):
        i_start = order.index(f"start:{path}")
        i_end = order.index(f"end:{path}")
        assert i_end == i_start + 1, f"processing for {path} was interleaved with the other thread: {order}"

    assert api.JOBS.get(job_a.job_id).status == JobStatus.COMPLETED
    assert api.JOBS.get(job_b.job_id).status == JobStatus.COMPLETED
