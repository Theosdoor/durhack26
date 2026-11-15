"""Export seasons from .eval logs for the static replay site in docs/ (GitHub Pages).

    uv run undercooked site <dir of .eval logs> [--out docs]

writes
- `<out>/data/index.json`: one row per season (what the season browser filters and sorts on),
  and each level's map (for the thumbnails);
- `<out>/data/seasons/<id>.json.gz`: one gzipped season each: the Replay header and records
  (minus what the site never shows) plus the sprite fragments the page composes its kitchen
  from, rendered here by `undercooked.sprites` so the site looks like the Streamlit viewer.

Season ids are ASCII letters, digits and hyphens: `<eval id prefix>-<level>-e<epoch>`, e.g.
`5Yqi6vJD-split-room-e2`, so they can sit in a URL hash unescaped. Only finished logs
(status "success") are exported. No network, no model calls.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from pathlib import Path

from undercooked.dashboard import RULES_LABEL, Season, family, models_label, season_stats
from undercooked.engine import Kitchen
from undercooked.replay import AgentState, DecisionRecord, Frame, PotState, Replay
from undercooked.replay_io import eval_seasons, eval_status
from undercooked.sprites import AGENT_COLOURS, DEFS, STRIP, T, chef, counter_item, map_tiles, pot_tile

# events the site never shows (the decision records carry the same times)
DROP_EVENTS = {"decision_start", "decision_land"}
HOLDINGS = (None, "onion", "dish", "soup")


def eval_id(path: Path) -> str:
    """The random id at the end of an Inspect log name: ..._undercooked_<id>.eval -> <id>."""
    return re.sub(r"[^A-Za-z0-9]", "", path.stem.rsplit("_", 1)[-1]) or "log"


def season_id(prefix: str, level: str, epoch: int) -> str:
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9]+', '-', level).strip('-')}-e{epoch}"


def short_ids(paths: list[Path], n: int = 8) -> dict[Path, str]:
    """The shortest prefix (at least n chars) of each log's id that is unique among `paths`."""
    ids = {p: eval_id(p) for p in paths}
    while len({i[:n] for i in ids.values()}) < len(set(ids.values())):
        n += 2
    return {p: i[:n] for p, i in ids.items()}


def slim(rec: dict) -> dict:
    """A record without empty or default fields (the page treats a missing key as empty)."""
    out = {}
    for k, v in rec.items():
        if v is None or v == [] or v == {}:
            continue
        if k == "landed":
            if v is False:
                out[k] = False
            continue
        if v is False:
            continue
        if k == "plan_sent" and v == rec.get("plan"):
            continue
        out[k] = v
    return out


def svg(parts: list[str]) -> str:
    return "".join(parts)


def sprites(rep: Replay, practice_map: list[str] | None) -> dict:
    """The kitchen as fragments: static backgrounds (tiles only) per map, and every pot, chef and
    counter item that appears in this season, each drawn at the origin of its tile."""
    h = rep.header
    frames = [r for r in rep.records if isinstance(r, Frame)]
    bg = {"level": svg(map_tiles(h.map, [], {}, h.cook_s, top=0))}
    if practice_map:
        bg["practice"] = svg(map_tiles(practice_map, [], {}, h.cook_s, top=0))
    pots: dict[str, str] = {}
    items: dict[str, str] = {}
    chefs: dict[str, str] = {}
    colour = {a.name: AGENT_COLOURS[i % len(AGENT_COLOURS)] for i, a in enumerate(h.agents)}
    letter = {a.name: a.letter for a in h.agents}
    for f in frames:
        for p in f.pots:
            key = pot_key(p.onions, p.state, p.ready_in)
            if key not in pots:
                pots[key] = pot_tile(0, 0, p, h.cook_s)
        for item in f.counters.values():
            if item not in items:
                items[item] = counter_item(item, 0, 0)
        for a in f.agents:
            key = f"{a.name}|{a.holding or ''}|{int(a.status == 'deciding')}"
            if key not in chefs:
                state = AgentState(name=a.name, pos=(0, 0), holding=a.holding, status=a.status)
                chefs[key] = chef(state, letter.get(a.name, a.name[:1]),
                                  colour.get(a.name, AGENT_COLOURS[0]), 0, 0)
    for a in h.agents:   # every agent idle and empty-handed, so a chef can always be drawn
        key = f"{a.name}||0"
        if key not in chefs:
            chefs[key] = chef(AgentState(name=a.name, pos=(0, 0)), a.letter, colour[a.name], 0, 0)
    return {"T": T, "strip": STRIP, "defs": DEFS, "colours": AGENT_COLOURS, "bg": bg,
            "pots": pots, "items": items, "chefs": chefs}


def pot_key(onions: int, state: str, ready_in: int | None) -> str:
    return f"{onions}|{state}|{'' if ready_in is None else ready_in}"


def season_payload(sid: str, file: Path, rep: Replay) -> dict:
    has_practice = any(isinstance(r, Frame) and r.phase == "practice" for r in rep.records)
    practice_map = Kitchen.load("practice").map_rows() if has_practice else None
    header = rep.header.model_dump(mode="json")
    if practice_map:
        header["practice_map"] = practice_map
    records = [slim(r.model_dump(mode="json")) for r in rep.records
               if not (r.kind == "event" and r.type in DROP_EVENTS)]
    return {"id": sid, "file": file.name, "header": header, "records": records,
            "sprites": sprites(rep, practice_map)}


def finite(x):
    """NaN and infinities (which JSON cannot hold) as None, recursively."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [finite(v) for v in x]
    return x


def index_row(sid: str, file: Path, rep: Replay, scores: dict | None) -> dict:
    h = rep.header
    st = season_stats(Season(file, h.level, h.epoch, rep, scores))
    models = [a.model for a in h.agents]
    predicted = {d.agent: d.predicted_score for d in rep.records
                 if isinstance(d, DecisionRecord) and d.phase == "reflection"}
    return finite({
        "id": sid, "file": file.name, "config": h.config_name, "level": h.level, "epoch": h.epoch,
        "seed": h.seed, "rules": h.rules, "rules_label": RULES_LABEL.get(h.rules, h.rules),
        "comms": h.comms, "debrief": h.debrief, "reasoning": h.reasoning, "scoring": h.scoring,
        "reflection": h.reflection, "n_agents": len(h.agents), "rounds_n": h.rounds,
        "round_s": h.round_s,
        "team": models_label(models), "mixed": len(set(models)) > 1,
        "agents": [{"name": a.name, "model": a.model, "family": family(a.model),
                    "predicted": predicted.get(a.name)} for a in h.agents],
        "rounds": st["rounds"], "total": st["total"], "served": st["served"],
        "missed": st["missed"],
    })


def export_site(log_dir: str | Path, out: str | Path = "docs") -> dict:
    """Write index.json and one .json.gz per season under <out>/data; returns the index."""
    log_dir, out = Path(log_dir), Path(out)
    logs = sorted(p for p in log_dir.rglob("*.eval") if eval_status(p) == "success")
    if not logs:
        raise ValueError(f"no finished .eval logs under {log_dir}")
    prefix = short_ids(logs)
    seasons_dir = out / "data" / "seasons"
    seasons_dir.mkdir(parents=True, exist_ok=True)
    for old in seasons_dir.glob("*.json.gz"):   # a re-export replaces the whole set
        old.unlink()
    rows, maps = [], {}
    for path in logs:
        for rep, scores in eval_seasons(path):
            sid = season_id(prefix[path], rep.header.level, rep.header.epoch)
            payload = json.dumps(finite(season_payload(sid, path, rep)), separators=(",", ":"))
            (seasons_dir / f"{sid}.json.gz").write_bytes(
                gzip.compress(payload.encode(), compresslevel=9, mtime=0))
            rows.append(index_row(sid, path, rep, scores))
            maps.setdefault(rep.header.level, rep.header.map)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate season ids")
    index = {"seasons": rows, "logs": len(logs), "maps": maps}
    (out / "data" / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return index
