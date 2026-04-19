import { useEffect, useState } from "react";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
const AUDIO_ACCEPT = ".mp3,.wav,.m4a,.aac,.flac,.ogg";
const TRANSCRIPT_ACCEPT = ".txt,.md,.json,.vtt,.srt,.csv,.log";
const GRAPH_WIDTH = 980;
const GRAPH_HEIGHT = 560;
const TAB_ITEMS = [
  { key: "ingest", label: "Ingestion", hint: "Upload notes" },
  { key: "graph", label: "Decision Graph", hint: "Inspect links" },
  { key: "query", label: "Query Agent", hint: "Ask questions" },
];
const INPUT_MODES = [
  { key: "audio", label: "Audio recording", help: "Transcribe a meeting file" },
  { key: "transcript", label: "Transcript file", help: "Upload text exports" },
  { key: "paste", label: "Paste transcript", help: "Drop text directly into the app" },
];
const NODE_COLORS = ["#f97316", "#38bdf8", "#34d399", "#a78bfa", "#facc15", "#fb7185", "#22c55e", "#60a5fa"];

function safeText(value, fallback = "N/A") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function truncate(value, maxLength = 28) {
  const text = safeText(value, "");
  if (!text) {
    return "";
  }
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}...`;
}

function formatTimestamp(value) {
  if (!value) {
    return "N/A";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return safeText(value);
  }
  return date.toLocaleString();
}

async function readErrorMessage(response, fallback) {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json();
      if (typeof payload === "string") {
        return payload;
      }
      if (payload && typeof payload === "object") {
        return payload.detail || payload.message || payload.error || JSON.stringify(payload);
      }
    } catch {
      return fallback;
    }
  }

  try {
    const text = await response.text();
    if (text.trim()) {
      return text;
    }
  } catch {
    return fallback;
  }

  return fallback;
}

function listify(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeGraphNodes(rawNodes) {
  const nodes = [];
  const seen = new Set();

  rawNodes.forEach((rawNode, index) => {
    const props = rawNode?.n ?? rawNode ?? {};
    const idValue = props.decision ?? props.name ?? props.id ?? `decision-${index + 1}`;
    const id = String(idValue);
    if (seen.has(id)) {
      return;
    }
    seen.add(id);
    nodes.push({
      id,
      label: safeText(props.decision ?? props.name ?? id),
      status: safeText(props.status, ""),
      owner: safeText(props.owner, ""),
      sourceMeeting: safeText(props.source_meeting, ""),
      timestamp: safeText(props.timestamp, ""),
    });
  });

  return nodes;
}

function buildGraphLayout(nodes, width, height) {
  if (!nodes.length) {
    return [];
  }

  const centerX = width / 2;
  const centerY = height / 2;

  if (nodes.length === 1) {
    return [
      {
        ...nodes[0],
        x: centerX,
        y: centerY,
        color: NODE_COLORS[0],
      },
    ];
  }

  const radius = Math.max(Math.min(width, height) / 2 - 110, 130);

  return nodes.map((node, index) => {
    const angle = (index / nodes.length) * Math.PI * 2 - Math.PI / 2;
    return {
      ...node,
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
      color: NODE_COLORS[index % NODE_COLORS.length],
    };
  });
}

function pickColor(index) {
  return NODE_COLORS[index % NODE_COLORS.length];
}

function StatTile({ label, value }) {
  return (
    <div className="stat-tile">
      <span>{label}</span>
      <strong>{safeText(value)}</strong>
    </div>
  );
}

function PanelCard({ eyebrow, title, description, action, children }) {
  return (
    <section className="card panel-card">
      <div className="section-head">
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h2>{title}</h2>
          {description ? <p className="section-description">{description}</p> : null}
        </div>
        {action ? <div className="section-action">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

function CardList({ items, emptyText, className = "card-grid", renderItem }) {
  if (!items.length) {
    return <div className="empty-inline">{emptyText}</div>;
  }

  return <div className={className}>{items.map((item, index) => renderItem(item, index))}</div>;
}

function GraphSvg({ nodes, edges }) {
  const layoutNodes = buildGraphLayout(nodes, GRAPH_WIDTH, GRAPH_HEIGHT);
  const nodeMap = new Map(layoutNodes.map((node) => [node.id, node]));

  if (!layoutNodes.length) {
    return <div className="empty-inline">No decisions have been stored yet.</div>;
  }

  return (
    <div className="graph-shell">
      <svg viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`} className="graph-svg" role="img" aria-label="Decision graph">
        <defs>
          <marker id="graph-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L8,3 z" fill="rgba(148, 163, 184, 0.85)" />
          </marker>
        </defs>
        {edges.map((edge, index) => {
          const source = nodeMap.get(String(edge.source));
          const target = nodeMap.get(String(edge.target));
          if (!source || !target) {
            return null;
          }
          return (
            <line
              key={`edge-${index}`}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              className="graph-edge"
              markerEnd="url(#graph-arrow)"
            />
          );
        })}
        {layoutNodes.map((node, index) => (
          <g key={node.id} transform={`translate(${node.x}, ${node.y})`} className="graph-node">
            <title>{node.label}</title>
            <circle r="32" fill={pickColor(index)} />
            <circle r="44" fill="none" stroke={pickColor(index)} strokeWidth="10" opacity="0.18" />
            <text y="5">{truncate(node.label, 18)}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("ingest");
  const [inputMode, setInputMode] = useState("audio");
  const [sourceMeeting, setSourceMeeting] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [pastedTranscript, setPastedTranscript] = useState("");
  const [ingestLoading, setIngestLoading] = useState(false);
  const [ingestResult, setIngestResult] = useState(null);
  const [ingestError, setIngestError] = useState("");

  const [graphData, setGraphData] = useState(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [lastGraphRefresh, setLastGraphRefresh] = useState(null);

  const [ingestionHistory, setIngestionHistory] = useState([]);
  const [ingestionHistoryLoading, setIngestionHistoryLoading] = useState(false);
  const [ingestionHistoryError, setIngestionHistoryError] = useState("");
  const [selectedIngestion, setSelectedIngestion] = useState(null);
  const [selectedIngestionLoading, setSelectedIngestionLoading] = useState(false);
  const [selectedIngestionError, setSelectedIngestionError] = useState("");

  const [queryText, setQueryText] = useState("");
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState("");

  useEffect(() => {
    void loadIngestionHistory();
  }, []);

  function handleTabChange(tabKey) {
    setActiveTab(tabKey);
    if (tabKey === "graph" && !graphData && !graphLoading) {
      void loadGraph();
    }
  }

  function handleModeChange(modeKey) {
    setInputMode(modeKey);
    setSelectedFile(null);
    setIngestError("");
  }

  async function loadGraph() {
    setGraphLoading(true);
    setGraphError("");

    try {
      const response = await fetch(`${API_BASE_URL}/graph`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Unable to load graph data."));
      }

      const payload = await response.json();
      setGraphData(payload);
      setLastGraphRefresh(new Date());
    } catch (error) {
      setGraphError(error.message || "Unable to load graph data.");
    } finally {
      setGraphLoading(false);
    }
  }

  async function loadIngestionHistory() {
    setIngestionHistoryLoading(true);
    setIngestionHistoryError("");

    try {
      const response = await fetch(`${API_BASE_URL}/ingestion/history?limit=12`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Unable to load saved ingestions."));
      }

      const payload = await response.json();
      setIngestionHistory(listify(payload.items));
    } catch (error) {
      setIngestionHistoryError(error.message || "Unable to load saved ingestions.");
    } finally {
      setIngestionHistoryLoading(false);
    }
  }

  async function loadIngestionDetails(transcriptId) {
    setSelectedIngestionLoading(true);
    setSelectedIngestionError("");

    try {
      const response = await fetch(`${API_BASE_URL}/ingestion/history/${encodeURIComponent(transcriptId)}`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Unable to load saved transcript."));
      }

      const payload = await response.json();
      setSelectedIngestion(payload);
    } catch (error) {
      setSelectedIngestionError(error.message || "Unable to load saved transcript.");
    } finally {
      setSelectedIngestionLoading(false);
    }
  }

  async function handleIngestionSubmit(event) {
    event.preventDefault();
    setIngestLoading(true);
    setIngestError("");
    setIngestResult(null);

    const meetingLabel = sourceMeeting.trim();
    const formData = new FormData();

    if (meetingLabel) {
      formData.append("source_meeting", meetingLabel);
    }

    if (inputMode === "paste") {
      const transcriptText = pastedTranscript.trim();
      if (!transcriptText) {
        setIngestError("Paste a transcript before submitting.");
        setIngestLoading(false);
        return;
      }
      formData.append("transcript_text", transcriptText);
    } else {
      if (!selectedFile) {
        setIngestError("Choose a file before submitting.");
        setIngestLoading(false);
        return;
      }
      formData.append("file", selectedFile);
    }

    try {
      const response = await fetch(`${API_BASE_URL}/ingestion/upload`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Failed to process meeting."));
      }

      const payload = await response.json();
      setIngestResult(payload);
      void loadIngestionHistory();

      if (activeTab === "graph" || graphData) {
        void loadGraph();
      }
    } catch (error) {
      setIngestError(error.message || "Failed to process meeting.");
    } finally {
      setIngestLoading(false);
    }
  }

  async function handleQuerySubmit(event) {
    event.preventDefault();
    const trimmedQuery = queryText.trim();
    if (!trimmedQuery) {
      setQueryError("Enter a question before submitting.");
      return;
    }

    setQueryLoading(true);
    setQueryError("");
    setQueryResult(null);

    try {
      const response = await fetch(`${API_BASE_URL}/query?q=${encodeURIComponent(trimmedQuery)}`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, "Query failed."));
      }

      const payload = await response.json();
      setQueryResult(payload);
    } catch (error) {
      setQueryError(error.message || "Query failed.");
    } finally {
      setQueryLoading(false);
    }
  }

  const graphNodes = normalizeGraphNodes(graphData?.nodes ?? []);
  const graphEdges = listify(graphData?.edges);
  const graphHistory = listify(graphData?.history);
  const graphStats = {
    nodes: graphNodes.length,
    edges: graphEdges.length,
    history: graphHistory.length,
  };
  const layoutNodes = buildGraphLayout(graphNodes, GRAPH_WIDTH, GRAPH_HEIGHT);
  const decisionDetails = queryResult?.decision_details || {};
  const querySources = listify(queryResult?.sources);
  const queryCandidates = listify(queryResult?.candidates);
  const upstreamDependencies = listify(queryResult?.upstream_dependencies);
  const downstreamDecisions = listify(queryResult?.downstream_decisions);

  function renderIngestionPanel() {
    return (
      <div className="content-grid">
        <PanelCard
          eyebrow="Capture"
          title="Bring meetings into the graph"
          description="Upload an audio file, import a transcript export, or paste raw transcript text."
          action={<span className="chip chip-muted">POST /ingestion/upload</span>}
        >
          <form className="form-stack" onSubmit={handleIngestionSubmit}>
            <div className="field-group">
              <label htmlFor="sourceMeeting">Source meeting label</label>
              <input
                id="sourceMeeting"
                type="text"
                value={sourceMeeting}
                onChange={(event) => setSourceMeeting(event.target.value)}
                placeholder="Weekly sync, customer call, design review"
              />
            </div>

            <div className="field-group">
              <span className="field-label">Input type</span>
              <div className="mode-grid">
                {INPUT_MODES.map((mode) => (
                  <button
                    key={mode.key}
                    type="button"
                    className={inputMode === mode.key ? "mode-card active" : "mode-card"}
                    onClick={() => handleModeChange(mode.key)}
                  >
                    <strong>{mode.label}</strong>
                    <span>{mode.help}</span>
                  </button>
                ))}
              </div>
            </div>

            {inputMode === "paste" ? (
              <div className="field-group">
                <label htmlFor="transcriptText">Paste transcript text</label>
                <textarea
                  id="transcriptText"
                  value={pastedTranscript}
                  onChange={(event) => setPastedTranscript(event.target.value)}
                  placeholder="Paste the meeting transcript here"
                />
              </div>
            ) : (
              <div className="field-group">
                <label htmlFor="meetingFile">{inputMode === "audio" ? "Upload audio file" : "Upload transcript file"}</label>
                <input
                  id="meetingFile"
                  type="file"
                  accept={inputMode === "audio" ? AUDIO_ACCEPT : TRANSCRIPT_ACCEPT}
                  onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                />
                <p className="hint-text">
                  {inputMode === "audio"
                    ? "Accepted audio: mp3, wav, m4a, aac, flac, ogg"
                    : "Accepted text exports: txt, md, json, vtt, srt, csv, log"}
                </p>
                {selectedFile ? <div className="chip file-chip">Selected file: {selectedFile.name}</div> : null}
              </div>
            )}

            {ingestError ? <div className="alert alert-error">{ingestError}</div> : null}

            <div className="action-row">
              <button type="submit" className="primary-button" disabled={ingestLoading}>
                {ingestLoading ? "Processing..." : "Process meeting"}
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={ingestLoading}
                onClick={() => {
                  setSourceMeeting("");
                  setSelectedFile(null);
                  setPastedTranscript("");
                  setIngestResult(null);
                  setIngestError("");
                  setInputMode("audio");
                }}
              >
                Reset
              </button>
            </div>
          </form>
        </PanelCard>

        <PanelCard
          eyebrow="Archive"
          title="Saved ingestions"
          description="All uploads are written to persistent ChromaDB storage and can be reloaded later."
        >
          <div className="stacked-content">
            {ingestResult ? (
              <div className="text-block">
                <span className="text-block-label">Latest extraction</span>
                <div className="stat-row">
                  <StatTile label="Status" value={ingestResult.status || "success"} />
                  <StatTile label="Input type" value={ingestResult.input_type} />
                  <StatTile label="Decisions" value={ingestResult.decisions_extracted} />
                  <StatTile label="Source meeting" value={ingestResult.source_meeting || "N/A"} />
                </div>
                <pre>{safeText(ingestResult.transcript, "No transcript returned.")}</pre>
              </div>
            ) : null}

            <div className="field-group">
              <div className="section-head compact-head">
                <div>
                  <p className="eyebrow">Persistent store</p>
                  <h2>Recent uploads</h2>
                </div>
                <button type="button" className="secondary-button" onClick={loadIngestionHistory} disabled={ingestionHistoryLoading}>
                  {ingestionHistoryLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>

              {ingestionHistoryError ? <div className="alert alert-error">{ingestionHistoryError}</div> : null}

              {ingestionHistory.length ? (
                <div className="history-list">
                  {ingestionHistory.map((item) => (
                    <button
                      key={item.transcript_id}
                      type="button"
                      className="detail-card history-card"
                      onClick={() => void loadIngestionDetails(item.transcript_id)}
                    >
                      <div className="detail-top">
                        <strong>{safeText(item.source_meeting || item.filename || item.transcript_id)}</strong>
                        {item.input_type ? <span className="chip">{item.input_type}</span> : null}
                      </div>
                      <p>{truncate(item.transcript_preview, 180)}</p>
                      <small>{formatTimestamp(item.recorded_at)} · {item.transcript_length || 0} chars</small>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="empty-inline">No saved ingestions yet. Upload something to persist it here.</div>
              )}

              {selectedIngestionError ? <div className="alert alert-error">{selectedIngestionError}</div> : null}

              {selectedIngestionLoading ? (
                <div className="empty-inline">Loading saved transcript...</div>
              ) : selectedIngestion ? (
                <div className="text-block saved-transcript-body">
                  <span className="text-block-label">Selected transcript</span>
                  <div className="stat-row">
                    <StatTile label="Transcript id" value={truncate(selectedIngestion.transcript_id, 20)} />
                    <StatTile label="Source meeting" value={selectedIngestion.source_meeting || "N/A"} />
                    <StatTile label="Input type" value={selectedIngestion.input_type || "N/A"} />
                    <StatTile label="Recorded at" value={formatTimestamp(selectedIngestion.recorded_at)} />
                  </div>
                  <pre>{safeText(selectedIngestion.transcript, "No transcript available.")}</pre>
                </div>
              ) : (
                <div className="empty-inline">Select a saved upload to view the full transcript and its stored metadata.</div>
              )}
            </div>
          </div>
        </PanelCard>
      </div>
    );
  }

  function renderGraphPanel() {
    return (
      <div className="content-grid">
        <PanelCard
          eyebrow="Graph"
          title="Decision relationships"
          description="Visualize saved decisions and the dependency edges between them."
          action={
            <button type="button" className="secondary-button" onClick={loadGraph} disabled={graphLoading}>
              {graphLoading ? "Refreshing..." : graphData ? "Refresh graph" : "Load graph"}
            </button>
          }
        >
          {graphError ? <div className="alert alert-error">{graphError}</div> : null}
          {graphData ? (
            <div className="stacked-content">
              <div className="stat-row">
                <StatTile label="Nodes" value={graphStats.nodes} />
                <StatTile label="Edges" value={graphStats.edges} />
                <StatTile label="Revisions" value={graphStats.history} />
                <StatTile label="Last refresh" value={lastGraphRefresh ? formatTimestamp(lastGraphRefresh) : "N/A"} />
              </div>
              <GraphSvg nodes={layoutNodes} edges={graphEdges} />
            </div>
          ) : (
            <div className="empty-inline">Load the graph to inspect dependencies and revision history.</div>
          )}
        </PanelCard>

        <PanelCard
          eyebrow="History"
          title="Recent revisions"
          description="Latest revision records returned by the graph endpoint."
        >
          {graphHistory.length ? (
            <div className="timeline">
              {graphHistory.slice(0, 5).map((item, index) => (
                <article className="timeline-item" key={`${item.decision || "revision"}-${item.recorded_at || index}`}>
                  <div className="timeline-top">
                    <strong>{safeText(item.decision)}</strong>
                    {item.status ? <span className="chip">{item.status}</span> : null}
                  </div>
                  <p>{safeText(item.source_meeting, "No source meeting")}</p>
                  <small>{formatTimestamp(item.recorded_at || item.timestamp)}</small>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty-inline">No revisions have been recorded yet.</div>
          )}
        </PanelCard>
      </div>
    );
  }

  function renderQueryPanel() {
    return (
      <div className="content-grid">
        <PanelCard
          eyebrow="Query"
          title="Ask the decision agent"
          description="Dependency queries use the graph first, then transcript search as a fallback."
          action={<span className="chip chip-muted">GET /query</span>}
        >
          <form className="form-stack" onSubmit={handleQuerySubmit}>
            <div className="field-group">
              <label htmlFor="queryText">Question</label>
              <textarea
                id="queryText"
                value={queryText}
                onChange={(event) => setQueryText(event.target.value)}
                placeholder='Why did we drop freemium?'
              />
              <p className="hint-text">
                Try questions like "What depends on AWS migration?" or "Why was the payment flow reversed?"
              </p>
            </div>

            {queryError ? <div className="alert alert-error">{queryError}</div> : null}

            <div className="action-row">
              <button type="submit" className="primary-button" disabled={queryLoading}>
                {queryLoading ? "Thinking..." : "Ask question"}
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={queryLoading}
                onClick={() => {
                  setQueryText("");
                  setQueryResult(null);
                  setQueryError("");
                }}
              >
                Clear
              </button>
            </div>
          </form>
        </PanelCard>

        <PanelCard
          eyebrow="Answer"
          title="Query result"
          description="See the answer plus the supporting sources returned by the backend."
        >
          {queryResult ? (
            <div className="stacked-content">
              <div className="chip-row">
                <span className="chip">Mode: {safeText(queryResult.mode, "semantic")}</span>
                {queryResult.relationship ? <span className="chip">Relationship: {queryResult.relationship}</span> : null}
                {queryResult.decision ? <span className="chip">Decision: {queryResult.decision}</span> : null}
              </div>

              <div className="answer-block">
                <span className="text-block-label">Answer</span>
                <p>{safeText(queryResult.answer, "No answer returned.")}</p>
              </div>

              {queryResult.mode === "dependency" && queryResult.decision_details ? (
                <>
                  <div className="stat-row">
                    <StatTile label="Decision" value={decisionDetails.decision} />
                    <StatTile label="Status" value={decisionDetails.status} />
                    <StatTile label="Owner" value={decisionDetails.owner} />
                    <StatTile label="Source meeting" value={decisionDetails.source_meeting} />
                  </div>
                  {decisionDetails.rationale ? (
                    <div className="text-block">
                      <span className="text-block-label">Rationale</span>
                      <p>{decisionDetails.rationale}</p>
                    </div>
                  ) : null}
                </>
              ) : null}

              {queryCandidates.length ? (
                <div className="subsection">
                  <h3>Candidate decisions</h3>
                  <CardList
                    items={queryCandidates}
                    emptyText="No candidate decisions returned."
                    className="card-grid"
                    renderItem={(item, index) => (
                      <article className="detail-card" key={`${item.decision || "candidate"}-${index}`}>
                        <div className="detail-top">
                          <strong>{safeText(item.decision)}</strong>
                          {item.status ? <span className="chip">{item.status}</span> : null}
                        </div>
                        <p>{safeText(item.owner, "No owner")}</p>
                        <small>{safeText(item.source_meeting || item.timestamp, "No source meeting")}</small>
                      </article>
                    )}
                  />
                </div>
              ) : null}

              <div className="subsection">
                <h3>Sources</h3>
                <CardList
                  items={querySources}
                  emptyText="No sources returned."
                  renderItem={(item, index) => {
                    const sourceLabel = safeText(item.decision || item.filename || item.source_meeting || `Source ${index + 1}`);
                    const bodyText = safeText(item.document || item.rationale || item.answer || item.text || "", "No preview available.");
                    const metaParts = [item.type, item.source_meeting, item.filename, item.timestamp].filter(Boolean);
                    return (
                      <article className="detail-card" key={`${sourceLabel}-${index}`}>
                        <div className="detail-top">
                          <strong>{sourceLabel}</strong>
                          {item.type ? <span className="chip">{item.type}</span> : null}
                        </div>
                        <p>{truncate(bodyText, 180)}</p>
                        <small>{metaParts.length ? metaParts.join(" | ") : "No metadata returned"}</small>
                      </article>
                    );
                  }}
                />
              </div>

              {upstreamDependencies.length || downstreamDecisions.length ? (
                <div className="relation-grid">
                  <div className="subsection">
                    <h3>Upstream dependencies</h3>
                    <CardList
                      items={upstreamDependencies}
                      emptyText="No upstream dependencies returned."
                      renderItem={(item, index) => (
                        <article className="detail-card" key={`upstream-${item.decision || index}`}>
                          <div className="detail-top">
                            <strong>{safeText(item.decision)}</strong>
                            {item.status ? <span className="chip">{item.status}</span> : null}
                          </div>
                          <p>{safeText(item.owner, "No owner")}</p>
                          <small>{safeText(item.source_meeting || item.timestamp, "No source meeting")}</small>
                        </article>
                      )}
                    />
                  </div>

                  <div className="subsection">
                    <h3>Downstream decisions</h3>
                    <CardList
                      items={downstreamDecisions}
                      emptyText="No downstream decisions returned."
                      renderItem={(item, index) => (
                        <article className="detail-card" key={`downstream-${item.decision || index}`}>
                          <div className="detail-top">
                            <strong>{safeText(item.decision)}</strong>
                            {item.status ? <span className="chip">{item.status}</span> : null}
                          </div>
                          <p>{safeText(item.owner, "No owner")}</p>
                          <small>{safeText(item.source_meeting || item.timestamp, "No source meeting")}</small>
                        </article>
                      )}
                    />
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="empty-inline">Submit a question to get a sourced answer from the graph or transcript index.</div>
          )}
        </PanelCard>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="hero">
        <div className="hero-copy">
          <span className="brand-badge">FastAPI / React / Vite</span>
          <h1>MeetingDNA</h1>
          <p>
            Upload transcripts, preserve decision history, and ask questions across the extracted graph without the
            legacy demo layer.
          </p>
        </div>

        <div className="hero-metrics">
          <StatTile label="Frontend" value="React + Vite" />
          <StatTile label="API" value={API_BASE_URL} />
          <StatTile label="Storage" value="Neo4j + ChromaDB" />
        </div>
      </header>

      <nav className="tab-bar" aria-label="MeetingDNA sections">
        {TAB_ITEMS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeTab === tab.key ? "tab-button active" : "tab-button"}
            onClick={() => handleTabChange(tab.key)}
            aria-pressed={activeTab === tab.key}
          >
            <strong>{tab.label}</strong>
            <span>{tab.hint}</span>
          </button>
        ))}
      </nav>

      <main className="workspace">
        {activeTab === "ingest" ? renderIngestionPanel() : null}
        {activeTab === "graph" ? renderGraphPanel() : null}
        {activeTab === "query" ? renderQueryPanel() : null}
      </main>
    </div>
  );
}
