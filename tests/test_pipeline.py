"""Pipelined decisions and parallel seasons change wall time only, never the game.

The fake models (tests/kitchen_fake.py, "chaos" flavour) post 1-25 words, cut off with and
without posts, write notes and send rejected plans, so landing times vary; DELAY gives every call
a pseudo-random latency so calls finish in a different order from the one they started in.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from inspect_ai import eval as inspect_eval
from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, get_model

from tests import kitchen_fake as fake
from undercooked.cli import main as cli
from undercooked.config import AgentSpec, Config
from undercooked.providers import served_providers, track_served_provider
from undercooked.replay import DecisionRecord, Event, Replay
from undercooked.replay_io import load_replay
from undercooked.season import pick_names
from undercooked.task import make_task

CHAOS = [AgentSpec(model="kitchenfake/cook+chaos"), AgentSpec(model="kitchenfake/passer+chaos")]


def config(**kw: Any) -> Config:
    base = dict(name="pipeline", level="forced_coordination", rounds=2, round_s=25, practice=True,
                practice_s=15, chat_max_posts=2, epochs=1, cook_s=2, agents=CHAOS,
                decision={"flat_s": 2, "post_words_per_s": 10})
    return Config.model_validate({**base, **kw})


def with_pipeline(cfg: Config, on: bool) -> Config:
    return cfg.model_copy(update={"decision": cfg.decision.model_copy(update={"pipeline": on})})


@pytest.fixture
def calls(monkeypatch):
    """Record the decision calls' wall-clock intervals."""
    monkeypatch.setattr(fake, "RECORD", True)
    monkeypatch.setattr(fake, "CALLS", [])
    return fake


def run(cfg: Config, log_dir: Path, max_samples: int | None = None) -> tuple[EvalLog, float]:
    clock = time.monotonic()
    [log] = inspect_eval(make_task(cfg), model="none/none", log_dir=str(log_dir),
                         display="none", max_samples=max_samples)
    wall = time.monotonic() - clock
    assert log.status == "success", log.error
    return read_eval_log(log.location, resolve_attachments=True), wall


def comparable(rep: Replay) -> dict[str, Any]:
    """The replay without its wall-clock fields (phase_end duration_s)."""
    d = rep.model_dump(mode="json")
    for r in d["records"]:
        if r["kind"] == "event":
            r["data"].pop("duration_s", None)
    return d


def no_ids(x: Any) -> Any:
    """Messages without their random ids (tool call ids come from a global counter)."""
    if isinstance(x, dict):
        return {k: no_ids(v) for k, v in x.items() if k not in ("id", "tool_call_id")}
    if isinstance(x, list):
        return [no_ids(v) for v in x]
    return x


def season_of(log: EvalLog, epoch: int) -> dict[str, Any]:
    [s] = [s for s in log.samples if s.epoch == epoch]
    return {"conversations": no_ids(s.store["conversations"]),
            "summary": s.store["undercooked"],
            "score": s.scores["season_score"].value}


def overlap(a: fake.Call, b: fake.Call) -> bool:
    return a.start < b.end and b.start < a.end


def cross_second_overlaps(calls: list[fake.Call], practice: bool) -> int:
    """Pairs of practice (or round) decision calls of one phase, for different game seconds,
    that were in flight at once."""
    calls = [c for c in calls if (c.phase == "Practice") == practice]
    return sum(a.phase == b.phase and a.t != b.t and overlap(a, b)
               for i, a in enumerate(calls) for b in calls[i + 1:])


def late_restarts(calls: list[fake.Call]) -> int:
    """Round seconds s where agent Y started deciding at s + 1 while agent X's decision from s
    was still running, and X decided again at s + 1 too: X's decision landed at s + 1 (a cutoff
    or rejected plan) only after Y had started. That is the case where the pipeline lands a
    decision late in its second and restarts its agent within that second."""
    rnd = [c for c in calls if c.phase.startswith("Round") and c.t is not None]
    n = 0
    for x, phase, s in {(c.agent, c.phase, c.t) for c in rnd}:
        mine = [c for c in rnd if (c.agent, c.phase, c.t) == (x, phase, s)]
        nxt = [c for c in rnd if c.phase == phase and c.t == s + 1]
        n += (any(c.agent == x for c in nxt)
              and any(c.agent != x and c.start < max(m.end for m in mine) for c in nxt))
    return n


# ------------------------------------------------------------------- (a) equivalence
CASES = [  # seed, flat_s, board, compact_at_tokens
    (0, 2, "pull", 32000),
    (1, 2, "push", 4000),          # push board, compactions
    (2, 1, "pull", 4000),
    (5, 3, "pull", 32000),         # (seed 3 drew no rejected plan once seats were shuffled)
    (4, 0, "pull", 32000),         # zero-cost decisions land in the second they start
]


def test_pipeline_matches_sequential(tmp_path, monkeypatch, calls):
    """Several seasons, each run sequentially and pipelined with the same seeded latencies:
    identical replays (bar phase durations), conversations, summaries and scores."""
    restarts = 0
    for seed, flat_s, board, compact_at in CASES:
        case = f"seed={seed} flat_s={flat_s} board={board}"
        monkeypatch.setattr(fake, "DELAY", (seed, 0.0, 0.03))
        cfg = config(seed=seed, board=board, compact_at_tokens=compact_at,
                     decision={"flat_s": flat_s, "post_words_per_s": 10})
        out = {}
        for on in (False, True):
            fake.CALLS.clear()
            log, _ = run(with_pipeline(cfg, on), tmp_path / f"{seed}-{on}")
            [rep] = load_replay(log.location)
            out[on] = (comparable(rep), season_of(log, 1), list(fake.CALLS))
        (seq, seq_season, seq_calls), (pipe, pipe_season, pipe_calls) = out[False], out[True]
        assert pipe == seq, case               # same frames, events, posts, decisions, order
        assert pipe_season == seq_season, case     # same conversations, summary, scores

        # the sequential loop never overlaps calls across game seconds; the pipeline does: the
        # two practice games always, and round decisions one second apart when flat_s >= 1
        for practice in (True, False):
            assert cross_second_overlaps(seq_calls, practice) == 0, case
        assert cross_second_overlaps(pipe_calls, practice=True) > 0, case
        assert (cross_second_overlaps(pipe_calls, practice=False) > 0) == (flat_s >= 1), case
        assert late_restarts(seq_calls) == 0, case
        restarts += late_restarts(pipe_calls)

        # the season covered what matters: varied landing times, cutoffs with and without
        # posts, rejected plans, simultaneous starts
        rep = Replay.model_validate(seq)
        decisions = [d for d in rep.select(DecisionRecord) if d.phase in ("practice", "round")]
        assert len({d.lands_t - d.t for d in decisions if d.lands_t is not None}) >= 3, case
        assert any(d.cutoff and d.post for d in decisions), case
        assert any(d.cutoff and not d.post for d in decisions), case
        lands = [e for e in rep.select(Event) if e.type == "decision_land"]
        assert any("reason" in e.data for e in lands), case
        starts = [(e.phase, e.round, e.t) for e in rep.select(Event)
                  if e.type == "decision_start"]
        assert len(starts) > len(set(starts)), case
    assert restarts > 0                        # a late landing + same-second restart happened


# ------------------------------------------------------------------- (b) speed-up
def test_pipeline_speedup(tmp_path, monkeypatch, capsys):
    """Wall time with 0.2-0.5 s calls, flat_s = 2: the whole eval, and the practice + round
    phases (the ones the pipeline changes; chat is turn-based and debrief already parallel)."""
    monkeypatch.setattr(fake, "DELAY", (7, 0.2, 0.5))
    cfg = config(rounds=1, round_s=20, practice_s=15)
    walls, phases, reps = {}, {}, {}
    for on in (False, True):
        log, walls[on] = run(with_pipeline(cfg, on), tmp_path / str(on))
        [rep] = load_replay(log.location)
        phases[on] = {e.phase: e.data["duration_s"] for e in rep.select(Event)
                      if e.type == "phase_end"}
        reps[on] = comparable(rep)
    assert reps[True] == reps[False]
    played = {on: phases[on]["practice"] + phases[on]["round"] for on in (False, True)}
    with capsys.disabled():
        print(f"\nfake 0.2-0.5 s calls, sequential vs pipelined: eval {walls[False]:.1f} s vs "
              f"{walls[True]:.1f} s; practice {phases[False]['practice']} vs "
              f"{phases[True]['practice']} s; round {phases[False]['round']} vs "
              f"{phases[True]['round']} s")
    assert played[True] < 0.9 * played[False]


# ------------------------------------------------------------------- (c) parallel seasons
def test_parallel_epochs_match_one_at_a_time(tmp_path, monkeypatch, calls, capsys):
    monkeypatch.setattr(fake, "DELAY", (11, 0.0, 0.04))
    agents = [AgentSpec(model="kitchenfake/cook+chaos+tagged"),
              AgentSpec(model="kitchenfake/passer+chaos+tagged")]
    cfg = config(epochs=2, rounds=1, round_s=20, practice_s=10, agents=agents)
    names = [set(pick_names(cfg.seed, e)) for e in (1, 2)]
    assert not names[0] & names[1]               # so a call's agent tells its season
    logs, walls, overlaps = {}, {}, {}
    for n in (1, 2):
        fake.CALLS.clear()
        logs[n], walls[n] = run(cfg, tmp_path / str(n), max_samples=n)
        e1, e2 = ([c for c in fake.CALLS if c.agent in names[e]] for e in (0, 1))
        overlaps[n] = any(overlap(a, b) for a in e1 for b in e2)
    assert not overlaps[1] and overlaps[2]       # max_samples=2 really ran them at once
    one = load_replay(logs[1].location)
    two = load_replay(logs[2].location)
    assert [r.header.epoch for r in two] == [1, 2]
    for a, b in zip(one, two):
        assert comparable(a) == comparable(b)
        assert season_of(logs[1], a.header.epoch) == season_of(logs[2], b.header.epoch)
    with capsys.disabled():
        print(f"\n2 epochs, fake 0-0.04 s calls: one at a time {walls[1]:.1f} s, "
              f"in parallel {walls[2]:.1f} s")


# ------------------------------------------------------------------- (d) served_by
def test_served_by_is_per_call_under_concurrency(monkeypatch):
    """Many concurrent calls through ONE model instance (one wrapped api.on_response), each
    served by a different provider after a random delay: each call sees only its own."""
    monkeypatch.setattr(fake, "DELAY", (5, 0.0, 0.05))
    model = track_served_provider(get_model("kitchenfake/cook+tagged"))
    assert model is track_served_provider(get_model("kitchenfake/cook+tagged"))  # memoized

    async def one(name: str) -> list[str]:
        await asyncio.sleep(random.Random(name).uniform(0, 0.02))
        msgs = [ChatMessageSystem(content=f"You are {name}, a cook."),
                ChatMessageUser(content="Summarise.")]
        with served_providers() as served:
            await model.generate(msgs)
        return served

    names = [f"Agent{i}" for i in range(40)]

    async def main() -> list[list[str]]:
        return await asyncio.gather(*(one(n) for n in names))

    assert asyncio.run(main()) == [[f"Cloud-{n}"] for n in names]


def test_served_by_per_agent_in_parallel_seasons(tmp_path, monkeypatch):
    """Two agents on the SAME model in each of two seasons running at once, pipelined, with
    random latencies: every DecisionRecord names its own agent's provider."""
    monkeypatch.setattr(fake, "DELAY", (3, 0.0, 0.03))
    same = AgentSpec(model="kitchenfake/cook+chaos+tagged")
    cfg = config(epochs=2, rounds=1, round_s=15, practice_s=8, agents=[same, same])
    log, _ = run(cfg, tmp_path, max_samples=2)
    reps = load_replay(log.location)
    assert len(reps) == 2
    seen = 0
    for rep in reps:
        for d in rep.select(DecisionRecord):
            assert d.served_by == f"Cloud-{d.agent}", (d.agent, d.served_by)
            seen += 1
    assert seen > 20


# ------------------------------------------------- the invariant the pipeline relies on
def test_landing_does_not_change_teammates_state_text():
    """A decision that lands at t without a post may land after the teammate's decision at t
    has started (season._run_game). That is only exact while a landing (plan set or rejected,
    status, notepad) changes nothing the teammate's state text shows; guard it here."""
    from undercooked.engine import Game, Kitchen

    cfg = config()
    g = Game(Kitchen.load("forced_coordination"), ["Kilo", "Tango"], cfg)
    g.set_status("Kilo", "deciding")
    g.set_status("Tango", "deciding")

    def tango() -> str:
        return g.state_text("Tango", unread_posts=1, notepad="n",
                            last_plan_result=g.last_plan_result("Tango"))

    before = tango()
    assert g.set_plan("Kilo", ["interact(pot_9)"]) is not None        # rejected
    g.set_status("Kilo", "idle")
    assert tango() == before
    g.set_status("Kilo", "deciding")
    assert g.set_plan("Kilo", ["interact(pot_1)", "wait(2)"]) is None  # accepted
    assert tango() == before


# ------------------------------------------------------------------------------- CLI
@pytest.mark.parametrize("flags, max_samples, pipeline", [
    ([], 2, True),                                   # default: every epoch at once
    (["--max-samples", "1", "--no-pipeline"], 1, False),
])
def test_cli_max_samples_and_pipeline(tmp_path, flags, max_samples, pipeline):
    cfg = config(rounds=1, round_s=4, practice=False)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    res = CliRunner().invoke(cli, ["run", str(path), "--epochs", "2", "--log-dir",
                                   str(tmp_path / "logs"), *flags])
    assert res.exit_code == 0, res.output
    log = read_eval_log(res.output.strip().splitlines()[-1], header_only=True)
    assert log.eval.config.max_samples == max_samples
    assert log.eval.metadata["config"]["decision"]["pipeline"] is pipeline
