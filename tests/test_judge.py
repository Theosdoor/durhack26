"""analysis/judge.py offline: transcript builder, JSON parser, and the task on mockllm."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model

sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
import judge  # noqa: E402
from tests.test_pipeline import config, run  # noqa: E402
from undercooked.replay_io import load_replay  # noqa: E402


@pytest.fixture(scope="module")
def season_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("seasons")
    run(config(reflection=True), d)
    return d


def answer(names):
    return json.dumps({"team": {"hierarchy": "single_leader", "leader": names[0],
                                "coordination_quality": 4, "strategy_summary": "s",
                                "strategy_change": "c"},
                       "agents": {n: {"bossiness": 3, "role": "leader", "responsiveness": 4,
                                      "evidence": ["#1", 2]} for n in names}})


def test_transcript_blind_and_ordered(season_dir):
    [log] = sorted(season_dir.glob("*.eval"))
    [rep] = load_replay(log)
    t = judge.build_transcript(rep)
    for a in rep.header.agents:
        assert a.model not in t and a.name in t
    assert f"rules" not in t.lower().split("final reflections")[0].split("map rows")[0]
    n = judge.post_count(t)
    assert n == len([r for r in rep.records if r.kind == "post"
                     and r.phase in ("chat", "round")])
    assert "team score" in t and "Final reflections:" in t
    ks = [int(k) for k in __import__("re").findall(r"\] #(\d+) ", t)]
    assert ks == list(range(1, n + 1))


def test_parse_fenced_and_prose():
    names = ["Kilo", "Foxtrot"]
    raw = "Here you go:\n```json\n" + answer(names) + "\n```\nDone"
    j = judge.parse_judgement(raw, names)
    assert j["agents"]["Kilo"]["evidence"] == ["#1", "#2"]
    assert judge.flat_values(j)["mean_bossiness"] == 3
    assert judge.parse_judgement(answer(names), names)["team"]["leader"] == "Kilo"


@pytest.mark.parametrize("bad", ["", "no json", "{", "[1]", '{"team": {}, "agents": {}}'])
def test_parse_failures(bad):
    with pytest.raises(judge.JudgeParseError):
        judge.parse_judgement(bad, ["Kilo"])
    assert judge.flat_values(None) == {"parse_ok": 0.0}


def test_parse_rejects_wrong_names_and_ranges():
    with pytest.raises(judge.JudgeParseError):
        judge.parse_judgement(answer(["Kilo", "Lima"]), ["Kilo", "Foxtrot"])
    d = json.loads(answer(["Kilo"]))
    d["agents"]["Kilo"]["bossiness"] = 6
    with pytest.raises(judge.JudgeParseError):
        judge.parse_judgement(json.dumps(d), ["Kilo"])
    d["agents"]["Kilo"]["bossiness"] = 2
    d["team"]["hierarchy"] = "boss"
    with pytest.raises(judge.JudgeParseError):
        judge.parse_judgement(json.dumps(d), ["Kilo"])


def test_task_end_to_end_mockllm(season_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    [rep] = load_replay(sorted(season_dir.glob("*.eval"))[0])
    names = [a.name for a in rep.header.agents]
    mock = get_model("mockllm/model", custom_outputs=[
        ModelOutput.from_content("mockllm/model", answer(names)),
        ModelOutput.from_content("mockllm/model", "not json")])
    asked = {}
    monkeypatch.setattr(judge, "get_model", lambda *a, **kw: asked.update(a=a, kw=kw) or mock)
    t = judge.judge_seasons(str(season_dir), limit=1)
    assert asked["a"] == ("openrouter/z-ai/glm-5.3-flash",)           # pinned, temperature 0
    assert asked["kw"]["provider"]["only"] == ["morph/fp8"]
    assert asked["kw"]["config"].temperature == 0 and asked["kw"]["config"].max_tokens == 4000
    [log] = inspect_eval(t, log_dir=str(tmp_path), display="none")
    assert log.status == "success", log.error
    [s] = read_eval_log(log.location).samples
    sc = s.scores["judgement_scorer"]
    assert sc.value["parse_ok"] == 1.0 and sc.metadata["parsed"]["agents"][names[0]]
    for k in ("log_file", "sample_id", "epoch", "level", "rules", "n_agents", "models", "names"):
        assert k in sc.metadata and k in s.metadata
    assert s.metadata["log_file"].endswith(".eval")


def test_table_stats():
    import judge_table as jt
    assert jt.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert jt.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert jt.spearman([1, 1, 2], [1, 2, 2]) == pytest.approx(0.5)
    assert jt.kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == pytest.approx(1)
    assert jt.kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0)
