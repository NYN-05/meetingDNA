from fastapi import APIRouter, Query
from app.core.graph_manager import graph_manager
from app.core.vector_store import vector_store
import asyncio
import re
from typing import Any, Dict, List, Optional
from app.core.ollama_client import ollama_client

router = APIRouter()

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


def _extract_json_payload(content: str) -> Any:
    return ollama_client._extract_json_payload(content)


async def _call_ollama(prompt: str, max_tokens: int, json_mode: bool = False) -> str:
    return await asyncio.to_thread(ollama_client.chat, prompt, max_tokens, 0.0, json_mode)


def _looks_like_dependency_query(q: str) -> bool:
    lowered = q.lower()
    return any(keyword in lowered for keyword in DEPENDENCY_KEYWORDS)


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


def _format_dependency_context(decision: Dict[str, Any], history: List[Dict[str, Any]], upstream: List[Dict[str, Any]], downstream: List[Dict[str, Any]], stale_impacts: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append(f"Decision: {decision.get('decision')}")
    lines.append(f"Status: {decision.get('status')}")
    if decision.get("owner"):
        lines.append(f"Owner: {decision.get('owner')}")
    if decision.get("source_meeting"):
        lines.append(f"Source meeting: {decision.get('source_meeting')}")
    if decision.get("timestamp"):
        lines.append(f"Timestamp: {decision.get('timestamp')}")

    if history:
        lines.append("Revision history:")
        for item in history[-5:]:
            lines.append(
                f"- {item.get('recorded_at')} | status={item.get('status')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
            )

    if upstream:
        lines.append("Upstream dependencies:")
        for item in upstream:
            lines.append(
                f"- {item.get('decision')} | status={item.get('status')} | owner={item.get('owner')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
            )

    if downstream:
        lines.append("Downstream decisions:")
        for item in downstream:
            lines.append(
                f"- {item.get('decision')} | status={item.get('status')} | owner={item.get('owner')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
            )

    if stale_impacts:
        lines.append("Potential stale downstream decisions:")
        for item in stale_impacts:
            lines.append(
                f"- {item.get('decision')} | status={item.get('status')} | source={item.get('source_meeting')} | timestamp={item.get('timestamp')}"
            )

    return "\n".join(lines)


def _format_semantic_context(results: Dict[str, Any]) -> Dict[str, Any]:
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]

    entries: List[Dict[str, Any]] = []
    context_lines: List[str] = []

    for index, document in enumerate(documents[:5]):
        metadata = metadatas[index] if index < len(metadatas) else {}
        snippet = document.replace("\n", " ").strip()
        if len(snippet) > 450:
            snippet = f"{snippet[:447]}..."
        context_lines.append(
            f"[{index + 1}] {snippet} | source={metadata.get('source_meeting')} | timestamp={metadata.get('timestamp')} | filename={metadata.get('filename')}"
        )
        entries.append(
            {
                "document": document,
                "source_meeting": metadata.get("source_meeting"),
                "timestamp": metadata.get("timestamp"),
                "filename": metadata.get("filename"),
            }
        )

    return {"context": "\n".join(context_lines), "sources": entries}


async def _classify_query(q: str) -> Dict[str, Any]:
    prompt = (
        "Classify the user query for a decision intelligence system. Return a JSON object with keys: "
        "intent (dependency or semantic), relationship (upstream, downstream, or unknown), decision_phrase (string or null), "
        "needs_history (boolean), and confidence (number between 0 and 1). "
        "Use dependency intent when the user asks what depends on something, what something depends on, what broke, reversal impact, or stale downstream effects. "
        "Query: "
        f"{q}"
    )

    try:
        content = await _call_ollama(prompt, max_tokens=256, json_mode=True)
        payload = _extract_json_payload(content)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    return {
        "intent": "dependency" if _looks_like_dependency_query(q) else "semantic",
        "relationship": "downstream" if any(keyword in q.lower() for keyword in ("depend on", "depends on", "what depends", "what broke", "impact")) else "unknown",
        "decision_phrase": None,
        "needs_history": _looks_like_dependency_query(q),
        "confidence": 0.25,
    }


def _decision_is_reversed(status: Optional[str]) -> bool:
    if not status:
        return False
    lowered = status.lower()
    return any(keyword in lowered for keyword in REVERSAL_STATUS_KEYWORDS)


@router.get("")
async def query_engine(q: str = Query(...)):
    """NL Query Engine: graph traversal first, semantic search as fallback."""
    classification = await _classify_query(q)
    relationship = (classification.get("relationship") or "unknown").lower()
    intent = (classification.get("intent") or "semantic").lower()
    decision_phrase = _guess_decision_phrase(q, classification)
    use_dependency_route = intent == "dependency" or _looks_like_dependency_query(q)

    candidates = []
    if decision_phrase:
        candidates = graph_manager.find_decision_candidates(decision_phrase)
    if not candidates:
        candidates = graph_manager.find_decision_candidates(q)

    resolved_decision = _select_candidate(candidates, decision_phrase)

    if use_dependency_route and resolved_decision:
        decision_name = resolved_decision.get("decision")
        decision_details = graph_manager.get_decision_details(decision_name)
        history = graph_manager.get_decision_history(decision_name)
        upstream = graph_manager.get_upstream_dependencies(decision_name)
        downstream = graph_manager.get_dependents(decision_name)

        stale_impacts: List[Dict[str, Any]] = []
        if _decision_is_reversed(decision_details.get("status")):
            stale_impacts = downstream

        context = _format_dependency_context(
            decision_details,
            history,
            upstream,
            downstream,
            stale_impacts,
        )

        prompt = (
            "Answer the user using only the supplied graph context. "
            "If source meeting or timestamp are present, cite them explicitly. "
            "If the decision has been reversed, superseded, cancelled, or rolled back, warn that downstream decisions may now be stale.\n\n"
            f"Question: {q}\n\n"
            f"Relationship focus: {relationship}\n\n"
            f"Graph context:\n{context}"
        )

        response_text = await _call_ollama(prompt, max_tokens=1024)
        sources = [
            {
                "type": "decision",
                "decision": decision_details.get("decision"),
                "source_meeting": decision_details.get("source_meeting"),
                "timestamp": decision_details.get("timestamp"),
                "status": decision_details.get("status"),
            }
        ]
        sources.extend(
            {
                "type": "history",
                "decision": item.get("decision"),
                "source_meeting": item.get("source_meeting"),
                "timestamp": item.get("timestamp"),
                "status": item.get("status"),
                "recorded_at": item.get("recorded_at"),
            }
            for item in history
        )

        return {
            "mode": "dependency",
            "answer": response_text,
            "decision": decision_name,
            "relationship": relationship,
            "candidates": candidates,
            "decision_details": decision_details,
            "history": history,
            "upstream_dependencies": upstream,
            "downstream_decisions": downstream,
            "stale_impacts": stale_impacts,
            "sources": sources,
        }

    semantic_results = vector_store.semantic_search(q)
    semantic_context = _format_semantic_context(semantic_results)

    prompt = (
        "Answer the user's question using only the transcript context. "
        "Cite source meeting and timestamp when available. "
        "If the context is insufficient, say so clearly instead of inventing details.\n\n"
        f"Question: {q}\n\n"
        f"Transcript context:\n{semantic_context['context']}"
    )

    response_text = await _call_ollama(prompt, max_tokens=1024)

    return {
        "mode": "semantic",
        "answer": response_text,
        "sources": semantic_context["sources"],
        "candidate_decisions": candidates,
    }
