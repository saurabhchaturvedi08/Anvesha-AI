"""Optional first step of a new research project: ask what an ambiguous question means.

The web app folds the answers into the question before starting the run, so the planner
(which never talks to the user) starts from a well-scoped question.
"""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from anvesha.agents import prompts
from anvesha.agents.structured import Completer, SubmitTool, structured_call
from anvesha.llm.types import Message

Option = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ClarifyingQuestion(BaseModel):
    question: str = Field(min_length=5, max_length=300)
    why: str = Field(default="", max_length=300, description="Why the answer matters.")
    options: list[Option] = Field(default_factory=list, max_length=4)


class Clarification(BaseModel):
    ready: bool = Field(description="True if the question can be researched as it is.")
    questions: list[ClarifyingQuestion] = Field(default_factory=list, max_length=3)


SUBMIT_CLARIFICATION = SubmitTool(
    "submit_clarification",
    "Submit whether the question is ready, and any clarifying questions.",
    Clarification,
)


async def clarify(completer: Completer, question: str) -> Clarification:
    result = await structured_call(
        completer,
        role="lead",
        messages=[
            Message.system(prompts.CLARIFIER),
            Message.user(f"Research question:\n{question}"),
        ],
        tool=SUBMIT_CLARIFICATION,
        max_tokens=1500,
    )
    if result.ready:
        result.questions = []
    elif not result.questions:
        result.ready = True
    return result
