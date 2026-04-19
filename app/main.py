import time
import logging

from fastapi import Request
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import ingestion, query, graph
from app.core.job_queue import ingestion_job_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="MeetingDNA API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|\[::1\])(:(\d+))?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def start_background_workers():
    ingestion_job_queue.start()


@app.on_event("shutdown")
async def stop_background_workers():
    ingestion_job_queue.stop()


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started_at) * 1000
    logging.getLogger("meetingdna.api").info(
        "%s %s -> %s %.2fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

app.include_router(ingestion.router, prefix="/ingestion", tags=["Ingestion"])
app.include_router(query.router, prefix="/query", tags=["Query"])
app.include_router(graph.router, prefix="/graph", tags=["Graph"])

@app.get("/")
async def root():
    return {"message": "MeetingDNA Decision Intelligence Engine is running."}


@app.get("/health")
async def health():
    return {"status": "ok"}
