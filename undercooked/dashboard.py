"""Many-runs data for the viewer: seasons from a directory of logs, grouped into conditions, with
the summary tables. Pure functions over `Replay` (no Streamlit), so tests can call them directly.

A condition is the group of seasons sharing rules + comms + debrief + reasoning + agent models.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from undercooked.replay import DecisionRecord, Event, Frame, Replay

OVERVIEW = "All conditions"

RULES_LABEL = {"full": "full rules", "recipe": "recipe rules", "minimal": "goal-only",
               "minimal_hint": "goal + hint"}
COMMS_LABEL = {"full": "comms", "planning_only": "planning-only comms", "none": "no comms"}
FAMILY_NAME = {"deepseek": "DeepSeek", "gemma": "Gemma", "gemini": "Gemini", "qwen": "Qwen",
               "llama": "Llama", "gpt": "GPT", "claude": "Claude", "glm": "GLM", "kimi": "Kimi",
               "mistral": "Mistral", "grok": "Grok"}


class Season(NamedTuple):
    file: Path
    level: str
    epoch: int
    rep: Replay
    scores: dict[str, float] | None = None


def family(model: str) -> str:
    """Model id to family name: "openrouter/deepseek/deepseek-v4.1-flash" -> "DeepSeek"."""
    token = re.split(r"[-_+:. ]", model.rsplit("/", 1)[-1])[0] or model
    return FAMILY_NAME.get(token.lower(), token[:1].upper() + token[1:])


def models_label(models: Sequence[str]) -> str:
    fams = [family(m) for m in models]
    if len(set(fams)) == 1:
        return f"{fams[0]}×{len(fams)}" if len(fams) > 1 else fams[0]
    return "+".join(fams)


def condition_key(rep: Replay) -> tuple:
    h = rep.header
    debrief = "score_only" if h.debrief == "score_only" else "private"   # "full" = "private"
    return (h.rules, h.comms, debrief, h.reasoning, tuple(a.model for a in h.agents))


def condition_label(key: tuple) -> str:
    rules, comms, debrief, reasoning, models = key
    parts = [RULES_LABEL.get(rules, rules), COMMS_LABEL.get(comms, comms)]
    if debrief == "score_only":
        parts.append("score-only debrief")
    if reasoning != "on":
        parts.append("no reasoning")
    parts.append(models_label(models))
    return " · ".join(parts)


def group_conditions(seasons: Sequence[Season]) -> dict[str, list[Season]]:
    """Seasons by condition label, conditions ordered by rules, comms and label."""
    groups: dict[tuple, list[Season]] = {}
    for s in seasons:
        groups.setdefault(condition_key(s.rep), []).append(s)
    rules = list(RULES_LABEL)
    comms = list(COMMS_LABEL)

    def order(k: tuple) -> tuple:
        return (rules.index(k[0]) if k[0] in rules else len(rules),
                comms.index(k[1]) if k[1] in comms else len(comms), condition_label(k))

    out: dict[str, list[Season]] = {}
    for key in sorted(groups, key=order):
        label = condition_label(key)
        while label in out:   # distinct conditions never share a label, but never drop one
            label += "'"
        out[label] = sorted(groups[key], key=lambda s: (s.level, str(s.file), s.epoch))
    return out


# ---------------------------------------------------------------- per-season numbers

def _num(v: float | None) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def season_stats(s: Season) -> dict:
    """total, per-round scores, served, missed, each agent's first serve ((round, t) or None),
    practice_finished, forced and cutoffs (round decisions) per agent. The scorer values when the
    log has them, else computed from the Replay."""
    rep, sc = s.rep, s.scores
    names = [a.name for a in rep.header.agents]
    n = len(names)
    rounds = []
    for r in range(1, rep.header.rounds + 1):
        frames = rep.select(Frame, "round", r)
        rounds.append(frames[-1].score if frames else None)
    last = [rep.select(Frame, "round", r)[-1] for r in range(1, rep.header.rounds + 1)
            if rep.select(Frame, "round", r)]
    first: list[tuple[int, int] | None] = []
    for name in names:
        serves = [e for e in rep.select(Event, agent=name) if e.type == "serve"]
        first.append((serves[0].round, serves[0].t) if serves else None)
    dec = [d for d in rep.select(DecisionRecord, "round")]
    out = {
        "total": sum(x for x in rounds if x is not None),
        "rounds": rounds,
        "served": sum(f.served for f in last),
        "missed": sum(f.missed for f in last),
        "first_serve": first,
        "practice_finished": [sum(1 for e in rep.select(Event, agent=nm) if e.type == "practice_finished")
                              for nm in names],
        "forced": [sum(d.forced for d in dec if d.agent == nm) for nm in names],
        "cutoffs": [sum(d.cutoff for d in dec if d.agent == nm) for nm in names],
    }
    if not sc:
        return out
    out["total"] = sc.get("total", out["total"])
    out["rounds"] = [sc.get(f"round_{i + 1}", v) for i, v in enumerate(out["rounds"])]
    out["served"] = sc.get("served", out["served"])
    out["missed"] = sc.get("missed", out["missed"])
    for key, scored in (("practice_finished", "practice_finished"), ("forced", "forced"),
                        ("cutoffs", "cutoffs")):
        out[key] = [sc.get(f"{scored}_agent{i}", out[key][i]) for i in range(n)]
    out["first_serve"] = [
        (int(sc[f"first_serve_round_agent{i}"]), int(sc[f"first_serve_t_agent{i}"]))
        if f"first_serve_round_agent{i}" in sc and _num(sc[f"first_serve_round_agent{i}"]) is not None
        else (None if f"first_serve_round_agent{i}" in sc else first[i])
        for i in range(n)]
    return out


def fmt_num(v: float | None) -> str:
    v = _num(v)
    return "-" if v is None else f"{v:g}"


def fmt_first(v: tuple[int, int] | None) -> str:
    return "never" if v is None else f"r{v[0]} t{v[1]}"


def season_labels(seasons: Sequence[Season]) -> list:
    """Labels for the Season selector: the epoch, or "epoch · file tag" when epochs repeat."""
    epochs = [s.epoch for s in seasons]
    if len(set(epochs)) == len(epochs):
        return epochs
    return [f"{s.epoch} · {s.file.stem[-6:]}" for s in seasons]


def summary_table(seasons: Sequence[Season]) -> pd.DataFrame:
    """One row per level x season. Agent columns are by index (names are reshuffled per season)."""
    models = [a.model for a in seasons[0].rep.header.agents] if seasons else []
    labels = {id(s): lab for s, lab in zip(seasons, season_labels(seasons))}
    rows = []
    for s in seasons:
        st = season_stats(s)
        row = {"level": s.level, "season": str(labels[id(s)]), "total": st["total"],
               "rounds": " / ".join(fmt_num(x) for x in st["rounds"]),
               "served": st["served"], "missed": st["missed"]}
        for i, m in enumerate(models):
            row[f"1st serve A{i} ({family(m)})"] = fmt_first(st["first_serve"][i])
        row["practice done"] = " / ".join(fmt_num(x) for x in st["practice_finished"])
        row["forced"] = " / ".join(fmt_num(x) for x in st["forced"])
        row["cutoffs"] = " / ".join(fmt_num(x) for x in st["cutoffs"])
        row["file"] = s.file.name
        rows.append(row)
    return pd.DataFrame(rows)


def overview_table(groups: dict[str, list[Season]]) -> pd.DataFrame:
    """Mean total per condition x level as "mean (n)", plus the number of seasons per condition."""
    levels = list(dict.fromkeys(s.level for ss in groups.values() for s in ss))
    rows = []
    for label, ss in groups.items():
        row: dict = {"condition": label, "seasons": len(ss)}
        for lv in levels:
            totals = [season_stats(s)["total"] for s in ss if s.level == lv]
            row[lv] = f"{sum(totals) / len(totals):.1f} ({len(totals)})" if totals else "-"
        rows.append(row)
    return pd.DataFrame(rows)
