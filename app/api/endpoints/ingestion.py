from fastapi import APIRouter, UploadFile, File, HTTPException, Form
import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from app.core.transcription import transcription_service
from app.core.extractor import extractor_service
from app.core.graph_manager import graph_manager
from app.core.vector_store import vector_store

router = APIRouter()

UPLOAD_DIR = os.path.join("data", "uploads")
TRANSCRIPT_EXTENSIONS = {".txt", ".md", ".json", ".vtt", ".srt", ".csv", ".log"}


def _safe_filename(filename: str) -> str:
    return os.path.basename(filename)


def _decode_text_payload(raw_bytes: bytes) -> str:
    return raw_bytes.decode("utf-8-sig", errors="replace").strip()


def _extract_json_text(payload):
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts = [_extract_json_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("transcript", "text", "body", "content", "utterances", "segments", "messages"):
            if key in payload:
                text = _extract_json_text(payload[key])
                if text:
                    return text

        parts = []
        for value in payload.values():
            text = _extract_json_text(value)
            if text:
                parts.append(text)
        return "\n".join(parts)
    return ""


def _is_transcript_file(file: UploadFile) -> bool:
    filename = (file.filename or "").lower()
    _, extension = os.path.splitext(filename)
    content_type = (file.content_type or "").lower()
    return extension in TRANSCRIPT_EXTENSIONS or content_type.startswith("text/") or content_type in {"application/json", "application/xml", "application/csv"}


async def _prepare_transcript_from_file(file: UploadFile, file_path: str) -> str:
    raw_bytes = await file.read()
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    with open(file_path, "wb") as handle:
        handle.write(raw_bytes)

    if _is_transcript_file(file):
        decoded = _decode_text_payload(raw_bytes)
        if (file.filename or "").lower().endswith(".json"):
            try:
                parsed = json.loads(decoded)
                extracted = _extract_json_text(parsed)
                if extracted:
                    return extracted
            except json.JSONDecodeError:
                pass
        return decoded

    transcript = await transcription_service.transcribe(file_path)
    return transcript


@router.post("/upload")
async def upload_audio(
    file: Optional[UploadFile] = File(None),
    transcript_text: Optional[str] = Form(None),
    source_meeting: Optional[str] = Form(None),
):
    """Pipeline: Audio or transcript input -> Transcript -> Decisions -> Graph/Vector Store."""
    if file is None and not transcript_text:
        raise HTTPException(status_code=400, detail="Provide either an uploaded file or transcript_text.")

    file_id = str(uuid.uuid4())
    transcript = ""
    input_type = "pasted-transcript"
    file_name = None

    if file is not None:
        file_name = _safe_filename(file.filename or f"{file_id}.bin")
        file_path = os.path.join(UPLOAD_DIR, f"{file_id}_{file_name}")
        transcript = await _prepare_transcript_from_file(file, file_path)
        input_type = "transcript-file" if _is_transcript_file(file) else "audio"
    elif transcript_text:
        transcript = transcript_text.strip()

    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript content is empty after processing the input.")

    try:
        recorded_at = datetime.now(timezone.utc).isoformat()

        # 1. Extraction
        decisions = await extractor_service.extract_decisions(transcript)

        # 2. Storage
        metadata = {
            "filename": file_name,
            "source_meeting": source_meeting,
            "input_type": input_type,
            "recorded_at": recorded_at,
            "transcript_length": len(transcript),
        }
        vector_store.add_transcript(file_id, transcript, metadata)

        # 3. Graph persistence
        for node in decisions:
            if source_meeting and not node.source_meeting:
                node.source_meeting = source_meeting
            graph_manager.save_decision(node)

        return {
            "status": "success",
            "input_type": input_type,
            "source_meeting": source_meeting,
            "transcript": transcript,
            "decisions_extracted": len(decisions),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_ingestion_history(limit: int = 25):
    """Returns persistent transcript records stored in ChromaDB."""
    safe_limit = max(1, min(limit, 100))
    return {"items": vector_store.list_transcripts(limit=safe_limit)}


@router.get("/history/{transcript_id}")
async def get_ingestion_history_item(transcript_id: str):
    """Returns a single stored transcript record by id."""
    item = vector_store.get_transcript(transcript_id)
    if not item:
        raise HTTPException(status_code=404, detail="Saved transcript not found.")
    return item
