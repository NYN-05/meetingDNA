from fastapi import APIRouter, Query

from app.core.hybrid_retrieval import hybrid_retrieval_service

router = APIRouter()


@router.get("")
async def query_engine(q: str = Query(...)):
    """Answer a natural language query using hybrid vector and graph retrieval."""
    return await hybrid_retrieval_service.answer(q)
