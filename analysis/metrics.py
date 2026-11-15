"""Quantitative analysis of finished Undercooked logs (offline: reads .eval files only).

    uv run python analysis/metrics.py <log dir> --out <dir>

Writes into --out:
  agent_season.csv   one row per agent per season
  season.csv         one row per season
  report.html        Plotly figures (plotly.js from the CDN once; light and dark readable)

Logs whose status is not "success" (still running, errored) are skipped and counted. Everything
is computed from the Replay records and the scorer values via undercooked.replay_io /
undercooked.dashboard (season_stats), so the numbers match the viewer.

DEFINITIONS
- Posts: Post records of phase "chat" (pre-round board chat) and "round" (posted during the
  180 s round). Words = len(text.split()). `share of team words` = agent words / team words
  (chat + round together). Debrief/reflection text is not counted.
- Directive message (`DIRECTIVE_RE`, a cheap bossiness proxy, not a judge): a message is
  split into clauses at . ! ? ; : newline and dashes; a clause is a directive when ANY of
    A. it starts (after an optional "Name," and optional "please") with an imperative verb from
       VERBS ("grab the dish", "Foxtrot, bring onions", "please wait");
    B. "you" (optionally + will/should/can/could/must/need to/have to/'ll) followed by a verb
       from VERBS ("you take the onions", "you should grab");
    C. "can/could/would/will you";
    D. "please", "let's" or "let us";
    E. "go to" unless the same clause is a self-commitment (matches COMMIT_RE).
  directive_rate = directive messages / all posts by the agent (NaN if no posts);
  directive_share_of_team = the agent's directive messages / the team's directive messages.
- Commitment message (`COMMIT_RE`): contains "I'll", "I will", "I'm going to", "I am going to",
  "I can take". commit_rate likewise. These are self-assignments, not orders, so they are kept
  apart; `directive_or_commit_rate` is the union ("any plan talk").
- Active vs idle (rounds only, one sample per game second = one Frame): the agent is ACTIVE in
  a second when its position changed since the previous frame of that round, or it has a
  successful interaction event (pickup, putdown, add_to_pot, plate, serve) at that second.
  Otherwise IDLE (standing still: waiting for a model decision, a finished plan, a blocked
  move, a failed action, or an explicit wait). active_share = active seconds / round frames
  over all rounds; idle_share = 1 - active_share. deciding_share = fraction of frames whose
  status is "deciding" (a model call in flight; may overlap active seconds).
- Plan following: over the agent's round decisions, plan_valid_rate = share with a non-empty
  plan in which no action was cut for being invalid (len(plan) == len(plan_sent) > 0);
  plan_truncated_rate = share where the plan was cut (len(plan) < len(plan_sent));
  no_plan_rate = share with an empty plan (rejected or no act()). forced_round / cutoffs_round
  are the scorer's per-agent counts (round decisions only), as in dashboard.season_stats.
- Discovery: first_{onion,cook,plate,serve}_{round,t}: the agent's first add_to_pot,
  cook_start, plate, serve event (round 0 = practice, t = game second), from the scorer values.
- predicted_score: the agent's PREDICTED_SCORE from the final reflection (NaN if none);
  pred_error = predicted_score - team_total (it predicted a full-instructions season).
- Error bars in the figures: standard error of the mean over seasons (NaN for n < 2).
"""

from __future__ import annotations

import argparse
import html
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from undercooked.dashboard import Season, family, season_stats
from undercooked.replay import DecisionRecord, Event, Frame, Post, Replay
from undercooked.replay_io import eval_seasons, eval_status

VERBS = ("go|grab|take|bring|put|pick|get|fetch|stay|wait|cook|serve|plate|place|drop|move|"
         "start|add|fill|hand|pass|check|use|keep|stand|come|head|deliver|collect|load|cover|"
         "handle|focus|stop|leave|swap|switch|help|clear|finish|continue|follow|watch")
_NAME = r"(?:\w+,\s*)?(?:please,?\s+)?"
_CLAUSE_SPLIT = re.compile(r"[.!?;:\n]+|\s[-–—]+\s")
DIRECTIVE_RE = re.compile(
    rf"^{_NAME}(?:{VERBS})\b"                                                           # A
    rf"|\byou(?:'ll|\s+(?:will|should|can|could|must|need to|have to|better))?\s+(?:{VERBS})\b"  # B
    r"|\b(?:can|could|would|will)\s+you\b"                                              # C
    r"|\bplease\b|\blet'?s\b|\blet us\b",                                               # D
    re.I)
GO_TO_RE = re.compile(r"\bgo to\b", re.I)                                               # E
COMMIT_RE = re.compile(r"\bI(?:'ll|\s+will|'m going to|\s+am going to|\s+can take)\b", re.I)
INTERACTIONS = {"pickup", "putdown", "add_to_pot", "plate", "serve"}
DISCOVERY_KINDS = ("onion", "cook", "plate", "serve")
RULES_ORDER = ["full", "bare", "open"]


def is_directive(text: str) -> bool:
    for clause in _CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if DIRECTIVE_RE.search(clause):
            return True
        if GO_TO_RE.search(clause) and not COMMIT_RE.search(clause):
            return True
    return False


def is_commit(text: str) -> bool:
    return bool(COMMIT_RE.search(text))


# ---------------------------------------------------------------- loading

def load_logs(directory: str | Path) -> tuple[list[Season], list[str]]:
    """Every season of every successful .eval under `directory`; skipped logs are named."""
    seasons: list[Season] = []
    skipped: list[str] = []
    for f in sorted(Path(directory).rglob("*.eval")):
        try:
            status = eval_status(f)
            if status != "success":
                skipped.append(f"{f.name} ({status})")
                continue
            seasons += [Season(f, r.header.level, r.header.epoch, r, sc) for r, sc in eval_seasons(f)]
        except Exception as exc:   # a log still being written can be half a zip file
            skipped.append(f"{f.name} ({type(exc).__name__})")
    return seasons, skipped


# ---------------------------------------------------------------- per-season metrics

def _nan(v) -> float:
    return float("nan") if v is None else float(v)


def activity(rep: Replay) -> dict[str, dict[str, float]]:
    """Per agent name: active_s, frames, deciding_s over all rounds (see module docstring)."""
    out = {a.name: {"active_s": 0, "frames": 0, "deciding_s": 0} for a in rep.header.agents}
    for r in range(1, rep.header.rounds + 1):
        frames = sorted(rep.select(Frame, "round", r), key=lambda f: f.t)
        acted: dict[str, set[int]] = {}
        for e in rep.select(Event, "round", r):
            if e.type in INTERACTIONS and e.agent:
                acted.setdefault(e.agent, set()).add(e.t)
        prev: dict[str, tuple] = {}
        for f in frames:
            for a in f.agents:
                if a.name not in out:
                    continue
                moved = a.name in prev and prev[a.name] != tuple(a.pos)
                prev[a.name] = tuple(a.pos)
                o = out[a.name]
                o["frames"] += 1
                o["active_s"] += int(moved or f.t in acted.get(a.name, ()))
                o["deciding_s"] += int(a.status == "deciding")
    return out


def season_rows(s: Season) -> tuple[list[dict], dict]:
    """(one dict per agent, one dict for the season)."""
    rep, sc = s.rep, s.scores or {}
    h = rep.header
    st = season_stats(s)
    n = len(h.agents)
    models = [a.model for a in h.agents]
    fams = sorted(family(m) for m in models)
    pair = fams[0] + "×" + str(n) if len(set(fams)) == 1 else "+".join(fams)
    season_id = f"{s.file.stem[-8:]}:{h.level}:e{h.epoch}"
    base = {
        "season_id": season_id, "file": s.file.name, "level": h.level, "rules": h.rules,
        "scoring": h.scoring, "reflection": h.reflection, "n_agents": n, "epoch": h.epoch,
        "seed": h.seed, "team": pair, "mixed": len(set(fams)) > 1,
        "team_total": st["total"], "served": st["served"], "missed": st["missed"],
    }
    for i in range(h.rounds):
        base[f"round_{i + 1}"] = _nan(st["rounds"][i]) if i < len(st["rounds"]) else float("nan")
    posts = rep.select(Post)
    act = activity(rep)
    dirs = {a.name: 0 for a in h.agents}
    words_all = {a.name: 0 for a in h.agents}
    rows = []
    for i, a in enumerate(h.agents):
        mine = [p for p in posts if p.agent == a.name and p.phase in ("chat", "round")]
        chat = [p for p in mine if p.phase == "chat"]
        rnd = [p for p in mine if p.phase == "round"]
        w = lambda ps: sum(len(p.text.split()) for p in ps)  # noqa: E731
        d = sum(is_directive(p.text) for p in mine)
        c = sum(is_commit(p.text) for p in mine)
        dc = sum(is_directive(p.text) or is_commit(p.text) for p in mine)
        dirs[a.name] = d
        words_all[a.name] = w(mine)
        dec = [x for x in rep.select(DecisionRecord, "round", agent=a.name)]
        nd = len(dec)
        valid = sum(1 for x in dec if x.plan and len(x.plan) == len(x.plan_sent))
        trunc = sum(1 for x in dec if x.plan and len(x.plan) < len(x.plan_sent))
        ac = act.get(a.name, {"active_s": 0, "frames": 0, "deciding_s": 0})
        fr = ac["frames"]
        pred = sc.get(f"predicted_score_agent{i}")
        if pred is None:
            refl = [x.predicted_score for x in rep.select(DecisionRecord, "reflection", agent=a.name)
                    if x.predicted_score is not None]
            pred = refl[-1] if refl else None
        pred = _nan(pred)
        row = {
            **base, "agent_index": i, "agent": a.name, "model": a.model, "family": family(a.model),
            "seat": a.seat,
            "posts_chat": len(chat), "words_chat": w(chat), "posts_round": len(rnd),
            "words_round": w(rnd), "posts_total": len(mine), "words_total": w(mine),
            "directive_msgs": d, "commit_msgs": c,
            "directive_rate": d / len(mine) if mine else float("nan"),
            "commit_rate": c / len(mine) if mine else float("nan"),
            "directive_or_commit_rate": dc / len(mine) if mine else float("nan"),
            "active_s": ac["active_s"], "round_frames": fr,
            "active_share": ac["active_s"] / fr if fr else float("nan"),
            "idle_share": 1 - ac["active_s"] / fr if fr else float("nan"),
            "deciding_share": ac["deciding_s"] / fr if fr else float("nan"),
            "round_decisions": nd,
            "plan_valid_rate": valid / nd if nd else float("nan"),
            "plan_truncated_rate": trunc / nd if nd else float("nan"),
            "no_plan_rate": sum(1 for x in dec if not x.plan) / nd if nd else float("nan"),
            "forced_round": st["forced"][i], "cutoffs_round": st["cutoffs"][i],
            "practice_finished": st["practice_finished"][i],
            "predicted_score": pred, "pred_error": pred - st["total"],
        }
        for kind in DISCOVERY_KINDS:
            for part in ("round", "t"):
                row[f"first_{kind}_{part}"] = _nan(sc.get(f"first_{kind}_{part}_agent{i}"))
        rows.append(row)
    team_words = sum(words_all.values())
    team_dirs = sum(dirs.values())
    for r in rows:
        r["team_words"] = team_words
        r["words_share"] = r["words_total"] / team_words if team_words else float("nan")
        r["directive_share_of_team"] = r["directive_msgs"] / team_dirs if team_dirs else float("nan")
    ad = pd.DataFrame(rows)
    season = {
        **base, "agents": " + ".join(f"{r['family']}@{r['seat']}" for r in rows),
        "posts_total": int(ad.posts_total.sum()), "words_total": int(ad.words_total.sum()),
        "words_chat": int(ad.words_chat.sum()), "words_round": int(ad.words_round.sum()),
        "directive_rate": float(ad.directive_msgs.sum() / ad.posts_total.sum()) if ad.posts_total.sum() else float("nan"),
        "max_words_share": float(ad.words_share.max()) if team_words else float("nan"),
        "active_share": float(ad.active_share.mean()), "idle_share": float(ad.idle_share.mean()),
        "forced_round": int(ad.forced_round.sum()), "cutoffs_round": int(ad.cutoffs_round.sum()),
        "predicted_score_mean": float(ad.predicted_score.mean()),
    }
    return rows, season


def build_tables(seasons: list[Season]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not seasons:   # keep the columns the figures read
        return (pd.DataFrame(columns=["family", "seat", "rules", "level", "team_total", "agent",
                                      "predicted_score", "words_total", "directive_rate"]),
                pd.DataFrame(columns=["team", "rules", "level", "n_agents", "team_total",
                                      "active_share", "round_1"]))
    agent_rows, season_dicts = [], []
    for s in seasons:
        a, se = season_rows(s)
        agent_rows += a
        season_dicts.append(se)
    return pd.DataFrame(agent_rows), pd.DataFrame(season_dicts)


# ---------------------------------------------------------------- aggregation

def mean_se(values: pd.Series) -> tuple[float, float, int]:
    v = pd.to_numeric(values, errors="coerce").dropna()
    n = len(v)
    if n == 0:
        return float("nan"), float("nan"), 0
    return float(v.mean()), (float(v.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")), n


def summarise(df: pd.DataFrame, by: list[str], value: str) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(by, sort=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        m, se, n = mean_se(g[value])
        rows.append({**dict(zip(by, key)), "mean": m, "se": se, "n": n})
    return pd.DataFrame(rows, columns=[*by, "mean", "se", "n"])


# ---------------------------------------------------------------- figures

COLORS = ["#4c9be8", "#e69f00", "#2ca58d", "#d6604d", "#9a7bd1", "#c9a227", "#7f7f7f"]
RULES_COLOR = {"full": "#4c9be8", "bare": "#e69f00", "open": "#2ca58d"}
GRID = "rgba(128,128,128,0.28)"


def _go():
    import plotly.graph_objects as go
    return go


def _base_layout(fig, title: str, height: int = 430) -> None:
    fig.update_layout(
        title=dict(text=title, x=0.01, xanchor="left", font=dict(size=14)),
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=60, r=20, t=90, b=60), legend=dict(orientation="h", y=-0.18),
        font=dict(family="system-ui, sans-serif", size=12),   # colour is set by the page (CSS)
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID)


def _empty(title: str, why: str = "no data yet"):
    go = _go()
    fig = go.Figure()
    fig.add_annotation(text=why, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
    _base_layout(fig, title, 240)
    return fig


def _order(vals, preferred=()):
    vals = list(dict.fromkeys(vals))
    return [v for v in preferred if v in vals] + sorted(v for v in vals if v not in preferred)


def grouped_bars(df: pd.DataFrame, value: str, x: str, color: str, facet: str | None,
                 title: str, ytitle: str, color_map: dict | None = None,
                 x_order=(), color_order=()):
    """Mean of `value` over seasons per x and color (one panel per `facet`), SE error bars, and
    n = seasons in each bar printed above it."""
    from plotly.subplots import make_subplots
    go = _go()
    if df.empty:
        return _empty(title)
    facets = _order(df[facet], ()) if facet else [None]
    xs = _order(df[x], x_order)
    cols = _order(df[color], color_order)
    cmap = color_map or {c: COLORS[i % len(COLORS)] for i, c in enumerate(cols)}
    fig = make_subplots(rows=1, cols=len(facets), shared_yaxes=True, subplot_titles=[str(f) for f in facets]
                        if facet else None, horizontal_spacing=0.03)
    for ci, f in enumerate(facets, start=1):
        sub = df if f is None else df[df[facet] == f]
        agg = summarise(sub, [x, color], value)
        for c in cols:
            a = agg[agg[color] == c].set_index(x).reindex(xs)
            if a["n"].fillna(0).sum() == 0:
                continue
            fig.add_trace(go.Bar(
                x=xs, y=a["mean"], name=str(c), legendgroup=str(c), showlegend=(ci == 1),
                marker_color=cmap.get(c, "#7f7f7f"),
                error_y=dict(type="data", array=a["se"].fillna(0), color="rgba(128,128,128,0.9)"),
                text=[f"n={int(v)}" if v == v and v > 0 else "" for v in a["n"]],
                textposition="outside", cliponaxis=False, textfont=dict(size=10),
                hovertemplate=f"{c}<br>%{{x}}<br>mean %{{y:.2f}} (n=%{{text}})<extra></extra>"), 1, ci)
    _base_layout(fig, title)
    fig.update_yaxes(title_text=ytitle, col=1)
    fig.update_xaxes(type="category")
    fig.update_layout(barmode="group", uniformtext=dict(mode=False))
    return fig


def fig_a(season: pd.DataFrame):
    d = season[(season.n_agents == 2)]
    return grouped_bars(
        d, "team_total", "rules", "team", "level",
        "(a) Team score by model pair and rules, 2 agents, per map "
        "(mean over seasons; bar label = n seasons; error bars = SE)", "team total score",
        x_order=RULES_ORDER)


def fig_b(season: pd.DataFrame):
    d = season[season.team.str.startswith("DeepSeek×")].copy()
    d["n_agents"] = d.n_agents.astype(str) + " agents"
    return grouped_bars(
        d, "team_total", "n_agents", "rules", "level",
        "(b) DeepSeek self-play: team score by number of agents and rules, per map "
        "(mean over seasons; bar label = n seasons; error bars = SE)", "team total score",
        color_map=RULES_COLOR, color_order=RULES_ORDER)


def fig_c(season: pd.DataFrame):
    go = _go()
    from plotly.subplots import make_subplots
    title = ("(c) Learning curve: team score per round by rules, per number of agents "
             "(mean over seasons; hover = n; error bars = SE)")
    rc = [c for c in season.columns if re.fullmatch(r"round_\d+", c)]
    if season.empty or not rc:
        return _empty(title)
    ns = sorted(season.n_agents.unique())
    fig = make_subplots(rows=1, cols=len(ns), shared_yaxes=True,
                        subplot_titles=[f"{n} agents" for n in ns], horizontal_spacing=0.04)
    for ci, n in enumerate(ns, start=1):
        for rules in _order(season.rules, RULES_ORDER):
            g = season[(season.n_agents == n) & (season.rules == rules)]
            stats = [mean_se(g[c]) for c in rc]
            fig.add_trace(go.Scatter(
                x=list(range(1, len(rc) + 1)), y=[s[0] for s in stats], mode="lines+markers",
                name=rules, legendgroup=rules, showlegend=(ci == 1),
                line=dict(color=RULES_COLOR.get(rules, "#7f7f7f")),
                error_y=dict(type="data", array=[0 if math.isnan(s[1]) else s[1] for s in stats],
                             color="rgba(128,128,128,0.7)"),
                customdata=[s[2] for s in stats],
                hovertemplate=f"{rules} ({n} agents)<br>round %{{x}}: %{{y:.2f}} (n=%{{customdata}})<extra></extra>"),
                1, ci)
    _base_layout(fig, title)
    fig.update_xaxes(title_text="round", dtick=1)
    fig.update_yaxes(title_text="team score in round", col=1)
    return fig


def fig_d(agent: pd.DataFrame):
    go = _go()
    from plotly.subplots import make_subplots
    title = ("(d) Talkativeness and directive rate per agent by seat and model "
             "(mean over agent-seasons; label = n; error bars = SE)")
    if agent.empty:
        return _empty(title)
    fams = _order(agent.family)
    seats = sorted(agent.seat.unique())
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.1, subplot_titles=[
        "words posted per season (chat + round)", "directive message rate (share of posts)"])
    for ci, value in enumerate(("words_total", "directive_rate"), start=1):
        agg = summarise(agent, ["family", "seat"], value)
        for fi, fam in enumerate(fams):
            a = agg[agg.family == fam].set_index("seat").reindex(seats)
            fig.add_trace(go.Bar(
                x=[f"seat {s}" for s in seats], y=a["mean"], name=fam, legendgroup=fam,
                showlegend=(ci == 1), marker_color=COLORS[fi % len(COLORS)],
                error_y=dict(type="data", array=a["se"].fillna(0), color="rgba(128,128,128,0.9)"),
                text=[f"n={int(v)}" if v == v and v > 0 else "" for v in a["n"]],
                textposition="outside", cliponaxis=False), 1, ci)
    _base_layout(fig, title)
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="words", col=1)
    fig.update_yaxes(title_text="directive rate", range=[0, 1.1], col=2)
    return fig


def fig_e(seasons: list[Season], max_examples: int = 4):
    go = _go()
    from plotly.subplots import make_subplots
    title = ("(e) Message timeline: in-round posts over game time, example seasons "
             "(marker size = words; filled = directive, hollow = other; dashed = round boundary)")
    chosen: list[Season] = []
    seen: set = set()
    for s in sorted(seasons, key=lambda s: (-len(s.rep.select(Post, "round")), str(s.file))):
        key = (s.rep.header.rules, len(s.rep.header.agents))
        if key not in seen and s.rep.select(Post, "round"):
            seen.add(key)
            chosen.append(s)
        if len(chosen) == max_examples:
            break
    if not chosen:
        return _empty(title, "no in-round posts yet")
    fig = make_subplots(rows=len(chosen), cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=[
                            f"{s.level}, rules={s.rep.header.rules}, {len(s.rep.header.agents)} agents, "
                            f"epoch {s.epoch} ({s.file.stem[-6:]})" for s in chosen])
    for ri, s in enumerate(chosen, start=1):
        h = s.rep.header
        names = [a.name for a in h.agents]
        for ai, name in enumerate(names):
            ps = [p for p in s.rep.select(Post, "round") if p.agent == name]
            if not ps:
                continue
            dr = [is_directive(p.text) for p in ps]
            fig.add_trace(go.Scatter(
                x=[(p.round - 1) * h.round_s + p.t for p in ps], y=[name] * len(ps), mode="markers",
                name=name, legendgroup=f"{ri}{name}", showlegend=False,
                marker=dict(size=[6 + 1.2 * min(len(p.text.split()), 25) for p in ps],
                            color=COLORS[ai % len(COLORS)],
                            opacity=0.85, line=dict(width=1.5, color=COLORS[ai % len(COLORS)]),
                            symbol=["circle" if d else "circle-open" for d in dr]),
                text=[html.escape(p.text[:160]) for p in ps],
                hovertemplate=f"{name} r%{{customdata}} t=%{{x}}<br>%{{text}}<extra></extra>",
                customdata=[p.round for p in ps]), ri, 1)
        for r in range(1, h.rounds):
            fig.add_vline(x=r * h.round_s, line_dash="dash", line_color=GRID, row=ri, col=1)
    _base_layout(fig, title, 230 * len(chosen) + 100)
    fig.update_xaxes(title_text="game time over the rounds (s)", row=len(chosen), col=1)
    return fig


def fig_f(agent: pd.DataFrame):
    go = _go()
    title = ("(f) Predicted vs actual team score (final reflection; one point per agent; "
             "dashed = perfect prediction)")
    d = agent.dropna(subset=["predicted_score"])
    if d.empty:
        return _empty(title, "no predicted scores yet")
    fig = go.Figure()
    hi = float(max(d.predicted_score.max(), d.team_total.max())) * 1.05 + 1
    fig.add_trace(go.Scatter(x=[0, hi], y=[0, hi], mode="lines", showlegend=False,
                             line=dict(dash="dash", color="rgba(128,128,128,0.8)")))
    for rules in _order(d.rules, RULES_ORDER):
        g = d[d.rules == rules]
        fig.add_trace(go.Scatter(
            x=g.team_total, y=g.predicted_score, mode="markers", name=f"{rules} (n={len(g)} agents)",
            marker=dict(color=RULES_COLOR.get(rules, "#7f7f7f"), size=8, opacity=0.75),
            text=[f"{a} {f} {lv}" for a, f, lv in zip(g.agent, g.family, g.level)],
            hovertemplate="%{text}<br>actual %{x}, predicted %{y}<extra></extra>"))
    _base_layout(fig, title)
    fig.update_xaxes(title_text="actual team total")
    fig.update_yaxes(title_text="PREDICTED_SCORE")
    return fig


def fig_g(season: pd.DataFrame):
    d = season.copy()
    d["n_agents"] = d.n_agents.astype(str) + " agents"
    d["active_pct"] = d.active_share * 100
    return grouped_bars(
        d, "active_pct", "n_agents", "rules", None,
        "(g) Share of round seconds an agent is active (moving or interacting) by number of agents "
        "(season mean over agents; bar label = n seasons; error bars = SE)", "active % of round seconds",
        color_map=RULES_COLOR, color_order=RULES_ORDER)


def headline_tables(season: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    two = season[season.n_agents == 2]
    t1 = summarise(two, ["team", "rules"], "team_total") if len(two) else pd.DataFrame()
    ds = season[season.team.str.startswith("DeepSeek×")]
    t2 = summarise(ds, ["n_agents", "rules"], "team_total") if len(ds) else pd.DataFrame()
    return t1, t2


CSS = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --rule:#d0d7de; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --rule:#30363d; } }
body { background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; margin:0 auto; max-width:1200px; padding:1rem 1.5rem; }
h1 { font-size:1.4rem } h2 { font-size:1rem; margin-top:2rem } p.note { color:var(--muted); font-size:.85rem }
.fig { border-top:1px solid var(--rule); padding-top:.5rem }
table { border-collapse:collapse; font-size:.85rem } td,th { border:1px solid var(--rule); padding:.2rem .6rem; text-align:right }
"""
JS = """
function paint(){ var c=getComputedStyle(document.body).color;
  document.querySelectorAll('.js-plotly-plot').forEach(function(g){
    Plotly.relayout(g,{'font.color':c,'title.font.color':c,'legend.font.color':c}); }); }
window.addEventListener('load', function(){ setTimeout(paint,50); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paint);
"""


def _table_html(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p class='note'>no data yet</p>"
    d = df.copy()
    for c in ("mean", "se"):
        d[c] = d[c].map(lambda v: "-" if v != v else f"{v:.1f}")
    return d.to_html(index=False, border=0)


def build_report(seasons: list[Season], agent: pd.DataFrame, season: pd.DataFrame,
                 skipped: list[str]) -> str:
    import plotly
    figs = [fig_a(season), fig_b(season), fig_c(season), fig_d(agent), fig_e(seasons),
            fig_f(agent), fig_g(season)]
    version = plotly.offline.get_plotlyjs_version()
    t1, t2 = headline_tables(season)
    parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Undercooked analysis</title>"
             f"<meta name='color-scheme' content='light dark'><style>{CSS}</style>"
             f"<script src='https://cdn.plot.ly/plotly-{version}.min.js' charset='utf-8'></script></head><body>",
             "<h1>Undercooked: overnight experiments</h1>",
             f"<p class='note'>{len(season)} seasons, {len(agent)} agent-seasons read; "
             f"{len(skipped)} logs skipped (not finished): {html.escape(', '.join(skipped) or 'none')}. "
             "Error bars are standard errors over seasons; cells with one season show no bar. "
             "The directive rate is a lexical proxy (see analysis/metrics.py).</p>",
             "<h2>Mean team total, 2 agents, by team and rules</h2>", _table_html(t1),
             "<h2>Mean team total, DeepSeek self-play, by number of agents and rules</h2>", _table_html(t2)]
    for i, fig in enumerate(figs):
        parts.append("<div class='fig'>" + fig.to_html(full_html=False, include_plotlyjs=False,
                                                      div_id=f"fig{i}", config={"displaylogo": False}) + "</div>")
    parts.append(f"<script>{JS}</script></body></html>")
    return "\n".join(parts)


def run(directory: str | Path, out: str | Path) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    seasons, skipped = load_logs(directory)
    agent, season = build_tables(seasons)
    agent.to_csv(out / "agent_season.csv", index=False)
    season.to_csv(out / "season.csv", index=False)
    (out / "report.html").write_text(build_report(seasons, agent, season, skipped))
    n_logs = len(set(s.file for s in seasons))
    return {"logs_read": n_logs, "logs_skipped": skipped, "seasons": len(seasons),
            "agent_seasons": len(agent), "out": str(out.resolve())}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("logs", help="directory searched recursively for .eval logs")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args(argv)
    res = run(args.logs, args.out)
    print(f"read {res['logs_read']} logs ({res['seasons']} seasons, {res['agent_seasons']} agent-seasons); "
          f"skipped {len(res['logs_skipped'])}: {', '.join(res['logs_skipped']) or 'none'}")
    print(f"wrote {res['out']}/agent_season.csv, season.csv, report.html")


if __name__ == "__main__":
    main()
