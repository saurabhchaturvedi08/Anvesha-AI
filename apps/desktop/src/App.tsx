import { FormEvent, useEffect, useState } from 'react';

type Provider = {
  id: string;
  name: string;
  type: string;
  model: string;
  status: string;
  base_url?: string;
  note?: string;
};

type Project = {
  id: string;
  title: string;
  objective: string;
  research_depth: string;
  status: string;
  preferred_source_types: string[];
};

type Plan = {
  project_id: string;
  title: string;
  objective: string;
  planning_mode?: string;
  tasks: {
    id: string;
    title: string;
    description: string;
    estimated_minutes: number;
    search_query?: string | null;
  }[];
};

type Execution = {
  project_id: string;
  execution_id: string;
  status: string;
  summary: { source_count: number; research_status: string; verified_claim_count: number };
  report: string;
  source_details: { title: string; url: string; snippet: string }[];
  research_findings: {
    task_id: string;
    task_title: string;
    statement: string;
    quote: string;
    source_index: number;
  }[];
  task_coverage: { task_id: string; task_title: string; status: string; finding_count: number }[];
  extracted_entities: {
    value: string;
    type: string;
    quote: string;
    source_index: number;
  }[];
  extracted_dates: { value: string; quote: string; source_index: number }[];
  entity_extraction_status: string;
  claims: {
    id: number;
    statement: string;
    status: string;
    verification_note: string;
    verification_agent_verdict: string;
    source_url?: string | null;
    evidence_id?: number | null;
  }[];
  analysis: { statement: string; relation: string; claim_indices: number[] }[];
  verification_status: string;
  agent_trace: { agent: string; status: string; detail: string }[];
  plan: Plan;
  search_status: string;
  model_status: string;
};

type LocalDocument = {
  id: number;
  filename: string;
  content_bytes: number;
  chunk_count: number;
  created_at: string;
};

type FollowUpResult = {
  answer: string;
  retrieval_mode: string;
  embedding_status: string;
  model_status: string;
  verification_status: string;
  sources: { index: number; filename: string; text: string }[];
  claims: {
    statement: string;
    quote: string;
    source_index: number;
    status: string;
    verification_agent_verdict: string;
    verification_note: string;
    filename: string;
  }[];
  agent_trace: { agent: string; status: string; detail: string }[];
};

type StoredClaim = {
  id: number;
  execution_id: string;
  statement: string;
  status: string;
  verification_note: string;
};

type StoredEvidence = {
  id: number;
  execution_id: string;
  claim_id: number;
  source_id: number;
  quote: string;
  relation: string;
};

type StoredSource = {
  id: number;
  title: string;
  url: string;
  snippet: string;
  provider: string;
};

const defaultForm = {
  title: 'AI startup market scan',
  objective: 'Analyze emerging AI startup builders in the developer tooling space.',
  research_depth: 'standard',
  preferred_source_types: ['web', 'docs'],
};

function App() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [form, setForm] = useState(defaultForm);
  const [activePlan, setActivePlan] = useState<Plan | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [workingProjectId, setWorkingProjectId] = useState<string | null>(null);
  const [resumableExecutions, setResumableExecutions] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState('');
  const [knowledgeProjectId, setKnowledgeProjectId] = useState('');
  const [documents, setDocuments] = useState<LocalDocument[]>([]);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [followUpResult, setFollowUpResult] = useState<FollowUpResult | null>(null);
  const [knowledgeBusy, setKnowledgeBusy] = useState(false);
  const [storedClaims, setStoredClaims] = useState<StoredClaim[]>([]);
  const [storedEvidence, setStoredEvidence] = useState<StoredEvidence[]>([]);
  const [storedSources, setStoredSources] = useState<StoredSource[]>([]);
  const [evidenceQuery, setEvidenceQuery] = useState('');
  const [evidenceStatus, setEvidenceStatus] = useState('all');

  async function loadProviders() {
    try {
      const response = await fetch('http://127.0.0.1:8001/research/providers');
      if (!response.ok) {
        throw new Error('Failed to load providers');
      }
      const data = (await response.json()) as Provider[];
      setProviders(data);
    } catch {
      setProviders([]);
    }
  }

  async function loadProjects() {
    try {
      const response = await fetch('http://127.0.0.1:8001/research/projects');
      if (!response.ok) {
        throw new Error('Failed to load projects');
      }
      const data = (await response.json()) as Project[];
      setProjects(data);
      setKnowledgeProjectId((current) =>
        current && data.some((project) => project.id === current) ? current : data[0]?.id ?? '',
      );
      const recentProjects = data.slice(0, 3);
      const histories = await Promise.all(
        recentProjects.map(async (project) => {
          try {
            const historyResponse = await fetch(
              `http://127.0.0.1:8001/research/projects/${project.id}/executions`,
            );
            if (!historyResponse.ok) {
              return { projectId: project.id, resumableId: null };
            }
            const runs = (await historyResponse.json()) as { id: string; status: string }[];
            const resumable = runs.find((run) => run.status === 'failed' || run.status === 'running');
            return { projectId: project.id, resumableId: resumable?.id ?? null };
          } catch {
            return { projectId: project.id, resumableId: null };
          }
        }),
      );
      setResumableExecutions(
        Object.fromEntries(
          histories
            .filter((history): history is { projectId: string; resumableId: string } => history.resumableId !== null)
            .map((history) => [history.projectId, history.resumableId]),
        ),
      );
    } catch {
      setProjects([]);
      setResumableExecutions({});
    }
  }

  async function generatePlan(projectId: string) {
    setWorkingProjectId(projectId);
    setActionError('');
    try {
      const response = await fetch(`http://127.0.0.1:8001/research/projects/${projectId}/plan`, {
        method: 'POST',
      });
      if (!response.ok) {
        throw new Error('Could not generate the research plan.');
      }
      setActivePlan((await response.json()) as Plan);
      setExecution(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not generate the research plan.');
    } finally {
      setWorkingProjectId(null);
    }
  }

  async function executeProject(projectId: string) {
    setWorkingProjectId(projectId);
    setActionError('');
    try {
      const response = await fetch(`http://127.0.0.1:8001/research/projects/${projectId}/execute`, {
        method: 'POST',
      });
      if (!response.ok) {
        const errorBody = (await response.json()) as { detail?: { execution_id?: string } };
        const executionId = errorBody.detail?.execution_id;
        if (executionId) {
          setResumableExecutions((current) => ({ ...current, [projectId]: executionId }));
        }
        throw new Error('Could not run the research workflow.');
      }
      const result = (await response.json()) as Execution;
      setExecution(result);
      setActivePlan(result.plan);
      setKnowledgeProjectId(projectId);
      setResumableExecutions((current) => {
        const next = { ...current };
        delete next[projectId];
        return next;
      });
      await loadProjects();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not run the research workflow.');
    } finally {
      setWorkingProjectId(null);
    }
  }

  async function resumeProject(projectId: string, executionId: string) {
    setWorkingProjectId(projectId);
    setActionError('');
    try {
      const response = await fetch(
        `http://127.0.0.1:8001/research/projects/${projectId}/executions/${executionId}/resume`,
        { method: 'POST' },
      );
      if (!response.ok) {
        throw new Error('Could not resume the saved research execution.');
      }
      const result = (await response.json()) as Execution;
      setExecution(result);
      setActivePlan(result.plan);
      setKnowledgeProjectId(projectId);
      setResumableExecutions((current) => {
        const next = { ...current };
        delete next[projectId];
        return next;
      });
      await loadProjects();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not resume the saved research execution.');
    } finally {
      setWorkingProjectId(null);
    }
  }

  useEffect(() => {
    void loadProviders();
    void loadProjects();
  }, []);

  useEffect(() => {
    if (!knowledgeProjectId) {
      setDocuments([]);
      setStoredClaims([]);
      setStoredEvidence([]);
      setStoredSources([]);
      return;
    }
    const projectUrl = `http://127.0.0.1:8001/research/projects/${knowledgeProjectId}`;
    void Promise.all([
      fetch(`${projectUrl}/documents`),
      fetch(`${projectUrl}/claims`),
      fetch(`${projectUrl}/evidence`),
      fetch(`${projectUrl}/sources`),
    ])
      .then(async ([documentsResponse, claimsResponse, evidenceResponse, sourcesResponse]) => {
        if (![documentsResponse, claimsResponse, evidenceResponse, sourcesResponse].every((response) => response.ok)) {
          throw new Error('Could not load project research records.');
        }
        const [projectDocuments, claims, evidence, sources] = await Promise.all([
          documentsResponse.json() as Promise<LocalDocument[]>,
          claimsResponse.json() as Promise<StoredClaim[]>,
          evidenceResponse.json() as Promise<StoredEvidence[]>,
          sourcesResponse.json() as Promise<StoredSource[]>,
        ]);
        setDocuments(projectDocuments);
        setStoredClaims(claims);
        setStoredEvidence(evidence);
        setStoredSources(sources);
      })
      .catch(() => {
        setDocuments([]);
        setStoredClaims([]);
        setStoredEvidence([]);
        setStoredSources([]);
      });
  }, [knowledgeProjectId]);

  function downloadFile(filename: string, content: string, contentType: string) {
    const blobUrl = URL.createObjectURL(new Blob([content], { type: contentType }));
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(blobUrl);
  }

  function exportReport() {
    if (!execution) return;
    const projectTitle = projects.find((project) => project.id === execution.project_id)?.title ?? 'research';
    const claims = execution.claims.map((claim) => {
      const evidence = storedEvidence.find((item) => item.claim_id === claim.id);
      return `## ${claim.statement}\n\nStatus: ${claim.status}\n\n${claim.verification_note}${evidence ? `\n\n> ${evidence.quote}` : ''}`;
    });
    const sources = execution.source_details.map((source) => `- [${source.title}](${source.url})\n  ${source.snippet}`);
    const markdown = [
      `# ${execution.plan.title}`,
      '',
      `Execution: ${execution.execution_id}`,
      '',
      execution.report,
      '',
      '# Claim verification',
      '',
      ...claims,
      '',
      '# Sources',
      '',
      ...sources,
    ].join('\n');
    downloadFile(`${projectTitle.replace(/[^a-z0-9-_]+/gi, '-').toLowerCase()}-research.md`, markdown, 'text/markdown');
  }

  function exportEvidenceBundle() {
    const project = projects.find((item) => item.id === knowledgeProjectId);
    const bundle = {
      project: project ? { id: project.id, title: project.title, objective: project.objective } : null,
      claims: storedClaims,
      evidence: storedEvidence,
      sources: storedSources,
      exported_at: new Date().toISOString(),
    };
    const name = (project?.title ?? 'research').replace(/[^a-z0-9-_]+/gi, '-').toLowerCase();
    downloadFile(`${name}-evidence.json`, JSON.stringify(bundle, null, 2), 'application/json');
  }

  const visibleClaims = storedClaims.filter((claim) => {
    const matchesStatus = evidenceStatus === 'all' || claim.status === evidenceStatus;
    const matchesQuery = `${claim.statement} ${claim.verification_note}`.toLowerCase().includes(evidenceQuery.toLowerCase());
    return matchesStatus && matchesQuery;
  });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const response = await fetch('http://127.0.0.1:8001/research/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });

    if (response.ok) {
      const created = (await response.json()) as Project;
      setProjects((current) => [created, ...current]);
      setKnowledgeProjectId(created.id);
      setForm({ ...defaultForm, title: '', objective: '' });
      await loadProviders();
    }
  }

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!knowledgeProjectId || !documentFile) {
      setActionError('Choose a project and a text or Markdown file first.');
      return;
    }
    setKnowledgeBusy(true);
    setActionError('');
    try {
      const response = await fetch(`http://127.0.0.1:8001/research/projects/${knowledgeProjectId}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: documentFile.name, content: await documentFile.text() }),
      });
      if (!response.ok) {
        const body = (await response.json()) as { detail?: string };
        throw new Error(body.detail ?? 'Could not add the document.');
      }
      setDocumentFile(null);
      const input = document.getElementById('knowledge-file') as HTMLInputElement | null;
      if (input) input.value = '';
      const documentsResponse = await fetch(`http://127.0.0.1:8001/research/projects/${knowledgeProjectId}/documents`);
      setDocuments((await documentsResponse.json()) as LocalDocument[]);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not add the document.');
    } finally {
      setKnowledgeBusy(false);
    }
  }

  async function submitFollowUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!knowledgeProjectId || !knowledgeQuery.trim()) return;
    setKnowledgeBusy(true);
    setActionError('');
    try {
      const response = await fetch(`http://127.0.0.1:8001/research/projects/${knowledgeProjectId}/follow-up`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: knowledgeQuery }),
      });
      if (!response.ok) {
        throw new Error('Could not answer the follow-up question.');
      }
      setFollowUpResult((await response.json()) as FollowUpResult);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not answer the follow-up question.');
    } finally {
      setKnowledgeBusy(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">Anvesha</div>
        <nav>
          <a href="#home">Home</a>
          <a href="#new-research">New Research</a>
          <a href="#research-workspace">Research Workspace</a>
          <a href="#research-history">Research History</a>
          <a href="#knowledge-library">Knowledge Library</a>
        </nav>
      </aside>

      <section className="content" id="home">
        <header className="topbar">
          <div>
            <p className="eyebrow">Open-source research platform</p>
            <h1>Explore. Investigate. Discover.</h1>
          </div>
          <button className="primary">New Research</button>
        </header>

        <div className="grid">
          <article className="panel hero">
            <h2>Agentic, evidence-backed research</h2>
            <p>
              A planning agent decomposes each objective, search agents gather sources,
              and a supervisor can launch bounded follow-up research before claims are verified.
            </p>
            <ul>
              <li>AI-generated research tasks and targeted search queries</li>
              <li>Bounded supervisor-led follow-up searches</li>
              <li>AI analysis and report organization over verified claims</li>
              <li>Persisted claims with linked source evidence</li>
              <li>Quote-level support verification</li>
              <li>Agentic, quote-verified local-document follow-up</li>
              <li>Checkpoint recovery and visible agent activity traces</li>
            </ul>
          </article>

          <article className="panel status">
            <h3>Foundation status</h3>
            <div className="status-row">
              <span>Desktop shell</span>
              <strong>Ready</strong>
            </div>
            <div className="status-row">
              <span>Backend</span>
              <strong>Live</strong>
            </div>
            <div className="status-row">
              <span>Research API</span>
              <strong>Enabled</strong>
            </div>
          </article>
        </div>

        <div className="cards provider-grid" id="research-history">
          <article className="panel" id="integrations">
            <h3>Configured providers</h3>
            {providers.length === 0 ? (
              <p>Backend unavailable; provider status will load once the server is running.</p>
            ) : (
              providers.map((provider) => (
                <div className="provider-row" key={provider.id}>
                  <div>
                    <strong>{provider.name}</strong>
                    <small>{provider.model}</small>
                  </div>
                  <span className={`badge ${provider.status}`}>{provider.status}</span>
                </div>
              ))
            )}
          </article>

          <article className="panel">
            <h3>Recent research projects</h3>
            {projects.length === 0 ? (
              <p>No research projects yet.</p>
            ) : (
              projects.slice(0, 3).map((project) => (
                <div className="project-row" key={project.id}>
                  <div className="project-info">
                    <strong>{project.title}</strong>
                    <small>{project.status}</small>
                  </div>
                  <div className="project-actions">
                    <button
                      type="button"
                      className="secondary"
                      disabled={workingProjectId !== null}
                      onClick={() => void generatePlan(project.id)}
                    >
                      Agent plan
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={workingProjectId !== null}
                      onClick={() => void executeProject(project.id)}
                    >
                      {workingProjectId === project.id ? 'Running agents...' : 'Run agents'}
                    </button>
                    {resumableExecutions[project.id] && (
                      <button
                        type="button"
                        className="secondary"
                        disabled={workingProjectId !== null}
                        onClick={() => void resumeProject(project.id, resumableExecutions[project.id])}
                      >
                        Resume
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </article>

          <article className="panel">
            <h3>Getting started</h3>
            <p>Connect a local or cloud model.</p>
            <p>Select a research objective.</p>
            <p>Review the generated plan.</p>
          </article>
        </div>

        <form className="panel form-panel" id="new-research" onSubmit={handleSubmit}>
          <h3>Create a research project</h3>
          <label>
            Title
            <input
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              placeholder="AI startup market scan"
            />
          </label>

          <label>
            Objective
            <textarea
              value={form.objective}
              onChange={(event) => setForm({ ...form, objective: event.target.value })}
              rows={4}
              placeholder="Describe the research question and scope"
            />
          </label>

          <div className="inline-form">
            <label>
              Depth
              <select
                value={form.research_depth}
                onChange={(event) => setForm({ ...form, research_depth: event.target.value })}
              >
                <option value="quick">Quick</option>
                <option value="standard">Standard</option>
                <option value="deep">Deep</option>
              </select>
            </label>

            <label>
              Sources
              <input
                value={form.preferred_source_types.join(', ')}
                onChange={(event) =>
                  setForm({
                    ...form,
                    preferred_source_types: event.target.value
                      .split(',')
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })
                }
              />
            </label>
          </div>

          <button className="primary" type="submit">
            Create project
          </button>
        </form>

        <section className="panel knowledge-panel" id="knowledge-library">
          <div className="knowledge-header">
            <div>
              <p className="eyebrow">Local knowledge</p>
              <h2>Documents and follow-up</h2>
            </div>
            <label className="project-select-label">
              Project
              <select
                value={knowledgeProjectId}
                onChange={(event) => {
                  setKnowledgeProjectId(event.target.value);
                  setFollowUpResult(null);
                }}
                disabled={projects.length === 0}
              >
                {projects.length === 0 ? <option value="">No project</option> : null}
                {projects.map((project) => (
                  <option value={project.id} key={project.id}>{project.title}</option>
                ))}
              </select>
            </label>
          </div>

          <form className="knowledge-form" onSubmit={uploadDocument}>
            <label>
              Add text or Markdown
              <input
                id="knowledge-file"
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                onChange={(event) => setDocumentFile(event.target.files?.[0] ?? null)}
                disabled={!knowledgeProjectId || knowledgeBusy}
              />
            </label>
            <button className="secondary" type="submit" disabled={!documentFile || knowledgeBusy}>
              Add document
            </button>
          </form>

          <div className="document-list">
            {documents.length === 0 ? (
              <p>No local documents in this project.</p>
            ) : documents.map((item) => (
              <div className="document-row" key={item.id}>
                <strong>{item.filename}</strong>
                <small>{item.chunk_count} chunks</small>
              </div>
            ))}
          </div>

          <form className="knowledge-form follow-up-form" onSubmit={submitFollowUp}>
            <label>
              Ask about this project's documents
              <input
                value={knowledgeQuery}
                onChange={(event) => setKnowledgeQuery(event.target.value)}
                placeholder="What do the notes say about local search?"
                disabled={!knowledgeProjectId || knowledgeBusy}
              />
            </label>
            <button className="primary" type="submit" disabled={!knowledgeProjectId || !knowledgeQuery.trim() || knowledgeBusy}>
              {knowledgeBusy ? 'Working...' : 'Ask'}
            </button>
          </form>

          {followUpResult && (
            <div className="follow-up-result">
              <div className="execution-status">
                <span>Retrieval: {followUpResult.retrieval_mode}</span>
                <span>Embeddings: {followUpResult.embedding_status}</span>
                <span>Model: {followUpResult.model_status}</span>
                <span>Verification: {followUpResult.verification_status}</span>
              </div>
              {followUpResult.agent_trace.length > 0 && (
                <div className="agent-trace">
                  <h3>Agent activity</h3>
                  <ol>
                    {followUpResult.agent_trace.map((step, index) => (
                      <li key={`${step.agent}-${index}`}>
                        <div className="claim-heading">
                          <strong>{step.agent}</strong>
                          <span className={`badge ${step.status}`}>{step.status.replace('_', ' ')}</span>
                        </div>
                        <p>{step.detail}</p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
              <pre className="report-text">{followUpResult.answer}</pre>
              {followUpResult.claims.map((claim, index) => (
                <article className="claim-item" key={`${claim.source_index}-${index}`}>
                  <div className="claim-heading">
                    <strong>{claim.statement}</strong>
                    <span className={`badge ${claim.status}`}>{claim.status}</span>
                  </div>
                  <p>{claim.verification_note}</p>
                  <small>Verifier: {claim.verification_agent_verdict}</small>
                  <blockquote>{claim.quote}</blockquote>
                </article>
              ))}
              {followUpResult.sources.map((source) => (
                <article className="source-item" key={source.index}>
                  <strong>[{source.index}] {source.filename}</strong>
                  <p>{source.text}</p>
                </article>
              ))}
            </div>
          )}
        </section>

        {actionError && <p className="error-message" role="alert">{actionError}</p>}

        {activePlan && (
          <section className="panel result-panel">
            <p className="eyebrow">Research plan</p>
            <h2>{activePlan.title}</h2>
            <p>Planning: {activePlan.planning_mode?.replace('_', ' ') ?? 'template'}</p>
            <p>{activePlan.objective}</p>
            <ol className="task-list">
              {activePlan.tasks.map((task) => (
                <li key={task.id}>
                  <div>
                    <strong>{task.title}</strong>
                    <p>{task.description}</p>
                    {task.search_query && <small>Search: {task.search_query}</small>}
                  </div>
                  <small>{task.estimated_minutes} min</small>
                </li>
              ))}
            </ol>
          </section>
        )}

        {execution && (
          <section className="panel result-panel">
            <div className="result-heading">
              <div>
                <p className="eyebrow">Execution result</p>
                <h2>{execution.plan.title}</h2>
              </div>
              <button className="secondary" type="button" onClick={exportReport}>Export Markdown</button>
            </div>
            <div className="execution-status">
              <span>Planning: {execution.plan.planning_mode?.replace('_', ' ') ?? 'template'}</span>
              <span>Search: {execution.search_status}</span>
              <span>Model: {execution.model_status}</span>
              <span>AI verification: {execution.verification_status}</span>
              <span>Sources: {execution.summary.source_count}</span>
              <span>Verified claims: {execution.summary.verified_claim_count}</span>
            </div>
            {execution.agent_trace.length > 0 && (
              <div className="agent-trace">
                <h3>Agent activity</h3>
                <ol>
                  {execution.agent_trace.map((step, index) => (
                    <li key={`${step.agent}-${index}`}>
                      <div className="claim-heading">
                        <strong>{step.agent}</strong>
                        <span className={`badge ${step.status}`}>{step.status.replace('_', ' ')}</span>
                      </div>
                      <p>{step.detail}</p>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {execution.analysis.length > 0 && (
              <div className="claim-list">
                <h3>Agent analysis <small>(interpretations, not independently verified)</small></h3>
                {execution.analysis.map((item, index) => (
                  <article className="claim-item" key={`${item.relation}-${index}`}>
                    <div className="claim-heading">
                      <strong>{item.statement}</strong>
                      <span className="badge">{item.relation}</span>
                    </div>
                    <small>Based on verified claims {item.claim_indices.join(', ')}</small>
                  </article>
                ))}
              </div>
            )}
            {execution.task_coverage.length > 0 && (
              <div className="claim-list">
                <h3>Research task coverage</h3>
                {execution.task_coverage.map((task) => (
                  <article className="claim-item" key={task.task_id}>
                    <div className="claim-heading">
                      <strong>{task.task_title}</strong>
                      <span className={`badge ${task.status}`}>{task.status}</span>
                    </div>
                    <small>{task.finding_count} quote-checked finding(s)</small>
                  </article>
                ))}
              </div>
            )}
            {(execution.extracted_entities.length > 0 || execution.extracted_dates.length > 0) && (
              <div className="claim-list">
                <h3>Extracted entities and dates</h3>
                <small>Extraction: {execution.entity_extraction_status}</small>
                {[...execution.extracted_entities, ...execution.extracted_dates].map((item, index) => {
                  const entityType = 'type' in item && typeof item.type === 'string' ? item.type : null;
                  return (
                    <article className="claim-item" key={`${item.source_index}-${item.value}-${index}`}>
                      <div className="claim-heading">
                        <strong>{item.value}</strong>
                        {entityType && <span className="badge">{entityType}</span>}
                        <span>Source {item.source_index}</span>
                      </div>
                      <blockquote>{item.quote}</blockquote>
                    </article>
                  );
                })}
              </div>
            )}
            <pre className="report-text">{execution.report}</pre>
            {execution.claims.length > 0 && (
              <div className="claim-list">
                <h3>Claim verification</h3>
                {execution.claims.map((claim) => (
                  <article className="claim-item" key={claim.id}>
                    <div className="claim-heading">
                      <strong>{claim.statement}</strong>
                      <span className={`badge ${claim.status}`}>{claim.status}</span>
                    </div>
                    <p>{claim.verification_note}</p>
                    <small>Verifier: {claim.verification_agent_verdict}</small>
                    {claim.evidence_id && <small>Evidence record {claim.evidence_id}</small>}
                  </article>
                ))}
              </div>
            )}
            {execution.source_details.length > 0 && (
              <div className="source-list">
                <h3>Sources</h3>
                {execution.source_details.map((source) => (
                  <article className="source-item" key={source.url}>
                    <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                    <p>{source.snippet}</p>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}

        <section className="evidence-workspace" id="research-workspace" aria-labelledby="evidence-title">
          <div className="evidence-toolbar">
            <div>
              <p className="eyebrow">Research workspace</p>
              <h2 id="evidence-title">Evidence explorer</h2>
            </div>
            <div className="evidence-controls">
              <input
                aria-label="Filter claims"
                value={evidenceQuery}
                onChange={(event) => setEvidenceQuery(event.target.value)}
                placeholder="Filter claims"
              />
              <select aria-label="Filter by verification status" value={evidenceStatus} onChange={(event) => setEvidenceStatus(event.target.value)}>
                <option value="all">All statuses</option>
                <option value="supported">Supported</option>
                <option value="unsupported">Unsupported</option>
                <option value="unverified">Unverified</option>
              </select>
              <button className="secondary" type="button" onClick={exportEvidenceBundle} disabled={!knowledgeProjectId}>
                Export evidence JSON
              </button>
            </div>
          </div>
          {!knowledgeProjectId ? (
            <p>Select a project to inspect its evidence.</p>
          ) : visibleClaims.length === 0 ? (
            <p>No claims match this project and filter.</p>
          ) : (
            <div className="evidence-list">
              {visibleClaims.map((claim) => {
                const claimEvidence = storedEvidence.filter((item) => item.claim_id === claim.id);
                return (
                  <article className="evidence-item" key={claim.id}>
                    <div className="claim-heading">
                      <strong>{claim.statement}</strong>
                      <span className={`badge ${claim.status}`}>{claim.status}</span>
                    </div>
                    <p>{claim.verification_note}</p>
                    {claimEvidence.length > 0 ? claimEvidence.map((item) => {
                      const source = storedSources.find((candidate) => candidate.id === item.source_id);
                      return (
                        <blockquote key={item.id}>
                          <p>“{item.quote}”</p>
                          {source && <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>}
                        </blockquote>
                      );
                    }) : <small>No persisted supporting quote.</small>}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

export default App;
