"""
Ingestion job tracking — single-process, in-memory, best-effort.

This is NOT a durable distributed queue. A Job's state lives only in this
process's memory; a server restart loses every in-flight and completed job
record. That is an accepted, documented trade-off for a locally-run,
single-instance demo (see README's "Ingestion Job Model" section) — the
underlying pipeline output a completed job produced (facts, relations) is
unaffected, since that was already persisted to data/store.json by the time
the job reached COMPLETED; only the job's own bookkeeping record is
ephemeral.

Stages are restricted to what the pipeline can honestly report a real,
observable transition for (see Store.ingest()'s own `on_stage` docstring) —
there is no fabricated progress percentage anywhere in this module.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStage(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"          # covers extraction AND verification —
                                        # see Store.ingest()'s on_stage docstring
                                        # for why these aren't separately observable
    RESOLVING = "resolving"
    ADJUDICATING = "adjudicating"
    STORING = "storing"                # persisting to data/store.json, after ingest() returns
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    job_id: str
    filename: str
    status: JobStatus = JobStatus.QUEUED
    stage: JobStage = JobStage.QUEUED
    doc_id: Optional[str] = None
    already_ingested: bool = False
    error: Optional[str] = None        # sanitized — never a raw exception/stack trace/path
    result: Optional[dict] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "filename": self.filename,
            "status": self.status.value, "stage": self.stage.value,
            "doc_id": self.doc_id, "already_ingested": self.already_ingested,
            "error": self.error, "result": self.result,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class JobStore:
    """Thread-safe in-memory job registry, bounded only by process memory.
    A single global instance (api.py's `JOBS`) is shared across requests and
    the background threadpool FastAPI's BackgroundTasks runs sync callables
    on — every mutation goes through this lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str) -> Job:
        job = Job(job_id=uuid.uuid4().hex, filename=filename)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def set_stage(self, job_id: str, stage: JobStage) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.stage = stage
            job.status = JobStatus.PROCESSING
            job.touch()

    def complete(self, job_id: str, doc_id: Optional[str], result: dict, already_ingested: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.COMPLETED
            job.stage = JobStage.COMPLETED
            job.doc_id = doc_id
            job.result = result
            job.already_ingested = already_ingested
            job.touch()

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.stage = JobStage.FAILED
            job.error = error
            job.touch()
