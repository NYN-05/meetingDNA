from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.ingestion_service import ingestion_service
from app.core.job_queue import ingestion_job_queue
from app.core.vector_store import vector_store

router = APIRouter()


@router.post("/upload", status_code=202)
async def upload_audio(
    file: Optional[UploadFile] = File(None),
    transcript_text: Optional[str] = Form(None),
    source_meeting: Optional[str] = Form(None),
):
    """Accept audio or transcript input and queue background processing."""
    if file is None and not transcript_text:
        raise HTTPException(status_code=400, detail="Provide either an uploaded file or transcript_text.")

    raw_bytes = None
    filename = None
    content_type = None

    if file is not None:
        raw_bytes = await file.read()
        filename = file.filename
        content_type = file.content_type

    job_id = ingestion_job_queue.submit_upload(
        raw_bytes=raw_bytes,
        filename=filename,
        content_type=content_type,
        transcript_text=transcript_text,
        source_meeting=source_meeting,
    )

    return {
        "status": "queued",
        "job_id": job_id,
        "monitor_url": f"/ingestion/jobs/{job_id}",
        "source_meeting": source_meeting,
    }


@router.get("/jobs/{job_id}")
async def get_ingestion_job(job_id: str):
    """Returns the current status of an ingestion job."""
    job = ingestion_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found.")
    return job


@router.get("/history")
async def get_ingestion_history(limit: int = 25):
    """Returns persistent meeting records stored in ChromaDB."""
    safe_limit = max(1, min(limit, 100))
    return {"items": vector_store.list_meetings(limit=safe_limit)}


@router.get("/history/{transcript_id}")
async def get_ingestion_history_item(transcript_id: str):
    """Returns a single stored meeting record by id."""
    item = vector_store.get_meeting(transcript_id)
    if not item:
        raise HTTPException(status_code=404, detail="Saved meeting not found.")
    return item
