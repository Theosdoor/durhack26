"""render_svg: valid XML for every fixture frame, one chef per agent, all pot states drawn."""

import xml.etree.ElementTree as ET
from pathlib import Path

from undercooked.replay import Replay
from undercooked.sprites import render_svg

FIXTURE = Path(__file__).parent / "fixtures" / "replay_min.json"
REP = Replay.model_validate_json(FIXTURE.read_text())
FRAMES = REP.frames("round", 1)


def texts(svg: str) -> list[str]:
    return ["".join(e.itertext()) for e in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]


def test_every_frame_is_valid_xml_with_one_chef_per_agent():
    for f in FRAMES:
        svg = render_svg(REP.header, f)
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg") and "viewBox" in root.attrib
        letters = [t for t in texts(svg) if t in {a.letter for a in REP.header.agents}]
        assert sorted(letters) == sorted(a.letter for a in REP.header.agents)
        for a in f.agents:
            assert a.name in texts(svg)


def test_pot_states_render():
    by_state = {}
    for f in FRAMES:
        for p in f.pots:
            by_state.setdefault(p.state, (f, p))
    assert {"empty", "cooking"} <= set(by_state)
    f, p = by_state["cooking"]
    assert f"{p.ready_in}s" in texts(render_svg(REP.header, f))
    f, p = by_state["cooking"]   # the fixture has no ready pot, so make one
    f = f.model_copy(update={"pots": [p.model_copy(update={"state": "ready", "ready_in": None})]})
    assert "READY" in texts(render_svg(REP.header, f))
    ET.fromstring(render_svg(REP.header, by_state["empty"][0]))


def test_orders_and_urgency():
    f = next(f for f in FRAMES if f.orders)
    o = f.orders[0]
    assert f"{max(0, o.due_t - f.t)}s" in texts(render_svg(REP.header, f))
    soon = f.model_copy(update={"orders": [o.model_copy(update={"due_t": f.t + 5})]})
    assert "#d63b3b" in render_svg(REP.header, soon)
