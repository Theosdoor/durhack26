"""Bins: trashing, failing with empty hands, ids, `bins: false`, placement on every level."""

from __future__ import annotations

import re

import pytest

from undercooked.config import LEVELS_DIR, AgentSpec, Config
from undercooked.engine import Game, Kitchen
from undercooked.engine.actions import ParseError, parse_action
from undercooked.sprites import render_svg
from undercooked.replay import AgentInfo, Header

LEVEL_NAMES = sorted(p.stem for p in LEVELS_DIR.glob("*.txt"))


def cfg(**kw) -> Config:
    """bins is off by default (as in Config); the tests that need bins pass bins=True."""
    return Config(name="t", level="cramped_room", scoring="orders",
                  agents=[AgentSpec(model="mockllm/model")] * 2, **kw)


def solo_game(**kw) -> Game:
    """Cramped room, bin_1 at (2,3); Kilo stands at (2,2), right above it."""
    kw.setdefault("bins", True)
    g = Game(Kitchen.load("cramped_room", kw["bins"]), ["Kilo", "Tango"], cfg(**kw))
    g.agent("Kilo").pos, g.agent("Tango").pos = (2, 2), (3, 1)     # Kilo is next to bin_1
    return g


def do(g: Game, name: str, action: str) -> list:
    g.set_plan(name, [action])
    out = []
    for _ in range(12):
        out += g.tick()
        if not g.agent(name).plan:
            break
    return out


def test_bin_ids_in_reading_order():
    k = Kitchen.load("forced_coordination", True)
    assert [o.id for o in k.of_kind("bin")] == ["bin_1", "bin_2"]
    assert k.objects["bin_1"].pos == (4, 3) and k.objects["bin_2"].pos == (1, 4)
    assert k.of_kind("bin") and "counter_1_4" not in k.objects


@pytest.mark.parametrize("item", ["onion", "dish", "soup"])
def test_trash_each_item(item):
    g = solo_game()
    a = g.agent("Kilo")
    a.holding = item
    ev = do(g, "Kilo", "interact(bin_1)")
    assert a.holding is None and g.counters == {}
    trash = [e for e in ev if e.type == "trash"]
    assert len(trash) == 1 and trash[0].data == {"item": item, "at": "2,3"}
    assert not [e for e in ev if e.type == "fail"]


def test_trash_takes_one_second_after_arriving():
    g = solo_game()
    g.agent("Kilo").holding = "onion"
    t0 = g.t
    ev = do(g, "Kilo", "interact(bin_1)")
    assert g.t - t0 == 1 and [e.type for e in ev if e.agent] == ["trash"]


def test_empty_hands_fail_with_reason():
    g = solo_game()
    ev = do(g, "Kilo", "interact(bin_1)")
    fails = [e for e in ev if e.type == "fail"]
    assert len(fails) == 1
    assert fails[0].data["reason"] == "the bin is for throwing items away; you are holding nothing"
    assert g.last_plan_result("Kilo").startswith("failed: the bin is for throwing")


def test_unnumbered_bin_is_nearest_reachable():
    g = Game(Kitchen.load("forced_coordination", True), ["Kilo", "Tango"], cfg())
    k = g.kitchen
    g.agent("Kilo").pos, g.agent("Tango").pos = (3, 2), (1, 2)
    for name, want in [("Kilo", "bin_1"), ("Tango", "bin_2")]:   # right room / left room
        a = g.agent(name)
        assert k.reachable(a.pos, want)
        assert g._nearest(a.pos, "bin") == want
        act = parse_action("interact(bin)", k)
        assert act.kind_target == "bin" and act.target is None


@pytest.mark.parametrize("text", ["interact(bin_1)", "throw(bin 1)", "discard(bin_1)",
                                  "interact((2,3))", "interact(bin(2,3))", "bin_1"])
def test_parse_bin_forms(text):
    k = Kitchen.load("cramped_room", True)
    assert parse_action(text, k).target == "bin_1"
    assert parse_action("interact(trash)", k).kind_target == "bin"
    assert parse_action("interact(trash can)", k).kind_target == "bin"


def test_missing_bin_id_names_alternatives():
    with pytest.raises(ParseError, match=r"bin_2 does not exist \(this kitchen has: bin_1\)"):
        parse_action("interact(bin_2)", Kitchen.load("cramped_room", True))


def test_unknown_target_message_lists_bins():
    with pytest.raises(ParseError, match="bin_1"):
        parse_action("interact(banana)", Kitchen.load("cramped_room", True))


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_bins_false_is_the_old_map(level):
    text = (LEVELS_DIR / f"{level}.txt").read_text()
    old = Kitchen.load(level)
    assert "B" not in "".join(old.rows) and not old.of_kind("bin")
    assert "B" in text
    assert old.rows == [r.replace("B", "X") for r in Kitchen.load(level, True).rows]
    # the old layout has a counter where the bin is, and nothing else differs
    new = Kitchen.load(level, True)
    assert set(old.objects) - set(new.objects) == {f"counter_{o.pos[0]}_{o.pos[1]}"
                                                   for o in new.of_kind("bin")}
    assert old.floor == new.floor and old.starts == new.starts and old.room_of == new.room_of


def test_bins_false_game_has_no_bin():
    g = solo_game(bins=False)
    assert "bin_1" not in g.kitchen.objects and g.kitchen.by_pos[(2, 3)].kind == "counter"
    with pytest.raises(ParseError):
        parse_action("interact(bin_1)", g.kitchen)


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_every_room_with_a_start_has_a_reachable_bin(level):
    k = Kitchen.load(level, True)
    bins = k.of_kind("bin")
    assert bins
    for room in {k.room_of[p] for p in k.starts.values()}:
        assert any(room in k.rooms_touching(b.id) for b in bins), (level, room)


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_bins_replace_non_shared_wall_counters_and_block_nothing(level):
    k, old = Kitchen.load(level, True), Kitchen.load(level)
    for b in k.of_kind("bin"):
        assert len(k.rooms_touching(b.id)) == 1, "a bin must not be a shared wall counter"
        assert k.access_tiles(b.id)
    # every object stays reachable from every room it was reachable from before
    for oid, o in k.objects.items():
        if o.kind != "bin":
            assert k.rooms_touching(oid) == old.rooms_touching(oid)


def test_bin_sprite_and_text_map():
    g = solo_game()
    h = Header(config_name="t", level="cramped_room", map=g.kitchen.map_rows(),
               agents=[AgentInfo(name="Kilo", letter="K", model="m"),
                       AgentInfo(name="Tango", letter="T", model="m")],
               seed=0, round_s=180, rounds=1, bins=True)
    assert render_svg(h, g.frame()).count("data-tile='bin'") == 1
    assert "XDBSX" in g.kitchen.map_rows()
    from undercooked.engine.text import map_lines
    assert any(re.search(r"\bXDBSX\b", ln) for ln in map_lines(g))


def test_rules_text_mentions_bin_only_in_full_rules():
    from undercooked.engine import rules_text
    full = rules_text(cfg(bins=True))
    assert "B bin" in full and "bin         + holding item" in full
    assert rules_text(cfg(rules="bare", bins=True)) == "" and rules_text(cfg(rules="open", bins=True)) == ""


def _game(level="split_room", **kw) -> Game:
    c = Config(name="t", level=level, agents=[AgentSpec(model="mockllm/model")] * 2, **kw)
    return Game(Kitchen.load(level, True), ["Kilo", "Tango"], c)


def _state(g: Game) -> str:
    return g.state_text("Kilo", unread_posts=0, notepad=None, last_plan_result="done")


def test_counters_shown_default_is_occupied():
    assert Config(name="t", level="split_room",
                  agents=[AgentSpec(model="mockllm/model")]).counters_shown == "occupied"


def test_state_lists_only_occupied_counters_by_default():
    g = _game()
    assert "Counters: all empty." in _state(g)
    g.counters[(3, 2)] = "onion"
    line = [ln for ln in _state(g).split("\n") if ln.startswith("Counters")][0]
    assert line == "Counters: (3,2) onion."
    assert "(3,1)" not in line and "(3,3)" not in line


def test_state_shared_mode_lists_shared_counters_when_empty():
    g = _game(counters_shown="shared")
    line = [ln for ln in _state(g).split("\n") if ln.startswith("Counters")][0]
    assert all(f"(3,{y}) empty" in line for y in (1, 2, 3))
    g.counters[(3, 2)] = "onion"
    assert "(3,2) onion" in _state(g)


def test_overview_names_shared_counters_only_in_shared_mode():
    from undercooked import prompts
    assert "Counters listed in your state" not in prompts.kitchen_overview(_game(), "Kilo")
    assert "Counters listed in your state: (3,1), (3,2), (3,3)" in prompts.kitchen_overview(
        _game(counters_shown="shared"), "Kilo")


def test_header_counters_shown_defaults_to_shared_for_old_logs():
    h = Header(config_name="t", level="split_room", map=["XX"], agents=[], seed=0, round_s=1, rounds=1)
    assert h.counters_shown == "shared" and h.bins is False


def test_bins_are_off_by_default():
    c = Config(name="t", level="split_room", agents=[AgentSpec(model="mockllm/model")])
    assert c.bins is False
    for level in LEVEL_NAMES:       # the default kitchen is the pre-bin layout
        text = (LEVELS_DIR / f"{level}.txt").read_text().rstrip("\n").split("\n")
        k = Kitchen.load(level)
        assert not k.of_kind("bin") and k.rows == [r.replace("B", "X") for r in text]


def test_default_rules_text_does_not_mention_bins():
    from undercooked.engine import rules_text
    default = rules_text(cfg())
    assert "bin" not in default.lower() and "B bin" not in default
    assert "   B bin (trash)" in rules_text(cfg(bins=True))
    assert "bin_1, ... Counters" in rules_text(cfg(bins=True))
    assert "hatch_1, ... Counters" in default and "(onion_crate, dish_rack, pot, hatch) means" in default


def test_default_bins_false_trash_is_unknown_to_the_parser():
    k = Kitchen.load("cramped_room")
    with pytest.raises(ParseError):
        parse_action("interact(bin)", k)


def test_bin_words_unknown_without_bins_as_on_main():
    """bins: false must parse exactly as before bins existed (review finding on PR #6)."""
    import pytest
    from undercooked.engine.actions import ParseError, parse_action
    from undercooked.engine.kitchen import Kitchen
    k = Kitchen.load("split_room", bins=False)
    for text in ("throw(4,3)", "toss(onion_crate)", "discard(pot_1)"):
        with pytest.raises(ParseError, match="unknown action"):
            parse_action(text, k)
    for text in ("interact(bin)", "interact(trash)", "interact(bin_2)"):
        with pytest.raises(ParseError, match="unknown target"):
            parse_action(text, k)
    with pytest.raises(ParseError, match="hatch or counter"):
        parse_action("interact(2,1)", k)
    kb = Kitchen.load("split_room", bins=True)
    assert parse_action("throw(bin)", kb).kind == "interact"
