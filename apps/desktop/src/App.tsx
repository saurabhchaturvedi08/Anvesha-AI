function App() {
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
            <h2>Welcome to Anvesha</h2>
            <p>
              This is the foundation of a local-first research workspace designed for
              evidence-based investigations and multi-agent research execution.
            </p>
            <ul>
              <li>Local-first desktop setup</li>
              <li>Provider-agnostic model routing</li>
              <li>Structured research workflow</li>
              <li>Evidence-backed reporting</li>
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
              <strong>Bootstrapped</strong>
            </div>
            <div className="status-row">
              <span>Architecture</span>
              <strong>Documented</strong>
            </div>
          </article>
        </div>

        <div className="cards">
          <article className="panel">
            <h3>Quick research templates</h3>
            <p>Market scan</p>
            <p>Technology comparison</p>
            <p>Evidence review</p>
          </article>
          <article className="panel">
            <h3>Configured providers</h3>
            <p>Ollama</p>
            <p>OpenAI</p>
            <p>Anthropic</p>
          </article>
          <article className="panel">
            <h3>Getting started</h3>
            <p>Connect a local or cloud model.</p>
            <p>Select a research objective.</p>
            <p>Review the generated plan.</p>
          </article>
        </div>
      </section>
    </main>
  );
}

export default App;
