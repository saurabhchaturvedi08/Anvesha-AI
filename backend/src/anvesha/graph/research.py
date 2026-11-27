"""The research graph: plan → (approve) → research in parallel → verify → review → write.

    START → plan → review_plan ──Send(N)─► research ─► verify ─► reflect ─┬─Send(N)─► research
                   (interrupt for                                        └──────────► write → END
                    plan approval)

State holds only small, JSON-native values: sub-question ids and texts, contradictions,
and why research stopped. The evidence itself lives in Postgres (sub_questions,
findings), so checkpoints stay small and every step can be retried safely.
"""

import functools
import operator
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt
from pydantic import ValidationError
from sqlalchemy import delete, update

from anvesha import tracing
from anvesha.agents.context import RunCancelled, RunContext
from anvesha.agents.evidence import load_findings, load_sub_questions
from anvesha.agents.lead import PlannedQuestion, make_plan, review_progress
from anvesha.agents.researcher import Researcher
from anvesha.agents.verifier import verify_findings
from anvesha.agents.writer import write_report
from anvesha.db.models import Run, SubQuestion
from anvesha.errors import InvalidInputError
from anvesha.events import clip
from anvesha.llm.errors import BudgetExceededError


class ResearchState(TypedDict, total=False):
    round: int
    # Sub-questions to research next: [{"id", "question", "rationale", "round", "ordinal"}].
    pending: list[dict[str, Any]]
    researched: Annotated[list[str], operator.add]
    contradictions: list[dict[str, Any]]
    # Why research ended before the lead was satisfied ("budget"), if it did.
    stop_reason: str | None
    report_id: str
    report_status: str


class ResearchTask(TypedDict):
    sub_question: dict[str, Any]
    others: list[str]
    label: str


async def replace_round(
    ctx: RunContext, round_: int, questions: list[PlannedQuestion]
) -> list[dict[str, Any]]:
    """Store a round's sub-questions (replacing any from an interrupted attempt)."""
    rows = [
        SubQuestion(
            id=uuid.uuid4(),
            run_id=ctx.run_id,
            workspace_id=ctx.workspace_id,
            round=round_,
            ordinal=index,
            question=item.question,
            rationale=item.rationale,
        )
        for index, item in enumerate(questions, start=1)
    ]
    async with ctx.db.session() as session:
        await session.execute(
            delete(SubQuestion).where(SubQuestion.run_id == ctx.run_id, SubQuestion.round == round_)
        )
        session.add_all(rows)
        await session.commit()
    return [
        {
            "id": str(row.id),
            "question": row.question,
            "rationale": row.rationale,
            "round": row.round,
            "ordinal": row.ordinal,
        }
        for row in rows
    ]


async def _save_plan(ctx: RunContext, strategy: str, pending: list[dict[str, Any]]) -> None:
    plan = {
        "strategy": strategy,
        "sub_questions": [
            {"id": p["id"], "question": p["question"], "rationale": p["rationale"]} for p in pending
        ],
    }
    async with ctx.db.session() as session:
        await session.execute(update(Run).where(Run.id == ctx.run_id).values(plan=plan))
        await session.commit()


async def plan(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    await ctx.check_cancelled(force=True)
    await ctx.emit("stage", {"stage": "planning"}, agent="lead")
    result = await make_plan(ctx)
    pending = await replace_round(ctx, 1, result.sub_questions)
    await _save_plan(ctx, result.strategy, pending)
    await ctx.emit(
        "plan_created",
        {"strategy": clip(result.strategy), "sub_questions": [p["question"] for p in pending]},
        agent="lead",
    )
    return {"round": 1, "pending": pending}


def parse_plan_edits(decision: Any) -> list[PlannedQuestion] | None:
    """The user's edited sub-questions from an approval, or None to keep the plan."""
    if not isinstance(decision, dict) or not decision.get("sub_questions"):
        return None
    try:
        return [PlannedQuestion.model_validate(item) for item in decision["sub_questions"]]
    except ValidationError as exc:
        # The API validates edits first, so this only guards against stored bad data.
        raise InvalidInputError(f"Invalid plan edits: {exc}") from exc


async def review_plan(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    if ctx.autopilot:
        return {}
    # Pauses the run here. Everything above this line must be safe to repeat: the node
    # runs again from the top when the run resumes with the user's decision.
    decision = interrupt({"type": "plan_approval"})
    pending = state.get("pending", [])
    edits = parse_plan_edits(decision)
    if edits is not None:
        pending = await replace_round(ctx, 1, edits)
        strategy = str(decision.get("strategy") or "") if isinstance(decision, dict) else ""
        await _save_plan(ctx, strategy, pending)
    await ctx.emit(
        "plan_approved", {"edited": edits is not None, "sub_questions": len(pending)}, agent="user"
    )
    return {"pending": pending}


def dispatch(state: ResearchState) -> list[Send] | str:
    """One researcher per pending sub-question, all in parallel; or on to writing."""
    pending = state.get("pending") or []
    if not pending:
        return "write"
    questions = [item["question"] for item in pending]
    return [
        Send(
            "research",
            ResearchTask(
                sub_question=item,
                others=[q for q in questions if q != item["question"]],
                label=f"researcher:{item['round']}.{item['ordinal']}",
            ),
        )
        for item in pending
    ]


async def research(state: ResearchTask, runtime: Runtime[RunContext]) -> dict[str, Any]:
    await Researcher(runtime.context, state["sub_question"], state["label"]).run(state["others"])
    return {"researched": [state["sub_question"]["id"]]}


async def verify(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    await ctx.check_cancelled(force=True)
    await ctx.emit("stage", {"stage": "verifying"}, agent="verifier")
    summary = await verify_findings(ctx)
    await ctx.emit("verification_finished", asdict(summary), agent="verifier")
    return {}


async def reflect(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    round_ = state.get("round", 1)
    done: dict[str, Any] = {"pending": []}
    if ctx.budget.exhausted:
        await ctx.emit("stage", {"stage": "budget_reached"}, agent="lead")
        return done | {"stop_reason": "budget"}
    max_follow_ups = ctx.profile.max_follow_ups if round_ < ctx.profile.max_rounds else 0
    if max_follow_ups == 0:
        # Last round: nothing to decide. The writer notes contradictions itself.
        return done
    await ctx.check_cancelled(force=True)
    await ctx.emit("stage", {"stage": "reviewing", "round": round_}, agent="lead")
    try:
        review, contradictions = await review_progress(
            ctx,
            await load_sub_questions(ctx),
            await load_findings(ctx),
            max_follow_ups=max_follow_ups,
        )
    except BudgetExceededError:
        return done | {"stop_reason": "budget"}
    await ctx.emit(
        "review",
        {
            "assessment": clip(review.assessment),
            "contradictions": len(contradictions),
            "follow_up_questions": [q.question for q in review.follow_up_questions],
        },
        agent="lead",
    )
    update_: dict[str, Any] = {
        "contradictions": [*state.get("contradictions", []), *contradictions]
    }
    if not review.follow_up_questions:
        return done | update_
    pending = await replace_round(ctx, round_ + 1, review.follow_up_questions)
    return update_ | {"round": round_ + 1, "pending": pending}


async def write(state: ResearchState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    ctx = runtime.context
    stop_reason = state.get("stop_reason") or ("budget" if ctx.budget.exhausted else None)
    report_id, status = await write_report(
        ctx, contradictions=state.get("contradictions", []), stop_reason=stop_reason
    )
    return {"report_id": str(report_id), "report_status": status, "stop_reason": stop_reason}


def traced_node[F: Callable[..., Awaitable[dict[str, Any]]]](
    fn: F, kind: tracing.ObservationType
) -> F:
    """Run a graph node inside a span (named after the researcher for research steps).

    A paused graph (plan approval) and a cancelled run are control flow, not failures.
    """

    @functools.wraps(fn)
    async def node(state: Any, runtime: Runtime[RunContext]) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        name = fn.__name__
        if isinstance(state, dict) and "label" in state:  # a researcher's task
            name = str(state["label"])
            attributes["anvesha.sub_question"] = clip(state["sub_question"]["question"], 500)
        else:
            attributes["anvesha.round"] = state.get("round") if isinstance(state, dict) else None
        with tracing.span(
            name, kind=kind, attributes=attributes, quiet=(GraphBubbleUp, RunCancelled)
        ) as span:
            result = await fn(state, runtime)
            tracing.set_attributes(
                span, {"anvesha.budget.spent_usd": runtime.context.budget.spent_usd}
            )
            return result

    return node  # type: ignore[return-value]  # same signature, via functools.wraps


def build_research_graph(
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> CompiledStateGraph[ResearchState, RunContext, ResearchState, ResearchState]:
    graph = StateGraph(ResearchState, context_schema=RunContext)
    graph.add_node("plan", traced_node(plan, "agent"))
    graph.add_node("review_plan", traced_node(review_plan, "span"))
    graph.add_node("research", traced_node(research, "agent"), input_schema=ResearchTask)
    graph.add_node("verify", traced_node(verify, "evaluator"))
    graph.add_node("reflect", traced_node(reflect, "agent"))
    graph.add_node("write", traced_node(write, "agent"))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "review_plan")
    graph.add_conditional_edges("review_plan", dispatch, ["research", "write"])
    graph.add_edge("research", "verify")
    graph.add_edge("verify", "reflect")
    graph.add_conditional_edges("reflect", dispatch, ["research", "write"])
    graph.add_edge("write", END)
    return graph.compile(checkpointer=checkpointer)
