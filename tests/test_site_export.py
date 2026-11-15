"""The static site exporter (undercooked/site_export.py) on a scripted-bot season (no network)."""

from __future__ import annotations

import gzip
import json
import re

import pytest
from click.testing import CliRunner

from tests import kitchen_fake as fake
from tests.test_pipeline import config, run
from undercooked.cli import main as cli
from undercooked.site_export import pot_key, season_id, short_ids, slim


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(fake, "DELAY", (2, 0.0, 0.01))
    try:
        logs = tmp_path_factory.mktemp("logs")
        run(config(epochs=2, rounds=1, round_s=12, practice_s=8), logs)
    finally:
        mp.undo()
    out = tmp_path_factory.mktemp("docs")
    res = CliRunner().invoke(cli, ["site", str(logs), "--out", str(out)])
    assert res.exit_code == 0, res.output
    return out


def load(site, sid):
    return json.loads(gzip.decompress((site / "data" / "seasons" / f"{sid}.json.gz").read_bytes()))


def test_index_has_one_row_per_season(site):
    index = json.loads((site / "data" / "index.json").read_text())
    rows = index["seasons"]
    assert [r["epoch"] for r in rows] == [1, 2]
    assert index["logs"] == 1 and "forced_coordination" in index["maps"]
    for r in rows:
        assert re.fullmatch(r"[A-Za-z0-9-]+", r["id"])
        assert r["level"] == "forced_coordination" and r["n_agents"] == 2
        assert len(r["rounds"]) == 1 and r["total"] == sum(r["rounds"])
        assert [a["family"] for a in r["agents"]] and r["team"]
    files = sorted(p.name for p in (site / "data" / "seasons").iterdir())
    assert files == sorted(f"{r['id']}.json.gz" for r in rows)


def test_season_file_has_records_and_every_sprite_it_needs(site):
    index = json.loads((site / "data" / "index.json").read_text())
    for row in index["seasons"]:
        s = load(site, row["id"])
        assert s["id"] == row["id"] and s["header"]["level"] == row["level"]
        recs = s["records"]
        assert not any(r.get("type") in ("decision_start", "decision_land") for r in recs)
        frames = [r for r in recs if r["kind"] == "frame"]
        assert {r["phase"] for r in frames} == {"practice", "round"}
        assert s["header"]["practice_map"] and set(s["sprites"]["bg"]) == {"level", "practice"}
        sp = s["sprites"]
        for f in frames:
            for p in f.get("pots", []):
                assert pot_key(p["onions"], p["state"], p.get("ready_in")) in sp["pots"]
            for a in f["agents"]:
                key = f"{a['name']}|{a.get('holding') or ''}|{int(a.get('status') == 'deciding')}"
                assert key in sp["chefs"]
            for item in f.get("counters", {}).values():
                assert item in sp["items"]
        assert "<svg" not in sp["bg"]["level"] and "uc-onion" in sp["defs"]


def test_ids_and_slim():
    assert season_id("Ab12", "split_room", 3) == "Ab12-split-room-e3"
    from pathlib import Path
    a, b = Path("x_undercooked_ABCDEFGH1.eval"), Path("y_undercooked_ABCDEFGH2.eval")
    ids = short_ids([a, b])
    assert ids[a] != ids[b] and len(ids[a]) >= 8
    assert slim({"landed": True, "cutoff": False, "plan": ["a"], "plan_sent": ["a"], "note": None,
                 "t": 0}) == {"plan": ["a"], "t": 0}
    assert slim({"landed": False})["landed"] is False
