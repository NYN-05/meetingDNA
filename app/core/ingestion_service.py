from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, UploadFile

from app.core.extractor import extractor_service
from app.core.graph_manager import graph_manager
from app.core.transcription import transcription_service
from app.core.vector_store import vector_store
from app.models.meeting import MeetingRecord


logger = logging.getLogger(__name__)


class IngestionService:
    upload_dir = Path("data/uploads")
    transcript_extensions = {".txt", ".md", ".json", ".vtt", ".srt", ".csv", ".log"}

    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._jobs_lock = Lock()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        return os.path.basename(filename)

    @staticmethod
    def _decode_text_payload(raw_bytes: bytes) -> str:
        return raw_bytes.decode("utf-8-sig", errors="replace").strip()

    @classmethod
    def _extract_json_text(cls, payload):
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            parts = [cls._extract_json_text(item) for item in payload]
            return "\n".join(part for part in parts if part)
        if isinstance(payload, dict):
            for key in ("transcript", "text", "body", "content", "utterances", "segments", "messages"):
                if key in payload:
                    text = cls._extract_json_text(payload[key])
                    if text:
                        return text

            parts = []
            for value in payload.values():
                text = cls._extract_json_text(value)
                if text:
                    parts.append(text)
            return "\n".join(parts)
        return ""

    def _is_transcript_payload(self, filename: Optional[str], content_type: Optional[str]) -> bool:
        filename_value = (filename or "").lower()
        _, extension = os.path.splitext(filename_value)
        content_type_value = (content_type or "").lower()
        return (
            extension in self.transcript_extensions
            or content_type_value.startswith("text/")
            or content_type_value in {"application/json", "application/xml", "application/csv"}
        )

    def _is_transcript_file(self, file: UploadFile) -> bool:
        return self._is_transcript_payload(file.filename, file.content_type)

    def create_job(self, *, source_meeting: Optional[str] = None, filename: Optional[str] = None) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "source_meeting": source_meeting,
            "filename": filename,
            "meeting_id": None,
            "transcript_id": None,
            "input_type": None,
            "summary": None,
            "participants": [],
            "decision_count": 0,
            "action_item_count": 0,
            "error": None,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
        return job_id

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else {}

    def _update_job(self, job_id: str, **updates: Any) -> Dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return {}
            job.update(updates)
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            return dict(job)

    async def _prepare_transcript_from_payload(
        self,
        *,
        raw_bytes: bytes,
        filename: str,
        content_type: Optional[str],
        file_path: Path,
    ) -> Tuple[str, str]:
        self.upload_dir.mkdir(parents=True, exist_ok=True)

        with file_path.open("wb") as handle:
            handle.write(raw_bytes)

        if self._is_transcript_payload(filename, content_type):
            decoded = self._decode_text_payload(raw_bytes)
            if filename.lower().endswith(".json"):
                try:
                    parsed = json.loads(decoded)
                    extracted = self._extract_json_text(parsed)
                    if extracted:
                        return extracted, "transcript-file"
                except json.JSONDecodeError:
                    pass
            return decoded, "transcript-file"

        transcript = await transcription_service.transcribe(str(file_path))
        return transcript, "audio"

    async def _process_upload_payload(
        self,
        *,
        raw_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        transcript_text: Optional[str] = None,
        source_meeting: Optional[str] = None,
        meeting_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> MeetingRecord:
        if raw_bytes is None and not transcript_text:
            raise HTTPException(status_code=400, detail="Provide either an uploaded file or transcript_text.")

        meeting_id = meeting_id or str(uuid.uuid4())
        recorded_at = recorded_at or datetime.now(timezone.utc).isoformat()
        transcript = ""
        input_type = "pasted-transcript"
        file_name = None

        if raw_bytes is not None:
            file_name = self._safe_filename(filename or f"{meeting_id}.bin")
            file_path = self.upload_dir / f"{meeting_id}_{file_name}"
            transcript, input_type = await self._prepare_transcript_from_payload(
                raw_bytes=raw_bytes,
                filename=file_name,
                content_type=content_type,
                file_path=file_path,
            )
        elif transcript_text:
            transcript = transcript_text.strip()

        if not transcript:
            raise HTTPException(status_code=400, detail="Transcript content is empty after processing the input.")

        meeting = await extractor_service.extract_meeting(
            transcript,
            meeting_id=meeting_id,
            recorded_at=recorded_at,
            source_meeting=source_meeting,
            input_type=input_type,
            filename=file_name,
        )
        meeting.transcript_id = meeting_id
        meeting.source_meeting = meeting.source_meeting or source_meeting
        meeting.recorded_at = recorded_at
        meeting.input_type = input_type
        meeting.filename = file_name
        meeting.transcript = transcript
        meeting.metadata.setdefault("transcript_length", len(transcript))
        meeting.metadata.setdefault("source_meeting", source_meeting)
        meeting.metadata.setdefault("input_type", input_type)
        meeting.metadata.setdefault("filename", file_name)

        vector_store.add_meeting(meeting)
        graph_manager.save_meeting(meeting)

        logger.info(
            "Processed meeting upload",
            extra={
                "meeting_id": meeting.meeting_id,
                "source_meeting": meeting.source_meeting,
                "decisions": len(meeting.decisions),
                "action_items": len(meeting.action_items),
            },
        )

        return meeting

    async def process_upload(
        self,
        *,
        file: Optional[UploadFile] = None,
        transcript_text: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> MeetingRecord:
        raw_bytes = None
        filename = None
        content_type = None

        if file is not None:
            raw_bytes = await file.read()
            filename = file.filename
            content_type = file.content_type

        return await self._process_upload_payload(
            raw_bytes=raw_bytes,
            filename=filename,
            content_type=content_type,
            transcript_text=transcript_text,
            source_meeting=source_meeting,
        )

    async def process_upload_job(
        self,
        job_id: str,
        *,
        raw_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        transcript_text: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> None:
        self._update_job(job_id, status="running", error=None)

        try:
            meeting = await self._process_upload_payload(
                raw_bytes=raw_bytes,
                filename=filename,
                content_type=content_type,
                transcript_text=transcript_text,
                source_meeting=source_meeting,
            )
            self._update_job(
                job_id,
                status="completed",
                meeting_id=meeting.meeting_id,
                transcript_id=meeting.transcript_id,
                input_type=meeting.input_type,
                summary=meeting.summary,
                participants=meeting.participants,
                decision_count=len(meeting.decisions),
                action_item_count=len(meeting.action_items),
                source_meeting=meeting.source_meeting,
                filename=meeting.filename,
            )
        except HTTPException as exc:
            self._update_job(job_id, status="failed", error=str(exc.detail))
            logger.warning("Ingestion job failed validation", extra={"job_id": job_id, "detail": exc.detail})
        except Exception as exc:
            self._update_job(job_id, status="failed", error=str(exc))
            logger.exception("Ingestion job failed", extra={"job_id": job_id})


ingestion_service = IngestionService()
