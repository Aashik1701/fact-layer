"""Tests for B1 — upload security hardening in api.py.

Covers: path-traversal-proof filename handling, upload size limits, PDF
magic-byte validation, and cleanup of unparseable uploads.

Every test that reaches Store.ingest() here uses a minimal PDF with real
extractable text (not a blank page), so extract_document_defaults() always
attempts a real metadata LLM call — which always misses the replay cache
for content nobody has ever seen — and raises LLMError EARLY, before
Store.ingest() mutates self.facts/self.ingested_docs or ever reaches
api.py's STORE.save() line. That failure path is what makes these tests
safe to run against the real, shared api.STORE singleton without polluting
it or the real data/store.json — confirmed by an autouse fixture that
additionally redirects the save paths as defense in depth.

(An earlier version of this file used a blank, textless PDF: extract.py's
existing "skip the metadata call when a document has no text on pages 1-2"
behaviour meant triage selected no pages, no LLM call was ever attempted,
ingestion "succeeded" with zero facts, and Store.save() ran — silently
writing 5+ synthetic empty documents into the real data/store.json. That
mutation was found and reverted via `git checkout`; this rewrite closes
the gap that allowed it, rather than only cleaning up after it once.)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A minimal PDF with genuine extractable text, so any ingest attempt always
# tries a real (and therefore always-uncached) LLM call and fails cleanly
# with LLMError before any Store mutation — see module docstring.
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

_GARBAGE_WITH_PDF_MAGIC = b"%PDF-1.4\nthis is not a real pdf body, just noise\n%%EOF"


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/committed cache only")


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    # Defense in depth on top of the "LLMError fires before any mutation"
    # design above: even if a future change makes some case here reach the
    # save step, it lands in a throwaway temp file, never the real
    # data/store.json / data/resolution_log.json.
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
    """Record data/uploads/ contents before a test and remove anything the
    test caused to appear there — these tests must not leave files behind
    in a real, gitignored-but-persistent demo directory."""
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
# B1.1 — path traversal: unit tests on the sanitizer itself
# --------------------------------------------------------------------------

_TRAVERSAL_FILENAMES = [
    "../../evil.pdf",
    "../../../tmp/x.pdf",
    "/tmp/evil.pdf",
    "..\\..\\evil.pdf",
    "foo/../../evil.pdf",
]


@pytest.mark.parametrize("malicious", _TRAVERSAL_FILENAMES)
def test_sanitize_upload_filename_strips_traversal(malicious):
    import api
    safe = api._sanitize_upload_filename(malicious)
    assert "/" not in safe and "\\" not in safe
    assert ".." not in safe
    assert safe.lower().endswith(".pdf")


@pytest.mark.parametrize("malicious", _TRAVERSAL_FILENAMES)
def test_safe_upload_dest_stays_inside_uploads_dir(malicious, uploads_dir_snapshot):
    import api
    dest = api._safe_upload_dest(malicious)
    real_uploads = os.path.realpath(api._UPLOADS_DIR)
    real_dest = os.path.realpath(dest)
    assert os.path.commonpath([real_uploads, real_dest]) == real_uploads


# --------------------------------------------------------------------------
# B1.1 — path traversal: full endpoint integration
# --------------------------------------------------------------------------

@pytest.mark.parametrize("malicious", _TRAVERSAL_FILENAMES)
def test_ingest_with_malicious_filename_writes_only_inside_uploads_dir(
    client, malicious, uploads_dir_snapshot
):
    r = client.post("/ingest", files={"file": (malicious, _MINIMAL_TEXT_PDF, "application/pdf")})
    # A genuinely new document has no replay-cache entry, so this legitimately
    # 502s at the metadata LLM call — the security property under test is
    # WHERE the file landed before that, not whether extraction succeeded.
    assert r.status_code == 502

    uploads_dir = uploads_dir_snapshot
    real_uploads = os.path.realpath(uploads_dir)
    for name in os.listdir(uploads_dir):
        full = os.path.realpath(os.path.join(uploads_dir, name))
        assert os.path.commonpath([real_uploads, full]) == real_uploads
        assert ".." not in name

    # the malicious path itself must never have been created
    traversal_target = os.path.realpath(os.path.join(uploads_dir, malicious))
    if real_uploads not in traversal_target:
        assert not os.path.exists(traversal_target)


# --------------------------------------------------------------------------
# B1.2 — upload size limit
# --------------------------------------------------------------------------

def test_oversized_upload_is_rejected(client, monkeypatch, uploads_dir_snapshot):
    import api
    monkeypatch.setattr(api, "_MAX_UPLOAD_BYTES", 100)   # tiny cap for this test only
    oversized = _MINIMAL_TEXT_PDF + b"0" * 1000
    r = client.post("/ingest", files={"file": ("big.pdf", oversized, "application/pdf")})
    assert r.status_code == 413
    assert not any("big" in n for n in os.listdir(uploads_dir_snapshot))


def test_upload_within_size_limit_is_not_rejected_for_size(client, monkeypatch, uploads_dir_snapshot):
    import api
    monkeypatch.setattr(api, "_MAX_UPLOAD_BYTES", 10_000)
    assert len(_MINIMAL_TEXT_PDF) < 10_000
    r = client.post("/ingest", files={"file": ("small.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    assert r.status_code != 413


# --------------------------------------------------------------------------
# B1.3 — PDF magic-byte validation (extension alone is not trusted)
# --------------------------------------------------------------------------

def test_non_pdf_content_with_pdf_extension_is_rejected(client, uploads_dir_snapshot):
    r = client.post("/ingest", files={"file": ("fake.pdf", b"I am not a PDF, just text.", "application/pdf")})
    assert r.status_code == 400
    assert not any("fake" in n for n in os.listdir(uploads_dir_snapshot))


def test_existing_non_pdf_extension_rejection_still_works(client):
    """Pre-existing behaviour (filename extension check) must not regress."""
    r = client.post("/ingest", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# B1.4 — cleanup: an unparseable-but-PDF-signed upload is removed
# --------------------------------------------------------------------------

def test_unparseable_pdf_is_rejected_and_cleaned_up(client, uploads_dir_snapshot):
    r = client.post("/ingest", files={"file": ("garbage.pdf", _GARBAGE_WITH_PDF_MAGIC, "application/pdf")})
    assert r.status_code == 400
    assert not any("garbage" in n for n in os.listdir(uploads_dir_snapshot)), \
        "an unparseable upload must not be left behind in data/uploads/"


# --------------------------------------------------------------------------
# B1 — normal upload still passes validation (regression guard)
# --------------------------------------------------------------------------

def test_normal_valid_pdf_upload_passes_validation(client, uploads_dir_snapshot):
    """A genuinely new, valid, well-formed PDF passes every security check
    and reaches Store.ingest(), where it fails cleanly with 502 (no
    replay-cache entry exists for content nobody has ever seen). 502, not
    400/413, is what proves it got PAST validation rather than rejected by
    it — and the file is left in data/uploads/ (it parsed fine; only
    genuinely unparseable uploads are cleaned up)."""
    r = client.post("/ingest", files={"file": ("brand_new_document.pdf", _MINIMAL_TEXT_PDF, "application/pdf")})
    assert r.status_code == 502
    assert "extraction failed" in r.json()["detail"]
    assert any("brand_new_document" in n for n in os.listdir(uploads_dir_snapshot))
