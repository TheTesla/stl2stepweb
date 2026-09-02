import uuid
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class ConversionJob:
    id: str
    session_id: str
    original_filename: str
    upload_path: str
    output_path: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    position_in_queue: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    file_size_bytes: int = 0

    def to_dict(self, base_url: str = "") -> dict:
        result = {
            "id": self.id,
            "session_id": self.session_id,
            "original_filename": self.original_filename,
            "status": self.status.value,
            "position_in_queue": self.position_in_queue,
            "created_at": self.created_at,
        }
        if self.started_at:
            result["started_at"] = self.started_at
        if self.completed_at:
            result["completed_at"] = self.completed_at
        if self.error_message:
            result["error_message"] = self.error_message
        if self.output_path and self.status == JobStatus.COMPLETED:
            result["download_url"] = f"{base_url}/download/{self.id}"
        return result


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    last_keep_alive: float = field(default_factory=time.time)
    jobs: list[str] = field(default_factory=list)

    def is_expired(self, keep_alive_interval: int, max_retention: int) -> bool:
        now = time.time()
        if now - self.last_keep_alive > keep_alive_interval * 3:
            return True
        if self.jobs:
            return False
        return now - self.created_at > max_retention

    def touch(self):
        self.last_keep_alive = time.time()


class SessionManager:
    def __init__(self, settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.jobs: dict[str, ConversionJob] = {}
        self.queue: list[str] = []
        self.processing: set[str] = set()

    def create_session(self) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(id=session_id)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self.sessions.get(session_id)

    def keep_alive(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session:
            session.touch()
            return True
        return False

    def add_job(self, session_id: str, filename: str, upload_path: str, file_size: int) -> ConversionJob:
        job_id = str(uuid.uuid4())
        job = ConversionJob(
            id=job_id,
            session_id=session_id,
            original_filename=filename,
            upload_path=upload_path,
            file_size_bytes=file_size,
        )
        self.jobs[job_id] = job
        if session_id in self.sessions:
            self.sessions[session_id].jobs.append(job_id)
        self.queue.append(job_id)
        self._update_queue_positions()
        return job

    def get_job(self, job_id: str) -> Optional[ConversionJob]:
        return self.jobs.get(job_id)

    def get_session_jobs(self, session_id: str) -> list[ConversionJob]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        return [self.jobs[jid] for jid in session.jobs if jid in self.jobs]

    def start_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.QUEUED:
            return False
        if len(self.processing) >= self.settings.queue.max_parallel_conversions:
            return False
        if job_id not in self.queue:
            return False
        
        self.queue.remove(job_id)
        self.processing.add(job_id)
        job.status = JobStatus.PROCESSING
        job.started_at = time.time()
        self._update_queue_positions()
        return True

    def complete_job(self, job_id: str, output_path: str):
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.COMPLETED
            job.output_path = output_path
            job.completed_at = time.time()
            self.processing.discard(job_id)

    def fail_job(self, job_id: str, error: str):
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.FAILED
            job.error_message = error
            job.completed_at = time.time()
            self.processing.discard(job_id)

    def get_queue_status(self, job_id: str) -> Optional[int]:
        job = self.jobs.get(job_id)
        if not job:
            return None
        if job.status == JobStatus.QUEUED:
            try:
                return self.queue.index(job_id) + 1
            except ValueError:
                return 0
        return 0

    def _update_queue_positions(self):
        for i, job_id in enumerate(self.queue):
            job = self.jobs.get(job_id)
            if job:
                job.position_in_queue = i + 1

    def get_next_queued_job(self) -> Optional[ConversionJob]:
        if len(self.processing) >= self.settings.queue.max_parallel_conversions:
            return None
        if not self.queue:
            return None
        job_id = self.queue[0]
        return self.jobs.get(job_id)

    def cleanup_expired_sessions(self):
        now = time.time()
        expired_sessions = []
        for session_id, session in self.sessions.items():
            if session.is_expired(
                self.settings.session.keep_alive_interval_seconds,
                self.settings.session.max_file_retention_seconds
            ):
                expired_sessions.append(session_id)
        
        for session_id in expired_sessions:
            self._cleanup_session(session_id)

    def _cleanup_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            for job_id in session.jobs:
                job = self.jobs.pop(job_id, None)
                if job:
                    if job.upload_path and Path(job.upload_path).exists():
                        Path(job.upload_path).unlink(missing_ok=True)
                    if job.output_path and Path(job.output_path).exists():
                        Path(job.output_path).unlink(missing_ok=True)
                    self.queue = [j for j in self.queue if j != job_id]
                    self.processing.discard(job_id)
            self._update_queue_positions()

    def get_stats(self) -> dict:
        return {
            "total_sessions": len(self.sessions),
            "total_jobs": len(self.jobs),
            "queued_jobs": len(self.queue),
            "processing_jobs": len(self.processing),
            "max_parallel": self.settings.queue.max_parallel_conversions,
        }