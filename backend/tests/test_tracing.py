"""Tracing: what reaches Langfuse/OTLP from a research run, and what never does."""

import base64
import json
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from anvesha import tracing
from anvesha.config import Settings
from anvesha.graph.executor import RunExecutor
from tests.conftest import make_settings
from tests.test_runs import (  # noqa: F401  (fixtures)
    ScriptedModel,
    _project,
    _start,
    executor,
    model,
    test_settings,
)


def test_configuration() -> None:
    settings = make_settings(langfuse_public_key="pk-lf-1", langfuse_secret_key="sk-lf-1")
    assert settings.langfuse_enabled and settings.tracing_configured
    assert not make_settings(langfuse_public_key="pk-lf-1").tracing_configured
    assert not make_settings(langfuse_public_key="", langfuse_secret_key="").langfuse_enabled
    assert make_settings(otlp_traces_endpoint="http://localhost:4318/v1/traces").tracing_configured

    assert tracing.langfuse_endpoint("https://lf.example/") == (
        "https://lf.example/api/public/otel/v1/traces"
    )
    auth = tracing.langfuse_headers("pk", "sk")["Authorization"]
    assert base64.b64decode(auth.removeprefix("Basic ")) == b"pk:sk"
    assert tracing.parse_headers("a=1, b = two=2,broken,") == {"a": "1", "b": "two=2"}

    exporters = tracing.exporters_from_settings(
        make_settings(
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
            otlp_traces_endpoint="http://collector:4318/v1/traces",
        )
    )
    assert len(exporters) == 2
    for exporter in exporters:
        exporter.shutdown()


def test_without_configuration_tracing_is_a_no_op() -> None:
    assert tracing.setup_tracing(make_settings()) is False
    assert not tracing.enabled() and not tracing.content_enabled()
    with tracing.span("anything", input={"secret": "question"}) as span:
        assert not span.is_recording()


@pytest.fixture
def spans(test_settings: Settings) -> Iterator[InMemorySpanExporter]:  # noqa: F811
    exporter = InMemorySpanExporter()
    yield exporter
    tracing.shutdown_tracing()


def _install(exporter: InMemorySpanExporter, settings: Settings, **overrides: object) -> None:
    tracing.setup_tracing(
        settings.model_copy(update=overrides),
        processors=[SimpleSpanProcessor(exporter)],
    )


def _by_name(spans: tuple[ReadableSpan, ...], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


async def test_a_run_is_traced_with_agents_model_calls_and_tools(
    client: AsyncClient,
    model: ScriptedModel,  # noqa: F811
    executor: RunExecutor,  # noqa: F811
    spans: InMemorySpanExporter,
    test_settings: Settings,  # noqa: F811
) -> None:
    _install(spans, test_settings)  # by default, no content
    project_id = await _project(client)
    run = await _start(client, project_id, autopilot=True)
    await executor.execute(uuid.UUID(run["id"]))

    finished = spans.get_finished_spans()
    [root] = _by_name(finished, "research run")
    attributes = dict(root.attributes or {})
    assert attributes["langfuse.session.id"] == run["id"]
    assert attributes["langfuse.trace.name"] == "research run"
    assert attributes["anvesha.run.status"] == "completed"
    assert root.status.status_code != StatusCode.ERROR
    assert all(s.context.trace_id == root.context.trace_id for s in finished)

    names = {s.name for s in finished}
    for expected in (
        "plan",
        "review_plan",
        "researcher:1.1",
        "researcher:1.2",
        "verify",
        "write",
        "fetch_page",
        "record_finding",
    ):
        assert expected in names, expected

    generations = [
        s for s in finished if (s.attributes or {}).get("langfuse.observation.type") == "generation"
    ]
    assert len(generations) == len(model.calls)
    first = dict(generations[0].attributes or {})
    assert first["gen_ai.request.model"] == "gpt-4o-mini"
    assert first["gen_ai.usage.input_tokens"] == 1000
    assert json.loads(str(first["langfuse.observation.cost_details"]))["total"] > 0

    # A researcher's model calls and tools nest under its own span.
    [researcher] = _by_name(finished, "researcher:1.1")
    children = [s for s in finished if s.parent and s.parent.span_id == researcher.context.span_id]
    assert {"researcher · gpt-4o-mini", "fetch_page", "record_finding"} <= {
        s.name for s in children
    }

    # Content tracing is off: no prompts, questions, outputs or tool arguments.
    for span in finished:
        keys = set((span.attributes or {}).keys())
        assert not keys & {"langfuse.observation.input", "langfuse.observation.output"}, span.name


async def test_content_is_attached_only_when_enabled_and_pauses_are_not_errors(
    client: AsyncClient,
    model: ScriptedModel,  # noqa: F811
    executor: RunExecutor,  # noqa: F811
    spans: InMemorySpanExporter,
    test_settings: Settings,  # noqa: F811
) -> None:
    _install(spans, test_settings, trace_content=True)
    project_id = await _project(client)
    run = await _start(client, project_id)  # waits for plan approval
    await executor.execute(uuid.UUID(run["id"]))

    finished = spans.get_finished_spans()
    [root] = _by_name(finished, "research run")
    assert root.status.status_code != StatusCode.ERROR
    assert dict(root.attributes or {})["anvesha.run.status"] == "awaiting_approval"
    assert "How did renewables grow" in str((root.attributes or {})["langfuse.observation.input"])
    [paused] = _by_name(finished, "review_plan")
    assert dict(paused.attributes or {})["langfuse.observation.level"] == "WARNING"
    assert paused.status.status_code != StatusCode.ERROR

    [generation] = [s for s in finished if s.name.startswith("lead")]
    messages = json.loads(str((generation.attributes or {})["langfuse.observation.input"]))
    assert messages[0]["role"] == "system"
    assert "submit_plan" in str((generation.attributes or {})["langfuse.observation.output"])
