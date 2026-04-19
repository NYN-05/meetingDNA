from pydantic import BaseModel, Field, field_validator
from typing import List, Optional

class DecisionNode(BaseModel):
    decision: str = Field(..., description="The actual choice made")
    owner: Optional[str] = Field(None, description="Accountable individual or team")
    rationale: Optional[str] = Field(None, description="Why this choice was made")
    status: Optional[str] = Field(None, description="Current state of the decision (e.g., Active, Reversed, Completed)")
    dependencies: List[str] = Field(default_factory=list, description="List of other decisions this depends on")
    timestamp: Optional[str] = Field(None, description="When the decision was discussed or recorded")
    source_meeting: Optional[str] = Field(None, description="Meeting or transcript source for the decision")
    meeting_id: Optional[str] = Field(None, description="Stable meeting identifier associated with the decision")
    decision_id: Optional[str] = Field(None, description="Stable revision identifier for the decision")
    version: int = Field(1, description="Monotonic version number for temporal tracking")
    supersedes_decision_id: Optional[str] = Field(None, description="Previous decision revision replaced by this one")

    @field_validator("dependencies", mode="before")
    @classmethod
    def _normalize_dependencies(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class DecisionRevision(BaseModel):
    revision_id: str = Field(..., description="Unique identifier for a stored revision")
    decision: str = Field(..., description="Decision name for the revision")
    decision_id: Optional[str] = Field(None, description="Stable revision identifier for the decision")
    meeting_id: Optional[str] = Field(None, description="Meeting identifier associated with the revision")
    version: int = Field(1, description="Monotonic version number for the revision")
    owner: Optional[str] = Field(None, description="Owner captured at the time of the revision")
    rationale: Optional[str] = Field(None, description="Rationale captured at the time of the revision")
    status: Optional[str] = Field(None, description="Status captured at the time of the revision")
    source_meeting: Optional[str] = Field(None, description="Meeting or transcript source for the revision")
    timestamp: Optional[str] = Field(None, description="Timestamp attached to the decision")
    recorded_at: Optional[str] = Field(None, description="When the revision was persisted")
    previous_revision_id: Optional[str] = Field(None, description="Previous revision linked by the temporal graph")
