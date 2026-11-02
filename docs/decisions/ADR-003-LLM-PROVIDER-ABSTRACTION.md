# ADR-003: LLM Provider Abstraction

## Status

Accepted

## Context

The product must support local and cloud LLMs without coupling the research engine to a single provider. We need flexible model routing across planning, research, extraction, analysis, verification, and report generation.

## Decision

Create explicit provider interfaces for model capabilities, request configuration, validation, usage tracking, and fallback behavior. The research workflow will call provider adapters rather than provider-specific SDKs directly.

## Consequences

### Positive

- Flexible model selection by role
- Local-first use without mandating cloud providers
- Easier extensibility for providers and future custom inference servers

### Negative

- More interface design work up front
- Provider capability mismatch must be handled explicitly
- Usage and cost tracking require consistent metadata
