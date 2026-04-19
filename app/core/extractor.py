from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from app.core.ollama_client import ollama_client
from app.models.decision import DecisionNode
from app.models.meeting import ActionItem, MeetingDecision, MeetingRecord


def _normalize_string_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, dict):
        value = [value]

    items: List[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                items.append(cleaned)
            continue

        if isinstance(item, dict):
            for key in ("name", "topic", "title", "organization", "value"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    items.append(candidate.strip())
                    break
            continue

        text = str(item).strip()
        if text:
            items.append(text)

    return list(dict.fromkeys(items))


class DecisionExtractor:
    async def extract_meeting(
        self,
        transcript: str,
        *,
        meeting_id: str,
        recorded_at: str,
        source_meeting: Optional[str] = None,
        input_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> MeetingRecord:
        """Extract a validated meeting record from a transcript using the local Ollama model."""
        return await asyncio.to_thread(
            self._extract_meeting_sync,
            transcript,
            meeting_id,
            recorded_at,
            source_meeting,
            input_type,
            filename,
        )

    async def extract_decisions(self, transcript: str) -> List[DecisionNode]:
        """Compatibility wrapper that returns only the decision nodes."""
        meeting = await self.extract_meeting(
            transcript,
            meeting_id="transient-meeting",
            recorded_at="1970-01-01T00:00:00+00:00",
        )
        return [DecisionNode(**decision.model_dump()) for decision in meeting.decisions]

    def _extract_meeting_sync(
        self,
        transcript: str,
        meeting_id: str,
        recorded_at: str,
        source_meeting: Optional[str] = None,
        input_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> MeetingRecord:
        prompt = (
            "Analyze the following meeting transcript and extract a structured meeting record. "
            "Return a JSON object with keys: summary, participants, topics, organizations, decisions, action_items. "
            "participants, topics, and organizations should be lists of strings. decisions should be a list of objects with keys: "
            "decision, owner, rationale, status, dependencies, timestamp, source_meeting. "
            "action_items should be a list of objects with keys: task, owner, deadline, status. "
            "If a value is unknown, use null. If there are no participants, decisions, or action items, use empty lists. "
            "Do not wrap the response in markdown fences.\n\n"
            f"Transcript:\n{transcript}"
        )

        data = ollama_client.chat_json(prompt, max_tokens=4096)

        if isinstance(data, dict) and isinstance(data.get("meeting"), dict):
            data = data["meeting"]

        if not isinstance(data, dict):
            if isinstance(data, list):
                data = {"decisions": data}
            else:
                raise ValueError("Ollama response did not contain a JSON meeting object.")

        decisions_payload = data.get("decisions") or data.get("decision_items") or []
        if isinstance(decisions_payload, dict):
            decisions_payload = [decisions_payload]

        action_items_payload = data.get("action_items") or data.get("actions") or []
        if isinstance(action_items_payload, dict):
            action_items_payload = [action_items_payload]

        decisions: List[MeetingDecision] = []
        for index, item in enumerate(decisions_payload):
            if not isinstance(item, dict):
                if not isinstance(item, str):
                    continue
                item = {"decision": item}

            decision_payload = dict(item)
            decision_payload.setdefault("meeting_id", meeting_id)
            decision_payload.setdefault("source_meeting", source_meeting)
            decision_payload.setdefault("decision_id", f"{meeting_id}-decision-{index + 1}")
            try:
                decision_payload["version"] = int(decision_payload.get("version") or index + 1)
            except (TypeError, ValueError):
                decision_payload["version"] = index + 1
            decisions.append(MeetingDecision(**decision_payload))

        action_items: List[ActionItem] = []
        for item in action_items_payload:
            if isinstance(item, str):
                item = {"task": item}
            if isinstance(item, dict):
                action_payload = dict(item)
                action_payload.setdefault("meeting_id", meeting_id)
                action_items.append(ActionItem(**action_payload))

        summary = data.get("summary") or data.get("meeting_summary")
        participants = _normalize_string_list(data.get("participants") or data.get("attendees") or data.get("people"))
        topics = _normalize_string_list(data.get("topics") or data.get("themes") or data.get("subjects"))
        organizations = _normalize_string_list(data.get("organizations") or data.get("companies") or data.get("orgs"))

        metadata = {
            "transcript_length": len(transcript),
            "source_meeting": source_meeting,
            "input_type": input_type,
            "filename": filename,
        }

        return MeetingRecord(
            meeting_id=meeting_id,
            transcript_id=meeting_id,
            source_meeting=source_meeting,
            recorded_at=recorded_at,
            input_type=input_type,
            filename=filename,
            transcript=transcript,
            summary=summary,
            participants=participants,
            topics=topics,
            organizations=organizations,
            decisions=decisions,
            action_items=action_items,
            metadata=metadata,
        )

extractor_service = DecisionExtractor()
