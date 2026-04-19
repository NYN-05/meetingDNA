import asyncio
from app.core.ollama_client import ollama_client
from app.models.decision import DecisionNode
from typing import Any, List

class DecisionExtractor:
    async def extract_decisions(self, transcript: str) -> List[DecisionNode]:
        """Extracts structured decision nodes from a transcript using the local Ollama model."""
        return await asyncio.to_thread(self._extract_decisions_sync, transcript)

    def _extract_decisions_sync(self, transcript: str) -> List[DecisionNode]:
        prompt = (
            "Analyze the following meeting transcript and extract all key decisions. "
            "For each decision, identify: the decision itself, the owner, the rationale, "
            "the current status, any dependencies on other decisions mentioned, the source meeting, "
            "and the timestamp when the decision was made or discussed if it is available. "
            "If owner, rationale, status, source meeting, or timestamp is unknown, use null. "
            "If there are no dependencies, use an empty list. "
            "Return the result as a JSON list of objects with keys: "
            "'decision', 'owner', 'rationale', 'status', 'dependencies', 'timestamp', 'source_meeting'.\n\n"
            f"Transcript:\n{transcript}"
        )

        data = ollama_client.chat_json(prompt, max_tokens=4096)

        if isinstance(data, dict) and "decisions" in data:
            data = data["decisions"]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("Ollama response did not contain a JSON list of decisions.")

        decisions: List[DecisionNode] = []
        for item in data:
            if isinstance(item, dict):
                decisions.append(DecisionNode(**item))

        return decisions

extractor_service = DecisionExtractor()
