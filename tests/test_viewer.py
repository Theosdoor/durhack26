"""Viewer smoke tests: fixture validity, loader, and the Streamlit app via AppTest."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

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
    with pytest.raises(NotImplementedError):
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
