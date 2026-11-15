"""Viewer smoke tests: fixture validity, loader, and the Streamlit app via AppTest."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests import kitchen_fake  # noqa: F401  (registers the kitchenfake/* models)
from undercooked.replay import DecisionRecord, Post, Replay
from undercooked.replay_io import load_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_min.json"
VIEWER = Path(__file__).parent.parent / "undercooked" / "viewer.py"


def make_app(monkeypatch) -> AppTest:
    monkeypatch.setenv("UNDERCOOKED_REPLAY", str(FIXTURE))
    return AppTest.from_file(str(VIEWER), default_timeout=30)


def test_fixture_validates():
    rep = Replay.model_validate_json(FIXTURE.read_text())
    assert len(rep.frames("round", 1)) == 41
    assert rep.header.round_s == 40


def test_load_replay():
    assert len(load_replay(FIXTURE)) == 1
    with pytest.raises(FileNotFoundError):   # .eval export exists now (tests/test_season.py)
        load_replay("x.eval")


def test_app_runs_and_scrubs(monkeypatch):
    at = make_app(monkeypatch).run()
    assert not at.exception
    # Phases are chat, round 1, debrief; the first (chat) has no frames, so pick the round.
    at.sidebar.selectbox[1].select("round 1").run()
    assert not at.exception
    at.slider[0].set_value(40).run()
    assert not at.exception
    served = {m.label: m.value for m in at.metric}
    assert served["Score"] == "10" and served["Served"] == "1" and served["Missed"] == "1"


def test_app_chat_and_debrief(monkeypatch):
    at = make_app(monkeypatch).run()
    assert not at.exception
    at.sidebar.selectbox[1].select("chat before round 1").run()   # the default is now round 1
    assert len(at.chat_message) >= 4
    at.sidebar.selectbox[1].select("debrief after round 1").run()
    assert not at.exception


def test_two_rounds_keep_chats_separate(tmp_path, monkeypatch):
    rep = Replay.model_validate_json(FIXTURE.read_text())
    chat2 = [r.model_copy(update={"round": 2}) for r in rep.select(phase="chat", round=1)]
    rep2 = rep.model_copy(update={"records": rep.records + chat2})
    assert rep2.phases().count(("chat", 1)) == 1 and ("chat", 2) in rep2.phases()
    assert len(rep2.select(Post, "chat", 1)) == len(rep2.select(Post, "chat", 2)) == 4
    assert len(rep2.select(DecisionRecord, "chat", 2)) == 4
    path = tmp_path / "two.json"
    path.write_text(rep2.model_dump_json())
    monkeypatch.setenv("UNDERCOOKED_REPLAY", str(path))
    at = AppTest.from_file(str(VIEWER), default_timeout=30).run()
    at.sidebar.selectbox[1].select("chat before round 2").run()
    assert not at.exception
    assert len(at.chat_message) == 4


def round_app(monkeypatch) -> AppTest:
    at = make_app(monkeypatch).run()
    at.sidebar.selectbox[1].select("round 1").run()
    return at


def button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


def test_play_button_toggles_and_ticks(monkeypatch):
    at = round_app(monkeypatch)
    assert not at.exception
    assert button(at, "play_btn").label == "▶ Play"
    assert at.selectbox(key="speed").options == ["0.5x", "1x", "2x", "4x", "8x"]
    assert at.selectbox(key="speed").value == "1x"
    button(at, "play_btn").click().run()
    assert not at.exception
    assert at.session_state["playing"] is True
    assert button(at, "play_btn").label == "⏸ Pause"
    t0 = at.slider[0].value
    at.run()                                   # a timer rerun: one more game second
    assert at.slider[0].value >= t0
    button(at, "play_btn").click().run()
    assert at.session_state["playing"] is False
    assert not at.exception


def test_play_stops_at_end_and_pause_controls_work(monkeypatch):
    at = round_app(monkeypatch)
    at.slider[0].set_value(39).run()
    button(at, "play_btn").click().run()
    at.session_state["last_tick"] = 0.0
    at.run()
    assert at.slider[0].value == 40 and at.session_state["playing"] is False
    assert not at.exception
    at.selectbox(key="speed").select("4x").run()
    assert not at.exception
    at.slider[0].set_value(5).run()
    assert at.slider[0].value == 5


def test_full_screen_hides_board_and_exits(monkeypatch):
    at = round_app(monkeypatch)
    assert any("Board" in m.value for m in at.markdown)
    button(at, "fs_enter").click().run()
    assert not at.exception
    assert not any(m.value == "**Board**" for m in at.markdown)
    assert any("Latest post" in m.value for m in at.markdown)   # the one-line ticker
    assert len(at.tabs) == 0 and len(at.slider) == 1
    button(at, "fs_exit").click().run()
    assert not at.exception
    assert any(m.value == "**Board**" for m in at.markdown)


# ---------------------------------------------------------------- many runs: a directory of logs

def make_log(cfg_kw: dict, out: Path) -> Path:
    from inspect_ai import eval as inspect_eval

    from undercooked.config import AgentSpec, Config
    from undercooked.task import make_task

    base = dict(name="dash", level="forced_coordination", rounds=2, round_s=12, practice=False,
                chat_max_posts=2, epochs=2, cook_s=2, decision={"flat_s": 0, "post_words_per_s": 10},
                agents=[AgentSpec(model="kitchenfake/cook"), AgentSpec(model="kitchenfake/passer")])
    out.mkdir(parents=True, exist_ok=True)
    [log] = inspect_eval(make_task(Config.model_validate({**base, **cfg_kw})), model="none/none",
                         log_dir=str(out), display="none")
    assert log.status == "success", log.error
    return Path(log.location)


FULL = "full rules · comms · Cook+Passer"
GOAL = "goal-only · no comms · Cook+Passer"


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory):
    from inspect_ai.log import read_eval_log, write_eval_log

    root = tmp_path_factory.mktemp("pilot")
    good = make_log({"comms": "full"}, root / "a")                                  # 2 seasons
    make_log({"comms": "none", "rules": "minimal"}, root / "b")                     # 2 seasons
    make_log({"comms": "full", "levels": ["forced_coordination", "split_room"], "epochs": 1},
             root / "c")                                                            # 2 seasons
    for status, name in (("started", "running"), ("error", "failed")):   # in-progress and failed logs
        log = read_eval_log(str(good))
        log.status = status
        write_eval_log(log, str(root / "a" / f"{name}.eval"))
    return root


def dash_app(monkeypatch, root) -> AppTest:
    monkeypatch.setenv("UNDERCOOKED_REPLAY", str(root))
    return AppTest.from_file(str(VIEWER), default_timeout=60).run()


def test_dir_overview_and_conditions(monkeypatch, log_dir):
    at = dash_app(monkeypatch, log_dir)
    assert not at.exception
    ctrl = at.segmented_control(key="cond")
    assert ctrl.value == "All conditions"
    assert ctrl.options == ["All conditions", FULL, GOAL]
    over = at.dataframe[0].value
    assert list(over["condition"]) == [FULL, GOAL] and list(over["seasons"]) == [4, 2]
    assert over["forced_coordination"][0].endswith("(3)") and over["forced_coordination"][1].endswith("(2)")
    assert over["split_room"][0].endswith("(1)") and over["split_room"][1] == "-"
    assert len(at.sidebar.selectbox) == 0            # the overview has no per-season selectors
    assert any("skipped 2" in c.value for c in at.sidebar.caption)   # the running and failed logs


def test_dir_switch_condition(monkeypatch, log_dir):
    at = dash_app(monkeypatch, log_dir)
    at.segmented_control(key="cond").select(GOAL).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert len(table) == 2 and table["level"].unique().tolist() == ["forced_coordination"]
    assert {"total", "rounds", "forced", "cutoffs", "practice done"} <= set(table.columns)
    assert any(c.startswith("1st serve A0") for c in table.columns)
    at.sidebar.selectbox[1].select("round 1").run()                  # one level: Season, Phase
    assert not at.exception and at.slider
    at.segmented_control(key="cond").select(FULL).run()
    assert not at.exception
    assert len(at.dataframe[0].value) == 4                          # 2 + 2 seasons over 2 levels
    assert at.sidebar.selectbox[0].label == "Level"


def test_dir_skips_running_and_failed(log_dir):
    from undercooked.dashboard import Season, group_conditions
    from undercooked.replay_io import eval_seasons, eval_status

    statuses = {p.name: eval_status(p) for p in log_dir.rglob("*.eval")}
    assert sorted(statuses.values()) == ["error", "started", "success", "success", "success"]
    done = [p for p in log_dir.rglob("*.eval") if statuses[p.name] == "success"]
    seasons = [Season(p, r.header.level, r.header.epoch, r, sc) for p in done for r, sc in eval_seasons(p)]
    assert len(seasons) == 6 and all(s.scores for s in seasons)
    assert sorted(len(v) for v in group_conditions(seasons).values()) == [2, 4]
