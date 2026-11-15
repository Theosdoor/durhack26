"""analysis/metrics.py on a short offline season (scripted kitchenfake models, no network)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from tests.test_season import config, run_season

sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
import metrics  # noqa: E402


@pytest.fixture(scope="module")
def logs(tmp_path_factory):
    d = tmp_path_factory.mktemp("logs")
    run_season(config(), d)
    return d


def test_directive_regex():
    yes = ["Grab the dish and bring it to the hatch", "Foxtrot, bring onions", "you take the onions",
           "You should grab a dish", "Can you plate now?", "let's swap", "Please wait at (2,1)",
           "Plan: U: take pot_2", "go to the hatch"]
    no = ["I am at the pots.", "Score 60 (3 soups).", "I'll go to the hatch", "Soup is ready in 6s",
          "The wall splits the map"]
    assert all(metrics.is_directive(t) for t in yes), [t for t in yes if not metrics.is_directive(t)]
    assert not any(metrics.is_directive(t) for t in no), [t for t in no if metrics.is_directive(t)]
    assert metrics.is_commit("OK I'll take pot_2") and not metrics.is_commit("take pot_2")


def test_tables_from_a_real_log(logs):
    seasons, skipped = metrics.load_logs(logs)
    assert len(seasons) == 1 and skipped == []
    agent, season = metrics.build_tables(seasons)
    assert len(agent) == 2 and len(season) == 1
    sc = seasons[0].scores
    assert season.team_total[0] == sc["total"] and season.round_1[0] == sc["round_1"]
    assert agent.team_total.tolist() == [sc["total"]] * 2
    # words and posts agree with the scorer's post counts and the Post records
    assert agent.posts_total.sum() >= agent.posts_round.sum() > 0
    assert agent.words_share.sum() == pytest.approx(1.0)
    assert (agent.words_total == agent.words_chat + agent.words_round).all()
    for col in ("active_share", "idle_share"):
        assert agent[col].between(0, 1).all()
    assert (agent.active_share + agent.idle_share).tolist() == pytest.approx([1.0, 1.0])
    assert (agent.round_frames > 0).all()
    assert set(agent.seat) == {1, 2}
    share = agent.directive_share_of_team.dropna().sum()
    assert share == pytest.approx(1.0) or share == 0


def test_active_counts_moves_and_interactions(logs):
    [s], _ = metrics.load_logs(logs)
    act = metrics.activity(s.rep)
    assert sum(a["active_s"] for a in act.values()) > 0
    assert all(a["active_s"] <= a["frames"] for a in act.values())


def test_skips_unfinished_and_handles_missing_cells(logs, tmp_path):
    (tmp_path / "half.eval").write_bytes(b"not a zip")
    for f in logs.glob("*.eval"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    seasons, skipped = metrics.load_logs(tmp_path)
    assert len(seasons) == 1 and len(skipped) == 1 and "half.eval" in skipped[0]
    # report on a one-season dataset: most cells empty, nothing raises
    out = tmp_path / "out"
    res = metrics.run(tmp_path, out)
    assert res["seasons"] == 1 and len(res["logs_skipped"]) == 1
    html = (out / "report.html").read_text()
    assert html.count("cdn.plot.ly") == 1 and "(a)" in html and "(g)" in html
    assert len(pd.read_csv(out / "agent_season.csv")) == 2
    assert len(pd.read_csv(out / "season.csv")) == 1


def test_empty_directory_is_fine(tmp_path):
    res = metrics.run(tmp_path, tmp_path / "out")
    assert res["seasons"] == 0 and (tmp_path / "out" / "report.html").exists()


def test_mean_se_and_unequal_epochs():
    m, se, n = metrics.mean_se(pd.Series([1.0, 3.0, float("nan")]))
    assert (m, n) == (2.0, 2) and se == pytest.approx(1.0)
    m, se, n = metrics.mean_se(pd.Series([5.0]))
    assert m == 5.0 and n == 1 and math.isnan(se)
    # cells with 3 vs 2 seasons (mixed pairs) aggregate independently
    df = pd.DataFrame({"team": ["A+B"] * 3 + ["A+C"] * 2, "rules": ["full"] * 5,
                       "v": [1.0, 2.0, 3.0, 4.0, 6.0]})
    agg = metrics.summarise(df, ["team", "rules"], "v").set_index("team")
    assert agg.n.to_dict() == {"A+B": 3, "A+C": 2}
