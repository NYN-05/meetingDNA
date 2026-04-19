# MeetingDNA Production Architecture

## 1. System Goal

MeetingDNA is a meeting intelligence platform that turns raw meeting transcripts into structured knowledge, graph relationships, and AI-assisted answers.

The design goal is not a transcript viewer. The goal is a system that can:

- Ingest audio or text meetings asynchronously
- Extract people, topics, organizations, decisions, and action items
- Persist a meaningful knowledge graph
- Index meetings and decisions for semantic retrieval
- Answer hybrid questions with graph traversal plus RAG
- Keep revision history so knowledge evolves over time

## 2. High-Level Architecture

```text
Client / UI
   |
   v
FastAPI API Gateway
   |
   +--> Ingestion Service --> In-process Job Queue --> Worker Thread(s)
   |                             |                        |
   |                             |                        +--> Whisper / text cleaning
   |                             |                        +--> LLM extraction
   |                             |                        +--> Embeddings
   |                             |                        +--> Graph write
   |                             |                        +--> Vector write
   |                             v
   |                        Job Status API
   |
   +--> Query Service --> Hybrid Retrieval Engine --> LLM Answer
                                 |
                                 +--> ChromaDB meetings
                                 +--> ChromaDB decisions
                                 +--> Neo4j graph lookup

Data Layer
- Neo4j: relationships and decision history
- ChromaDB: meeting and decision embeddings
- Local JSON: fallback graph store for development
- Optional future: Postgres for structured metadata, Redis for durable queue/cache, S3 for raw uploads
```

## 3. What Exists Now

The repo now contains the core runtime pieces for a production path:

- `app/api/endpoints/ingestion.py`: upload endpoint plus job status endpoint
- `app/core/job_queue.py`: background ingestion worker queue
- `app/core/ingestion_service.py`: end-to-end upload processing
- `app/core/extractor.py`: structured meeting extraction
- `app/models/meeting.py`: canonical meeting contract
- `app/core/vector_store.py`: meeting and decision embeddings
- `app/core/graph_manager.py`: ontology-aware graph persistence
- `app/core/hybrid_retrieval.py`: hybrid query planner and response builder
- `app/main.py`: app bootstrap, logging, health check, queue lifecycle

## 4. Canonical Data Contract

This is the contract every ingestion job must produce before any storage write:

```json
{
  "meeting_id": "uuid",
  "transcript_id": "uuid",
  "source_meeting": "budget-review-2026-04",
  "recorded_at": "2026-04-19T12:34:56Z",
  "input_type": "audio | transcript-file | pasted-transcript",
  "filename": "optional-file-name",
  "transcript": "normalized transcript text",
  "summary": "short meeting summary",
  "participants": ["Alice", "Bob"],
  "topics": ["budget", "hiring"],
  "organizations": ["Contoso"],
  "decisions": [
    {
      "decision_id": "uuid",
      "meeting_id": "uuid",
      "decision": "Hire two frontend engineers",
      "owner": "Alice",
      "rationale": "Need more frontend capacity",
      "status": "approved",
      "dependencies": ["Q2 hiring freeze lifted"],
      "timestamp": "2026-04-19T12:20:00Z",
      "source_meeting": "budget-review-2026-04",
      "version": 1
    }
  ],
  "action_items": [
    {
      "action_item_id": "uuid",
      "task": "Draft hiring plan",
      "owner": "Bob",
      "deadline": "2026-04-25",
      "status": "open"
    }
  ],
  "metadata": {
    "transcript_length": 18342,
    "source_meeting": "budget-review-2026-04"
  }
}
```

This schema is important because it gives every downstream system the same object shape:

- Graph persistence does not guess what a decision looks like
- Vector indexing does not store arbitrary payloads
- Query routing can rely on meeting IDs, decision IDs, and topics

## 5. Ingestion Pipeline

### Runtime flow

1. Client uploads audio, transcript text, or a transcript file.
2. API creates a job record and returns `202 Accepted`.
3. Worker pulls the job from the queue.
4. The service normalizes the payload.
5. Whisper runs only for audio.
6. LLM extraction produces the canonical meeting record.
7. Graph store is updated.
8. Vector store is updated.
9. Job status moves to `completed` or `failed`.

### Current behavior

The queue is in-process for now. That is acceptable for a single-node deployment and hackathon scale. For real production, the queue should move to Redis + Celery or RabbitMQ/Kafka workers.

### Why this matters

Without async ingestion, transcription and extraction block request latency and make the API unstable under load. The queue turns the upload path into a fast accept/reject boundary and moves heavy work to workers.

## 6. NLP / AI Processing Layer

The extractor now asks the LLM for a structured response with:

- summary
- participants
- topics
- organizations
- decisions
- action items

### Processing model

```text
Raw transcript
   |
   +--> normalization / cleanup
   +--> entity extraction
   +--> decision detection
   +--> action item extraction
   +--> summarization
   |
Canonical meeting record
```

### Why LLM plus schema validation

LLM output is not trusted directly. The response is validated into Pydantic models before any write.

That gives two benefits:

- Flexible extraction from unstructured transcripts
- Predictable downstream storage and querying

## 7. Knowledge Graph Design

The graph is intentionally opinionated. The goal is not to dump JSON into Neo4j. The goal is to model meeting semantics.

### Node types

- `Person`
- `Meeting`
- `Topic`
- `Decision`
- `ActionItem`
- `Organization`

### Relationships

- `ATTENDED`: `Person -> Meeting`
- `DISCUSSED`: `Meeting -> Topic`
- `MADE_DECISION`: `Meeting -> Decision`
- `ASSIGNED_TO`: `Person -> ActionItem` or `Person -> Decision`
- `RELATED_TO`: generic semantic link between `Decision -> Topic`, `Decision -> Decision`, or `Decision -> Organization`
- `UPDATED_TO`: `DecisionRevision -> DecisionRevision`

### Design rationale

- `Meeting` is the anchor node. Everything else attaches to it.
- `Topic` allows semantic grouping across meetings.
- `Decision` is a first-class node with revision history.
- `ActionItem` is separate from decisions because execution work is not the same as a decision.
- `RELATED_TO` is the flexible semantic edge that prevents the graph from becoming brittle.

### Sample Cypher queries

Find all decisions for a topic in the last month:

```cypher
MATCH (m:Meeting)-[:DISCUSSED]->(t:Topic {name: $topic})
MATCH (m)-[:MADE_DECISION]->(d:Decision)
WHERE datetime(m.recorded_at) >= datetime() - duration({days: 30})
RETURN m, d
ORDER BY m.recorded_at DESC
```

Find who owns action items from a meeting:

```cypher
MATCH (m:Meeting {meeting_id: $meeting_id})-[:HAS_ACTION_ITEM]->(a:ActionItem)
OPTIONAL MATCH (p:Person)-[:ASSIGNED_TO]->(a)
RETURN a, p
```

Find decision evolution:

```cypher
MATCH (d:Decision {decision: $decision})-[:HAS_REVISION]->(r:DecisionRevision)
OPTIONAL MATCH (r)-[:UPDATED_TO]->(next:DecisionRevision)
RETURN r, next
ORDER BY r.recorded_at ASC
```

Find topics related to a decision:

```cypher
MATCH (d:Decision {decision: $decision})-[:RELATED_TO]->(t:Topic)
RETURN t.name
ORDER BY t.name
```

## 8. Vector Database Layer

The vector layer uses ChromaDB with two logical collections:

- Meeting embeddings
- Decision embeddings

### What gets embedded

- Meeting transcript plus summary plus topic hints
- Decision text plus rationale plus meeting context

### Why split them

Meetings and decisions serve different retrieval use cases:

- Meeting search answers broad contextual questions
- Decision search answers exact or near-exact decision questions

If both are mixed in one bucket, ranking quality drops and query routing becomes noisy.

## 9. RAG Query Engine

The query engine is hybrid by design.

```text
User query
   |
   +--> LLM intent classification
   +--> vector search over meetings
   +--> vector search over decisions
   +--> graph lookup by meeting_id / decision
   +--> context merge
   +--> LLM answer synthesis
```

### Supported question patterns

- What decisions were made about budget last month?
- Who is responsible for action items from Project X?
- What changed between revision 1 and revision 2?
- Which meetings discussed hiring and what decisions followed?

### Why hybrid retrieval is important

Vector search alone gives semantic similarity.
Graph traversal alone gives relationships.
The platform needs both.

The graph is not just storage; it is the relational layer that improves answer quality by:

- expanding a semantic hit into connected decisions
- surfacing action item owners
- showing revisions and stale downstream decisions

## 10. Backend Architecture Upgrade

### Current code layout

```text
app/
  api/
    endpoints/
      graph.py
      ingestion.py
      query.py
  core/
    extractor.py
    graph_manager.py
    hybrid_retrieval.py
    ingestion_service.py
    job_queue.py
    ollama_client.py
    transcription.py
    vector_store.py
  models/
    decision.py
    meeting.py
  utils/
    config.py
  main.py
```

### Production target layout

```text
backend/
  api/
    routes/
    middleware/
  services/
    ingestion/
    query/
    processing/
  workers/
    tasks.py
  core/
    config.py
    logging.py
  db/
    neo4j.py
    vector.py
    postgres.py
  models/
    schema.py
  main.py
```

### What to harden next

- Move the in-process queue to Redis + Celery or RabbitMQ
- Add Postgres for structured metadata and ingestion audit records
- Add S3 or blob storage for raw files
- Add auth and rate limiting
- Add Redis caching for repeated queries

## 11. Public API Surface

### Ingestion

`POST /ingestion/upload`

- Accepts file or transcript text
- Returns `202 Accepted`
- Response includes `job_id` and monitoring URL

`GET /ingestion/jobs/{job_id}`

- Returns queued / running / completed / failed status

`GET /ingestion/history`

- Returns recently ingested meetings

`GET /ingestion/history/{transcript_id}`

- Returns one stored meeting record

### Query

`GET /query?q=...`

- Returns LLM answer
- Includes candidate decisions and source context
- Uses semantic + graph retrieval

### Graph

`GET /graph`

- Returns current graph snapshot for visualization or debugging

### Health

`GET /health`

- Liveness endpoint for deployment checks

## 12. Sample Data Flow

```text
1. User uploads transcript
2. API enqueues job and returns job_id
3. Worker loads transcript and extracts meeting schema
4. Graph layer stores:
   - Meeting
   - People
   - Topics
   - Organizations
   - Decisions
   - Action items
5. Vector layer stores:
   - meeting embedding
   - decision embeddings
6. Query arrives
7. Intent classifier routes to dependency or semantic path
8. Retrieval engine merges vector and graph context
9. LLM answers with citations and revision context
```

## 13. Observability

Already present:

- Structured application logging
- Request latency logging middleware
- Health endpoint

Should be added next:

- Prometheus metrics
- OpenTelemetry tracing
- Error tracking such as Sentry
- Worker metrics for queue depth, failures, and latency

## 14. Security Considerations

The current codebase is development-friendly, not production-hardened.

Add next:

- JWT authentication
- Role-based access control
- Rate limiting
- File size limits and MIME validation
- Secrets management through env vars or a secret store
- Audit logging for uploads and query access

## 15. Deployment Strategy

Recommended path:

1. Containerize API, worker, Neo4j, and Chroma.
2. Use Docker Compose for local development.
3. Move API and worker to Kubernetes or a managed container service.
4. Keep Neo4j and vector store backed by managed volumes or managed services.
5. Introduce Postgres and Redis once load requires durability and caching.

### Good production split

- API container
- Worker container
- Neo4j container or managed Neo4j
- Vector store volume or managed service
- Redis queue/cache
- Postgres metadata store

## 16. Tech Stack Justification

- FastAPI: fast async API layer, strong typing, easy worker integration
- Whisper: reliable speech-to-text for meeting audio
- Ollama / OpenAI: structured extraction and answer synthesis
- Neo4j: best fit for meeting relationship traversal and revision history
- ChromaDB: simple, practical semantic retrieval for transcripts and decisions
- Python threading/queue now, Redis/Celery later: keeps the current repo runnable while providing a clear scale path

## 17. Implementation Plan

### Phase 1

- Canonical meeting schema
- Background ingestion queue
- LLM extraction of topics, decisions, action items, organizations
- Vector search over meetings and decisions
- Graph writes with meeting/topic/decision/action item edges

### Phase 2

- Redis-backed durable queue
- Postgres metadata store
- S3/object storage for raw files
- Auth and rate limiting
- Worker observability metrics

### Phase 3

- Advanced graph intelligence
- Decision evolution timelines
- Stale dependency warnings
- Conflict detection between revisions
- UI dashboard for graph exploration and decision tracking

## 18. Critical Code Snippets

### Enqueue an ingestion job

```python
job_id = ingestion_job_queue.submit_upload(
    raw_bytes=raw_bytes,
    filename=filename,
    content_type=content_type,
    transcript_text=transcript_text,
    source_meeting=source_meeting,
)
```

### Persist a meeting into the graph

```python
meeting = await extractor_service.extract_meeting(...)
vector_store.add_meeting(meeting)
graph_manager.save_meeting(meeting)
```

### Hybrid retrieval flow

```python
classification = await hybrid_retrieval_service.classify_query(q)
if classification["intent"] == "dependency":
    # graph-first path
else:
    # semantic-first path
```

### Neo4j meeting linking

```cypher
MERGE (m:Meeting {meeting_id: $meeting_id})
MERGE (m)-[:DISCUSSED]->(t:Topic)
MERGE (m)-[:MADE_DECISION]->(d:Decision)
MERGE (p)-[:ATTENDED]->(m)
```

## 19. Frontend Notes

The UI should eventually show:

- Meeting timeline
- Decision evolution
- Topic clusters
- Action item ownership
- Graph visualization with relationship filters

The current frontend can stay minimal until the backend stabilizes.

## 20. Bottom Line

The platform is no longer a simple tool chain. It now has the pieces of a real meeting intelligence system:

- structured ingestion
- queue-based async processing
- ontology-aware graph storage
- dual-collection vector retrieval
- hybrid RAG query handling

The next real production step is replacing the in-process queue and local metadata fallbacks with durable infrastructure.
