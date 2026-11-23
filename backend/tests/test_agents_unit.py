import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from anvesha.agents.citations import render_citations, report_title
from anvesha.agents.evidence import FindingView
from anvesha.agents.lead import Plan, PlannedQuestion, dedupe_questions
from anvesha.agents.researcher import quote_hash, trim_old_tool_outputs
from anvesha.agents.structured import SubmitTool, structured_call
from anvesha.graph.research import dispatch, parse_plan_edits
from anvesha.llm.budget import Budget
from anvesha.llm.errors import BudgetExceededError, LLMInvalidResponseError
from anvesha.llm.types import Completion, Message, TokenUsage, ToolCall
from anvesha.migrate import procrastinate_migrations_after
from anvesha.tools.base import tool_parameters_schema


def _finding(source: uuid.UUID, title: str, url: str | None = None) -> FindingView:
    return FindingView(
        id=uuid.uuid4(),
        sub_question_id=None,
        statement="s",
        quote="q",
        status="verified",
        judge_note=None,
        page=None,
        source_id=source,
        source_title=title,
        source_url=url,
        source_filename=None if url else "notes.pdf",
        published="2025",
        site_name=None,
    )


def test_citations_number_sources_in_order_of_first_use() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    findings = [
        _finding(a, "Agency report", "https://a.example"),
        _finding(b, "Notes"),
        _finding(a, "Agency report", "https://a.example"),
    ]
    draft = "# Title\n\nFirst [F2]. Second [F1; F3]. Both [F3, F2]. Bad [F7]. Odd [ F1 ,F2 ]."
    rendered = render_citations(draft, findings)
    assert rendered.markdown.splitlines()[2] == (
        "First [1]. Second [2]. Both [1, 2]. Bad. Odd [1, 2]."
    )
    assert rendered.unknown_markers == ["F7"]
    assert [c["number"] for c in rendered.citations] == [1, 2]
    assert rendered.citations[1]["finding_ids"] == [str(findings[0].id), str(findings[2].id)]
    assert rendered.markdown.endswith(
        "## Sources\n\n1. Notes (2025) (uploaded file: notes.pdf)\n"
        "2. Agency report (2025) <https://a.example>\n"
    )


def test_model_written_reference_lists_are_replaced_and_markdown_kept() -> None:
    finding = _finding(uuid.uuid4(), "Report", "https://r.example")
    draft = (
        "# T\n\nLine one  \nkeeps its break [F1]\n\n    code  block\n\n### References\n\n1. Fake"
    )
    rendered = render_citations(draft, [finding])
    assert "Fake" not in rendered.markdown
    assert "Line one  \nkeeps its break [1]" in rendered.markdown
    assert "    code  block" in rendered.markdown
    assert render_citations("No citations at all.", [finding]).citations == []
    second = _finding(uuid.uuid4(), "Other", "https://o.example")
    merged = render_citations("A [F1][F2]. B [F2] [F1] [F1]. C [F1] and [F2].", [finding, second])
    assert merged.markdown.startswith("A [1, 2]. B [1, 2]. C [1] and [2].")
    assert report_title("intro\n# My title\n", "fallback") == "My title"
    assert report_title("no heading", "fallback") == "fallback"


def test_old_tool_outputs_are_trimmed_but_recent_ones_kept() -> None:
    long = "x" * 2000
    messages = [Message.system("s"), Message.user("u")]
    for index in range(8):
        messages.append(Message.tool_result(f"c{index}", f"{index}{long}"))
    trimmed = trim_old_tool_outputs(messages, keep=6)
    assert [len(m.content) < 1000 for m in trimmed[2:]] == [True, True] + [False] * 6
    assert "Older tool output shortened" in trimmed[2].content
    assert trimmed[2].tool_call_id == "c0"
    assert messages[2].content.startswith("0x")  # the originals aren't modified


def test_quote_hash_ignores_harmless_differences() -> None:
    assert quote_hash("The “Quick” fox") == quote_hash('the "quick"   fox')
    assert quote_hash("a fox") != quote_hash("a dog")


class _Inner(BaseModel):
    title: str
    count: int = 0


class _Outer(BaseModel):
    title: str
    items: list[_Inner]
    best: _Inner | None = None


def test_tool_schemas_are_inlined_and_keep_properties_named_title() -> None:
    schema = tool_parameters_schema(_Outer)
    assert "$defs" not in schema and "title" not in schema
    assert set(schema["properties"]) == {"title", "items", "best"}
    item = schema["properties"]["items"]["items"]
    assert set(item["properties"]) == {"title", "count"} and "title" not in item
    assert "$ref" not in str(schema)


class _FakeContext:
    def __init__(self, replies: list[Completion]) -> None:
        self.replies = replies
        self.seen: list[list[Message]] = []

    async def complete(self, role: str, messages: list[Message], **_: Any) -> Completion:
        self.seen.append(list(messages))
        return self.replies.pop(0)


def _reply(text: str = "", *calls: ToolCall) -> Completion:
    return Completion(
        text=text, tool_calls=calls, usage=TokenUsage(), provider="p", model="m", finish_reason=None
    )


SUBMIT = SubmitTool("submit_plan", "Submit.", Plan)


async def test_structured_call_repairs_prose_and_invalid_arguments() -> None:
    good = {"strategy": "s", "sub_questions": [{"question": "What changed in 2025?"}]}
    ctx = _FakeContext(
        [
            _reply("Here is my plan in prose."),
            _reply("", ToolCall("1", "submit_plan", {"sub_questions": []})),
            _reply("", ToolCall("2", "submit_plan", good), ToolCall("3", "other", {})),
        ]
    )
    plan = await structured_call(ctx, role="lead", messages=[Message.user("q")], tool=SUBMIT)  # type: ignore[arg-type]
    assert plan.sub_questions[0].question == "What changed in 2025?"
    assert "not as text" in ctx.seen[1][-1].content
    assert "Invalid arguments" in ctx.seen[2][-1].content and ctx.seen[2][-1].tool_call_id == "1"

    stubborn = _FakeContext([_reply("no"), _reply("still no")])
    with pytest.raises(LLMInvalidResponseError):
        await structured_call(stubborn, role="lead", messages=[], tool=SUBMIT, attempts=2)  # type: ignore[arg-type]


def test_plan_helpers() -> None:
    questions = [
        PlannedQuestion(question=q)
        for q in ("What is solar?", "what  is SOLAR?", "What is wind?", "What is hydro?")
    ]
    assert [q.question for q in dedupe_questions(questions, 2)] == [
        "What is solar?",
        "What is wind?",
    ]
    assert (
        dedupe_questions(questions, 5, existing={"what is wind?"})[1].question == "What is hydro?"
    )
    assert parse_plan_edits({"action": "approve"}) is None
    edits = parse_plan_edits({"action": "approve", "sub_questions": [{"question": "Edited one?"}]})
    assert edits is not None and edits[0].question == "Edited one?"
    assert dispatch({"pending": []}) == "write"
    sends = dispatch(
        {
            "pending": [
                {"id": "1", "question": "A question?", "round": 1, "ordinal": 1, "rationale": ""},
                {"id": "2", "question": "B question?", "round": 1, "ordinal": 2, "rationale": ""},
            ]
        }
    )
    assert isinstance(sends, list) and [s.arg["label"] for s in sends] == [
        "researcher:1.1",
        "researcher:1.2",
    ]
    assert sends[0].arg["others"] == ["B question?"]


def test_budget_reserve_for_the_writer() -> None:
    budget = Budget(max_cost_usd=Decimal("1.00"), usable_fraction=Decimal("0.85"))
    budget.record(Decimal("0.90"), TokenUsage(input_tokens=10))
    assert budget.exhausted
    with pytest.raises(BudgetExceededError, match="available for this stage"):
        budget.check()
    budget.usable_fraction = Decimal(1)
    budget.check()  # the reserve is available for writing
    assert not budget.exhausted


def test_job_queue_schema_upgrades_pick_the_right_scripts() -> None:
    scripts = [p.name for p in procrastinate_migrations_after("3.0.0", "3.4.0")]
    assert scripts[0].startswith("03.01.00_01") and scripts[-1].startswith("03.04.00_50")
    assert not any(name.startswith("03.00.00") for name in scripts)
    assert procrastinate_migrations_after("3.11.0", "3.11.0") == []
