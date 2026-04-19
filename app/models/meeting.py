from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return list(value)


class ActionItem(BaseModel):
    action_item_id: Optional[str] = Field(None, description="Stable identifier for the action item")
    task: str = Field(..., description="The action to be completed")
    owner: Optional[str] = Field(None, description="Person or team responsible")
    deadline: Optional[str] = Field(None, description="Due date or deadline if available")
    status: Optional[str] = Field(None, description="Open, in progress, blocked, or done")
    meeting_id: Optional[str] = Field(None, description="Meeting that introduced the action item")


class MeetingDecision(BaseModel):
    decision_id: Optional[str] = Field(None, description="Stable identifier for the decision revision")
    meeting_id: Optional[str] = Field(None, description="Parent meeting identifier")
    decision: str = Field(..., description="The actual choice made")
    owner: Optional[str] = Field(None, description="Accountable individual or team")
    rationale: Optional[str] = Field(None, description="Why this choice was made")
    status: Optional[str] = Field(None, description="Current state of the decision")
    dependencies: List[str] = Field(default_factory=list, description="Decisions this one depends on")
    timestamp: Optional[str] = Field(None, description="When the decision was discussed or recorded")
    source_meeting: Optional[str] = Field(None, description="Meeting or transcript source for the decision")
    version: int = Field(1, description="Monotonic revision number for temporal tracking")
    supersedes_decision_id: Optional[str] = Field(None, description="Previous decision revision superseded by this one")

    @field_validator("dependencies", mode="before")
    @classmethod
    def _normalize_dependencies(cls, value):
        return _string_list(value)


class MeetingRecord(BaseModel):
    meeting_id: str = Field(..., description="Unique identifier for the meeting record")
    transcript_id: Optional[str] = Field(None, description="Transcript upload identifier")
    source_meeting: Optional[str] = Field(None, description="Human-readable meeting label")
    recorded_at: str = Field(..., description="UTC timestamp when the record was persisted")
    input_type: Optional[str] = Field(None, description="audio, transcript-file, or pasted-transcript")
    filename: Optional[str] = Field(None, description="Uploaded file name if available")
    transcript: str = Field(..., description="Normalized transcript text")
    summary: Optional[str] = Field(None, description="Short LLM-generated summary of the meeting")
    participants: List[str] = Field(default_factory=list, description="Mentioned participants or attendees")
    topics: List[str] = Field(default_factory=list, description="Meeting topics or themes discussed")
    organizations: List[str] = Field(default_factory=list, description="Organizations mentioned in the meeting")
    decisions: List[MeetingDecision] = Field(default_factory=list, description="Validated decision records")
    action_items: List[ActionItem] = Field(default_factory=list, description="Validated action items")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional ingestion metadata")
    previous_meeting_id: Optional[str] = Field(None, description="Prior meeting in the same series")
    related_meeting_ids: List[str] = Field(default_factory=list, description="Other related meeting records")

    @field_validator("participants", "topics", "organizations", "related_meeting_ids", mode="before")
    @classmethod
    def _normalize_lists(cls, value):
        return _string_list(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value):
        if value is None:
            return {}
        if isinstance(value, dict):
            normalized: Dict[str, Any] = {}
            for key, item in value.items():
                if item is None:
                    continue
                if isinstance(item, (str, int, float, bool, list, dict)):
                    normalized[key] = item
                else:
                    normalized[key] = str(item)
            return normalized
        return {"value": str(value)}

    def to_index_metadata(self) -> Dict[str, Any]:
        """Return a compact metadata payload for the vector store."""
        return {
            "meeting_id": self.meeting_id,
            "transcript_id": self.transcript_id,
            "source_meeting": self.source_meeting,
            "recorded_at": self.recorded_at,
            "input_type": self.input_type,
            "filename": self.filename,
            "summary": self.summary,
            "participant_count": len(self.participants),
            "topic_count": len(self.topics),
            "organization_count": len(self.organizations),
            "decision_count": len(self.decisions),
            "action_item_count": len(self.action_items),
            **self.metadata,
        }


class MeetingSnapshot(BaseModel):
    meeting: MeetingRecord
    graph_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    graph_edges: List[Dict[str, Any]] = Field(default_factory=list)
