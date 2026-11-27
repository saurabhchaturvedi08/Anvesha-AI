# Evals

How we know a change to prompts, models or agent code made Anvesha better rather than worse.

`golden.toml` holds research questions with settled answers. Each one comes with a reference page that's added to the project before the run, and with the facts a correct report must state. The eval runner puts every question through the real pipeline (planner, researchers, verifier, writer) on autopilot, scores the saved report, and checks the scores against the gate.

## Metrics

All metrics are computed in code from what the run saved. No model grades another model.

| Metric | What it measures |
|---|---|
| **Citation precision** | Of the findings the report cites, plus any finding numbers the writer made up (they're dropped when the report is rendered), the share whose quote still matches its source text word for word and that the verifier didn't reject. Citations are rendered in code, so anything below 1.0 points to a bug or a writer inventing citations. |
| **Cited-sentence rate** | The share of claim sentences in the report that cite evidence. Headings, short lines, italic notes, the generated source list and sections such as "Open questions" don't count. |
| **Fact recall** | The share of a case's expected facts that the report states. A fact is a list of phrase groups, and every group must match at least one phrase (case-insensitive, with typography normalised). |
| **Sub-question coverage** | Each researcher's own verdict, averaged: answered = 1, partial = 0.5, not found = 0. Reported but not gated. |
| **Cost** | USD, tokens and model calls from `usage_events`, per case and as a mean. |

## Running

You need PostgreSQL (`docker compose up -d postgres`) and a model key in `.env`, for example `ANVESHA_OPENAI_API_KEY`.

```sh
cd backend
uv run python -m anvesha.evals run ../evals/golden.toml --model openai:gpt-4o-mini
uv run python -m anvesha.evals run ../evals/golden.toml --model openai:gpt-4o-mini \
    --role-model writer=anthropic:<model-id> --case crispr --case jwst \
    --out ../evals/results/latest.json
```

- Runs use a separate database, `anvesha_eval` on the same server (or `ANVESHA_EVAL_DATABASE_URL`). It's created and migrated automatically, so eval projects never show up in your workspace. You can still open them in the web app by pointing a backend at that database.
- `--parallel N` runs N cases at a time. `--no-gate` only reports. `--summary FILE` appends the Markdown table to FILE (CI passes `$GITHUB_STEP_SUMMARY`).
- Exit codes: 0 means the gate passed, 1 means it failed, and 2 means the eval couldn't run (bad suite, no model, and so on).
- With tracing on, each case is a Langfuse trace named `eval golden/<case>` and tagged `eval`, with the scores attached as metadata.

To score any finished run, including a production run without expected facts:

```sh
uv run python -m anvesha.evals score <run-id>
uv run python -m anvesha.evals score <run-id> --suite ../evals/golden.toml --case crispr
```

## The gate

The `[gate]` table in the suite sets the thresholds. Means are taken over completed cases, and cases that didn't complete count against `max_failed_cases`. Pass `--baseline results.json` to also fail on regressions: a quality metric dropping by more than `max_regression`, or the mean cost growing by more than `max_cost_increase`.

The gate runs in two places:

1. **On every push** (`.github/workflows/ci.yml`, in `backend/tests/test_evals.py`). The whole harness runs on a scripted model and fake websites: running cases, scoring, the gate failing a report with invented citations, and the gate passing a good one. This catches broken metrics, citation rendering or verification without spending anything.
2. **Live** (`.github/workflows/evals.yml`). The golden suite runs on a real model when triggered manually, every Monday, and on pull requests labelled `run-evals`. It needs a repository secret `EVAL_OPENAI_API_KEY` (or `EVAL_ANTHROPIC_API_KEY`, `EVAL_GEMINI_API_KEY`, `EVAL_OPENROUTER_API_KEY`), and the model comes from the `EVAL_MODEL` variable (default `openai:gpt-4o-mini`). `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` secrets add traces. If `evals/baseline.json` exists, runs are compared against it.

### Calibrating

The thresholds in `golden.toml` are starting points and haven't been measured yet. Fact recall in particular depends on the model. Before relying on the gate:

1. Run the suite three times with your eval model and look at the spread.
2. Set `min_fact_recall` and the cost limits just below the worst run.
3. Commit one run's results as `evals/baseline.json`, so later runs are also compared with it.

## Adding a case

Choose a question with a settled answer and a stable source that allows fetching (Wikipedia works well). Keep facts to names, numbers and terms that any correct report would use, and list synonyms:

```toml
[[case]]
id = "my-case"            # lowercase, digits, - and _
question = "..."
urls = ["https://..."]    # seed sources (optional)
depth = "quick"           # optional, default from [defaults]
max_cost_usd = "0.50"     # optional

[[case.facts]]
claim = "What a correct report says"
match = [["first", "synonym"], ["second term"]]
```
