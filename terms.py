"""Validator terminology tooltips: definition dictionary + term() helper.

Implements the P0 accessibility/UX item: judges should not need to know
internal vocabulary. Pure-CSS tooltips (hover + keyboard focus + touch
tap), no JavaScript, no external dependency.
"""
import re

# One place for every definition. Keep each concise; the cards link to
# Methods for methodology depth.
TERM_DEFINITIONS = {
    "vote-account state": (
        "Whether a validator's vote account is voting on the current chain "
        "(current) or has stopped keeping up (delinquent)."
    ),
    "current validators": (
        "Vote accounts that voted recently — an actively participating validator."
    ),
    "delinquent validator": (
        "A validator that stopped voting for recent slots; its stake stops "
        "earning and weakens consensus."
    ),
    "activated stake": (
        "SOL that is staked and actively securing the network right now."
    ),
    "nakamoto coefficient": (
        "How many validators together would need to collude to halt or "
        "censor the chain (measured at the 33⅓% stake threshold). Lower "
        "is more decentralized."
    ),
    "top 10 share": (
        "Percent of activated stake held by the ten largest validators."
    ),
    "commission": (
        "The percentage of staking rewards a validator keeps before paying "
        "delegators."
    ),
    "skip rate": (
        "Share of leader slots a validator missed (no block produced). "
        "Lower is better."
    ),
    "measured": "Read directly from the named source at snapshot time.",
    "sampled": (
        "Calculated from a stated sample window; not a complete-network total."
    ),
    "provider-reported": (
        "A value reported by an external data provider; definitions can "
        "differ between providers, so ranges are shown when they disagree."
    ),
    "recorded": "A source statement captured into the snapshot at collection time.",
    "derived": "Calculated deterministically from recorded inputs shown in this report.",
}

_TERM_CLASS = "term-tip"


def term(name: str, label: str | None = None) -> str:
    """Wrap a term in an accessible, dependency-free tooltip affordance."""
    definition = TERM_DEFINITIONS.get(name)
    if not definition:
        return html_escape(label or name)
    text = html_escape(label or name)
    definition_escaped = html_escape(definition)
    return (
        f"<span class='{_TERM_CLASS}' tabindex='0' role='button' "
        f"aria-label='{name}: {definition_escaped}'>"
        f"{text}<span class='{_TERM_CLASS}-bubble' role='tooltip'>"
        f"{definition_escaped}</span></span>"
    )


def html_escape(value: str) -> str:
    import html
    return html.escape(value, quote=True)
