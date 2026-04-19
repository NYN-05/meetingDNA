from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import ingestion, query, graph

app = FastAPI(title="MeetingDNA API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|\[::1\])(:(\d+))?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion.router, prefix="/ingestion", tags=["Ingestion"])
app.include_router(query.router, prefix="/query", tags=["Query"])
app.include_router(graph.router, prefix="/graph", tags=["Graph"])

@app.get("/")
async def root():
    return {"message": "MeetingDNA Decision Intelligence Engine is running."}
