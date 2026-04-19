from __future__ import annotations

import asyncio
import logging
from queue import Queue, Empty
from threading import Event, Lock, Thread
from typing import Any, Dict, Optional

from app.core.ingestion_service import ingestion_service


logger = logging.getLogger(__name__)


class IngestionJobQueue:
    def __init__(self, worker_count: int = 2):
        self.worker_count = max(1, worker_count)
        self._queue: Queue[Optional[Dict[str, Any]]] = Queue()
        self._stop_event = Event()
        self._started = False
        self._lock = Lock()
        self._workers: list[Thread] = []

    def start(self):
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            self._workers = []
            for index in range(self.worker_count):
                worker = Thread(target=self._worker_loop, name=f"meetingdna-ingestion-{index + 1}", daemon=True)
                worker.start()
                self._workers.append(worker)
            self._started = True

    def stop(self):
        with self._lock:
            if not self._started:
                return
            self._stop_event.set()
            for _ in self._workers:
                self._queue.put(None)
            for worker in self._workers:
                worker.join(timeout=1)
            self._workers = []
            self._started = False

    def submit_upload(
        self,
        *,
        raw_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        transcript_text: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> str:
        job_id = ingestion_service.create_job(source_meeting=source_meeting, filename=filename)
        self._queue.put(
            {
                "job_id": job_id,
                "raw_bytes": raw_bytes,
                "filename": filename,
                "content_type": content_type,
                "transcript_text": transcript_text,
                "source_meeting": source_meeting,
            }
        )
        return job_id

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                payload = self._queue.get(timeout=0.5)
            except Empty:
                continue

            if payload is None:
                self._queue.task_done()
                break

            try:
                asyncio.run(
                    ingestion_service.process_upload_job(
                        payload["job_id"],
                        raw_bytes=payload.get("raw_bytes"),
                        filename=payload.get("filename"),
                        content_type=payload.get("content_type"),
                        transcript_text=payload.get("transcript_text"),
                        source_meeting=payload.get("source_meeting"),
                    )
                )
            except Exception:
                logger.exception("Unexpected ingestion worker failure", extra={"job_id": payload.get("job_id")})
            finally:
                self._queue.task_done()


ingestion_job_queue = IngestionJobQueue()
