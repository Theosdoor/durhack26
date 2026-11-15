"""The rules knob (how much the system prompt explains) and the discovery metrics."""

from __future__ import annotations

import math
import re

import pytest
import yaml
from click.testing import CliRunner
from inspect_ai.log import read_eval_log

from tests import kitchen_fake as fake
from tests.test_conditions import names_of, users
from tests.test_season import config, run_season
from undercooked.cli import main as cli
from undercooked.promptfiles import load
from undercooked.prompts import orders_rules, system_prompt
from undercooked.replay import Event
from undercooked.replay_io import load_replay

MODES = ["full", "recipe", "minimal", "minimal_hint"]
KEPT = ["map_key", "actions", "plans", "notepad", "goal"]     # unchanged in every mode
RECIPE_WORDS = re.compile(r"\b(onions?|3 onions|dish(es)?|plat(e|ed|ing)|recipe|due|expires?)\b",
                          re.I)


def kw(cfg) -> dict:
    o = cfg.orders
    return dict(cook_s=cfg.cook_s, every_s=o.every_s, max_open=o.max_open, due_s=o.due_s,
                reward=o.reward, penalty=o.penalty, max_plan_len=cfg.max_plan_len, wait_min=1,
                wait_max=30, blocked_limit=3, bin_key="", bin_row="", bin_ids="", bin_kind="")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("comms", ["full", "none"])
@pytest.mark.parametrize("scoring", ["soups", "orders"])
def test_system_prompt_per_mode(mode, comms, scoring):
    cfg = config(rules=mode, comms=comms, scoring=scoring)
    text = system_prompt(cfg, "Kilo", "Tango")
    full = system_prompt(config(comms=comms, scoring=scoring), "Kilo", "Tango")
    for key in KEPT:
        assert load("system", key, **kw(cfg)) in text, key
    moving = load("system", "kitchen_rules", **kw(cfg))
    recipe = load("system", "recipe", **kw(cfg))
    assert moving in text                                   # time, walking, interacting
    # identity ... season, deciding: the same text before and after the rules
    assert text.split("HOW THE KITCHEN WORKS")[0] == full.split("HOW THE KITCHEN WORKS")[0]
    assert text.split("PLANS")[1].split("\n\n", 1)[1] == full.split("PLANS")[1].split("\n\n", 1)[1]
    if mode == "full":
        assert text == full and "INTERACT TABLE" in text and recipe in text
        return
    assert "INTERACT TABLE" not in text and recipe not in text
    legacy = "rules_recipe_soups" if (mode, scoring) == ("recipe", "soups") else f"rules_{mode}"
    assert load("system", legacy) in text
    rules = text.split("THE KITCHEN\n", 1)[1].split("\n\n")[0]
    if mode == "recipe":
        assert "onion soup" in rules and "dish" in rules
    else:
        assert not RECIPE_WORDS.search(rules)
        assert ("Practice is a good time" in rules) == (mode == "minimal_hint")


@pytest.mark.parametrize("scoring", ["soups", "orders"])
@pytest.mark.parametrize("mode", ["minimal", "recipe"])
def test_no_recipe_in_messages(tmp_path, mode, scoring):
    """A season under rules other than full: no order rules in the chat, no recipe in the act
    example; under minimal the orders in the state name no dish."""
    fake_requests: list[fake.Request] = []
    mp = pytest.MonkeyPatch()
    mp.setattr(fake, "REQUESTS", fake_requests)
    try:
        log, [rep] = run_season(config(rules=mode, round_s=10, scoring=scoring), tmp_path)
    finally:
        mp.undo()
    assert rep.header.rules == mode
    assert log.samples[0].metadata["condition"]["rules"] == mode
    rules_line = orders_rules(config(rules=mode, round_s=10, scoring=scoring))
    for name in names_of(rep):
        for u in users(log, name):
            assert rules_line not in u and "Orders: the first arrives" not in u
            if mode == "minimal":
                assert "onion soup" not in u
    acts = [r for r in fake_requests if "act" in r.tools]
    assert acts and all("onion_crate" not in r.tool_text for r in acts)
    assert any("Chat before round 1" in r.last_user for r in fake_requests)


def test_cli_rules_flag(tmp_path):
    cfg = config(practice=False, round_s=4, rounds=1)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    res = CliRunner().invoke(cli, ["run", str(path), "--epochs", "1", "--log-dir",
                                   str(tmp_path / "logs"), "--rules", "minimal_hint"])
    assert res.exit_code == 0, res.output
    log_path = res.output.strip().splitlines()[-1]
    [rep] = load_replay(log_path)
    assert rep.header.rules == "minimal_hint"
    assert read_eval_log(log_path, header_only=True).eval.metadata["config"]["rules"] \
        == "minimal_hint"
    assert CliRunner().invoke(cli, ["run", str(path), "--rules", "none"]).exit_code != 0


# ------------------------------------------------------------------- discovery metrics
def test_discovery_firsts(tmp_path):
    log, [rep] = run_season(config(), tmp_path)
    v = log.samples[0].scores["season_score"].value
    events = [e for e in rep.records if isinstance(e, Event)]
    kinds = {"onion": "add_to_pot", "cook": "cook_start", "plate": "plate", "serve": "serve"}
    seen_any = False
    for i, name in enumerate(names_of(rep)):
        for kind, etype in kinds.items():
            mine = [e for e in events if e.type == etype and e.agent == name]
            r, t = v[f"first_{kind}_round_agent{i}"], v[f"first_{kind}_t_agent{i}"]
            if mine:
                seen_any = True
                assert (r, t) == (mine[0].round, mine[0].t), (name, kind)
            else:
                assert math.isnan(r) and math.isnan(t), (name, kind)
    assert seen_any
    # every agent-caused event names its agent
    caused = {"pickup", "putdown", "add_to_pot", "cook_start", "plate", "serve", "fail", "bump"}
    assert all(e.agent for e in events if e.type in caused)
    totals = {s.name: s for s in log.results.scores}
    assert "first_serve_t_agent0" in totals          # NaN-free keys still get metrics
