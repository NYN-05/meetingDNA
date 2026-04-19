import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List

from neo4j import GraphDatabase

from app.models.decision import DecisionNode
from app.utils.config import config


class GraphManager:
    def __init__(self):
        self.use_neo4j = config.NEO4J_ENABLED
        self.driver = None
        if self.use_neo4j:
            self.driver = GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USERNAME, config.NEO4J_PASSWORD),
            )
        self.local_store_path = Path("data/decision_graph.json")
        self._local_store_lock = Lock()

    def close(self):
        if self.driver is not None:
            self.driver.close()

    def save_decision(self, node: DecisionNode):
        """Persist a decision locally and optionally sync it to Neo4j."""
        self._save_local_decision(node)

        if not self.use_neo4j or self.driver is None:
            return

        try:
            with self.driver.session() as session:
                session.execute_write(self._create_decision_node, node)
        except Exception:
            pass

    @staticmethod
    def _create_decision_node(tx, node: DecisionNode):
        now = datetime.now(timezone.utc).isoformat()
        revision_id = str(uuid.uuid4())

        query = (
            "MERGE (d:Decision {decision: $decision}) "
            "ON CREATE SET d.created_at = $now "
            "SET d.owner = $owner, d.rationale = $rationale, d.status = $status, "
            "d.source_meeting = $source_meeting, d.timestamp = $timestamp, d.updated_at = $now "
            "WITH d "
            "OPTIONAL MATCH (d)-[existing:DEPENDS_ON]->(:Decision) "
            "DELETE existing "
            "WITH d "
            "CREATE (rev:DecisionRevision {revision_id: $revision_id, decision: $decision, owner: $owner, "
            "rationale: $rationale, status: $status, source_meeting: $source_meeting, timestamp: $timestamp, recorded_at: $now}) "
            "MERGE (d)-[:HAS_REVISION]->(rev) "
            "RETURN d, rev"
        )
        tx.run(
            query,
            decision=node.decision,
            owner=node.owner,
            rationale=node.rationale,
            status=node.status,
            source_meeting=node.source_meeting,
            timestamp=node.timestamp,
            now=now,
            revision_id=revision_id,
        )

        for dep in node.dependencies:
            dep_query = (
                "MERGE (dep:Decision {decision: $dep_name}) "
                "ON CREATE SET dep.created_at = $now "
                "MATCH (d:Decision {decision: $decision}) "
                "MERGE (d)-[:DEPENDS_ON]->(dep)"
            )
            tx.run(dep_query, dep_name=dep, decision=node.decision, now=now)

    def get_dependencies(self, decision_name: str) -> List[Dict]:
        """Finds all decisions that depend on the given decision."""
        return self.get_dependents(decision_name)

    def get_dependents(self, decision_name: str) -> List[Dict]:
        """Finds all downstream decisions that depend on the given decision."""
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    query = (
                        "MATCH (dep:Decision)-[:DEPENDS_ON]->(d:Decision {decision: $name}) "
                        "RETURN dep.decision as decision, dep.status as status, dep.owner as owner, "
                        "dep.rationale as rationale, dep.source_meeting as source_meeting, dep.timestamp as timestamp "
                        "ORDER BY dep.decision"
                    )
                    result = session.run(query, name=decision_name)
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_dependents(decision_name)

    def get_upstream_dependencies(self, decision_name: str) -> List[Dict]:
        """Finds all upstream decisions that the given decision depends on."""
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    query = (
                        "MATCH (d:Decision {decision: $name})-[:DEPENDS_ON]->(dep:Decision) "
                        "RETURN dep.decision as decision, dep.status as status, dep.owner as owner, "
                        "dep.rationale as rationale, dep.source_meeting as source_meeting, dep.timestamp as timestamp "
                        "ORDER BY dep.decision"
                    )
                    result = session.run(query, name=decision_name)
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_upstream_dependencies(decision_name)

    def get_decision_details(self, decision_name: str) -> Dict:
        """Retrieves full details of a specific decision."""
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    query = (
                        "MATCH (d:Decision {decision: $name}) "
                        "RETURN d.decision as decision, d.owner as owner, d.rationale as rationale, "
                        "d.status as status, d.source_meeting as source_meeting, d.timestamp as timestamp, "
                        "d.created_at as created_at, d.updated_at as updated_at"
                    )
                    result = session.run(query, name=decision_name)
                    record = result.single()
                    return record.data() if record else {}
            except Exception:
                pass
        return self._get_local_decision_details(decision_name)

    def get_decision_history(self, decision_name: str) -> List[Dict]:
        """Retrieves the revision history for a decision."""
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    query = (
                        "MATCH (d:Decision {decision: $name})-[:HAS_REVISION]->(rev:DecisionRevision) "
                        "RETURN rev.revision_id as revision_id, rev.decision as decision, rev.owner as owner, "
                        "rev.rationale as rationale, rev.status as status, rev.source_meeting as source_meeting, "
                        "rev.timestamp as timestamp, rev.recorded_at as recorded_at "
                        "ORDER BY rev.recorded_at ASC"
                    )
                    result = session.run(query, name=decision_name)
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_decision_history(decision_name)

    def find_decision_candidates(self, search_term: str, limit: int = 5) -> List[Dict]:
        """Finds likely decision names from a natural language search term."""
        search_term = (search_term or "").strip()
        if not search_term:
            return []

        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    query = (
                        "MATCH (d:Decision) "
                        "WHERE toLower(d.decision) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.owner, '')) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.rationale, '')) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.source_meeting, '')) CONTAINS toLower($term) "
                        "RETURN d.decision as decision, d.owner as owner, d.status as status, "
                        "d.rationale as rationale, d.source_meeting as source_meeting, d.timestamp as timestamp "
                        "ORDER BY CASE WHEN toLower(d.decision) = toLower($term) THEN 0 "
                        "WHEN toLower(d.decision) CONTAINS toLower($term) THEN 1 ELSE 2 END, size(d.decision) "
                        "LIMIT $limit"
                    )
                    result = session.run(query, term=search_term, limit=limit)
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._find_local_decision_candidates(search_term, limit)

    def list_graph_data(self) -> Dict[str, List[Dict]]:
        """Returns nodes, edges, and history for visualization."""
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    nodes = session.run("MATCH (n:Decision) RETURN n").data()
                    edges = session.run(
                        "MATCH (a:Decision)-[r:DEPENDS_ON]->(b:Decision) RETURN a.decision as source, b.decision as target"
                    ).data()
                    history = session.run(
                        "MATCH (d:Decision)-[:HAS_REVISION]->(rev:DecisionRevision) "
                        "RETURN d.decision as decision, rev.status as status, rev.source_meeting as source_meeting, "
                        "rev.timestamp as timestamp, rev.recorded_at as recorded_at ORDER BY rev.recorded_at DESC"
                    ).data()
                    return {"nodes": nodes, "edges": edges, "history": history}
            except Exception:
                pass
        return self._get_local_graph_data()

    def _save_local_decision(self, node: DecisionNode):
        now = datetime.now(timezone.utc).isoformat()
        revision_id = str(uuid.uuid4())

        with self._local_store_lock:
            store = self._load_local_store()
            decisions = store.setdefault("decisions", {})
            record = decisions.get(node.decision) or {
                "decision": node.decision,
                "created_at": now,
                "history": [],
            }

            record["decision"] = node.decision
            record["owner"] = node.owner
            record["rationale"] = node.rationale
            record["status"] = node.status
            record["source_meeting"] = node.source_meeting
            record["timestamp"] = node.timestamp
            record["updated_at"] = now
            record["dependencies"] = list(dict.fromkeys(node.dependencies or []))

            history = record.setdefault("history", [])
            history.append(
                {
                    "revision_id": revision_id,
                    "decision": node.decision,
                    "owner": node.owner,
                    "rationale": node.rationale,
                    "status": node.status,
                    "source_meeting": node.source_meeting,
                    "timestamp": node.timestamp,
                    "recorded_at": now,
                }
            )

            decisions[node.decision] = record
            self._write_local_store(store)

    def _load_local_store(self) -> Dict[str, Any]:
        if not self.local_store_path.exists():
            return {"decisions": {}}

        try:
            with self.local_store_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict):
                    payload.setdefault("decisions", {})
                    return payload
        except Exception:
            pass

        return {"decisions": {}}

    def _write_local_store(self, payload: Dict[str, Any]):
        self.local_store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.local_store_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path.replace(self.local_store_path)

    def _get_local_decision_details(self, decision_name: str) -> Dict:
        record = self._load_local_store().get("decisions", {}).get(decision_name)
        if not record:
            return {}
        return {
            "decision": record.get("decision"),
            "owner": record.get("owner"),
            "rationale": record.get("rationale"),
            "status": record.get("status"),
            "source_meeting": record.get("source_meeting"),
            "timestamp": record.get("timestamp"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    def _get_local_decision_history(self, decision_name: str) -> List[Dict]:
        record = self._load_local_store().get("decisions", {}).get(decision_name)
        if not record:
            return []
        return list(record.get("history", []))

    def _get_local_upstream_dependencies(self, decision_name: str) -> List[Dict]:
        record = self._load_local_store().get("decisions", {}).get(decision_name)
        if not record:
            return []

        decisions = self._load_local_store().get("decisions", {})
        entries: List[Dict] = []
        for dependency_name in record.get("dependencies", []):
            dependency = decisions.get(dependency_name) or {}
            entries.append(
                {
                    "decision": dependency.get("decision", dependency_name),
                    "status": dependency.get("status"),
                    "owner": dependency.get("owner"),
                    "rationale": dependency.get("rationale"),
                    "source_meeting": dependency.get("source_meeting"),
                    "timestamp": dependency.get("timestamp"),
                }
            )
        return entries

    def _get_local_dependents(self, decision_name: str) -> List[Dict]:
        decisions = self._load_local_store().get("decisions", {})
        entries: List[Dict] = []
        for record in decisions.values():
            if decision_name in (record.get("dependencies") or []):
                entries.append(
                    {
                        "decision": record.get("decision"),
                        "status": record.get("status"),
                        "owner": record.get("owner"),
                        "rationale": record.get("rationale"),
                        "source_meeting": record.get("source_meeting"),
                        "timestamp": record.get("timestamp"),
                    }
                )
        entries.sort(key=lambda item: item.get("decision") or "")
        return entries

    def _find_local_decision_candidates(self, search_term: str, limit: int = 5) -> List[Dict]:
        lowered_term = search_term.lower()
        candidates: List[Dict] = []
        for record in self._load_local_store().get("decisions", {}).values():
            haystack = " ".join(
                str(record.get(field) or "")
                for field in ("decision", "owner", "rationale", "source_meeting")
            ).lower()
            if lowered_term in haystack:
                candidates.append(
                    {
                        "decision": record.get("decision"),
                        "owner": record.get("owner"),
                        "status": record.get("status"),
                        "rationale": record.get("rationale"),
                        "source_meeting": record.get("source_meeting"),
                        "timestamp": record.get("timestamp"),
                    }
                )

        candidates.sort(
            key=lambda item: (
                0 if (item.get("decision") or "").lower() == lowered_term else 1,
                len(item.get("decision") or ""),
            )
        )
        return candidates[:limit]

    def _get_local_graph_data(self) -> Dict[str, List[Dict]]:
        decisions = self._load_local_store().get("decisions", {})
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []

        for record in decisions.values():
            nodes.append({"n": {k: v for k, v in record.items() if k != "history"}})
            for dependency in record.get("dependencies", []):
                edges.append({"source": record.get("decision"), "target": dependency})
            for revision in record.get("history", []):
                history.append(
                    {
                        "decision": revision.get("decision"),
                        "status": revision.get("status"),
                        "source_meeting": revision.get("source_meeting"),
                        "timestamp": revision.get("timestamp"),
                        "recorded_at": revision.get("recorded_at"),
                    }
                )

        history.sort(key=lambda item: item.get("recorded_at") or "", reverse=True)
        return {"nodes": nodes, "edges": edges, "history": history}


graph_manager = GraphManager()
