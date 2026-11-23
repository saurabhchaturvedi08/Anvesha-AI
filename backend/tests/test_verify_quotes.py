import pytest

from anvesha.verify.quotes import MAX_FRAGMENT_GAP, context_around, match_quote, normalize

SOURCE = (
    "In 2025, global solar capacity grew by 24 percent, according to the IEA.\n"
    "The agency\u2019s report said “growth was strongest in India” — a "
    "first.\nResearchers studied evalu-\nation methods; self-\nreported scores rose. "
    "ﬁnal ﬁgures arrive in March. Le café reste ouvert pour les étudiants."
)


def _matched(quote: str) -> list[str]:
    result = match_quote(quote, SOURCE)
    return [SOURCE[start:end] for start, end in result.spans]


def test_exact_quote_is_found_with_its_span() -> None:
    quote = "global solar capacity grew by 24 percent"
    result = match_quote(quote, SOURCE)
    assert result.status == "exact" and result.supported
    assert _matched(quote) == [quote]


@pytest.mark.parametrize(
    "quote",
    [
        'The agency\'s report said "growth was strongest in India" - a first.',  # typography
        "GLOBAL SOLAR CAPACITY   grew by 24 percent",  # case and whitespace
        "“global solar capacity grew by 24 percent”",  # wrapping quotes
        "final figures arrive in March",  # ligatures
        "le café reste ouvert pour les étudiants",  # combining accents
    ],
)
def test_harmless_differences_are_forgiven(quote: str) -> None:
    result = match_quote(quote, SOURCE)
    assert result.status in ("exact", "normalized")
    assert result.supported


def test_words_hyphenated_across_a_line_break_are_joined() -> None:
    assert _matched("Researchers studied evaluation methods") == [
        "Researchers studied evalu-\nation methods"
    ]


def test_real_hyphens_at_a_line_break_are_kept() -> None:
    assert _matched("methods; self-reported scores rose") == [
        "methods; self-\nreported scores rose"
    ]


def test_ellipsis_joins_fragments_in_order() -> None:
    result = match_quote("global solar capacity grew ... according to the IEA", SOURCE)
    assert result.status == "fragments"
    assert [SOURCE[s:e] for s, e in result.spans] == [
        "global solar capacity grew",
        "according to the IEA",
    ]
    assert match_quote("according to the IEA … global solar capacity grew", SOURCE).status == (
        "not_found"
    )


def test_fragments_must_be_close_together() -> None:
    filler = "x " * MAX_FRAGMENT_GAP
    text = f"the first important fragment {filler} the second important fragment"
    assert (
        match_quote("the first important fragment ... the second important fragment", text).status
        == "not_found"
    )
    near = "the first important fragment and then the second important fragment"
    assert match_quote(
        "the first important fragment [...] the second important fragment", near
    ).supported


@pytest.mark.parametrize(
    "quote",
    [
        "solar capacity grew by 25 percent",  # changed number
        "global wind capacity grew by 24 percent",  # changed word
        "capacity grew 24 percent globally in 2025",  # paraphrase
    ],
)
def test_altered_quotes_are_rejected(quote: str) -> None:
    result = match_quote(quote, SOURCE)
    assert result.status == "not_found" and not result.supported


@pytest.mark.parametrize("quote", ["IEA", "24 percent", "", "   ", "global solar ... IEA"])
def test_quotes_too_short_to_mean_anything_are_rejected(quote: str) -> None:
    assert match_quote(quote, SOURCE).status == "too_short"


def test_normalize_and_context() -> None:
    assert normalize("  The “Quick”\n\nfox—ok ") == 'the "quick" fox-ok'
    text = "a" * 500 + "TARGET" + "b" * 500
    snippet = context_around(text, (500, 506), width=10)
    assert snippet == "…" + "a" * 10 + "TARGET" + "b" * 10 + "…"
