from fastapi import APIRouter
from app.core.graph_manager import graph_manager

router = APIRouter()

@router.get("")
async def get_graph_data():
    """Returns all decision nodes and relationships for visualization."""
    return graph_manager.list_graph_data()
