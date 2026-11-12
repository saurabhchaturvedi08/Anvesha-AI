# Agent Design

## Agent hierarchy

### Supervisor Agent

Coordinates lifecycle state, retries, checkpoints, budgets, task scheduling, and verification triggers.

### Research Planner Agent

Breaks a user goal into structured research questions, constraints, and tasks with explicit completion criteria.

### Research Agent

Executes subtasks, collects sources, and returns findings with provenance.

### Search Agent

Uses tool adapters to search provider APIs and normalize results into canonical source records.

### Extraction Agent

Pulls out claims, named entities, and explicit dates and links them to source identifiers. Every extraction includes a valid source index and exact quote; entity/date values must occur in that quoted source text.

### Analysis Agent

Compares findings, identifies contradictions, and summarizes patterns with citations.

### Verification Agent

Reviews whether each claim follows from its exact quoted source passage, considering negation, qualifiers, and scope. Its verdict can reject or qualify a claim, but cannot independently approve evidence: a separate deterministic gate requires a literal quote match before a claim can be marked supported. If the model reviewer is unavailable, the quote-match gate remains in effect and the fallback is visible.

### Report Agent

Organizes verified claim IDs into a report outline. The renderer uses only the referenced verified claims and their original citations.

### Document Follow-up Agents

The local-document workflow uses a planner to propose retrieval queries, a retrieval tool agent to fetch project-scoped excerpts, a supervisor to decide whether to run one additional retrieval round, an answer agent to extract claims with exact quotes, and the Verification Agent to review semantic entailment. The deterministic exact-quote check is performed before review; AI can reject a claim, but only exact-quote matches can be considered supported.

## Execution model

The workflow is a bounded, stateful LangGraph graph with explicit transitions:

1. Initialize research
2. Plan tasks and targeted search queries
3. Search and deduplicate sources
4. Extract candidate claims
5. Have the Verification Agent assess whether each claim follows from its cited passage
6. Require a deterministic exact-quote match and apply the verifier's rejection verdicts
7. Analyze relationships among verified claims
8. Let the supervisor schedule bounded follow-up searches
9. Organize verified claims into report sections
10. Render and persist the cited report

The current research graph uses AI-generated planning, task-directed search, a Research Agent that maps source-backed findings to planned tasks, structured claim/entity/date extraction, semantic claim review, cross-claim analysis, supervisor-led follow-up search, and report organization. Research Agent findings must cite an existing plan task, a valid source index, and a literal quote in that source. Extracted entities and dates are only surfaced when their exact quote and value match the cited source. The Verification Agent must review every candidate claim with a valid `supports`, `contradicts`, or `insufficient` verdict. Only a literal quote match combined with no rejecting AI verdict can be marked supported; AI approval alone can never satisfy evidence verification. If the Verification Agent is unavailable or returns an invalid/incomplete review, literal quote matching still applies and the response exposes that fallback. Quick, standard, and deep runs allow at most one, two, and three search rounds, respectively; each round searches at most three queries. The supervisor persists a checkpoint after each stage. Runs with no sources or no supported claims branch to an explicit incomplete report; they do not emit inferred findings. Failed or interrupted runs can resume from their most recent saved next-node state. If an agent is unavailable or returns unusable output, its status is visible and the graph uses a constrained fallback or omits that agent's output.

The local-document follow-up graph plans up to three retrieval queries, gathers project-scoped excerpts, and lets the supervisor schedule at most one additional round. The answer agent must return exact quotes and valid excerpt indices. The Verification Agent checks whether each answer statement follows from its excerpt; only literal quote matches that are not rejected by semantic review are included in the answer. If semantic review fails, the exact-quote-only fallback is visible. Both workflows expose an activity trace (agent, status, and action summary), never hidden chain-of-thought.

Project creation, document upload, persistence, retrieval execution, exact-quote verification, report rendering, and other CRUD/file operations are intentionally deterministic tool actions rather than AI decisions. AI is used for interpreting goals, selecting bounded work, extracting source-linked records, evaluating semantic claim support, comparing verified findings, and drafting evidence-linked answer claims.

## State design

The graph state stores the research identity, user request, config, research plan, tasks, execution status, claims, evidence, report metadata, and budget counters. Larger artifacts live outside the graph and are referenced by IDs.

## Safety rules

- Do not expose hidden chain-of-thought or private reasoning traces
- Do not mark a claim as supported without source-backed evidence
- Use bounded retries and cancellation controls
- Keep provider routing explicit and user-controlled
