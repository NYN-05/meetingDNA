from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from app.core.graph_manager import graph_manager
from app.core.ollama_client import ollama_client
from app.core.vector_store import vector_store


logger = logging.getLogger(__name__)

DEPENDENCY_KEYWORDS = (
    "depend",
    "dependency",
    "impact",
    "downstream",
    "upstream",
    "what broke",
    "broke when",
    "reverse",
    "reversed",
    "rollback",
    "stale",
)
REVERSAL_STATUS_KEYWORDS = ("reversed", "superseded", "deprecated", "cancelled", "rolled back")


class HybridRetrievalService:
    @staticmethod
    def _extract_json_payload(content: str) -> Any:
        return ollama_client._extract_json_payload(content)

    @staticmethod
    async def _call_ollama(prompt: str, max_tokens: int, json_mode: bool = False) -> str:
        return await asyncio.to_thread(ollama_client.chat, prompt, max_tokens, 0.0, json_mode)

    @staticmethod
    def _looks_like_dependency_query(q: str) -> bool:
        lowered = q.lower()
        return any(keyword in lowered for keyword in DEPENDENCY_KEYWORDS)

    @staticmethod
    def _guess_decision_phrase(q: str, classification: Dict[str, Any]) -> Optional[str]:
        for key in ("decision_phrase", "decision", "target", "decision_name"):
            value = classification.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().strip("'\" ")

        quoted = re.search(r"['\"]([^'\"]+)['\"]", q)
        if quoted:
            return quoted.group(1).strip()

        patterns = [
            r"(?:depend(?:s|ed)? on|about|for|of|around|impact of|what broke when|reversed)\s+(?:the\s+)?(.+?)(?:\?|$)",
            r"(?:decision|migration|change|initiative)\s+(.+?)(?:\?|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                phrase = match.group(1).strip("'\" .")
                if phrase:
                    return phrase

        return None

    @staticmethod
    def _select_candidate(candidates: List[Dict[str, Any]], phrase: Optional[str]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        if phrase:
            normalized_phrase = phrase.lower()
            for candidate in candidates:
                if candidate.get("decision", "").lower() == normalized_phrase:
                    return candidate
            for candidate in candidates:
                if normalized_phrase in candidate.get("decision", "").lower():
                    return candidate
        return candidates[0]

    @staticmethod
    def _decision_is_reversed(status: Optional[str]) -> bool:
        if not status:
            return False
        lowered = status.lower()
        return any(keyword in lowered for keyword in REVERSAL_STATUS_KEYWORDS)

    @staticmethod
    def _format_semantic_context(results: Dict[str, Any]) -> Dict[str, Any]:
        entries: List[Dict[str, Any]] = []
        context_lines: List[str] = []
        meeting_ids: List[str] = []

        def append_result_set(result_set: Dict[str, Any], source_type: str):
            if not result_set:
                return

            documents = result_set.get("documents") or []
            metadatas = result_set.get("metadatas") or []
            ids = result_set.get("ids") or []

            if documents and isinstance(documents[0], list):
                documents = documents[0]
            if metadatas and isinstance(metadatas[0], list):
                metadatas = metadatas[0]
            if ids and isinstance(ids[0], list):
                ids = ids[0]

            for index, document in enumerate(documents[:5]):
                metadata = metadatas[index] if index < len(metadatas) else {}
                record_id = ids[index] if index < len(ids) else None
                meeting_id = metadata.get("meeting_id") or record_id
                if meeting_id and meeting_id not in meeting_ids:
                    meeting_ids.append(meeting_id)

                snippet = document.replace("\n", " ").strip()
                if len(snippet) > 450:
                    snippet = f"{snippet[:447]}..."

                context_lines.append(
                    "[{}] {} | type={} | meeting_id={} | source={} | summary={} | timestamp={} | filename={}".format(
                        len(context_lines) + 1,
                        snippet,
                        source_type,
                        meeting_id,
                        metadata.get("source_meeting"),
                        metadata.get("summary"),
                        metadata.get("recorded_at"),
                        metadata.get("filename"),
                    )
                )
                entries.append(
                    {
                        "type": source_type,
                        "record_id": record_id,
                        "meeting_id": meeting_id,
                        "document": document,
                        "source_meeting": metadata.get("source_meeting"),
                        "summary": metadata.get("summary"),
                        "timestamp": metadata.get("recorded_at"),
                        "filename": metadata.get("filename"),
                        "decision_count": metadata.get("decision_count"),
                        "action_item_count": metadata.get("action_item_count"),
                    }
                )

        if "meeting_results" in results or "decision_results" in results:
            append_result_set(results.get("meeting_results"), "meeting")
            append_result_set(results.get("decision_results"), "decision")
        else:
            append_result_set(results, "meeting")

        return {"context": "\n".join(context_lines), "sources": entries, "meeting_ids": meeting_ids}

    @staticmethod
    def _format_meeting_context(meeting_contexts: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for context in meeting_contexts:
            if not context:
                continue

            participants = context.get("participants") or []
            participant_names = []
            for participant in participants:
                if isinstance(participant, dict):
                    participant_names.append(participant.get("name") or "")
                else:
                    participant_names.append(str(participant))

            lines.append(f"Meeting {context.get('meeting_id')}: {context.get('summary') or 'No summary available.'}")
            if participant_names:
                lines.append(f"Participants: {', '.join(name for name in participant_names if name)}")

            topics = context.get("topics") or []
            if topics:
                lines.append(f"Topics: {', '.join(topic for topic in topics if topic)}")

            organizations = context.get("organizations") or []
            if organizations:
                lines.append(f"Organizations: {', '.join(org for org in organizations if org)}")

            decisions = context.get("decisions") or []
            if decisions:
                lines.append("Decisions:")
                for decision in decisions:
                    if isinstance(decision, dict):
                        lines.append(
                            f"- {decision.get('decision')} | status={decision.get('status')} | owner={decision.get('owner')} | version={decision.get('version')} | source={decision.get('source_meeting') or context.get('source_meeting')}"
                        )

            action_items = context.get("action_items") or []
            if action_items:
                lines.append("Action items:")
                for item in action_items:
                    if isinstance(item, dict):
                        lines.append(
                            f"- {item.get('task')} | owner={item.get('owner')} | deadline={item.get('deadline')} | status={item.get('status')}"
                        )

            lines.append("")

        return "\n".join(lines).strip()

    async def classify_query(self, q: str) -> Dict[str, Any]:
        prompt = (
            "Classify the user query for a decision intelligence system. Return a JSON object with keys: "
            "intent (dependency or semantic), relationship (upstream, downstream, or unknown), decision_phrase (string or null), "
            "needs_history (boolean), and confidence (number between 0 and 1). "
            "Use dependency intent when the user asks what depends on something, what something depends on, what broke, reversal impact, or stale downstream effects. "
            f"Query: {q}"
        )

        try:
            content = await self._call_ollama(prompt, max_tokens=256, json_mode=True)
            payload = self._extract_json_payload(content)
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

        return {
            "intent": "dependency" if self._looks_like_dependency_query(q) else "semantic",
            "relationship": "downstream"
            if any(keyword in q.lower() for keyword in ("depend on", "depends on", "what depends", "what broke", "impact"))
            else "unknown",
            "decision_phrase": None,
            "needs_history": self._looks_like_dependency_query(q),
            "confidence": 0.25,
        }

    def _build_dependency_context(self, q: str, classification: Dict[str, Any]) -> Dict[str, Any]:
        relationship = (classification.get("relationship") or "unknown").lower()
        decision_phrase = self._guess_decision_phrase(q, classification)

        candidates: List[Dict[str, Any]] = []
        if decision_phrase:
            candidates = graph_manager.find_decision_candidates(decision_phrase)
        if not candidates:
            candidates = graph_manager.find_decision_candidates(q)

        resolved_decision = self._select_candidate(candidates, decision_phrase)
        if not resolved_decision:
            return {"candidates": candidates, "mode": "dependency", "answer_context": "", "resolved_decision": None}

        decision_name = resolved_decision.get("decision")
        decision_details = graph_manager.get_decision_details(decision_name)
        history = graph_manager.get_decision_history(decision_name)
        upstream = graph_manager.get_upstream_dependencies(decision_name)
        downstream = graph_manager.get_dependents(decision_name)

        stale_impacts: List[Dict[str, Any]] = []
        if self._decision_is_reversed(decision_details.get("status")):
            stale_impacts = downstream

        meeting_contexts = []
        meeting_id = decision_details.get("meeting_id") or resolved_decision.get("meeting_id")
        if meeting_id:
            meeting_context = graph_manager.get_meeting_context(meeting_id)
            if meeting_context:
                meeting_contexts.append(meeting_context)

        graph_lines = [
            f"Decision: {decision_details.get('decision')}",
            f"Status: {decision_details.get('status')}",
        ]
        if decision_details.get("owner"):
            graph_lines.append(f"Owner: {decision_details.get('owner')}")
        if decision_details.get("source_meeting"):
            graph_lines.append(f"Source meeting: {decision_details.get('source_meeting')}")
        if decision_details.get("timestamp"):
            graph_lines.append(f"Timestamp: {decision_details.get('timestamp')}")

        if history:
            graph_lines.append("Revision history:")
            for item in history[-5:]:
                graph_lines.append(
                    f"- v{item.get('version')} | {item.get('recorded_at')} | status={item.get('status')} | source={item.get('source_meeting')}"
                )

        if upstream:
            graph_lines.append("Upstream dependencies:")
            for item in upstream:
                graph_lines.append(
                    f"- {item.get('decision')} | status={item.get('status')} | owner={item.get('owner')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
                )

        if downstream:
            graph_lines.append("Downstream decisions:")
            for item in downstream:
                graph_lines.append(
                    f"- {item.get('decision')} | status={item.get('status')} | owner={item.get('owner')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
                )

        if stale_impacts:
            graph_lines.append("Potential stale downstream decisions:")
            for item in stale_impacts:
                graph_lines.append(
                    f"- {item.get('decision')} | status={item.get('status')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
                )

        if meeting_contexts:
            graph_lines.append("")
            graph_lines.append("Related meeting context:")
            graph_lines.append(self._format_meeting_context(meeting_contexts))

        answer_context = "\n".join(graph_lines).strip()

        return {
            "mode": "dependency",
            "relationship": relationship,
            "resolved_decision": resolved_decision,
            "candidates": candidates,
            "decision_details": decision_details,
            "history": history,
            "upstream_dependencies": upstream,
            "downstream_decisions": downstream,
            "stale_impacts": stale_impacts,
            "meeting_contexts": meeting_contexts,
            "answer_context": answer_context,
        }

    def _build_semantic_context(self, q: str) -> Dict[str, Any]:
        semantic_results = vector_store.semantic_search(q, n_results=5)
        semantic_context = self._format_semantic_context(semantic_results)

        meeting_contexts: List[Dict[str, Any]] = []
        for meeting_id in semantic_context.get("meeting_ids", []):
            meeting_context = graph_manager.get_meeting_context(meeting_id)
            if meeting_context and meeting_context not in meeting_contexts:
                meeting_contexts.append(meeting_context)

        graph_context = self._format_meeting_context(meeting_contexts)

        lines = ["Transcript context:", semantic_context.get("context", "")]
        if graph_context:
            lines.extend(["", "Related meeting context:", graph_context])

        return {
            "mode": "hybrid-semantic",
            "semantic_results": semantic_results,
            "semantic_context": semantic_context,
            "meeting_contexts": meeting_contexts,
            "candidates": graph_manager.find_decision_candidates(q),
            "answer_context": "\n".join(line for line in lines if line is not None).strip(),
        }

    async def answer(self, q: str) -> Dict[str, Any]:
        classification = await self.classify_query(q)
        intent = (classification.get("intent") or "semantic").lower()
        use_dependency_route = intent == "dependency" or self._looks_like_dependency_query(q)

        if use_dependency_route:
            dependency_payload = self._build_dependency_context(q, classification)
            if dependency_payload.get("resolved_decision"):
                prompt = (
                    "Answer the user using only the supplied graph and meeting context. "
                    "If source meeting or timestamp are present, cite them explicitly. "
                    "If the decision has been reversed, superseded, cancelled, or rolled back, warn that downstream decisions may now be stale.\n\n"
                    f"Question: {q}\n\n"
                    f"Classification: {classification}\n\n"
                    f"Context:\n{dependency_payload.get('answer_context', '')}"
                )
                response_text = await self._call_ollama(prompt, max_tokens=1024)

                sources = [
                    {
                        "type": "decision",
                        "decision": dependency_payload.get("decision_details", {}).get("decision"),
                        "source_meeting": dependency_payload.get("decision_details", {}).get("source_meeting"),
                        "meeting_id": dependency_payload.get("decision_details", {}).get("meeting_id"),
                        "timestamp": dependency_payload.get("decision_details", {}).get("timestamp"),
                        "status": dependency_payload.get("decision_details", {}).get("status"),
                    }
                ]
                sources.extend(
                    {
                        "type": "history",
                        "decision": item.get("decision"),
                        "source_meeting": item.get("source_meeting"),
                        "meeting_id": item.get("meeting_id"),
                        "timestamp": item.get("timestamp"),
                        "status": item.get("status"),
                        "recorded_at": item.get("recorded_at"),
                        "version": item.get("version"),
                    }
                    for item in dependency_payload.get("history", [])
                )

                return {
                    "mode": dependency_payload.get("mode", "dependency"),
                    "answer": response_text,
                    "classification": classification,
                    "decision": dependency_payload.get("resolved_decision", {}).get("decision"),
                    "relationship": dependency_payload.get("relationship"),
                    "candidates": dependency_payload.get("candidates", []),
                    "decision_details": dependency_payload.get("decision_details", {}),
                    "history": dependency_payload.get("history", []),
                    "upstream_dependencies": dependency_payload.get("upstream_dependencies", []),
                    "downstream_decisions": dependency_payload.get("downstream_decisions", []),
                    "stale_impacts": dependency_payload.get("stale_impacts", []),
                    "meeting_contexts": dependency_payload.get("meeting_contexts", []),
                    "sources": sources,
                }

        payload = self._build_semantic_context(q)
        prompt = (
            "Answer the user's question using only the transcript and meeting context. "
            "Cite source meeting and timestamp when available. "
            "If the context is insufficient, say so clearly instead of inventing details.\n\n"
            f"Question: {q}\n\n"
            f"Classification: {classification}\n\n"
            f"Context:\n{payload.get('answer_context', '')}"
        )

        response_text = await self._call_ollama(prompt, max_tokens=1024)

        return {
            "mode": payload.get("mode", "hybrid-semantic"),
            "answer": response_text,
            "classification": classification,
            "sources": payload.get("semantic_context", {}).get("sources", []),
            "meeting_contexts": payload.get("meeting_contexts", []),
            "candidate_decisions": payload.get("candidates", []),
        }


hybrid_retrieval_service = HybridRetrievalService()
