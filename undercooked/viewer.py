"""Replay viewer for Undercooked (Streamlit).

Run:  uv run streamlit run undercooked/viewer.py -- <replay.json | log.eval>
The path may also come from the UNDERCOOKED_REPLAY env var or the sidebar text box.

Reads `Replay` objects only (see undercooked/replay.py).
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# `streamlit run undercooked/viewer.py` puts undercooked/ on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from undercooked.replay import DecisionRecord, Event, Frame, Post, Replay  # noqa: E402
from undercooked.replay_io import load_replay  # noqa: E402

# Colours are semi-transparent and text is never set explicitly, so the page's own text
# colour (light or dark theme) always has contrast against them.
TILE_FILL = {
    "X": "rgba(128,128,128,0.35)",   # counter
    " ": "rgba(128,128,128,0.06)",   # floor
    "O": "rgba(255,170,0,0.40)",     # onion crate
    "D": "rgba(0,160,255,0.35)",     # dish rack
    "P": "rgba(255,70,70,0.30)",     # pot
    "S": "rgba(0,200,100,0.40)",     # serving hatch
}
TILE_NAME = {"X": "", " ": "", "O": "ONIONS", "D": "DISHES", "P": "POT", "S": "HATCH"}
AGENT_FILLS = ["rgba(60,120,255,0.65)", "rgba(230,60,200,0.60)", "rgba(40,190,120,0.60)", "rgba(240,160,30,0.65)"]


# ---------------------------------------------------------------- data helpers

def phase_label(phase: str, rnd: int) -> str:
    """Human label for a (phase, round) pair; chat and debrief are tied to a round."""
    if phase == "round":
        return f"round {rnd}"
    if phase == "chat":
        return f"chat before round {rnd}"
    if phase == "debrief":
        return f"debrief after round {rnd}"
    return phase  # briefing / practice (round 0)


def frame_at(frames: list[Frame], t: int) -> Frame | None:
    """Latest frame at or before t (frames are one per second, but be forgiving)."""
    earlier = [f for f in frames if f.t <= t]
    return earlier[-1] if earlier else (frames[0] if frames else None)


def latest_decision(decisions: list[DecisionRecord], agent: str, t: int) -> DecisionRecord | None:
    mine = [d for d in decisions if d.agent == agent and d.t <= t]
    return mine[-1] if mine else None


# ---------------------------------------------------------------- map rendering

def tile_text(ch: str, x: int, y: int, frame: Frame) -> str:
    """The text shown inside a tile: its label plus whatever dynamic state sits on it."""
    parts = [TILE_NAME[ch]] if TILE_NAME[ch] else []
    if ch == "P":
        pot = next((p for p in frame.pots if tuple(p.pos) == (x, y)), None)
        if pot:
            if pot.state == "cooking":
                parts.append(f"cooking {pot.ready_in}s")
            elif pot.state == "ready":
                parts.append("READY")
            else:
                parts.append(f"{pot.onions}/3 onions")
    item = frame.counters.get(f"{x},{y}")
    if item:
        parts.append(f"[{item}]")
    return "<br>".join(parts)


def render_map(rep: Replay, frame: Frame) -> str:
    letters = {a.name: a.letter for a in rep.header.agents}
    order = [a.name for a in rep.header.agents]
    at = {tuple(a.pos): a for a in frame.agents}
    rows = []
    for y, line in enumerate(rep.header.map):
        cells = []
        for x, ch in enumerate(line):
            style = f"background:{TILE_FILL.get(ch, TILE_FILL[' '])};"
            inner = f"<div style='font-size:10px;opacity:0.85'>{tile_text(ch, x, y, frame)}</div>"
            agent = at.get((x, y))
            if agent:
                fill = AGENT_FILLS[order.index(agent.name) % len(AGENT_FILLS)]
                held = f"<br><span style='font-size:10px'>{html.escape(agent.holding)}</span>" if agent.holding else ""
                inner += (f"<div style='display:inline-block;border-radius:50%;padding:2px 9px;font-weight:700;"
                          f"background:{fill};border:1px solid rgba(128,128,128,0.7)'>"
                          f"{html.escape(letters[agent.name])}</div>{held}")
            cells.append(f"<td style='{style}width:76px;height:64px;text-align:center;"
                         f"border:1px solid rgba(128,128,128,0.45)'>{inner}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table style='border-collapse:collapse;margin:auto'>" + "".join(rows) + "</table>"


# ---------------------------------------------------------------- panels

def step_time(key: str, delta: int, lo: int, hi: int) -> None:
    """Button callback: move the slider (stored in session_state[key]) by delta seconds."""
    st.session_state[key] = max(lo, min(hi, st.session_state[key] + delta))


def show_status(frame: Frame, t: int) -> None:
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", frame.score)
    c2.metric("Served", frame.served)
    c3.metric("Missed", frame.missed)
    if frame.orders:
        st.caption("Open orders: " + ", ".join(
            f"#{o.id} {o.dish} (due t={o.due_t}, {o.due_t - t}s left)" for o in frame.orders))
    else:
        st.caption("No open orders.")


def show_board(posts: list[Post], t: int, agents: list[str]) -> None:
    st.markdown("**Board**")
    visible = [p for p in posts if p.t <= t and p.agent in agents]
    if not visible:
        st.caption("No posts yet.")
    for p in visible:
        now = p.t == t
        # Highlight posts made in the current second with a semi-transparent band.
        bg = "background:rgba(255,200,0,0.25);" if now else ""
        st.markdown(f"<div style='{bg}padding:2px 6px;border-radius:4px'>"
                    f"<code>t={p.t}</code> <b>{html.escape(p.agent)}</b>: {html.escape(p.text)}</div>",
                    unsafe_allow_html=True)


def decision_details(d: DecisionRecord) -> None:
    """Everything the viewer shows about one DecisionRecord."""
    flags = [name for name, on in [("read_board", d.read_board), ("CUTOFF", d.cutoff), ("ready", d.ready)] if on]
    st.caption(f"decided t={d.t}, lands t={d.lands_t} | tokens in/out/reasoning: "
               f"{d.input_tokens}/{d.output_tokens}/{d.reasoning_tokens} | served_by: {d.served_by} "
               + (f"| {', '.join(flags)}" if flags else ""))
    if d.plan:
        st.write("Plan: " + " > ".join(d.plan))
    if d.post:
        st.write(f"Post: {d.post}")
    if d.note:
        st.write(f"Note: {d.note}")
    if d.text:
        st.write(f"Text: {d.text}")
    if d.reasoning:
        with st.expander("Reasoning"):
            st.write(d.reasoning)


def show_agent_decisions(decisions: list[DecisionRecord], t: int, agents: list[str]) -> None:
    for name in agents:
        d = latest_decision(decisions, name, t)
        st.markdown(f"**{name}**" + ("" if d else " (no decision yet)"))
        if d:
            inflight = d.lands_t is not None and d.lands_t > t
            if inflight:
                st.caption("decision still in flight at this time")
            decision_details(d)


def decisions_table(decisions: list[DecisionRecord]) -> pd.DataFrame:
    return pd.DataFrame([
        {"t": d.t, "agent": d.agent, "plan": " > ".join(d.plan), "post": d.post or "",
         "tokens": (d.input_tokens or 0) + (d.output_tokens or 0)} for d in decisions])


def events_table(events: list[Event]) -> pd.DataFrame:
    return pd.DataFrame([{"t": e.t, "type": e.type, "agent": e.agent or "", "data": str(e.data)} for e in events])


def show_conversation(rep: Replay, phase: str, rnd: int, agents: list[str]) -> None:
    """Chat / debrief phases have no frames: show posts and decisions as a conversation."""
    for r in rep.select(None, phase, rnd):
        if isinstance(r, Post) and r.agent in agents:
            with st.chat_message("user" if r.agent == agents[0] else "assistant"):
                st.markdown(f"**{r.agent}** (t={r.t}): {r.text}")
        elif isinstance(r, DecisionRecord) and r.agent in agents:
            with st.expander(f"{r.agent}'s decision at t={r.t}"):
                decision_details(r)


# ---------------------------------------------------------------- app

def initial_path() -> str:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    return os.environ.get("UNDERCOOKED_REPLAY") or (args[-1] if args else "")


@st.cache_data(show_spinner=False)
def cached_load(path: str) -> list[Replay]:
    return load_replay(path)


def main() -> None:
    st.set_page_config(page_title="Undercooked replay", layout="wide")
    st.title("Undercooked replay")

    # ---- sidebar: file, season, phase, agent filter
    path = st.sidebar.text_input("Replay file (.json / .eval)", value=initial_path(), key="replay_path")
    if not path:
        st.info("Give a replay file in the sidebar, or run: streamlit run undercooked/viewer.py -- <path>")
        return
    try:
        replays = cached_load(path)
    except (OSError, ValueError, NotImplementedError) as exc:
        st.error(f"Could not load {path}: {exc}")
        return
    epoch = st.sidebar.selectbox("Season (epoch)", [r.header.epoch for r in replays])
    rep = next(r for r in replays if r.header.epoch == epoch)
    phases = rep.phases()
    label = st.sidebar.selectbox("Phase", [phase_label(*p) for p in phases])
    phase, rnd = phases[[phase_label(*p) for p in phases].index(label)]
    names = [a.name for a in rep.header.agents]
    agents = st.sidebar.multiselect("Agents", names, default=names) or names
    st.sidebar.caption(f"{rep.header.config_name} | level {rep.header.level} | seed {rep.header.seed}")

    frames = rep.select(Frame, phase, rnd)
    decisions = rep.select(DecisionRecord, phase, rnd)
    posts = rep.select(Post, phase, rnd)
    events = rep.select(Event, phase, rnd)

    tab_replay, tab_dec, tab_ev = st.tabs(["Replay", "Decisions", "Events"])

    with tab_replay:
        if not frames:
            show_conversation(rep, phase, rnd, agents)
        else:
            hi = rep.header.round_s if phase == "round" else max(f.t for f in frames)
            key = f"t_{epoch}_{phase}_{rnd}"   # per-phase key so each phase keeps its own position
            st.session_state.setdefault(key, 0)
            left, right = st.columns([3, 2])
            with left:
                t = st.slider("Time (s)", 0, hi, key=key)
                b1, b2, _ = st.columns([1, 1, 6])
                b1.button("< -1 s", on_click=step_time, args=(key, -1, 0, hi))
                b2.button("+1 s >", on_click=step_time, args=(key, +1, 0, hi))
                frame = frame_at(frames, t)
                st.markdown(render_map(rep, frame), unsafe_allow_html=True)
                show_status(frame, t)
            with right:
                show_board(posts, t, agents)
                st.divider()
                show_agent_decisions(decisions, t, agents)

    with tab_dec:
        shown = [d for d in decisions if d.agent in agents]
        st.dataframe(decisions_table(shown), use_container_width=True, hide_index=True)
    with tab_ev:
        shown = [e for e in events if e.agent is None or e.agent in agents]
        st.dataframe(events_table(shown), use_container_width=True, hide_index=True)


main()
