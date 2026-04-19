import chromadb
from chromadb.utils import embedding_functions
from app.utils.config import config
from datetime import datetime, timezone
from typing import Dict, Any, List

class VectorStoreManager:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
        self.collection = self.client.get_or_create_collection(
            name="meeting_transcripts",
            embedding_function=embedding_functions.DefaultEmbeddingFunction()
        )

    def add_transcript(self, transcript_id: str, text: str, metadata: dict):
        """Adds transcript chunks to the vector store."""
        # In a real app, we would chunk the text here.
        cleaned_metadata = self._clean_metadata(metadata)
        payload: Dict[str, Any] = {
            "documents": [text],
            "ids": [transcript_id],
        }
        if cleaned_metadata:
            payload["metadatas"] = [cleaned_metadata]

        self.collection.add(**payload)

    def list_transcripts(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Returns recently stored transcripts from persistent Chroma storage."""
        results = self.collection.get(include=["documents", "metadatas"])
        ids = results.get("ids") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []

        entries: List[Dict[str, Any]] = []
        for index, transcript_id in enumerate(ids):
            document = documents[index] if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else {}
            entries.append(
                {
                    "transcript_id": transcript_id,
                    "source_meeting": metadata.get("source_meeting"),
                    "input_type": metadata.get("input_type"),
                    "filename": metadata.get("filename"),
                    "recorded_at": metadata.get("recorded_at"),
                    "transcript_length": len(document or ""),
                    "transcript_preview": (document or "").strip()[:400],
                }
            )

        entries.sort(key=lambda item: self._parse_timestamp(item.get("recorded_at")), reverse=True)
        return entries[:limit]

    def get_transcript(self, transcript_id: str) -> Dict[str, Any]:
        """Returns a stored transcript and its metadata by id."""
        results = self.collection.get(ids=[transcript_id], include=["documents", "metadatas"])
        ids = results.get("ids") or []
        if not ids:
            return {}

        document = (results.get("documents") or [""])[0]
        metadata = (results.get("metadatas") or [{}])[0]
        return {
            "transcript_id": transcript_id,
            "source_meeting": metadata.get("source_meeting"),
            "input_type": metadata.get("input_type"),
            "filename": metadata.get("filename"),
            "recorded_at": metadata.get("recorded_at"),
            "transcript_length": len(document or ""),
            "transcript_preview": (document or "").strip()[:400],
            "transcript": document,
        }

    def semantic_search(self, query: str, n_results: int = 3):
        """Performs semantic search over transcripts."""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"]
        )
        return results

    @staticmethod
    def _clean_metadata(metadata: dict) -> dict:
        cleaned_metadata = {}
        for key, value in (metadata or {}).items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                cleaned_metadata[key] = value
            else:
                cleaned_metadata[key] = str(value)
        return cleaned_metadata

    @staticmethod
    def _parse_timestamp(value: Any):
        if isinstance(value, str) and value:
            normalized_value = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized_value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

vector_store = VectorStoreManager()
