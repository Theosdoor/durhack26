"""Every level: the Overcooked-AI copies, parsing, rooms, crossings, sprites and bots."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from undercooked.bots import SoloBot, run, team
from undercooked.config import LEVELS_DIR, AgentSpec, Config
from undercooked.engine import Game, Kitchen
from undercooked.replay import AgentInfo, Header
from undercooked.sprites import is_pass_through, render_svg

# The `grid` field of overcooked_ai_py/data/layouts/<name>.layout in overcooked-ai==1.1.0
# (counter_circuit = counter_circuit_o_1order), read from the installed package with
#   uvx --with overcooked-ai==1.1.0 --with scipy python -I -c "...ast.literal_eval(...)['grid']"
OVERCOOKED_AI = {
    "forced_coordination": ["XXXPX", "O X1P", "O2X X", "D X X", "XXXSX"],
    "cramped_room": ["XXPXX", "O  2O", "X1  X", "XDXSX"],
    "asymmetric_advantages": ["XXXXXXXXX", "O XSXOX S", "X   P 1 X", "X2  P   X", "XXXDXDXXX"],
    "coordination_ring": ["XXXPX", "X 1 P", "D2X X", "O   X", "XOSXX"],
    "counter_circuit": ["XXXPPXXX", "X  2   X", "D XXXX S", "X  1   X", "XXXOOXXX"],
}
SPLIT_ROOM = ["XXXXXPX", "O  X 1P", "O2 X  X", "S  X  D", "XXXXXXX"]   # approved by the researcher
LEVELS = [*OVERCOOKED_AI, "split_room"]
PASS = {"forced_coordination": 3, "split_room": 3}                       # pass-through counters
CFG = Config(name="levels", level="split_room", agents=[AgentSpec(model="mockllm/model")] * 2)


def rows(level: str) -> list[str]:
    return (LEVELS_DIR / f"{level}.txt").read_text().rstrip("\n").split("\n")


def base_rows(level: str) -> list[str]:
    """The layout with the extra start spots (digits 3 and 4, added for N-agent runs) as floor."""
    return [r.replace("3", " ").replace("4", " ") for r in rows(level)]


@pytest.mark.parametrize("level", list(OVERCOOKED_AI))
def test_overcooked_ai_layouts_are_verbatim(level):
    assert base_rows(level) == OVERCOOKED_AI[level]


def test_split_room_layout():
    assert base_rows("split_room") == SPLIT_ROOM
    k = Kitchen.load("split_room")
    left, right = k.room_of[k.starts[2]], k.room_of[k.starts[1]]
    assert left != right and len(set(k.room_of.values())) == 2
    assert {p for p, r in k.room_of.items() if r == left} == {(x, y) for x in (1, 2)
                                                              for y in (1, 2, 3)}
    assert {p for p, r in k.room_of.items() if r == right} == {(x, y) for x in (4, 5)
                                                               for y in (1, 2, 3)}
    for kind, room in [("onion_crate", left), ("hatch", left), ("pot", right),
                       ("dish_rack", right)]:
        assert all(k.rooms_touching(o.id) == {room} for o in k.of_kind(kind)), kind
    assert [o.id for o in k.shared_counters()] == ["counter_3_1", "counter_3_2", "counter_3_3"]


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_parses_with_two_seats(level):
    k = Kitchen.load(level)
    assert {1, 2} <= set(k.starts) <= {1, 2, 3, 4}      # seats 3 and 4 only where the level has them
    assert all(k.is_floor(p) for p in k.starts.values())
    # ids: numbered in reading order per kind, counters by position
    for kind in ("onion_crate", "dish_rack", "pot", "hatch"):
        objs = k.of_kind(kind)
        assert objs, (level, kind)
        assert [o.id for o in objs] == [f"{kind}_{i + 1}" for i in range(len(objs))]
        assert [o.pos for o in objs] == sorted((o.pos for o in objs), key=lambda p: (p[1], p[0]))
        assert all(k.access_tiles(o.id) for o in objs), (level, kind)
    assert all(o.id == f"counter_{o.pos[0]}_{o.pos[1]}" for o in k.of_kind("counter"))
    # every object a seat can reach is reached by pathfinding from that seat
    for seat in k.starts.values():
        for o in k.objects.values():
            assert k.reachable(seat, o.id) == (k.distance(seat, k.access_tiles(o.id)) is not None)
    game = Game(k, ["Kilo", "Tango"], CFG)
    assert [a.pos for a in game.agents] == [k.starts[1], k.starts[2]]


def test_asymmetric_advantages_shares_its_pots_between_rooms():
    k = Kitchen.load("asymmetric_advantages")
    assert len(set(k.room_of.values())) == 2 and not k.shared_counters()
    for pot in ("pot_1", "pot_2"):
        assert len(k.access_tiles(pot)) == 2 and len(k.rooms_touching(pot)) == 2
    assert [o.id for o in k.of_kind("hatch")] == ["hatch_1", "hatch_2"]
    # each side fills pot_1 from its own tile; together they start it cooking
    g = Game(k, ["Kilo", "Tango"], CFG)
    kilo, tango = g.agent("Kilo"), g.agent("Tango")
    kilo.pos, tango.pos = (5, 2), (3, 2)
    kilo.holding = tango.holding = "onion"
    g.pots["pot_1"].onions = 1
    assert g.set_plan("Kilo", ["interact(pot_1)"]) is None
    assert g.set_plan("Tango", ["interact(pot_1)"]) is None
    events = g.tick()
    assert sum(e.type == "add_to_pot" for e in events) == 2
    assert g.pots["pot_1"].cooking


def test_coordination_ring_pots_share_one_tile():
    k = Kitchen.load("coordination_ring")
    assert k.access_tiles("pot_1") == k.access_tiles("pot_2") == [(3, 1)]


def header(level: str) -> Header:
    return Header(config_name="t", level=level, map=Kitchen.load(level).map_rows(),
                  agents=[AgentInfo(name="Kilo", letter="K", model="m"),
                          AgentInfo(name="Tango", letter="T", model="m")],
                  seed=0, round_s=180, rounds=1)


@pytest.mark.parametrize("level", LEVELS)
def test_sprites_render_every_level(level):
    h = header(level)
    svg = render_svg(h, Game(Kitchen.load(level), ["Kilo", "Tango"], CFG).frame())
    ET.fromstring(svg)
    assert svg.count("data-tile='pass'") == PASS.get(level, 0)
    assert svg.count("data-tile='hatch'") == len(Kitchen.load(level).of_kind("hatch"))
    assert svg.count(">Kilo<") == svg.count(">Tango<") == 1
    k = Kitchen.load(level)
    for o in k.of_kind("counter"):
        assert is_pass_through(h.map, *o.pos) == (len(k.rooms_touching(o.id)) >= 2)


def test_counter_circuit_island_is_not_pass_through():
    """Floor on both sides, but one room: an ordinary counter."""
    m = Kitchen.load("counter_circuit").map_rows()
    assert m[2][2:6] == "XXXX" and not any(is_pass_through(m, x, 2) for x in range(2, 6))


# ------------------------------------------------------------------- bots on every level
@pytest.mark.parametrize("level", LEVELS)
def test_bot_team_serves_on_every_level(level):
    g = Game(Kitchen.load(level), ["Kilo", "Tango"], CFG)
    run(g, team(g))
    assert g.served >= 3, (level, g.served)


@pytest.mark.parametrize("level", ["cramped_room", "asymmetric_advantages", "coordination_ring",
                                   "counter_circuit"])
def test_solo_bot_serves_alone_on_shared_levels(level):
    g = Game(Kitchen.load(level), ["Kilo"], CFG)
    run(g, {"Kilo": SoloBot("Kilo")})
    assert g.served >= 2, (level, g.served)


def test_split_room_pair_makes_both_crossings():
    g = Game(Kitchen.load("split_room"), ["Kilo", "Tango"], CFG)    # Kilo: right room (pots)
    events = run(g, team(g))
    assert g.served >= 3
    middle = {f"3,{y}" for y in (1, 2, 3)}
    put = {(e.agent, e.data["item"]) for e in events if e.type == "putdown" and e.data["at"] in middle}
    took = {(e.agent, e.data["item"]) for e in events if e.type == "pickup" and e.data["at"] in middle}
    assert put == {("Tango", "onion"), ("Kilo", "soup")}
    assert took == {("Kilo", "onion"), ("Tango", "soup")}
    assert {e.agent for e in events if e.type == "serve"} == {"Tango"}
    assert {e.agent for e in events if e.type == "plate"} == {"Kilo"}
    assert any(e.type == "pickup" and e.agent == "Kilo" and e.data["item"] == "dish"
               for e in events)                                    # its own rack
