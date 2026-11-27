"""The eval harness: suite files, deterministic metrics, the gate, and a scripted run
scored end to end (the offline half of the CI eval gate)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI

from anvesha.evals import metrics
from anvesha.evals.__main__ import main as evals_main
from anvesha.evals.runner import (
    CaseResult,
    SuiteResult,
    aggregate,
    gate_checks,
    markdown_summary,
    parse_model,
    run_suite,
)
from anvesha.evals.suite import Fact, Gate, Suite, load_suite
from anvesha.graph.executor import RunExecutor
from anvesha.services import Services
from tests.test_runs import (  # noqa: F401  (fixtures)
    SOLAR_URL,
    ScriptedModel,
    executor,
    model,
    test_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

REPORT = """# Renewables in 2025

## Summary

Global solar capacity grew by 24 percent in 2025 [1]. Wind power grew by only six percent that year [2]. Storage is harder to measure because data is scarce in most countries.

## Findings

### Growth

- Solar installations outpaced every other technology during the year [1, 2].
- Short line [1].

_Some findings were not reviewed by the verifier._

| Technology | Growth |
|---|---|
| Solar module prices fell for a third straight year | [1] |

## Open questions

Nobody knows how fast offshore wind will recover after the delays of 2025.

## Sources

1. Solar in 2025 <https://energy.example/solar-2025>
2. Wind in 2025 <https://energy.example/wind-2025>
"""


def test_claim_sentences_skip_headings_notes_gaps_and_sources() -> None:
    sentences = metrics.claim_sentences(REPORT)
    assert sentences == [
        "Global solar capacity grew by 24 percent in 2025 [1].",
        "Wind power grew by only six percent that year [2].",
        "Storage is harder to measure because data is scarce in most countries.",
        "Solar installations outpaced every other technology during the year [1, 2].",
        "Solar module prices fell for a third straight year   [1]",
    ]
    assert metrics.cited_sentence_rate(REPORT) == pytest.approx(4 / 5)
    assert metrics.cited_sentence_rate("# Title\n\nShort.\n") is None


def test_a_trailing_citation_belongs_to_its_sentence() -> None:
    text = (
        "Solar capacity grew very quickly in 2025. [1] Wind power grew far more slowly than that."
    )
    assert metrics.claim_sentences(text) == [
        "Solar capacity grew very quickly in 2025 [1].",
        "Wind power grew far more slowly than that.",
    ]


def test_fact_recall_matches_every_group_after_normalisation() -> None:
    facts = [
        Fact(claim="Solar grew 24%", match=[["solar"], ["24 percent", "24%"]]),
        Fact(claim="Wind lagged", match=["wind", ["six percent", "6%"]]),
        Fact(claim="Hydro", match=["hydro"]),
        Fact(claim="Quoted", match=[["“third straight year”", "third straight year"]]),
    ]
    recall, missed = metrics.fact_recall(REPORT, facts)
    assert recall == pytest.approx(3 / 4) and missed == ["Hydro"]
    # The generated sources list doesn't count as the report stating something.
    assert metrics.fact_recall(REPORT, [Fact(claim="url", match=["energy.example"])]) == (
        0.0,
        ["url"],
    )
    assert metrics.fact_recall(REPORT, []) == (None, [])


def test_citation_precision_counts_invented_and_unmatched_citations() -> None:
    cited = [
        metrics.CitedFinding("a", "verified", True),
        metrics.CitedFinding("b", "quote_verified", True),
        metrics.CitedFinding("c", "verified", False),  # quote no longer in the source
    ]
    score = metrics.citation_precision(cited, invented=1)
    assert (score.precision, score.supported, score.unreviewed) == (0.5, 2, 1)
    assert metrics.citation_precision([], 0).precision is None
    assert metrics.sub_question_coverage(["answered", "partial", None]) == 0.5


def test_suite_validation_and_selection() -> None:
    suite = Suite.model_validate(
        {
            "name": "tiny",
            "case": [
                {"id": "one", "question": "What is the first question?", "facts": []},
                {"id": "two", "question": "What is the second question?", "depth": "standard"},
            ],
        }
    )
    assert [c.id for c in suite.select(["two"])] == ["two"]
    assert suite.depth(suite.cases[0]) == "quick" and suite.depth(suite.cases[1]) == "standard"
    assert suite.budget(suite.cases[0]) == Decimal("0.50")
    with pytest.raises(ValueError, match="unknown case ids: three"):
        suite.select(["three"])
    with pytest.raises(ValueError, match="duplicate case ids"):
        Suite.model_validate(
            {"case": [{"id": "x", "question": "Question one?!"}] * 2}  # same id twice
        )
    with pytest.raises(ValueError):
        Fact(claim="empty group", match=[[]])
    assert parse_model("openrouter:anthropic/claude:beta") == (
        "openrouter",
        "anthropic/claude:beta",
    )
    with pytest.raises(ValueError, match="unknown provider"):
        parse_model("nope:model")


def test_the_shipped_golden_suite_is_valid() -> None:
    suite = load_suite(REPO_ROOT / "evals" / "golden.toml")
    assert len(suite.cases) >= 5
    assert all(case.facts and case.urls for case in suite.cases)
    assert suite.gate.min_citation_precision is not None


def test_gate_checks_thresholds_and_baseline() -> None:
    cases = [
        CaseResult(
            "a",
            run_status="completed",
            citation_precision=1.0,
            fact_recall=0.5,
            cited_sentence_rate=0.9,
            cost_usd=Decimal("0.10"),
        ),
        CaseResult(
            "b",
            run_status="completed",
            citation_precision=0.8,
            fact_recall=None,
            cited_sentence_rate=0.7,
            cost_usd=Decimal("0.30"),
        ),
        CaseResult("c", run_status="failed", error="provider_auth_failed: bad key"),
    ]
    agg = aggregate(cases)
    assert agg["failed_cases"] == 1 and agg["citation_precision"] == pytest.approx(0.9)
    assert agg["fact_recall"] == 0.5  # only cases with facts
    assert agg["mean_cost_usd"] == Decimal("0.40") / 3

    gate = Gate(
        min_citation_precision=0.85,
        min_fact_recall=0.6,
        max_failed_cases=1,
        max_case_cost_usd=Decimal("0.25"),
    )
    verdicts = {c.name: c.passed for c in gate_checks(agg, gate)}
    assert verdicts == {
        "citation precision": True,
        "fact recall": False,
        "highest case cost (USD)": False,
        "failed cases": True,
    }

    baseline = {"citation_precision": 0.99, "fact_recall": 0.5, "mean_cost_usd": "0.05"}
    regressions = {c.name: c.passed for c in gate_checks(agg, Gate(), baseline)}
    assert regressions == {
        "citation precision vs baseline": False,  # 0.90 < 0.99 - 0.05
        "fact recall vs baseline": True,
        "mean cost vs baseline (USD)": False,  # 0.133 > 0.05 * 1.5
    }

    result = SuiteResult(
        "tiny", {"lead": "openai:gpt-4o-mini"}, "now", cases, agg, gate_checks(agg, gate)
    )
    summary = markdown_summary(result)
    assert "❌ failed" in summary and "`c`: provider_auth_failed: bad key" in summary
    assert json.loads(json.dumps(result.to_json()))["passed"] is False


def _services(app: FastAPI) -> Services:
    state = app.state
    return Services(
        settings=state.settings,
        db=state.db,
        vault=state.vault,
        http=state.http,
        fetcher=state.fetcher,
        storage=state.storage,
        gateway=state.gateway,
        sources=state.sources,
        tools=state.tools,
    )


def _suite(**gate: object) -> Suite:
    return Suite.model_validate(
        {
            "name": "scripted",
            "gate": gate,
            "case": [
                {
                    "id": "renewables",
                    "question": "How did renewables grow in 2025?",
                    "urls": [SOLAR_URL, "https://energy.example/missing"],
                    "facts": [
                        {"claim": "Solar grew", "match": [["solar"]]},
                        {"claim": "Solar grew 24%", "match": [["solar"], ["24 percent", "24%"]]},
                    ],
                }
            ],
        }
    )


async def test_a_scripted_run_is_scored_and_gated(
    app: FastAPI,
    model: ScriptedModel,  # noqa: F811
    executor: RunExecutor,  # noqa: F811
) -> None:
    model.reject_words = {"Wind"}
    suite = _suite(min_citation_precision=0.9, min_fact_recall=0.4, max_failed_cases=0)
    principal = app.state.local_principal
    result = await run_suite(_services(app), executor, principal, suite, suite.cases)

    [case] = result.cases
    assert case.run_status == "completed" and case.report_status == "complete", case
    # The writer cites [F1] (verified), [F2] (rejected, so not citable) and [F9] (made up).
    assert (case.citations, case.citations_supported, case.citations_invented) == (1, 1, 2)
    assert case.citation_precision == pytest.approx(1 / 3)
    assert case.cited_sentence_rate == 1.0  # "Solar grew fast [1] while wind lagged."
    assert case.fact_recall == 0.5 and case.facts_missed == ["Solar grew 24%"]
    assert (case.findings_verified, case.findings_rejected) == (1, 1)
    assert case.sub_question_coverage == 1.0
    assert case.cost_usd > 0 and case.model_calls == len(model.calls)
    assert len(case.seed_errors) == 1 and "energy.example/missing" in case.seed_errors[0]
    assert result.models == {
        role: "openai:gpt-4o-mini" for role in ("lead", "researcher", "verifier", "writer")
    }

    checks = {c.name: c.passed for c in gate_checks(result.aggregate, suite.gate)}
    assert checks == {"citation precision": False, "fact recall": True, "failed cases": True}


async def test_a_good_report_passes_the_gate(
    app: FastAPI,
    model: ScriptedModel,  # noqa: F811
    executor: RunExecutor,  # noqa: F811
) -> None:
    model.writer_text = (
        "# Renewables\n\n## Summary\n\nGlobal solar capacity grew by 24 percent in 2025 [F1]. "
        "Wind power capacity grew by only 6 percent in 2025 [F2].\n"
    )
    suite = _suite(min_citation_precision=0.95, min_cited_sentence_rate=0.9, min_fact_recall=1.0)
    result = await run_suite(
        _services(app), executor, app.state.local_principal, suite, suite.cases
    )
    result.checks = gate_checks(result.aggregate, suite.gate)
    assert result.passed, markdown_summary(result)
    assert result.cases[0].citation_precision == 1.0


def test_cli_reports_bad_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "x"\n[[case]]\nid = "Not A Slug"\nquestion = "Why is it so?"\n')
    assert evals_main(["run", str(bad), "--no-gate"]) == 2
    assert "error:" in capsys.readouterr().err
