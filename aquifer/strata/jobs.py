"""Background job runner for batch de-identification.

Runs file processing in a background thread, updates job progress in
the database, and notifies connected WebSocket clients in real time.

Usage:
    runner = JobRunner(db, vault_manager, config)
    job_id = runner.submit(practice_id, user_id, file_specs)
    # Client connects to /ws/jobs/{job_id} for live progress
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FileSpec:
    """A file queued for de-identification."""
    filename: str
    path: Path
    suffix: str
    file_size: int


@dataclass
class JobProgress:
    """Current state of a running job."""
    job_id: str
    status: str
    total_files: int
    completed_files: int = 0
    failed_files: int = 0
    current_file: str | None = None
    results: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def percent(self) -> float:
        if self.total_files == 0:
            return 100.0
        return round((self.completed_files + self.failed_files) / self.total_files * 100, 1)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total_files": self.total_files,
            "completed_files": self.completed_files,
            "failed_files": self.failed_files,
            "current_file": self.current_file,
            "percent": self.percent,
            "error": self.error,
        }


class JobRunner:
    """Manages background de-identification jobs."""

    def __init__(self, db, vault_manager, config):
        self.db = db
        self.db_path = db.db_path  # For creating thread-local connections
        self.vault_manager = vault_manager
        self.config = config
        # WebSocket subscribers: job_id → set of asyncio.Queue
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        practice_id: str,
        user_id: str,
        file_specs: list[FileSpec],
    ) -> str:
        """Submit a batch job. Returns the job_id immediately."""
        job_id = str(uuid.uuid4())

        self.db.create_job(
            id=job_id,
            practice_id=practice_id,
            user_id=user_id,
            job_type="batch_deid",
            total_files=len(file_specs),
        )

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, practice_id, user_id, file_specs),
            daemon=True,
        )
        thread.start()

        return job_id

    def subscribe(self, job_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a job. Returns an asyncio.Queue."""
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            if job_id not in self._subscribers:
                self._subscribers[job_id] = set()
            self._subscribers[job_id].add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from job progress updates."""
        with self._lock:
            subs = self._subscribers.get(job_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[job_id]

    def _notify(self, job_id: str, progress: JobProgress) -> None:
        """Send progress update to all subscribers."""
        with self._lock:
            subs = self._subscribers.get(job_id)
            if not subs:
                return
            msg = progress.to_dict()
            for queue in subs:
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    pass  # Drop if consumer is too slow

    def _run_job(
        self,
        job_id: str,
        practice_id: str,
        user_id: str,
        file_specs: list[FileSpec],
    ) -> None:
        """Process all files in a background thread.

        Creates its own DB connection since SQLite connections are
        not thread-safe.
        """
        from aquifer.engine.pipeline import process_file
        from aquifer.strata.database import StrataDB

        # Thread-local DB connection
        db = StrataDB(self.db_path)
        db.connect()

        progress = JobProgress(
            job_id=job_id,
            status="processing",
            total_files=len(file_specs),
        )

        db.update_job_progress(job_id, status="processing")
        self._notify(job_id, progress)

        practice = db.get_practice(practice_id)
        if not practice:
            progress.status = "failed"
            progress.error = "Practice not found"
            db.update_job_progress(job_id, status="failed", error_message=progress.error)
            self._notify(job_id, progress)
            return

        vault = self.vault_manager.open_vault(
            practice_id, practice["vault_key_encrypted"]
        )

        for spec in file_specs:
            progress.current_file = spec.filename
            db.update_job_progress(job_id, current_file=spec.filename)
            self._notify(job_id, progress)

            file_id = str(uuid.uuid4())
            aqf_output = self.vault_manager.aqf_dir(practice_id) / f"{file_id}.aqf"

            try:
                # Create DB record
                db.create_file_record(
                    id=file_id,
                    practice_id=practice_id,
                    original_filename=spec.filename,
                    source_type=spec.suffix.lstrip("."),
                    source_hash="pending",
                    file_size_bytes=spec.file_size,
                )
                db.update_file_record(file_id, status="processing")

                result = process_file(
                    spec.path, aqf_output, vault,
                    use_ner=self.config.use_ner, verbose=False,
                )

                if result.errors:
                    db.update_file_record(
                        file_id, status="failed", error_message=result.errors[0],
                    )
                    progress.failed_files += 1
                    progress.results.append({
                        "file_id": file_id,
                        "filename": spec.filename,
                        "status": "failed",
                        "error": result.errors[0],
                    })
                else:
                    data_domain = None
                    if result.aqf_path:
                        try:
                            from aquifer.format.reader import read_aqf
                            aqf_data = read_aqf(Path(result.aqf_path))
                            data_domain = aqf_data.metadata.data_domain
                        except Exception:
                            pass

                    db.update_file_record(
                        file_id, status="completed",
                        aqf_hash=result.aqf_hash,
                        aqf_storage_path=str(aqf_output),
                        token_count=result.token_count,
                        data_domain=data_domain,
                    )
                    progress.completed_files += 1
                    progress.results.append({
                        "file_id": file_id,
                        "filename": spec.filename,
                        "status": "completed",
                        "token_count": result.token_count,
                    })

                    db.log_usage(
                        practice_id, "deid", user_id=user_id,
                        file_id=file_id, bytes_processed=spec.file_size,
                    )

            except Exception as e:
                logger.error(f"Job {job_id}: failed to process {spec.filename}: {e}")
                progress.failed_files += 1
                progress.results.append({
                    "file_id": file_id,
                    "filename": spec.filename,
                    "status": "failed",
                    "error": str(e),
                })

            db.update_job_progress(
                job_id,
                completed_files=progress.completed_files,
                failed_files=progress.failed_files,
            )
            self._notify(job_id, progress)

            # Clean up temp file
            spec.path.unlink(missing_ok=True)

        # Job complete
        progress.status = "completed"
        progress.current_file = None
        result_json = json.dumps({
            "results": progress.results,
            "total": progress.total_files,
            "succeeded": progress.completed_files,
            "failed": progress.failed_files,
        })

        db.update_job_progress(
            job_id, status="completed",
            completed_files=progress.completed_files,
            failed_files=progress.failed_files,
            current_file="",
            result_json=result_json,
        )
        self._notify(job_id, progress)

        db.close()

        logger.info(
            f"Job {job_id}: completed {progress.completed_files}/{progress.total_files} "
            f"({progress.failed_files} failed)"
        )
