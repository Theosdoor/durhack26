"""A full short season through Inspect with scripted offline models (tests/kitchen_fake.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from inspect_ai import eval as inspect_eval
from inspect_ai.event import CompactionEvent, InfoEvent, ToolEvent
from inspect_ai.log import read_eval_log
from streamlit.testing.v1 import AppTest

from tests import kitchen_fake as fake
from undercooked.board import Board
from undercooked.cli import main as cli
from undercooked.config import NATO_NAMES, AgentSpec, Config
from undercooked.engine import Kitchen
from undercooked.prompts import cutoff_notice
from undercooked.providers import pick_endpoint
from undercooked.replay import DecisionRecord, Event, Frame, Header, Post, Replay
from undercooked.replay_io import load_replay
from undercooked.season import assign_seats, first_speaker, pick_names
from undercooked.task import make_task

VIEWER = Path(__file__).parent.parent / "undercooked" / "viewer.py"
AGENTS = [AgentSpec(model="kitchenfake/cook"), AgentSpec(model="kitchenfake/passer")]


def seed_where(seats: list[int], first: int) -> int:
    """A seed whose epoch 1 gives these seats and this first speaker in chat 1."""
    return next(s for s in range(1000)
                if assign_seats(s, 1) == seats and first_speaker(s, 1, 1) == first)


# agents[0] (the fake cook) starts in the pots-and-hatch room and speaks first in chat 1, so
# "cook" below is both agents[0] and the seat-1 player; test_shuffled_seats covers the rest
SEED = seed_where([1, 2], 0)


def config(**kw) -> Config:
    base = dict(name="test_season", level="forced_coordination", rounds=1, round_s=30, practice=True,
                practice_s=20, chat_max_posts=2, epochs=1, cook_s=2, agents=AGENTS,
                decision={"flat_s": 0, "post_words_per_s": 10}, seed=SEED)
    return Config.model_validate({**base, **kw})


def run_season(cfg: Config, log_dir: Path):
    [log] = inspect_eval(make_task(cfg), model="none/none", log_dir=str(log_dir), display="none")
    assert log.status == "success", log.error
    full = read_eval_log(log.location, resolve_attachments=True)
    return full, load_replay(log.location)


@pytest.fixture(scope="module")
def season(tmp_path_factory):
    full, replays = run_season(config(), tmp_path_factory.mktemp("logs"))
    assert len(replays) == 1
    return full, replays[0]


def names_of(rep: Replay) -> tuple[str, str]:
    return rep.header.agents[0].name, rep.header.agents[1].name


# ----------------------------------------------------------------------------- full season
def test_export_and_header(season):
    log, rep = season
    Replay.model_validate(rep.model_dump(mode="json"))
    sample = log.samples[0]
    kitchen = [e for e in sample.events if isinstance(e, InfoEvent) and e.source == "kitchen"]
    assert kitchen[0].data["kind"] == "header"
    assert all(e.data["kind"] != "header" for e in kitchen[1:])
    h = rep.header
    assert isinstance(h, Header) and h.epoch == 1 and h.level == "forced_coordination" and h.round_s == 30
    assert [a.name for a in h.agents] == pick_names(SEED, 1)
    assert [a.seat for a in h.agents] == [1, 2]
    assert all(a.name in NATO_NAMES and a.letter == a.name[0] for a in h.agents)
    assert [a.model for a in h.agents] == ["kitchenfake/cook", "kitchenfake/passer"]
    assert h.map == ["XXXPX", "O X P", "O X X", "D X X", "XXXSX"] and h.bins is False
    assert h.cook_s == 2 and h.practice_s == 20


def test_phases_in_order(season):
    _, rep = season
    assert rep.phases() == [("briefing", 0), ("practice", 0), ("chat", 1), ("round", 1),
                            ("debrief", 1), ("reflection", 1)]
    for phase, rnd in rep.phases():
        types = [e.type for e in rep.select(Event, phase, rnd)]
        assert types[0] == "phase_start" and types[-1] == "phase_end"


def test_frames_and_serving(season):
    _, rep = season
    frames = rep.frames("round", 1)
    assert [f.t for f in frames] == list(range(31))
    assert all(len(f.agents) == 2 for f in frames)
    assert frames[-1].served >= 1 and frames[-1].score == 20 * frames[-1].served
    assert any(e.type == "serve" for e in rep.select(Event, "round", 1))
    practice = rep.frames("practice", 0)
    assert len(practice) == 2 * 21 and all(len(f.agents) == 1 for f in practice)


def test_chat_turns(season):
    _, rep = season
    cook, passer = names_of(rep)
    decisions = rep.select(DecisionRecord, "chat", 1)
    assert [(d.t, d.agent) for d in decisions] == [(0, cook), (1, passer), (2, cook)]
    assert [d.ready for d in decisions] == [False, True, True]
    posts = rep.select(Post, "chat", 1)
    assert [(p.t, p.agent, p.text) for p in posts] == [(0, cook, fake.CHAT_POST),
                                                       (1, passer, "OK, onions coming.")]


def test_board_is_pull_only(season):
    log, rep = season
    cook, passer = names_of(rep)
    [post] = rep.select(Post, "round", 1)
    assert post.agent == cook and post.text == fake.COOK_ROUND_POST
    conv = log.samples[0].store["conversations"][passer]
    users = [m["content"] for m in conv if m["role"] == "user"]
    unread = [u for u in users if "Board: 1 unread post." in u]
    assert unread, "the passer was never told it had an unread post"
    assert all(fake.COOK_ROUND_POST not in u for u in users)     # never pushed into the state
    reads = [m for m in conv if m["role"] == "tool" and m.get("function") == "read_board"]
    assert len(reads) == 1 and fake.COOK_ROUND_POST in reads[0]["content"]
    [d] = [d for d in rep.select(DecisionRecord, "round", 1, passer) if d.read_board]
    assert d.plan                      # read the board, then acted in the same decision
    assert [e.t for e in rep.select(Event, "round", 1, passer) if e.type == "read_board"] == [d.t]
    tool_events = [e for e in log.samples[0].events if isinstance(e, ToolEvent)]
    assert {e.function for e in tool_events} >= {"act", "post", "read_board", "note", "ready"}


def test_posts_cost_game_time(season):
    _, rep = season
    cook, _ = names_of(rep)
    [post] = rep.select(Post, "round", 1)
    [d] = [d for d in rep.select(DecisionRecord, "round", 1, cook) if d.post]
    words = len(fake.COOK_ROUND_POST.split())
    assert d.lands_t - d.t == 0 + math.ceil(words / 10) == 1
    assert post.t == d.lands_t
    # without a post a decision costs flat_s (= 0 here) and lands in the second it started
    assert all(x.lands_t == x.t for x in rep.select(DecisionRecord, "round", 1)
               if not x.post and x.lands_t is not None)


def test_cutoff_and_rejected_plan(season):
    log, rep = season
    cook, passer = names_of(rep)
    [cut] = [d for d in rep.select(DecisionRecord, "practice", 0) if d.cutoff]
    assert cut.agent == cook and cut.t == 0 and cut.lands_t == 1 and cut.plan == []
    assert any(e.type == "cutoff" and e.agent == cook and e.t == 0
               for e in rep.select(Event, "practice", 0))
    starts = [e.t for e in rep.select(Event, "practice", 0, cook) if e.type == "decision_start"]
    assert starts[:2] == [0, 1]                     # waited 1 s, then decided again
    cook_users = [m["content"] for m in log.samples[0].store["conversations"][cook]
                  if m["role"] == "user"]
    assert sum(cutoff_notice(1) in u for u in cook_users) == 1
    first = rep.select(DecisionRecord, "practice", 0, passer)[0]
    assert first.plan_sent == [fake.BAD_ACTION] and first.plan == []      # rejected: none runs
    land = next(e for e in rep.select(Event, "practice", 0, passer) if e.type == "decision_land")
    assert "pot_9" in land.data["reason"]
    conv = log.samples[0].store["conversations"][passer]
    act = next(m for m in conv if m["role"] == "tool" and m.get("function") == "act")
    assert act["content"].startswith("Plan rejected:") and "pot_9" in act["content"]
    starts = [e.t for e in rep.select(Event, "practice", 0, passer) if e.type == "decision_start"]
    assert starts[:2] == [0, 1]


def test_notes_and_decision_records(season):
    _, rep = season
    cook, passer = names_of(rep)
    debrief = rep.select(DecisionRecord, "debrief", 1)
    assert [(d.agent, d.t) for d in debrief] == [(cook, 0), (passer, 1)]
    assert all(d.note and d.note.endswith(fake.NOTE) for d in debrief)
    assert any(d.note == fake.NOTE for d in rep.select(DecisionRecord, "round", 1, passer))
    for d in rep.select(DecisionRecord):
        assert d.served_by == fake.SERVED_BY
        assert d.input_tokens and d.output_tokens and d.reasoning_tokens
    # the passer's notepad is shown back to it at its next decision
    assert any(e.type == "note" for e in rep.select(Event, "round", 1, passer))


def test_scorer(season):
    log, rep = season
    score = log.samples[0].scores["season_score"]
    v = score.value
    final = rep.frames("round", 1)[-1]
    assert v["total"] == v["round_1"] == final.score > 0
    assert v["served"] == final.served and v["missed"] == final.missed
    assert v["posts_agent0"] == 2 and v["posts_agent1"] == 1
    assert v["read_board_agent0"] == 0 and v["read_board_agent1"] == 1
    # cutoffs, forced, decisions: rounds only; the cook's one cutoff was in practice
    assert v["cutoffs_agent0"] == v["cutoffs_agent1"] == 0
    assert v["cutoffs_practice_agent0"] == 1 and v["cutoffs_practice_agent1"] == 0
    cook, passer = names_of(rep)
    for i, name in enumerate((cook, passer)):
        assert v[f"decisions_agent{i}"] == len(rep.select(DecisionRecord, "round", 1, name))
        assert v[f"decisions_practice_agent{i}"] == len(
            rep.select(DecisionRecord, "practice", 0, name))
        assert v[f"practice_finished_agent{i}"] == 0
    assert cook in score.explanation and "team score" in score.explanation
    metrics = {k for s in log.results.scores for k in s.metrics}
    assert {"mean", "stderr"} <= metrics


def test_viewer_on_exported_replay(season, monkeypatch):
    log, rep = season
    monkeypatch.setenv("UNDERCOOKED_REPLAY", log.location)
    at = AppTest.from_file(str(VIEWER), default_timeout=60).run()
    assert not at.exception
    at.sidebar.selectbox[1].select("round 1").run()
    assert not at.exception
    at.slider[0].set_value(30).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Score"] == str(rep.frames("round", 1)[-1].score)
    at.sidebar.selectbox[1].select("practice").run()
    assert not at.exception
    at.sidebar.selectbox[2].select(rep.header.agents[1].name).run()
    assert not at.exception
    at.sidebar.selectbox[1].select("chat before round 1").run()
    assert not at.exception and len(at.chat_message) == 2


def test_cli_export(season, tmp_path):
    log, rep = season
    out = tmp_path / "replay.json"
    res = CliRunner().invoke(cli, ["export", log.location, str(out)])
    assert res.exit_code == 0, res.output
    again = Replay.model_validate_json(out.read_text())
    assert again.phases() == rep.phases() and len(again.records) == len(rep.records)


def test_agents_never_see_model_or_provider_names(season):
    log, _ = season
    for conv in log.samples[0].store["conversations"].values():
        for m in conv:
            if m["role"] in ("system", "user", "tool"):
                text = str(m["content"]).lower()
                assert "kitchenfake" not in text and "fakecloud" not in text


def test_shuffled_seats(tmp_path):
    """agents[0] (the fake cook model) gets seat 2 (the onion/dish room) and agents[1] speaks
    first: the header records the seats, the agents start at their seats' digits, the chat starts
    with the drawn speaker, and the per-agent scores stay keyed by agent index (= model)."""
    seed = seed_where([2, 1], 1)
    log, [rep] = run_season(config(seed=seed, practice=False), tmp_path)
    a0, a1 = rep.header.agents
    assert (a0.model, a0.seat, a1.model, a1.seat) == ("kitchenfake/cook", 2, "kitchenfake/passer", 1)
    kitchen = Kitchen.load("forced_coordination")
    start = {a.name: tuple(a.pos) for a in rep.frames("round", 1)[0].agents}
    assert start == {a0.name: kitchen.starts[2], a1.name: kitchen.starts[1]}
    [chat_start] = [e for e in rep.select(Event, "chat", 1) if e.type == "phase_start"]
    assert chat_start.data["first"] == a1.name
    assert rep.select(DecisionRecord, "chat", 1)[0].agent == a1.name
    assert rep.frames("round", 1)[-1].served >= 1       # the fakes play the seat they got
    v = log.samples[0].scores["season_score"].value
    assert v["posts_agent1"] == 2 and v["posts_agent0"] == 1      # agent1 has the cook's job
    assert v["cutoffs_agent0"] == 0 and log.samples[0].store["undercooked"]["seats"] == [2, 1]


# ------------------------------------------------------------------------ other settings
def test_flat_cost_push_board_and_cli_run(tmp_path):
    """flat_s = 2, push board, no practice, via `undercooked run`."""
    cfg = config(practice=False, round_s=8, board="push", decision={"flat_s": 2})
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    res = CliRunner().invoke(cli, ["run", str(path), "--epochs", "1", "--log-dir",
                                   str(tmp_path / "logs")])
    assert res.exit_code == 0, res.output
    log_path = res.output.strip().splitlines()[-1]
    [rep] = load_replay(log_path)
    cook, passer = names_of(rep)
    assert rep.phases() == [("briefing", 0), ("chat", 1), ("round", 1), ("debrief", 1),
                            ("reflection", 1)]
    assert rep.header.practice_s is None and rep.header.cook_s == 2
    first = {d.agent: d for d in reversed(rep.select(DecisionRecord, "round", 1))}
    assert first[passer].t == 0 and first[passer].lands_t == 2              # flat_s
    assert first[cook].t == 0 and first[cook].lands_t == 3                  # flat_s + post
    for f in rep.frames("round", 1)[1:3]:          # after seconds 0->1 and 1->2
        assert {a.name: a.status for a in f.agents} == {cook: "deciding", passer: "deciding"}
        assert {a.name: tuple(a.pos) for a in f.agents} == {cook: (3, 1), passer: (1, 2)}
    log = read_eval_log(log_path, resolve_attachments=True)
    conv = log.samples[0].store["conversations"][passer]
    assert any("New posts on the board:" in m["content"] and fake.COOK_ROUND_POST in m["content"]
               for m in conv if m["role"] == "user")
    assert not any(d.read_board for d in rep.select(DecisionRecord))


def test_compaction(tmp_path):
    cfg = config(practice=False, round_s=20, compact_at_tokens=2400)
    log, [rep] = run_season(cfg, tmp_path)
    compactions = [e for e in rep.select(Event) if e.type == "compaction"]
    assert compactions
    assert any(isinstance(e, CompactionEvent) for e in log.samples[0].events)
    for name, conv in log.samples[0].store["conversations"].items():
        assert conv[0]["role"] == "system"
    summaries = [m for conv in log.samples[0].store["conversations"].values() for m in conv
                 if (m.get("metadata") or {}).get("summary")]
    assert summaries and all("Summary:" in m["content"] for m in summaries)
    # compaction takes no game time: the round still runs every second
    assert [f.t for f in rep.frames("round", 1)] == list(range(21))


# ------------------------------------------------------------------------------- units
def test_pick_names_distinct_and_seeded():
    for epoch in range(1, 30):
        a = pick_names(3, epoch)
        assert a == pick_names(3, epoch) and len(set(a)) == 2 and set(a) <= set(NATO_NAMES)
    assert pick_names(0, 1) != pick_names(0, 2) or pick_names(0, 2) != pick_names(0, 3)


def test_board_unread_and_scope():
    b = Board(["Kilo", "Tango"])
    b.add(Post(phase="chat", round=1, t=0, agent="Kilo", text="hi"))
    assert b.unread("Kilo", 1) == [] and len(b.unread("Tango", 1)) == 1
    b.add(Post(phase="round", round=1, t=5, agent="Kilo", text="onions"))
    assert [p.text for p in b.read_all("Tango", 1)] == ["hi", "onions"]
    assert b.unread("Tango", 1) == []
    b.add(Post(phase="chat", round=2, t=0, agent="Kilo", text="again"))
    assert [p.text for p in b.read_all("Tango", 2)] == ["again"]


def test_pick_endpoint():
    eps = [
        {"tag": "cheap/fp4", "quantization": "fp4", "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools", "tool_choice"]},
        {"tag": "notools/fp8", "quantization": "fp8",
         "pricing": {"prompt": "0.0000001", "completion": "0"}, "supported_parameters": ["tools"]},
        {"tag": "a/bf16", "quantization": "bf16",
         "pricing": {"prompt": "0.0000003", "completion": "0.000001"},
         "supported_parameters": ["tools", "tool_choice", "reasoning"]},
        {"tag": "b/fp8", "quantization": "fp8",
         "pricing": {"prompt": "0.0000002", "completion": "0.000001"},
         "supported_parameters": ["tool_choice", "tools"]},
        {"tag": "c/unknown", "quantization": "unknown",
         "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools", "tool_choice"]},
    ]
    assert pick_endpoint(eps, "fp8")["tag"] == "b/fp8"
    assert pick_endpoint(eps, "bf16")["tag"] == "a/bf16"
    with pytest.raises(ValueError):
        pick_endpoint(eps[:2], "fp8")
    json.dumps(eps)
