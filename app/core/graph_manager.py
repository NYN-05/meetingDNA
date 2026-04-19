from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase

from app.models.decision import DecisionNode
from app.models.meeting import MeetingRecord
from app.utils.config import config


REVERSAL_STATUS_KEYWORDS = ("reversed", "superseded", "deprecated", "cancelled", "rolled back")


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
        with self._local_store_lock:
            store = self._load_local_store()
            self._save_decision_record(store, self._decision_payload(node))
            self._write_local_store(store)

        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    session.execute_write(self._sync_decision_tx, self._decision_payload(node))
            except Exception:
                pass

    def save_meeting(self, meeting: MeetingRecord):
        with self._local_store_lock:
            store = self._load_local_store()
            self._save_meeting_record(store, meeting)
            self._write_local_store(store)

        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    session.execute_write(self._sync_meeting_tx, meeting)
            except Exception:
                pass

    @staticmethod
    def _sync_decision_tx(tx, node_payload: Dict[str, Any]):
        GraphManager._create_decision_tx(tx, node_payload, datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _sync_meeting_tx(tx, meeting: MeetingRecord):
        GraphManager._create_meeting_graph(tx, meeting)

    @staticmethod
    def _decision_payload(node: Any) -> Dict[str, Any]:
        version = getattr(node, "version", None)
        try:
            version = int(version) if version is not None else None
        except (TypeError, ValueError):
            version = None

        dependencies = getattr(node, "dependencies", []) or []
        if isinstance(dependencies, str):
            dependencies = [dependencies]

        topics = getattr(node, "topics", []) or []
        organizations = getattr(node, "organizations", []) or []

        return {
            "decision": getattr(node, "decision", None),
            "owner": getattr(node, "owner", None),
            "rationale": getattr(node, "rationale", None),
            "status": getattr(node, "status", None),
            "dependencies": list(dict.fromkeys(dependencies)),
            "timestamp": getattr(node, "timestamp", None),
            "source_meeting": getattr(node, "source_meeting", None),
            "meeting_id": getattr(node, "meeting_id", None),
            "decision_id": getattr(node, "decision_id", None),
            "version": version,
            "supersedes_decision_id": getattr(node, "supersedes_decision_id", None),
            "topics": list(dict.fromkeys(topics)),
            "organizations": list(dict.fromkeys(organizations)),
        }

    @staticmethod
    def _decision_is_reversed(status: Optional[str]) -> bool:
        if not status:
            return False
        lowered = status.lower()
        return any(keyword in lowered for keyword in REVERSAL_STATUS_KEYWORDS)

    def _load_local_store(self) -> Dict[str, Any]:
        if not self.local_store_path.exists():
            return {
                "meetings": {},
                "decisions": {},
                "people": {},
                "action_items": {},
                "topics": {},
                "organizations": {},
            }

        try:
            with self.local_store_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict):
                    payload.setdefault("meetings", {})
                    payload.setdefault("decisions", {})
                    payload.setdefault("people", {})
                    payload.setdefault("action_items", {})
                    payload.setdefault("topics", {})
                    payload.setdefault("organizations", {})
                    return payload
        except Exception:
            pass

        return {
            "meetings": {},
            "decisions": {},
            "people": {},
            "action_items": {},
            "topics": {},
            "organizations": {},
        }

    def _write_local_store(self, payload: Dict[str, Any]):
        self.local_store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.local_store_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path.replace(self.local_store_path)

    @staticmethod
    def _create_meeting_graph(tx, meeting: MeetingRecord):
        now = datetime.now(timezone.utc).isoformat()
        meeting_id = meeting.meeting_id

        tx.run(
            "MERGE (m:Meeting {meeting_id: $meeting_id}) "
            "ON CREATE SET m.created_at = $now "
            "SET m.transcript_id = $transcript_id, m.source_meeting = $source_meeting, m.recorded_at = $recorded_at, "
            "m.input_type = $input_type, m.filename = $filename, m.summary = $summary, m.topics = $topics, "
            "m.organizations = $organizations, m.transcript_length = $transcript_length, m.updated_at = $now",
            meeting_id=meeting_id,
            transcript_id=meeting.transcript_id or meeting_id,
            source_meeting=meeting.source_meeting,
            recorded_at=meeting.recorded_at,
            input_type=meeting.input_type,
            filename=meeting.filename,
            summary=meeting.summary,
            topics=list(dict.fromkeys(meeting.topics or [])),
            organizations=list(dict.fromkeys(meeting.organizations or [])),
            transcript_length=len(meeting.transcript or ""),
            now=now,
        )

        for participant in list(dict.fromkeys(meeting.participants or [])):
            if not participant:
                continue
            tx.run(
                "MERGE (p:Person {name: $name}) "
                "WITH p "
                "MATCH (m:Meeting {meeting_id: $meeting_id}) "
                "MERGE (p)-[:ATTENDED]->(m)",
                name=participant,
                meeting_id=meeting_id,
            )

        for topic in list(dict.fromkeys(meeting.topics or [])):
            if not topic:
                continue
            tx.run(
                "MERGE (t:Topic {name: $topic}) "
                "WITH t "
                "MATCH (m:Meeting {meeting_id: $meeting_id}) "
                "MERGE (m)-[:DISCUSSED]->(t)",
                topic=topic,
                meeting_id=meeting_id,
            )

        for organization in list(dict.fromkeys(meeting.organizations or [])):
            if not organization:
                continue
            tx.run(
                "MERGE (o:Organization {name: $organization}) "
                "WITH o "
                "MATCH (m:Meeting {meeting_id: $meeting_id}) "
                "MERGE (m)-[:MENTIONED]->(o)",
                organization=organization,
                meeting_id=meeting_id,
            )

        for index, action_item in enumerate(meeting.action_items or []):
            action_item_id = action_item.action_item_id or f"{meeting_id}:action:{index + 1}"
            tx.run(
                "MERGE (a:ActionItem {action_item_id: $action_item_id}) "
                "SET a.task = $task, a.owner = $owner, a.deadline = $deadline, a.status = $status, "
                "a.meeting_id = $meeting_id, a.updated_at = $now "
                "WITH a "
                "MATCH (m:Meeting {meeting_id: $meeting_id}) "
                "MERGE (m)-[:HAS_ACTION_ITEM]->(a)",
                action_item_id=action_item_id,
                task=action_item.task,
                owner=action_item.owner,
                deadline=action_item.deadline,
                status=action_item.status,
                meeting_id=meeting_id,
                now=now,
            )
            if action_item.owner:
                tx.run(
                    "MERGE (p:Person {name: $name}) "
                    "WITH p "
                    "MATCH (a:ActionItem {action_item_id: $action_item_id}) "
                    "MERGE (p)-[:ASSIGNED_TO]->(a)",
                    name=action_item.owner,
                    action_item_id=action_item_id,
                )

        for decision in meeting.decisions or []:
            decision_payload = GraphManager._decision_payload(decision)
            decision_payload["meeting_id"] = decision_payload.get("meeting_id") or meeting_id
            decision_payload["topics"] = list(dict.fromkeys(meeting.topics or []))
            decision_payload["organizations"] = list(dict.fromkeys(meeting.organizations or []))
            GraphManager._create_decision_tx(tx, decision_payload, now)

    @staticmethod
    def _create_decision_tx(tx, decision: Dict[str, Any], now: str):
        decision_name = decision.get("decision")
        if not decision_name:
            return

        meeting_id = decision.get("meeting_id") or decision.get("source_meeting")
        revision_id = decision.get("decision_id") or str(uuid.uuid4())
        previous_revision_record = tx.run(
            "MATCH (d:Decision {decision: $decision})-[:HAS_REVISION]->(rev:DecisionRevision) "
            "RETURN rev.revision_id AS revision_id ORDER BY rev.recorded_at DESC LIMIT 1",
            decision=decision_name,
        ).single()
        previous_revision_id = previous_revision_record["revision_id"] if previous_revision_record else None

        tx.run(
            "MERGE (d:Decision {decision: $decision}) "
            "ON CREATE SET d.created_at = $now "
            "SET d.owner = $owner, d.rationale = $rationale, d.status = $status, d.source_meeting = $source_meeting, "
            "d.timestamp = $timestamp, d.meeting_id = $meeting_id, d.version = $version, d.topics = $topics, "
            "d.organizations = $organizations, d.updated_at = $now "
            "WITH d "
            "FOREACH (dep_name IN $dependencies | "
            "  MERGE (dep:Decision {decision: dep_name}) "
            "  ON CREATE SET dep.created_at = $now "
            "  MERGE (d)-[:DEPENDS_ON]->(dep) "
            "  MERGE (d)-[:RELATED_TO]->(dep))",
            decision=decision_name,
            owner=decision.get("owner"),
            rationale=decision.get("rationale"),
            status=decision.get("status"),
            source_meeting=decision.get("source_meeting"),
            timestamp=decision.get("timestamp"),
            meeting_id=meeting_id,
            version=decision.get("version") or 1,
            dependencies=list(dict.fromkeys(decision.get("dependencies") or [])),
            topics=list(dict.fromkeys(decision.get("topics") or [])),
            organizations=list(dict.fromkeys(decision.get("organizations") or [])),
            now=now,
        )

        tx.run(
            "MATCH (m:Meeting {meeting_id: $meeting_id}) "
            "MATCH (d:Decision {decision: $decision}) "
            "MERGE (m)-[:MADE_DECISION]->(d) "
            "MERGE (m)-[:HAS_DECISION]->(d)",
            meeting_id=meeting_id,
            decision=decision_name,
        )

        for topic in list(dict.fromkeys(decision.get("topics") or [])):
            tx.run(
                "MERGE (t:Topic {name: $topic}) "
                "WITH t "
                "MATCH (d:Decision {decision: $decision}) "
                "MERGE (d)-[:RELATED_TO]->(t)",
                topic=topic,
                decision=decision_name,
            )

        for organization in list(dict.fromkeys(decision.get("organizations") or [])):
            tx.run(
                "MERGE (o:Organization {name: $organization}) "
                "WITH o "
                "MATCH (d:Decision {decision: $decision}) "
                "MERGE (d)-[:RELATED_TO]->(o)",
                organization=organization,
                decision=decision_name,
            )

        if decision.get("owner"):
            tx.run(
                "MERGE (p:Person {name: $name}) "
                "WITH p "
                "MATCH (d:Decision {decision: $decision}) "
                "MERGE (p)-[:ASSIGNED_TO]->(d)",
                name=decision["owner"],
                decision=decision_name,
            )

        tx.run(
            "MATCH (d:Decision {decision: $decision}) "
            "CREATE (rev:DecisionRevision {revision_id: $revision_id, decision: $decision, decision_id: $decision_id, "
            "version: $version, owner: $owner, rationale: $rationale, status: $status, source_meeting: $source_meeting, "
            "meeting_id: $meeting_id, timestamp: $timestamp, recorded_at: $now, previous_revision_id: $previous_revision_id}) "
            "MERGE (d)-[:HAS_REVISION]->(rev)",
            decision=decision_name,
            revision_id=revision_id,
            decision_id=decision.get("decision_id") or revision_id,
            version=decision.get("version") or 1,
            owner=decision.get("owner"),
            rationale=decision.get("rationale"),
            status=decision.get("status"),
            source_meeting=decision.get("source_meeting"),
            meeting_id=meeting_id,
            timestamp=decision.get("timestamp"),
            now=now,
            previous_revision_id=previous_revision_id,
        )

        if previous_revision_id:
            tx.run(
                "MATCH (prev:DecisionRevision {revision_id: $previous_revision_id}) "
                "MATCH (rev:DecisionRevision {revision_id: $revision_id}) "
                "MERGE (prev)-[:UPDATED_TO]->(rev)",
                previous_revision_id=previous_revision_id,
                revision_id=revision_id,
            )

    def get_dependencies(self, decision_name: str) -> List[Dict]:
        return self.get_dependents(decision_name)

    def get_dependents(self, decision_name: str) -> List[Dict]:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "MATCH (dep:Decision)-[:DEPENDS_ON]->(d:Decision {decision: $name}) "
                        "RETURN dep.decision as decision, dep.status as status, dep.owner as owner, dep.rationale as rationale, "
                        "dep.source_meeting as source_meeting, dep.timestamp as timestamp, dep.meeting_id as meeting_id "
                        "ORDER BY dep.decision",
                        name=decision_name,
                    )
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_dependents(decision_name)

    def get_upstream_dependencies(self, decision_name: str) -> List[Dict]:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "MATCH (d:Decision {decision: $name})-[:DEPENDS_ON]->(dep:Decision) "
                        "RETURN dep.decision as decision, dep.status as status, dep.owner as owner, dep.rationale as rationale, "
                        "dep.source_meeting as source_meeting, dep.timestamp as timestamp, dep.meeting_id as meeting_id "
                        "ORDER BY dep.decision",
                        name=decision_name,
                    )
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_upstream_dependencies(decision_name)

    def get_decision_details(self, decision_name: str) -> Dict:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "MATCH (d:Decision {decision: $name}) "
                        "RETURN d.decision as decision, d.owner as owner, d.rationale as rationale, d.status as status, "
                        "d.source_meeting as source_meeting, d.timestamp as timestamp, d.created_at as created_at, "
                        "d.updated_at as updated_at, d.meeting_id as meeting_id, d.version as version, d.topics as topics, d.organizations as organizations",
                        name=decision_name,
                    )
                    record = result.single()
                    return record.data() if record else {}
            except Exception:
                pass
        return self._get_local_decision_details(decision_name)

    def get_decision_history(self, decision_name: str) -> List[Dict]:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "MATCH (d:Decision {decision: $name})-[:HAS_REVISION]->(rev:DecisionRevision) "
                        "RETURN rev.revision_id as revision_id, rev.decision_id as decision_id, rev.version as version, rev.decision as decision, "
                        "rev.owner as owner, rev.rationale as rationale, rev.status as status, rev.source_meeting as source_meeting, "
                        "rev.meeting_id as meeting_id, rev.timestamp as timestamp, rev.recorded_at as recorded_at, rev.previous_revision_id as previous_revision_id "
                        "ORDER BY rev.recorded_at ASC",
                        name=decision_name,
                    )
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._get_local_decision_history(decision_name)

    def find_decision_candidates(self, search_term: str, limit: int = 5) -> List[Dict]:
        search_term = (search_term or "").strip()
        if not search_term:
            return []

        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "MATCH (d:Decision) "
                        "WHERE toLower(d.decision) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.owner, '')) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.rationale, '')) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.source_meeting, '')) CONTAINS toLower($term) OR "
                        "toLower(coalesce(d.meeting_id, '')) CONTAINS toLower($term) OR "
                        "any(topic IN coalesce(d.topics, []) WHERE toLower(topic) CONTAINS toLower($term)) OR "
                        "any(org IN coalesce(d.organizations, []) WHERE toLower(org) CONTAINS toLower($term)) "
                        "RETURN d.decision as decision, d.owner as owner, d.status as status, d.rationale as rationale, "
                        "d.source_meeting as source_meeting, d.timestamp as timestamp, d.meeting_id as meeting_id, d.version as version "
                        "ORDER BY CASE WHEN toLower(d.decision) = toLower($term) THEN 0 WHEN toLower(d.decision) CONTAINS toLower($term) THEN 1 ELSE 2 END, size(d.decision) "
                        "LIMIT $limit",
                        term=search_term,
                        limit=limit,
                    )
                    return [record.data() for record in result]
            except Exception:
                pass
        return self._find_local_decision_candidates(search_term, limit)

    def get_meeting_context(self, meeting_id: str) -> Dict[str, Any]:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    meeting_row = session.run(
                        "MATCH (m:Meeting {meeting_id: $meeting_id}) RETURN properties(m) as meeting",
                        meeting_id=meeting_id,
                    ).single()
                    if not meeting_row:
                        return {}

                    participants = session.run(
                        "MATCH (p:Person)-[:ATTENDED]->(m:Meeting {meeting_id: $meeting_id}) RETURN DISTINCT p.name as name ORDER BY name",
                        meeting_id=meeting_id,
                    ).data()
                    topics = session.run(
                        "MATCH (m:Meeting {meeting_id: $meeting_id})-[:DISCUSSED]->(t:Topic) RETURN DISTINCT t.name as name ORDER BY name",
                        meeting_id=meeting_id,
                    ).data()
                    organizations = session.run(
                        "MATCH (m:Meeting {meeting_id: $meeting_id})-[:MENTIONED]->(o:Organization) RETURN DISTINCT o.name as name ORDER BY name",
                        meeting_id=meeting_id,
                    ).data()
                    decisions = session.run(
                        "MATCH (m:Meeting {meeting_id: $meeting_id})-[:MADE_DECISION|HAS_DECISION]->(d:Decision) "
                        "RETURN d.decision as decision, d.owner as owner, d.rationale as rationale, d.status as status, "
                        "d.source_meeting as source_meeting, d.timestamp as timestamp, d.version as version, d.meeting_id as meeting_id, d.topics as topics, d.organizations as organizations "
                        "ORDER BY d.decision",
                        meeting_id=meeting_id,
                    ).data()
                    action_items = session.run(
                        "MATCH (m:Meeting {meeting_id: $meeting_id})-[:HAS_ACTION_ITEM]->(a:ActionItem) "
                        "RETURN a.action_item_id as action_item_id, a.task as task, a.owner as owner, a.deadline as deadline, a.status as status, a.meeting_id as meeting_id "
                        "ORDER BY a.task",
                        meeting_id=meeting_id,
                    ).data()

                    meeting = meeting_row["meeting"]
                    meeting["participants"] = [row["name"] for row in participants]
                    meeting["topics"] = [row["name"] for row in topics]
                    meeting["organizations"] = [row["name"] for row in organizations]
                    meeting["decisions"] = decisions
                    meeting["action_items"] = action_items
                    return meeting
            except Exception:
                pass
        return self._get_local_meeting_context(meeting_id)

    def list_graph_data(self) -> Dict[str, List[Dict]]:
        if self.use_neo4j and self.driver is not None:
            try:
                with self.driver.session() as session:
                    nodes = session.run(
                        "MATCH (n) RETURN labels(n) as labels, properties(n) as properties ORDER BY labels(n)"
                    ).data()
                    edges = session.run(
                        "MATCH (a)-[r]->(b) RETURN labels(a) as source_labels, properties(a) as source_properties, type(r) as relationship, labels(b) as target_labels, properties(b) as target_properties"
                    ).data()
                    history = session.run(
                        "MATCH (d:Decision)-[:HAS_REVISION]->(rev:DecisionRevision) "
                        "RETURN d.decision as decision, rev.version as version, rev.status as status, rev.source_meeting as source_meeting, "
                        "rev.meeting_id as meeting_id, rev.timestamp as timestamp, rev.recorded_at as recorded_at, rev.previous_revision_id as previous_revision_id "
                        "ORDER BY rev.recorded_at DESC"
                    ).data()
                    return {"nodes": nodes, "edges": edges, "history": history}
            except Exception:
                pass
        return self._get_local_graph_data()

    def _save_meeting_record(self, store: Dict[str, Any], meeting: MeetingRecord):
        now = datetime.now(timezone.utc).isoformat()
        meetings = store.setdefault("meetings", {})
        people = store.setdefault("people", {})
        action_items = store.setdefault("action_items", {})
        topics = store.setdefault("topics", {})
        organizations = store.setdefault("organizations", {})

        participants = list(dict.fromkeys(meeting.participants or []))
        topic_names = list(dict.fromkeys(meeting.topics or []))
        organization_names = list(dict.fromkeys(meeting.organizations or []))

        meeting_record = {
            "meeting_id": meeting.meeting_id,
            "transcript_id": meeting.transcript_id or meeting.meeting_id,
            "source_meeting": meeting.source_meeting,
            "recorded_at": meeting.recorded_at,
            "input_type": meeting.input_type,
            "filename": meeting.filename,
            "summary": meeting.summary,
            "topics": topic_names,
            "organizations": organization_names,
            "transcript_length": len(meeting.transcript or ""),
            "transcript_preview": (meeting.transcript or "").strip()[:1000],
            "participants": participants,
            "decision_names": [decision.decision for decision in meeting.decisions],
            "decision_ids": [decision.decision_id for decision in meeting.decisions if decision.decision_id],
            "action_item_ids": [],
            "action_item_count": len(meeting.action_items),
            "decision_count": len(meeting.decisions),
            "metadata": meeting.metadata,
            "updated_at": now,
        }

        for index, action_item in enumerate(meeting.action_items):
            action_item_id = action_item.action_item_id or f"{meeting.meeting_id}:action:{index + 1}"
            action_item.action_item_id = action_item_id
            action_items[action_item_id] = {
                "action_item_id": action_item_id,
                "task": action_item.task,
                "owner": action_item.owner,
                "deadline": action_item.deadline,
                "status": action_item.status,
                "meeting_id": action_item.meeting_id or meeting.meeting_id,
                "recorded_at": now,
            }
            meeting_record["action_item_ids"].append(action_item_id)

            if action_item.owner:
                person = people.get(action_item.owner) or {"name": action_item.owner, "attended_meetings": [], "assigned_decisions": [], "assigned_action_items": []}
                if meeting.meeting_id not in person["attended_meetings"]:
                    person["attended_meetings"].append(meeting.meeting_id)
                if action_item_id not in person["assigned_action_items"]:
                    person["assigned_action_items"].append(action_item_id)
                people[action_item.owner] = person

        for participant in participants:
            person = people.get(participant) or {"name": participant, "attended_meetings": [], "assigned_decisions": [], "assigned_action_items": []}
            if meeting.meeting_id not in person["attended_meetings"]:
                person["attended_meetings"].append(meeting.meeting_id)
            people[participant] = person

        for topic in topic_names:
            topic_record = topics.get(topic) or {"name": topic, "meetings": [], "decisions": []}
            if meeting.meeting_id not in topic_record["meetings"]:
                topic_record["meetings"].append(meeting.meeting_id)
            topics[topic] = topic_record

        for organization in organization_names:
            org_record = organizations.get(organization) or {"name": organization, "meetings": []}
            if meeting.meeting_id not in org_record["meetings"]:
                org_record["meetings"].append(meeting.meeting_id)
            organizations[organization] = org_record

        meetings[meeting.meeting_id] = meeting_record

        for decision in meeting.decisions:
            self._save_decision_record(store, self._decision_payload(decision), meeting)

    def _save_decision_record(self, store: Dict[str, Any], decision: Dict[str, Any], meeting: Optional[MeetingRecord] = None):
        now = datetime.now(timezone.utc).isoformat()
        decisions = store.setdefault("decisions", {})
        people = store.setdefault("people", {})
        topics = store.setdefault("topics", {})
        organizations = store.setdefault("organizations", {})

        decision_name = decision.get("decision")
        if not decision_name:
            return

        record = decisions.get(decision_name) or {
            "decision": decision_name,
            "created_at": now,
            "history": [],
            "version": 0,
        }

        existing_version = int(record.get("version") or 0)
        incoming_version = decision.get("version")
        try:
            incoming_version = int(incoming_version) if incoming_version is not None else None
        except (TypeError, ValueError):
            incoming_version = None

        version_candidates = [existing_version + 1 if existing_version else 1]
        if incoming_version:
            version_candidates.append(incoming_version)
        version = max(version_candidates)

        revision_id = decision.get("decision_id") or str(uuid.uuid4())
        previous_revision_id = record.get("latest_revision_id")
        meeting_id = decision.get("meeting_id") or (meeting.meeting_id if meeting else None) or decision.get("source_meeting")
        source_meeting = decision.get("source_meeting") or (meeting.source_meeting if meeting else None)
        decision_topics = list(dict.fromkeys(decision.get("topics") or (meeting.topics if meeting else []) or []))
        decision_organizations = list(dict.fromkeys(decision.get("organizations") or (meeting.organizations if meeting else []) or []))

        record.update(
            {
                "decision": decision_name,
                "owner": decision.get("owner"),
                "rationale": decision.get("rationale"),
                "status": decision.get("status"),
                "source_meeting": source_meeting,
                "meeting_id": meeting_id,
                "timestamp": decision.get("timestamp"),
                "updated_at": now,
                "version": version,
                "latest_revision_id": revision_id,
                "supersedes_decision_id": decision.get("supersedes_decision_id"),
                "dependencies": list(dict.fromkeys(decision.get("dependencies") or [])),
                "topics": decision_topics,
                "organizations": decision_organizations,
            }
        )

        record.setdefault("history", []).append(
            {
                "revision_id": revision_id,
                "decision_id": revision_id,
                "decision": decision_name,
                "owner": decision.get("owner"),
                "rationale": decision.get("rationale"),
                "status": decision.get("status"),
                "source_meeting": source_meeting,
                "meeting_id": meeting_id,
                "timestamp": decision.get("timestamp"),
                "recorded_at": now,
                "version": version,
                "previous_revision_id": previous_revision_id,
                "topics": decision_topics,
                "organizations": decision_organizations,
            }
        )

        if decision.get("owner"):
            person = people.get(decision["owner"]) or {"name": decision["owner"], "attended_meetings": [], "assigned_decisions": [], "assigned_action_items": []}
            if decision_name not in person["assigned_decisions"]:
                person["assigned_decisions"].append(decision_name)
            if meeting_id and meeting_id not in person["attended_meetings"]:
                person["attended_meetings"].append(meeting_id)
            people[decision["owner"]] = person

        for topic in decision_topics:
            topic_record = topics.get(topic) or {"name": topic, "meetings": [], "decisions": []}
            if meeting_id and meeting_id not in topic_record["meetings"]:
                topic_record["meetings"].append(meeting_id)
            if decision_name not in topic_record["decisions"]:
                topic_record["decisions"].append(decision_name)
            topics[topic] = topic_record

        for organization in decision_organizations:
            org_record = organizations.get(organization) or {"name": organization, "meetings": []}
            if meeting_id and meeting_id not in org_record["meetings"]:
                org_record["meetings"].append(meeting_id)
            organizations[organization] = org_record

        decisions[decision_name] = record

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
            "meeting_id": record.get("meeting_id"),
            "timestamp": record.get("timestamp"),
            "topics": list(record.get("topics") or []),
            "organizations": list(record.get("organizations") or []),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "version": record.get("version"),
            "latest_revision_id": record.get("latest_revision_id"),
            "supersedes_decision_id": record.get("supersedes_decision_id"),
            "dependencies": list(record.get("dependencies") or []),
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
                    "meeting_id": dependency.get("meeting_id"),
                    "timestamp": dependency.get("timestamp"),
                    "topics": list(dependency.get("topics") or []),
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
                        "meeting_id": record.get("meeting_id"),
                        "timestamp": record.get("timestamp"),
                        "topics": list(record.get("topics") or []),
                    }
                )
        entries.sort(key=lambda item: item.get("decision") or "")
        return entries

    def _find_local_decision_candidates(self, search_term: str, limit: int = 5) -> List[Dict]:
        lowered_term = search_term.lower()
        candidates: List[Dict] = []
        store = self._load_local_store()

        for record in store.get("decisions", {}).values():
            haystack = " ".join(
                str(record.get(field) or "")
                for field in ("decision", "owner", "rationale", "source_meeting", "meeting_id", "status")
            ).lower()
            topic_haystack = " ".join(list(record.get("topics") or []) + list(record.get("organizations") or [])).lower()
            if lowered_term in haystack or lowered_term in topic_haystack:
                candidates.append(
                    {
                        "decision": record.get("decision"),
                        "owner": record.get("owner"),
                        "status": record.get("status"),
                        "rationale": record.get("rationale"),
                        "source_meeting": record.get("source_meeting"),
                        "meeting_id": record.get("meeting_id"),
                        "timestamp": record.get("timestamp"),
                        "topics": list(record.get("topics") or []),
                    }
                )

        for topic_name, topic_record in store.get("topics", {}).items():
            if lowered_term in topic_name.lower():
                for decision_name in topic_record.get("decisions", []):
                    decision = store.get("decisions", {}).get(decision_name) or {}
                    candidates.append(
                        {
                            "decision": decision.get("decision", decision_name),
                            "owner": decision.get("owner"),
                            "status": decision.get("status"),
                            "rationale": decision.get("rationale"),
                            "source_meeting": decision.get("source_meeting"),
                            "meeting_id": decision.get("meeting_id"),
                            "timestamp": decision.get("timestamp"),
                            "topics": list(decision.get("topics") or []),
                        }
                    )

        for organization_name, org_record in store.get("organizations", {}).items():
            if lowered_term in organization_name.lower():
                for meeting_id in org_record.get("meetings", []):
                    meeting = store.get("meetings", {}).get(meeting_id) or {}
                    for decision_name in meeting.get("decision_names", []):
                        decision = store.get("decisions", {}).get(decision_name) or {}
                        candidates.append(
                            {
                                "decision": decision.get("decision", decision_name),
                                "owner": decision.get("owner"),
                                "status": decision.get("status"),
                                "rationale": decision.get("rationale"),
                                "source_meeting": decision.get("source_meeting") or meeting.get("source_meeting"),
                                "meeting_id": meeting.get("meeting_id"),
                                "timestamp": decision.get("timestamp") or meeting.get("recorded_at"),
                                "topics": list(decision.get("topics") or []),
                            }
                        )

        candidates.sort(
            key=lambda item: (
                0 if (item.get("decision") or "").lower() == lowered_term else 1,
                len(item.get("decision") or ""),
            )
        )
        return candidates[:limit]

    def _get_local_meeting_context(self, meeting_id: str) -> Dict[str, Any]:
        store = self._load_local_store()
        meeting = store.get("meetings", {}).get(meeting_id)
        if not meeting:
            return {}

        decision_names = list(meeting.get("decision_names") or [])
        decisions = []
        for decision_name in decision_names:
            decision_details = self._get_local_decision_details(decision_name)
            if decision_details:
                decision_details["history"] = self._get_local_decision_history(decision_name)
                decisions.append(decision_details)

        action_items = []
        for action_item_id in meeting.get("action_item_ids", []):
            item = store.get("action_items", {}).get(action_item_id)
            if item:
                action_items.append(item)

        participants = []
        for participant in meeting.get("participants", []):
            person = store.get("people", {}).get(participant) or {"name": participant}
            participants.append(person)

        return {
            **meeting,
            "participants": participants,
            "topics": list(meeting.get("topics") or []),
            "organizations": list(meeting.get("organizations") or []),
            "decisions": decisions,
            "action_items": action_items,
        }

    def _get_local_graph_data(self) -> Dict[str, List[Dict]]:
        store = self._load_local_store()
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []

        for meeting in store.get("meetings", {}).values():
            nodes.append(
                {
                    "id": meeting.get("meeting_id"),
                    "type": "Meeting",
                    "label": meeting.get("source_meeting") or meeting.get("meeting_id"),
                    "properties": meeting,
                }
            )
            for participant in meeting.get("participants", []):
                edges.append({"source": participant, "target": meeting.get("meeting_id"), "relationship": "ATTENDED"})
            for topic in meeting.get("topics", []):
                edges.append({"source": meeting.get("meeting_id"), "target": topic, "relationship": "DISCUSSED"})
            for organization in meeting.get("organizations", []):
                edges.append({"source": meeting.get("meeting_id"), "target": organization, "relationship": "MENTIONED"})
            for decision_name in meeting.get("decision_names", []):
                edges.append({"source": meeting.get("meeting_id"), "target": decision_name, "relationship": "MADE_DECISION"})
            for action_item_id in meeting.get("action_item_ids", []):
                edges.append({"source": meeting.get("meeting_id"), "target": action_item_id, "relationship": "HAS_ACTION_ITEM"})

        for person in store.get("people", {}).values():
            nodes.append({"id": person.get("name"), "type": "Person", "label": person.get("name"), "properties": person})

        for topic in store.get("topics", {}).values():
            nodes.append({"id": topic.get("name"), "type": "Topic", "label": topic.get("name"), "properties": topic})

        for organization in store.get("organizations", {}).values():
            nodes.append({"id": organization.get("name"), "type": "Organization", "label": organization.get("name"), "properties": organization})

        for action_item in store.get("action_items", {}).values():
            nodes.append(
                {
                    "id": action_item.get("action_item_id"),
                    "type": "ActionItem",
                    "label": action_item.get("task") or action_item.get("action_item_id"),
                    "properties": action_item,
                }
            )
            if action_item.get("owner"):
                edges.append({"source": action_item.get("owner"), "target": action_item.get("action_item_id"), "relationship": "ASSIGNED_TO"})

        for record in store.get("decisions", {}).values():
            nodes.append(
                {
                    "id": record.get("decision"),
                    "type": "Decision",
                    "label": record.get("decision"),
                    "properties": {k: v for k, v in record.items() if k != "history"},
                }
            )
            for dependency in record.get("dependencies", []):
                edges.append({"source": record.get("decision"), "target": dependency, "relationship": "DEPENDS_ON"})
                edges.append({"source": record.get("decision"), "target": dependency, "relationship": "RELATED_TO"})
            for topic in record.get("topics", []):
                edges.append({"source": record.get("decision"), "target": topic, "relationship": "RELATED_TO"})
            for organization in record.get("organizations", []):
                edges.append({"source": record.get("decision"), "target": organization, "relationship": "RELATED_TO"})
            for revision in record.get("history", []):
                history.append(
                    {
                        "decision": revision.get("decision"),
                        "version": revision.get("version"),
                        "status": revision.get("status"),
                        "source_meeting": revision.get("source_meeting"),
                        "meeting_id": revision.get("meeting_id"),
                        "timestamp": revision.get("timestamp"),
                        "recorded_at": revision.get("recorded_at"),
                        "previous_revision_id": revision.get("previous_revision_id"),
                    }
                )

        history.sort(key=lambda item: item.get("recorded_at") or "", reverse=True)
        return {"nodes": nodes, "edges": edges, "history": history}


graph_manager = GraphManager()
