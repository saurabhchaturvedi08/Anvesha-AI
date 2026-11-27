"""Eval suites: golden research questions with the facts a good report must contain.

Suites are TOML files (see ``evals/golden.toml`` in the repository root)::

    name = "golden"

    [defaults]
    depth = "quick"
    max_cost_usd = "0.50"

    [gate]
    min_citation_precision = 0.95

    [[case]]
    id = "crispr"
    question = "Who developed CRISPR-Cas9 genome editing, and how does it cut DNA?"
    urls = ["https://en.wikipedia.org/wiki/CRISPR_gene_editing"]

    [[case.facts]]
    claim = "Doudna and Charpentier developed it"
    match = [["doudna"], ["charpentier"]]

A fact counts as covered when the report contains, for every group in ``match``, at
least one of the group's phrases (case-insensitive, after the same typography
normalisation used for quote checks). Plain strings in ``match`` are one-phrase groups.
"""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Depth = Literal["quick", "standard", "deep"]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=500)
    match: list[list[Phrase]] = Field(min_length=1, max_length=10)

    @field_validator("match", mode="before")
    @classmethod
    def _plain_strings_are_groups(cls, value: object) -> object:
        if isinstance(value, list):
            return [[item] if isinstance(item, str) else item for item in value]
        return value

    @field_validator("match")
    @classmethod
    def _no_empty_groups(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not group for group in value):
            raise ValueError("every match group needs at least one phrase")
        return value


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: Slug
    question: str = Field(min_length=10, max_length=10_000)
    depth: Depth | None = None
    # Sources added to the project before the run (like a user's own links), so the case
    # doesn't depend on which search provider happens to be configured.
    urls: list[str] = Field(default_factory=list, max_length=20)
    facts: list[Fact] = Field(default_factory=list, max_length=50)
    # The run's spending limit; defaults to the suite's.
    max_cost_usd: Decimal | None = Field(default=None, gt=0)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: Depth = "quick"
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0)


class Gate(BaseModel):
    """Thresholds a suite run must meet. Unset thresholds aren't checked.

    Means are taken over the cases where a metric applies (fact recall only for cases
    with facts); cases whose run didn't complete count as failed instead.
    """

    model_config = ConfigDict(extra="forbid")

    min_citation_precision: float | None = Field(default=None, ge=0, le=1)
    min_cited_sentence_rate: float | None = Field(default=None, ge=0, le=1)
    min_fact_recall: float | None = Field(default=None, ge=0, le=1)
    max_mean_cost_usd: Decimal | None = Field(default=None, ge=0)
    max_case_cost_usd: Decimal | None = Field(default=None, ge=0)
    max_failed_cases: int | None = Field(default=None, ge=0)
    # Compared with a baseline results file (``--baseline``): the most a quality metric
    # may drop, and the most the mean cost may grow (0.5 = 50%).
    max_regression: float = Field(default=0.05, ge=0, le=1)
    max_cost_increase: float = Field(default=0.5, ge=0)


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Slug = "suite"
    description: str = ""
    defaults: Defaults = Field(default_factory=Defaults)
    gate: Gate = Field(default_factory=Gate)
    cases: list[Case] = Field(alias="case", min_length=1)

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, cases: list[Case]) -> list[Case]:
        ids = [case.id for case in cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
        return cases

    def depth(self, case: Case) -> Depth:
        return case.depth or self.defaults.depth

    def budget(self, case: Case) -> Decimal:
        return case.max_cost_usd or self.defaults.max_cost_usd

    def select(self, ids: list[str]) -> list[Case]:
        if not ids:
            return list(self.cases)
        known = {case.id for case in self.cases}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise ValueError(f"unknown case ids: {', '.join(unknown)}")
        return [case for case in self.cases if case.id in ids]


def load_suite(path: Path) -> Suite:
    with path.open("rb") as file:
        return Suite.model_validate(tomllib.load(file))
