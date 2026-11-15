"""analysis/render_prompts.py: the labelled example of every prompt in one season."""

from __future__ import annotations

import re

import pytest

from analysis import render_prompts as rp
from tests import kitchen_fake as fake
from undercooked import prompts, promptfiles


@pytest.fixture(scope="module")
def rendered():
    text, rec, conv = rp.build(plain=False)
    return text, rec, conv


def marker_labels(text: str) -> set[str]:
    return set(rp.LABEL_RE.findall(text))


def test_runs_and_restores_the_library(rendered):
    text, rec, conv = rendered
    assert text.startswith("# Example: every prompt one agent sees in one season")
    assert "### 1. system: system prompt" in text and "uv run python -m analysis.render_prompts" in text
    assert conv[0]["role"] == "system" and rec.shown_name in conv[0]["content"]
    # the monkeypatches are gone: the library and the fake are as they were
    assert promptfiles.load.__module__ == "undercooked.promptfiles"
    assert "⟦" not in prompts.system_prompt(rp.example_config(), "Kilo", "Tango")
    assert fake.script.__module__ == "tests.kitchen_fake"


def test_the_season_covers_the_rarer_prompts(rendered):
    text, _, _ = rendered
    for needle in ("forced-answer request", "after a cutoff", "compaction summary",
                   "Plan rejected", "debrief, round 2", "chat before round 2"):
        assert needle in text, needle
    assert re.search(r"^### \d+\. user: round 1 start$", text, re.M)
    assert re.search(r"more decisions? like this", text)


def test_every_section_used_appears_in_a_marker(rendered):
    text, rec, _ = rendered
    used = {rp.section_label(n, s) for (n, s) in rec.used if s}
    shown = marker_labels(text)
    assert used - shown <= LOADED_BUT_NOT_SENT, sorted(used - shown - LOADED_BUT_NOT_SENT)
    for label in shown:                              # every marker names a real section or code
        assert label.startswith("code: ") or label in {
            rp.section_label(n, s) for n, s in rp.all_sections()}, label


# loaded during the season but not sent unless a condition holds: tools.py builds the "empty
# board" result when it makes the read_board tool, and returns it only if the board is empty
LOADED_BUT_NOT_SENT = {"tools.md › read_board.empty"}


def test_unused_table_is_complete_and_correct(rendered):
    text, _, _ = rendered
    shown = marker_labels(text)
    table = text.split("## Sections that never appear in this example")[1]
    rows = re.findall(r"^\| `([^`]+)` \| (.+) \|$", table, re.M)
    listed = {label for label, _ in rows}
    every = {rp.section_label(n, s) for n, s in rp.all_sections()}
    assert listed == every - shown
    assert all(cond != "(always used)" for _, cond in rows)    # every one has a real condition


def test_every_trigger_names_a_real_section():
    every = {rp.section_label(n, s) for n, s in rp.all_sections()}
    assert set(rp.TRIGGERS) <= every, sorted(set(rp.TRIGGERS) - every)


def test_plain_has_no_markers():
    text, _, _ = rp.build(plain=True)
    assert "⟦" not in text and "⟧" not in text.replace("⟦/⟧", "")
    assert "### 1. system: system prompt" in text
