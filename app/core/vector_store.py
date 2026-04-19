from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

from app.models.meeting import MeetingDecision, MeetingRecord
from app.utils.config import config


class VectorStoreManager:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
        self.meeting_collection = self.client.get_or_create_collection(
            name="meeting_transcripts",
            embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        )
        self.decision_collection = self.client.get_or_create_collection(
            name="decision_embeddings",
            embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        )

    def add_meeting(self, meeting: MeetingRecord):
        """Index a meeting and all of its decisions."""
        meeting_document = self._meeting_embedding_text(meeting)
        meeting_metadata = self._clean_metadata(meeting.to_index_metadata())
        meeting_payload: Dict[str, Any] = {
            "documents": [meeting_document],
            "ids": [meeting.meeting_id],
        }
        if meeting_metadata:
            meeting_payload["metadatas"] = [meeting_metadata]

        self._upsert(self.meeting_collection, meeting_payload)
        self.add_decisions(meeting)

    def add_decisions(self, meeting: MeetingRecord):
        decision_documents: List[str] = []
        decision_ids: List[str] = []
        decision_metadatas: List[Dict[str, Any]] = []

        for index, decision in enumerate(meeting.decisions):
            decision_id = decision.decision_id or f"{meeting.meeting_id}:decision:{index + 1}"
            decision_ids.append(decision_id)
            decision_documents.append(self._decision_embedding_text(decision, meeting))
            decision_metadatas.append(
                self._clean_metadata(
                    {
                        "decision_id": decision_id,
                        "meeting_id": meeting.meeting_id,
                        "source_meeting": decision.source_meeting or meeting.source_meeting,
                        "decision": decision.decision,
                        "owner": decision.owner,
                        "status": decision.status,
                        "timestamp": decision.timestamp,
                        "version": decision.version,
                        "topics": meeting.topics,
                        "organizations": meeting.organizations,
                    }
                )
            )

        if not decision_documents:
            return

        payload: Dict[str, Any] = {
            "documents": decision_documents,
            "ids": decision_ids,
            "metadatas": decision_metadatas,
        }
        self._upsert(self.decision_collection, payload)

    def add_transcript(self, transcript_id: str, text: str, metadata: dict):
        """Compatibility wrapper for older callers."""
        meeting = MeetingRecord(
            meeting_id=transcript_id,
            transcript_id=metadata.get("transcript_id") or transcript_id,
            source_meeting=metadata.get("source_meeting"),
            recorded_at=metadata.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
            input_type=metadata.get("input_type"),
            filename=metadata.get("filename"),
            transcript=text,
            summary=metadata.get("summary"),
            participants=metadata.get("participants") or [],
            topics=metadata.get("topics") or [],
            organizations=metadata.get("organizations") or [],
            decisions=[],
            action_items=[],
            metadata=metadata,
        )
        self.add_meeting(meeting)

    def list_meetings(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Returns recently stored meeting records from persistent Chroma storage."""
        results = self.meeting_collection.get(include=["documents", "metadatas"])
        ids = results.get("ids") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []

        entries: List[Dict[str, Any]] = []
        for index, meeting_id in enumerate(ids):
            document = documents[index] if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else {}
            entries.append(
                {
                    "meeting_id": metadata.get("meeting_id") or meeting_id,
                    "transcript_id": metadata.get("transcript_id") or meeting_id,
                    "source_meeting": metadata.get("source_meeting"),
                    "input_type": metadata.get("input_type"),
                    "filename": metadata.get("filename"),
                    "summary": metadata.get("summary"),
                    "topics": self._decode_json_list(metadata.get("topics")),
                    "organizations": self._decode_json_list(metadata.get("organizations")),
                    "recorded_at": metadata.get("recorded_at"),
                    "participant_count": metadata.get("participant_count"),
                    "topic_count": metadata.get("topic_count"),
                    "organization_count": metadata.get("organization_count"),
                    "decision_count": metadata.get("decision_count"),
                    "action_item_count": metadata.get("action_item_count"),
                    "transcript_length": len(document or ""),
                    "transcript_preview": (document or "").strip()[:400],
                }
            )

        entries.sort(key=lambda item: self._parse_timestamp(item.get("recorded_at")), reverse=True)
        return entries[:limit]

    def list_transcripts(self, limit: int = 25) -> List[Dict[str, Any]]:
        return self.list_meetings(limit=limit)

    def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        """Returns a stored meeting record and its transcript by id."""
        results = self.meeting_collection.get(ids=[meeting_id], include=["documents", "metadatas"])
        ids = results.get("ids") or []
        if not ids:
            return {}

        document = (results.get("documents") or [""])[0]
        metadata = (results.get("metadatas") or [{}])[0]
        return {
            "meeting_id": metadata.get("meeting_id") or meeting_id,
            "transcript_id": metadata.get("transcript_id") or meeting_id,
            "source_meeting": metadata.get("source_meeting"),
            "input_type": metadata.get("input_type"),
            "filename": metadata.get("filename"),
            "summary": metadata.get("summary"),
            "topics": self._decode_json_list(metadata.get("topics")),
            "organizations": self._decode_json_list(metadata.get("organizations")),
            "recorded_at": metadata.get("recorded_at"),
            "participant_count": metadata.get("participant_count"),
            "topic_count": metadata.get("topic_count"),
            "organization_count": metadata.get("organization_count"),
            "decision_count": metadata.get("decision_count"),
            "action_item_count": metadata.get("action_item_count"),
            "transcript_length": len(document or ""),
            "transcript_preview": (document or "").strip()[:400],
            "transcript": document,
        }

    def get_transcript(self, transcript_id: str) -> Dict[str, Any]:
        return self.get_meeting(transcript_id)

    def search_meetings(self, query: str, n_results: int = 3) -> Dict[str, Any]:
        return self.meeting_collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

    def search_decisions(self, query: str, n_results: int = 3) -> Dict[str, Any]:
        return self.decision_collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

    def semantic_search(self, query: str, n_results: int = 3):
        """Performs semantic search over meetings and decisions."""
        meeting_results = self.search_meetings(query, n_results=n_results)
        decision_results = self.search_decisions(query, n_results=n_results)
        return {"meeting_results": meeting_results, "decision_results": decision_results}

    def _upsert(self, collection, payload: Dict[str, Any]):
        if hasattr(collection, "upsert"):
            collection.upsert(**payload)
        else:
            collection.add(**payload)

    @staticmethod
    def _meeting_embedding_text(meeting: MeetingRecord) -> str:
        components = [
            f"Summary: {meeting.summary}" if meeting.summary else "",
            f"Topics: {', '.join(meeting.topics)}" if meeting.topics else "",
            f"Organizations: {', '.join(meeting.organizations)}" if meeting.organizations else "",
            f"Participants: {', '.join(meeting.participants)}" if meeting.participants else "",
            meeting.transcript or "",
        ]
        return "\n".join(component for component in components if component).strip()

    @staticmethod
    def _decision_embedding_text(decision: MeetingDecision, meeting: MeetingRecord) -> str:
        components = [
            f"Decision: {decision.decision}",
            f"Rationale: {decision.rationale}" if decision.rationale else "",
            f"Status: {decision.status}" if decision.status else "",
            f"Owner: {decision.owner}" if decision.owner else "",
            f"Topics: {', '.join(meeting.topics)}" if meeting.topics else "",
            f"Organizations: {', '.join(meeting.organizations)}" if meeting.organizations else "",
            f"Meeting: {meeting.source_meeting or meeting.meeting_id}",
        ]
        return "\n".join(component for component in components if component).strip()

    @staticmethod
    def _decode_json_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            try:
                payload = json.loads(value)
                if isinstance(payload, list):
                    return [str(item).strip() for item in payload if str(item).strip()]
            except json.JSONDecodeError:
                cleaned = value.strip()
                return [cleaned] if cleaned else []
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _clean_metadata(metadata: dict) -> dict:
        cleaned_metadata = {}
        for key, value in (metadata or {}).items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                cleaned_metadata[key] = value
            elif isinstance(value, (list, dict)):
                cleaned_metadata[key] = json.dumps(value, ensure_ascii=False)
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
