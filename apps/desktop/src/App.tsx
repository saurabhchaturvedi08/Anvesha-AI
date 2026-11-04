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
    } catch {
      setProjects([]);
    }
  }

  useEffect(() => {
    void loadProviders();
    void loadProjects();
  }, []);

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
      setForm({ ...defaultForm, title: '', objective: '' });
      await loadProviders();
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">Anvesha</div>
        <nav>
          <a href="#">Home</a>
          <a href="#">New Research</a>
          <a href="#">Research Workspace</a>
          <a href="#">Research History</a>
          <a href="#">Knowledge Library</a>
          <a href="#">Agents</a>
          <a href="#">Integrations</a>
          <a href="#">Settings</a>
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Open-source research platform</p>
            <h1>Explore. Investigate. Discover.</h1>
          </div>
          <button className="primary">New Research</button>
        </header>

        <div className="grid">
          <article className="panel hero">
            <h2>Phase 1: research foundation</h2>
            <p>
              The desktop shell is connected to the local backend, and the first
              research workflow is now in place with provider awareness and project
              creation.
            </p>
            <ul>
              <li>Local-first backend and project models</li>
              <li>Provider registration and validation</li>
              <li>Structured research planning</li>
              <li>Desktop-ready workflow controls</li>
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

        <div className="cards provider-grid">
          <article className="panel">
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
                  <strong>{project.title}</strong>
                  <small>{project.status}</small>
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

        <form className="panel form-panel" onSubmit={handleSubmit}>
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
      </section>
    </main>
  );
}

export default App;
