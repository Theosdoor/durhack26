"""The prompt files in prompts/ render to exactly the texts in tests/fixtures/prompts_golden.json.

If this fails after an INTENTIONAL edit of a prompt file (or of the code that fills one in),
review the change, then regenerate the golden file and commit it with the edit:

    uv run python -m tests.make_prompt_golden
"""

from __future__ import annotations

import json

import pytest

from tests.make_prompt_golden import GOLDEN, render_all
from undercooked import promptfiles
from undercooked.promptfiles import PROMPTS_DIR, PromptError, load, placeholders, sections
from undercooked.prompts import push_board_text
from undercooked.tools import board_empty_text, post_cap_error

FILES = sorted(p.stem for p in PROMPTS_DIR.glob("*.md") if p.stem not in ("README", "EXAMPLE", "EXAMPLE.plain"))


def test_rendered_prompts_match_golden_file():
    got = json.loads(json.dumps(render_all(), sort_keys=True, ensure_ascii=False))
    want = json.loads(GOLDEN.read_text())

    def diff(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                yield from diff(a.get(k), b.get(k), f"{path}/{k}")
        elif a != b:
            yield f"{path}\n  got:  {a!r}\n  want: {b!r}"

    assert not list(diff(got, want)), "prompt text changed; if intended, regenerate the golden file"


def test_loose_helpers_render_the_golden_wording():
    assert post_cap_error(2) == "you have used all 2 posts for this chat"
    assert board_empty_text() == "The board is empty."
    assert push_board_text(None) == "Board: no new posts."
    assert push_board_text("[chat] a: b") == "New posts on the board:\n[chat] a: b"


def test_every_file_and_section_is_used_and_every_placeholder_is_provided():
    render_all()
    test_loose_helpers_render_the_golden_wording()
    unused, unprovided = [], []
    for name in FILES:
        secs = sections(name)
        for section in (secs or {None: None}):
            calls = promptfiles.CALLS.get((name, section))
            if not calls:
                unused.append(f"{name}.md" + (f" [{section}]" if section else ""))
                continue
            needed = placeholders(promptfiles.raw(name, section))
            for given in calls:
                if needed - given:
                    unprovided.append(f"{name}.md [{section}]: {sorted(needed - given)}")
    assert not unused, f"prompt files/sections no code loads: {unused}"
    assert not unprovided, f"placeholders without a value: {unprovided}"


def test_missing_placeholder_raises_a_clear_error_naming_the_file():
    with pytest.raises(PromptError, match=r"prompts/system\.md \[kitchen_rules\].*\{cook_s\}"):
        load("system", "kitchen_rules")
    with pytest.raises(PromptError, match=r"prompts/tools\.md \[act\.rejected\].*\{error\}"):
        load("tools", "act.rejected")
    with pytest.raises(PromptError, match="no section"):
        load("tools", "nope")
    with pytest.raises(PromptError, match="not found"):
        load("nope")


def test_readme_documents_every_file_and_placeholder():
    readme = (PROMPTS_DIR / "README.md").read_text()
    missing = [f"{name}.md" for name in FILES if f"`{name}.md`" not in readme]
    assert not missing, f"README.md does not list {missing}"
    names = set()
    for name in FILES:
        for section in (sections(name) or {None: None}):
            names |= placeholders(promptfiles.raw(name, section))
    undocumented = sorted(n for n in names if f"{{{n}}}" not in readme)
    assert not undocumented, f"README.md does not document placeholders {undocumented}"


def test_compaction_prompt_keeps_inspects_literal_placeholder():
    from undercooked import prompts
    from tests.make_prompt_golden import cfg

    for comms in ("full", "planning_only", "none"):
        text = prompts.compaction_prompt(cfg(comms=comms))
        assert text.endswith("At most 400 words.{addendums}")
        assert ("what you and your teammate posted" in text) == (comms != "none")
