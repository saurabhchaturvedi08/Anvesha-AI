"""Tracing: OpenTelemetry spans for runs, agent steps, model calls and tool calls.

Spans go to Langfuse (its OTLP endpoint) and/or any other OTLP/HTTP backend (Jaeger,
Honeycomb, Grafana Tempo...). With neither configured, every helper here is a cheap no-op.

Attributes follow the OpenTelemetry GenAI conventions (``gen_ai.*``) plus Langfuse's own
(``langfuse.*``), so Langfuse shows runs as traces, agents as nested observations, and
model calls as generations with tokens and cost. One research run is one Langfuse
session (``langfuse.session.id`` = run id): the first pass, the resume after plan
approval and any retries each make a trace in it.

Prompts, model outputs and tool arguments contain users' questions and text fetched
from the web, so they are only attached when ``ANVESHA_TRACE_CONTENT`` is on.

Anvesha keeps its own tracer provider instead of installing a process-wide one, so it
never changes what other libraries in the process do with OpenTelemetry.
"""

import base64
import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from anvesha import __version__
from anvesha.config import Settings

logger = logging.getLogger(__name__)

# Langfuse observation types (https://langfuse.com/docs/observability/features/observation-types).
ObservationType = Literal[
    "span", "generation", "embedding", "agent", "tool", "chain", "evaluator", "event"
]
AttributeValue = str | bool | int | float | Sequence[str]

# Attached content is clipped so one span never grows beyond what exporters accept.
MAX_CONTENT_CHARS = 32_000
MAX_MESSAGE_CHARS = 8_000


@dataclass
class _State:
    provider: TracerProvider | None = None
    tracer: Tracer = field(default_factory=trace.NoOpTracer)
    content: bool = False
    environment: str = "development"


_state = _State()


def langfuse_endpoint(host: str) -> str:
    return host.rstrip("/") + "/api/public/otel/v1/traces"


def langfuse_headers(public_key: str, secret_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def parse_headers(value: str | None) -> dict[str, str]:
    """``key=value,key2=value2`` (the OTEL_EXPORTER_OTLP_HEADERS format) as a dict."""
    headers: dict[str, str] = {}
    for item in (value or "").split(","):
        key, sep, val = item.partition("=")
        if sep and key.strip():
            headers[key.strip()] = val.strip()
    return headers


def exporters_from_settings(settings: Settings) -> list[SpanExporter]:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporters: list[SpanExporter] = []
    if settings.langfuse_enabled:
        assert settings.langfuse_public_key and settings.langfuse_secret_key
        exporters.append(
            OTLPSpanExporter(
                endpoint=langfuse_endpoint(settings.langfuse_host),
                headers=langfuse_headers(
                    settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value()
                ),
                timeout=10,
            )
        )
    if settings.otlp_traces_endpoint:
        headers = parse_headers(
            settings.otlp_headers.get_secret_value() if settings.otlp_headers else None
        )
        exporters.append(
            OTLPSpanExporter(endpoint=settings.otlp_traces_endpoint, headers=headers, timeout=10)
        )
    return exporters


def setup_tracing(
    settings: Settings,
    *,
    service: str = "anvesha",
    processors: Sequence[SpanProcessor] | None = None,
) -> bool:
    """Start exporting spans as configured. Returns whether tracing is on.

    ``processors`` replaces the configured exporters (tests pass an in-memory one).
    Calling this again replaces the previous setup.
    """
    shutdown_tracing()
    if processors is None:
        processors = [
            BatchSpanProcessor(exporter) for exporter in exporters_from_settings(settings)
        ]
    if not processors:
        return False
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service,
                "service.version": __version__,
                "deployment.environment.name": settings.env,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_rate)),
        shutdown_on_exit=False,
    )
    for processor in processors:
        provider.add_span_processor(processor)
    _state.provider = provider
    _state.tracer = provider.get_tracer("anvesha", __version__)
    _state.content = settings.trace_content
    _state.environment = settings.env
    logger.info(
        "Tracing on (%s)",
        ", ".join(
            name
            for name, enabled in (
                ("Langfuse", settings.langfuse_enabled),
                ("OTLP", bool(settings.otlp_traces_endpoint)),
            )
            if enabled
        )
        or "custom processors",
    )
    return True


def shutdown_tracing() -> None:
    """Flush buffered spans and turn tracing off."""
    provider = _state.provider
    _state.provider = None
    _state.tracer = trace.NoOpTracer()
    _state.content = False
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.warning("Flushing traces failed", exc_info=True)


def enabled() -> bool:
    return _state.provider is not None


def content_enabled() -> bool:
    """Whether prompts, outputs and tool arguments may be attached to spans."""
    return _state.provider is not None and _state.content


def _json(value: Any, limit: int = MAX_CONTENT_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean(attributes: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """Drop None values and stringify what OpenTelemetry can't store."""
    clean: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str | bool | int | float):
            clean[key] = value
        elif isinstance(value, Decimal):
            clean[key] = float(value)
        elif isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
            clean[key] = list(value)
        else:
            clean[key] = _json(value, 2000)
    return clean


def set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    if span.is_recording():
        span.set_attributes(_clean(attributes))


def set_input(span: Span, value: Any) -> None:
    """Attach what went into this step (only when content tracing is on)."""
    if content_enabled() and span.is_recording():
        span.set_attribute("langfuse.observation.input", _json(value))


def set_output(span: Span, value: Any) -> None:
    """Attach what came out of this step (only when content tracing is on)."""
    if content_enabled() and span.is_recording():
        span.set_attribute("langfuse.observation.output", _json(value))


def set_level(
    span: Span, level: Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"], message: str
) -> None:
    if span.is_recording():
        span.set_attribute("langfuse.observation.level", level)
        span.set_attribute("langfuse.observation.status_message", message[:1000])


def trace_attributes(
    *,
    name: str,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attributes that name a whole Langfuse trace; set them on its root span."""
    attributes: dict[str, Any] = {
        "langfuse.trace.name": name,
        "langfuse.session.id": session_id,
        "langfuse.user.id": user_id,
        "langfuse.environment": _state.environment,
    }
    if tags:
        attributes["langfuse.trace.tags"] = list(tags)
    for key, value in (metadata or {}).items():
        attributes[f"langfuse.trace.metadata.{key}"] = value
    return attributes


@contextmanager
def span(
    name: str,
    *,
    kind: ObservationType = "span",
    attributes: Mapping[str, Any] | None = None,
    input: Any = None,
    quiet: tuple[type[BaseException], ...] = (),
) -> Iterator[Span]:
    """A span around a block, nested under the current one.

    An exception marks the span as failed, unless it is one of ``quiet``: control flow
    such as a paused graph or a cancelled run, recorded as a warning instead.
    """
    with _state.tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as current:
        if current.is_recording():
            current.set_attribute("langfuse.observation.type", kind)
            if attributes:
                current.set_attributes(_clean(attributes))
            if input is not None:
                set_input(current, input)
        try:
            yield current
        except quiet as exc:
            set_level(current, "WARNING", type(exc).__name__)
            raise
        except BaseException as exc:
            record_error(current, exc)
            raise


def record_error(span: Span, exc: BaseException) -> None:
    if not span.is_recording():
        return
    message = f"{type(exc).__name__}: {exc}"
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, message[:500]))
    set_level(span, "ERROR", message)


def current_span() -> Span:
    return trace.get_current_span()


def chat_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """Model messages in the OpenAI-style shape Langfuse renders as a conversation."""
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.content
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[: MAX_MESSAGE_CHARS - 1] + "…"
        item: dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_calls:
            item["tool_calls"] = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        out.append(item)
    return out
