"""Several levels per run: one sample (season) per level, `epochs` each, per-level overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from inspect_ai.log import read_eval_log
from streamlit.testing.v1 import AppTest

from tests import kitchen_fake as fake
from tests.test_pipeline import config, run
from undercooked.cli import main as cli
from undercooked.config import REPO_ROOT, Config
from undercooked.engine import Kitchen
from undercooked.replay import Event
from undercooked.replay_io import export, load_replay
from undercooked.season import assign_seats, first_speaker, pick_names
from undercooked.task import make_task

LEVELS = ["split_room", "forced_coordination", "cramped_room"]
VIEWER = Path(__file__).parent.parent / "undercooked" / "viewer.py"


def multi(**kw) -> Config:
    base = dict(levels=LEVELS, epochs=2, rounds=1, round_s=10, practice=False, scoring="orders",
                level_overrides={"split_room": {"orders": {"every_s": 4}, "round_s": 8}})
    return config(**{**base, **kw})


@pytest.fixture(scope="module")
def multi_run(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(fake, "DELAY", (2, 0.0, 0.01))
    try:
        log, _ = run(multi(), tmp_path_factory.mktemp("multi"))
    finally:
        mp.undo()
    return log, load_replay(log.location)


def test_one_replay_per_level_and_epoch(multi_run):
    log, reps = multi_run
    assert [(r.header.level, r.header.epoch) for r in reps] == [
        (lv, e) for lv in LEVELS for e in (1, 2)]                 # config order, then epoch
    for r in reps:
        assert r.header.map == Kitchen.load(r.header.level).map_rows()
        start = {a.name: tuple(a.pos) for a in r.frames("round", 1)[0].agents}
        k = Kitchen.load(r.header.level)
        assert start == {a.name: k.starts[a.seat] for a in r.header.agents}
    assert sorted({s.id for s in log.samples}) == sorted(LEVELS)
    assert all(s.metadata["level"] == s.id for s in log.samples)
    assert all(s.store["undercooked"]["level"] == s.id for s in log.samples)


def test_level_overrides_apply(multi_run):
    _, reps = multi_run
    for r in reps:
        frames = r.frames("round", 1)
        new = [e.t for e in r.select(Event, "round", 1) if e.type == "order_new"]
        if r.header.level == "split_room":
            assert r.header.round_s == 8 and frames[-1].t == 8
            assert new == [0, 4]                                    # every_s 4
        else:
            assert r.header.round_s == 10 and frames[-1].t == 10
            assert new == [0]                                       # every_s 30 (config)
        # due_s is not overridden: every order is due 60 s after it arrives
        assert all(o.due_t - o.created_t == 60 for f in frames for o in f.orders)


def test_names_seats_and_seeds_differ_per_level_and_epoch(multi_run):
    _, reps = multi_run
    seasons = {(r.header.level, r.header.epoch): [a.name for a in r.header.agents] for r in reps}
    assert len({tuple(v) for v in seasons.values()}) == len(seasons)
    for (lv, e), names in seasons.items():
        assert names == pick_names(0, e, level=lv)
    draws = {(lv, e): (tuple(pick_names(0, e, level=lv)), tuple(assign_seats(0, e, level=lv)),
                       first_speaker(0, e, 1, level=lv))
             for lv in LEVELS for e in range(1, 9)}
    assert len({d[0] for d in draws.values()}) == len(draws)       # names never repeat here
    assert len({d[1] for d in draws.values()}) == 2                # both seatings occur
    # single-level runs keep their old streams
    assert pick_names(0, 1) == pick_names(0, 1, level=None) != pick_names(0, 1, level="x")


def test_grouped_metrics_per_level(multi_run):
    log, reps = multi_run
    scores = {s.name: s for s in log.results.scores}
    metrics = scores["total"].metrics
    for lv in LEVELS:
        mine = [r.frames("round", 1)[-1].score for r in reps if r.header.level == lv]
        assert metrics[f"grouped_{lv}"].value == pytest.approx(sum(mine) / len(mine))
    assert {f"grouped_{lv}" for lv in LEVELS} <= set(scores["served"].metrics)


def test_export_names_files_by_level(multi_run, tmp_path):
    log, _ = multi_run
    paths = export(log.location, tmp_path / "r.json")
    assert sorted(p.name for p in paths) == sorted(f"r-{lv}-e{e}.json" for lv in LEVELS
                                                   for e in (1, 2))


def test_viewer_level_selector(multi_run, monkeypatch):
    log, reps = multi_run
    monkeypatch.setenv("UNDERCOOKED_REPLAY", log.location)
    at = AppTest.from_file(str(VIEWER), default_timeout=60).run()
    assert not at.exception
    level, season = at.sidebar.selectbox[0], at.sidebar.selectbox[1]
    assert level.label == "Level" and list(level.options) == LEVELS
    assert season.label == "Season (epoch)"
    level.select("cramped_room").run()
    assert not at.exception
    assert any("level cramped_room" in c.value for c in at.sidebar.caption)


def test_config_levels_validation():
    with pytest.raises(ValueError, match="set level"):
        Config.model_validate({"name": "x", "agents": []})
    with pytest.raises(ValueError, match="duplicates"):
        multi(levels=["split_room", "split_room"])
    with pytest.raises(ValueError, match="unknown levels"):
        multi(level_overrides={"split_rom": {"round_s": 5}})
    cfg = multi()
    assert cfg.run_levels() == LEVELS
    sr = cfg.for_level("split_room")
    assert (sr.level, sr.round_s, sr.orders.every_s, sr.orders.due_s) == ("split_room", 8, 4, 60)
    assert cfg.for_level("cramped_room").orders == cfg.orders
    with pytest.raises(ValueError):
        cfg.for_level("open_plan")
    assert [s.id for s in make_task(cfg).dataset] == LEVELS


def test_cli_levels_flag(tmp_path):
    cfg = config(rounds=1, round_s=3, practice=False)
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    res = CliRunner().invoke(cli, ["run", str(path), "--epochs", "2", "--levels",
                                   "cramped_room,coordination_ring", "--log-dir",
                                   str(tmp_path / "logs")])
    assert res.exit_code == 0, res.output
    log_path = res.output.strip().splitlines()[-1]
    reps = load_replay(log_path)
    assert [(r.header.level, r.header.epoch) for r in reps] == [
        ("cramped_room", 1), ("cramped_room", 2), ("coordination_ring", 1),
        ("coordination_ring", 2)]
    head = read_eval_log(log_path, header_only=True)
    assert head.eval.config.max_samples == 4                      # 2 levels x 2 epochs at once


def test_all_levels_config():
    cfg = Config.load(REPO_ROOT / "configs" / "all_levels_nocomms.yaml")
    assert cfg.comms == "none" and cfg.rounds == 5 and cfg.epochs == 5
    assert cfg.run_levels() == ["forced_coordination", "split_room", "cramped_room",
                                "asymmetric_advantages", "coordination_ring", "counter_circuit"]
    assert set(cfg.level_overrides) == set(cfg.run_levels())
    assert all(ov.orders is not None and ov.orders.every_s for ov in cfg.level_overrides.values())
    assert [s.id for s in make_task(cfg).dataset] == cfg.run_levels()
    for lv in cfg.run_levels():
        Kitchen.load(lv)
