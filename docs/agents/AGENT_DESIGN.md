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

Pulls out claims, evidence, entities, and dates and links them to source identifiers.

### Analysis Agent

Compares findings, identifies contradictions, and summarizes patterns with citations.

### Verification Agent

Validates whether cited evidence actually supports the claim; flags unsupported or conflicting assertions.

### Report Agent

Produces a final report with methodology, findings, limitations, and references.

## Execution model

The workflow is a bounded, stateful LangGraph graph with explicit transitions:

1. Initialize research
2. Planner validation
3. Create research tasks
4. Execute research tasks
5. Extract evidence
6. Deduplicate sources
7. Analyze findings
8. Verify claim support
9. Regenerate or expand research if evidence is insufficient
10. Generate and persist report

## State design

The graph state stores the research identity, user request, config, research plan, tasks, execution status, claims, evidence, report metadata, and budget counters. Larger artifacts live outside the graph and are referenced by IDs.

## Safety rules

- Do not expose hidden chain-of-thought or private reasoning traces
- Do not mark a claim as supported without source-backed evidence
- Use bounded retries and cancellation controls
- Keep provider routing explicit and user-controlled
