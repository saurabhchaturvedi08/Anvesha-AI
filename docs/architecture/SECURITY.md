# Security Architecture

## Security boundary

The Python backend is treated as the primary security boundary. It binds only to localhost and manages all provider calls, file access, and persistence.

## Core controls

- Localhost-only backend binding
- Local, per-session auth token
- Restricted file access and safe path management
- Strict request validation and request-size limits
- SSRF protections for retrieval and web fetching
- Secret redaction in logs
- No arbitrary shell execution from research agents
- Least-privilege Tauri permissions

## Secret handling

API keys remain local. They must never be committed to the repository and should be stored using secure OS credential storage when available. The application should never print them in logs or telemetry output.

## Privacy and diagnostics

Diagnostics and telemetry are optional and disabled by default. Users should be able to see what data is sent to model providers, search providers, and document ingestion services.

## Threat model

The main threats are provider misuse, malicious or noisy web content, insecure filesystem access, and accidental disclosure of secrets. The architecture addresses them with explicit provider adapters, safe URL fetching rules, and local-only default storage.
