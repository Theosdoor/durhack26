"""Replay viewer for Undercooked (Streamlit).

Run:  uv run streamlit run undercooked/viewer.py -- <replay.json | log.eval | directory of .eval logs>
A directory (searched recursively) opens the many-runs mode: finished logs only, one tab per
condition (rules + comms + agent models) after an "All conditions" overview.
The path may also come from the UNDERCOOKED_REPLAY env var or the sidebar text box.

Reads `Replay` objects only (see undercooked/replay.py).
"""

from __future__ import annotations

import html
import os
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# `streamlit run undercooked/viewer.py` puts undercooked/ on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from undercooked.dashboard import (  # noqa: E402
    OVERVIEW, Season, group_conditions, overview_table, season_labels, summary_table)
from undercooked.engine import Kitchen  # noqa: E402
from undercooked.replay import DecisionRecord, Event, Frame, Post, Replay  # noqa: E402
from undercooked.replay_io import eval_seasons, eval_status, load_replay  # noqa: E402
from undercooked.sprites import render_svg  # noqa: E402

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


def show_scores(frame: Frame) -> None:
    """Compact Score / Served / Missed row, drawn above the map so it is visible without scrolling."""
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", frame.score)
    c2.metric("Served", frame.served)
    c3.metric("Missed", frame.missed)


def show_status(frame: Frame, t: int) -> None:
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
    flags = [name for name, on in [("read_board", d.read_board), ("CUTOFF", d.cutoff), ("ready", d.ready),
                                   ("NOT LANDED (phase ended first; no effect)", not d.landed)] if on]
    st.caption(f"decided t={d.t}, lands t={d.lands_t} | tokens in/out/reasoning: "
               f"{d.input_tokens}/{d.output_tokens}/{d.reasoning_tokens} | served_by: {d.served_by} "
               + (f"| {', '.join(flags)}" if flags else ""))
    if d.plan:
        st.write("Plan: " + " > ".join(d.plan))
    if d.plan_sent and d.plan_sent != d.plan:
        st.write("Sent: " + " | ".join(d.plan_sent))
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
                st.markdown(f"**{r.agent}** (turn {r.t}): {r.text}")
        elif isinstance(r, DecisionRecord) and r.agent in agents:
            with st.expander(f"{r.agent}'s decision at turn {r.t}"):
                decision_details(r)


# ---------------------------------------------------------------- play / full screen

SPEEDS = ["0.5x", "1x", "2x", "4x", "8x"]

# Collapse the sidebar, hide the title and widen the main block; the map scales to the window
# width (its aspect ratio is kept by the viewBox) but never taller than the viewport.
FULLSCREEN_CSS = """<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapseButton"],
[data-testid="stHeader"], h1 {display:none !important}
[data-testid="stMainBlockContainer"], .block-container {max-width:100% !important; padding:0.6rem 1.5rem 0.5rem !important}
[data-testid="stMetricValue"] {font-size:1.5rem !important} [data-testid="stMetric"] {gap:0 !important}
[data-testid="stVerticalBlock"] {gap:0.35rem !important}
[data-testid="stSlider"] label {display:none !important}
.st-key-cond, .st-key-summary {display:none !important}
svg[aria-label="Kitchen map"] {max-width:none !important; width:100% !important; max-height:calc(100vh - 290px)}
</style>"""

# Browser-native full screen of the map. Runs in the components iframe (same-origin srcdoc), so it
# reaches the parent document to fullscreen the markdown container that holds the map's SVG.
NATIVE_FS_HTML = """<button id=b style="font:14px sans-serif;padding:4px 10px;cursor:pointer">Browser full screen (map)</button>
<span id=m style="font:12px sans-serif;margin-left:8px;color:gray"></span>
<script>
document.getElementById('b').onclick = () => {
  try {
    const svg = window.parent.document.querySelector('svg[aria-label="Kitchen map"]');
    const box = svg.closest('[data-testid="stMarkdownContainer"]') || svg.parentElement;
    box.style.background = getComputedStyle(window.parent.document.body).backgroundColor;
    box.style.padding = '1rem';
    box.requestFullscreen().catch(e => document.getElementById('m').textContent = 'blocked: ' + e);
  } catch (e) { document.getElementById('m').textContent = 'unavailable: ' + e; }
};
</script>"""


def current_speed() -> float:
    return float(str(st.session_state.get("speed", "1x")).rstrip("x"))


def native_fullscreen_button() -> None:
    import streamlit.components.v1 as components
    components.html(NATIVE_FS_HTML, height=40)


def toggle_play(key: str, hi: int) -> None:
    """Button callback: flip play/pause; pressing Play at the end restarts from 0."""
    playing = not st.session_state.get("playing", False)
    if playing and st.session_state[key] >= hi:
        st.session_state[key] = 0
    st.session_state["playing"] = playing
    st.session_state["last_tick"] = 0.0


def advance(key: str, hi: int, speed: float) -> None:
    """One play tick: +1 game second, stopping at the phase end."""
    if not st.session_state.get("playing"):
        return
    now = time.monotonic()
    # Ignore reruns caused by the user touching a widget between timer ticks.
    if now - st.session_state.get("last_tick", 0.0) >= 0.8 / speed:
        st.session_state["last_tick"] = now
        st.session_state[key] = min(hi, st.session_state[key] + 1)
    if st.session_state[key] >= hi:
        st.session_state["playing"] = False


def show_ticker(posts: list[Post], decisions: list[DecisionRecord], agents: list[str], t: int) -> None:
    """One compact line under the map: the latest post and each agent's current plan."""
    visible = [p for p in posts if p.t <= t and p.agent in agents]
    bits = [f"<b>Latest post</b> t={p.t} {html.escape(p.agent)}: {html.escape(p.text)}" for p in visible[-1:]]
    bits = bits or ["<b>Latest post</b> none yet"]
    for name in agents:
        d = latest_decision(decisions, name, t)
        plan = " > ".join(d.plan) if d and d.plan else "no plan yet"
        bits.append(f"<b>{html.escape(name)}</b>: {html.escape(plan)}")
    st.markdown("<div style='font-size:0.9rem;padding:4px 8px;border-radius:6px;background:rgba(128,128,128,0.12);"
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>" + " &nbsp;|&nbsp; ".join(bits) + "</div>",
                unsafe_allow_html=True)


def run_replay(rep: Replay, frames: list[Frame], decisions: list[DecisionRecord], posts: list[Post],
               agents: list[str], graphics: bool, hi: int, key: str, fs: bool, speed: float) -> None:
    """The replay body: controls, map and (normal layout) side panels. Runs as a fragment, so the
    play timer re-runs only this, not the whole page. `speed` is what the timer was armed with."""
    st.session_state.setdefault(key, 0)
    advance(key, hi, speed)
    scores_slot = st.container()   # filled once the frame is known, shown above the slider
    playing = bool(st.session_state.get("playing"))
    c_play, c_speed, c_prev, c_next, _ = st.columns([1.4, 1, 1, 1, 5])
    c_play.button("⏸ Pause" if playing else "▶ Play", key="play_btn", on_click=toggle_play, args=(key, hi))
    c_speed.selectbox("Speed", SPEEDS, index=1, key="speed", label_visibility="collapsed")
    c_prev.button("< -1 s", on_click=step_time, args=(key, -1, 0, hi))
    c_next.button("+1 s >", on_click=step_time, args=(key, +1, 0, hi))
    t = st.slider("Time (s)", 0, hi, key=key)
    frame = frame_at(frames, t)
    with scores_slot:
        show_scores(frame)
    # Play state or speed changed inside the fragment (or playback hit the end): run_every was fixed
    # when the fragment was defined, so a full rerun is needed to re-arm (or disarm) the timer.
    if bool(st.session_state.get("playing")) != st.session_state.get("_armed") or current_speed() != speed:
        st.rerun(scope="app")
    map_html = render_svg(rep.header, frame) if graphics else render_map(rep, frame)
    if fs:
        st.markdown(map_html, unsafe_allow_html=True)
        show_status(frame, t)
        show_ticker(posts, decisions, agents, t)
    else:
        left, right = st.columns([3, 2])
        with left:
            st.markdown(map_html, unsafe_allow_html=True)
            show_status(frame, t)
        with right:
            show_board(posts, t, agents)
            st.divider()
            show_agent_decisions(decisions, t, agents)


# ---------------------------------------------------------------- app

def initial_path() -> str:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    return os.environ.get("UNDERCOOKED_REPLAY") or (args[-1] if args else "")


@st.cache_data(show_spinner=False)
def cached_load(path: str) -> list[Replay]:
    return load_replay(path)


@st.cache_data(show_spinner=False)
def cached_status(path: str, mtime: float) -> str:
    """Log status from the header only; keyed on mtime so a finished log is picked up on refresh."""
    return eval_status(path)


@st.cache_data(show_spinner=False)
def cached_seasons(path: str, mtime: float) -> list[tuple[Replay, dict[str, float] | None]]:
    return eval_seasons(path)


def load_directory(directory: str) -> tuple[list[Season], list[str]]:
    """Every finished .eval under `directory` as seasons; running, failed or unreadable logs are
    skipped (and named in the second result)."""
    seasons: list[Season] = []
    skipped: list[str] = []
    for f in sorted(Path(directory).rglob("*.eval")):
        try:
            mtime = f.stat().st_mtime
            status = cached_status(str(f), mtime)
            if status != "success":
                skipped.append(f"{f.name} ({status})")
                continue
            seasons += [Season(f, r.header.level, r.header.epoch, r, sc)
                        for r, sc in cached_seasons(str(f), mtime)]
        except Exception as exc:   # a log still being written can be half a zip file
            skipped.append(f"{f.name} ({type(exc).__name__})")
    return seasons, skipped


def pick_condition(seasons: list[Season]) -> tuple[str, dict[str, list[Season]]]:
    """Segmented control across the top; only the selected condition renders."""
    groups = group_conditions(seasons)
    options = [OVERVIEW, *groups]
    if st.session_state.get("cond") not in options:
        st.session_state.pop("cond", None)
    choice = st.segmented_control("Condition", options, default=OVERVIEW, key="cond",
                                  label_visibility="collapsed", width="stretch")
    return choice or OVERVIEW, groups


def main() -> None:
    st.set_page_config(page_title="Undercooked replay", layout="wide")
    st.title("Undercooked replay")

    # ---- sidebar: file, season, phase, agent filter
    path = st.sidebar.text_input("Replay file or directory (.json / .eval)", value=initial_path(), key="replay_path")
    if not path:
        st.info("Give a replay file or a directory of logs in the sidebar, or run: "
                "streamlit run undercooked/viewer.py -- <path>")
        return
    many = Path(path).is_dir()
    try:
        if many:
            seasons, skipped = load_directory(path)
        else:
            replays = cached_load(path)
            seasons = [Season(Path(path), r.header.level, r.header.epoch, r) for r in replays]
            skipped = []
    except (OSError, ValueError, NotImplementedError) as exc:
        st.error(f"Could not load {path}: {exc}")
        return
    cond_label = ""
    if many:
        if not seasons:
            st.warning(f"No finished .eval logs in {path}" + (f" (skipped: {', '.join(skipped)})" if skipped else ""))
            return
        cond_label, groups = pick_condition(seasons)
        st.sidebar.caption(f"{len(seasons)} seasons from {len({s.file for s in seasons})} finished logs"
                           + (f"; skipped {len(skipped)} running or failed" if skipped else ""))
        if skipped:
            with st.sidebar.expander("Skipped logs"):
                st.write("\n".join(f"- {x}" for x in skipped))
        if cond_label == OVERVIEW:
            st.caption("Mean total score per condition and level, as mean (number of seasons).")
            st.dataframe(overview_table(groups), use_container_width=True, hide_index=True)
            return
        seasons = groups[cond_label]
        if not st.session_state.get("fs_on"):
            with st.container(key="summary"):
                st.dataframe(summary_table(seasons), use_container_width=True, hide_index=True)
    # a multi-level run has one season per level and epoch: pick the level, then the season
    levels = list(dict.fromkeys(s.level for s in seasons))
    level = st.sidebar.selectbox("Level", levels) if len(levels) > 1 else levels[0]
    seasons = [s for s in seasons if s.level == level]
    labels = season_labels(seasons)
    epoch = st.sidebar.selectbox("Season (epoch)", labels)
    season = seasons[labels.index(epoch)]
    rep = season.rep
    phases = rep.phases()
    # Open on the first round (briefing has nothing to draw); fall back to the first phase.
    first_round = next((i for i, (ph, _) in enumerate(phases) if ph == "round"), 0)
    label = st.sidebar.selectbox("Phase", [phase_label(*p) for p in phases], index=first_round)
    phase, rnd = phases[[phase_label(*p) for p in phases].index(label)]
    names = [a.name for a in rep.header.agents]
    agents = st.sidebar.multiselect("Agents", names, default=names) or names
    graphics = st.sidebar.toggle("Graphics", value=True)   # illustrated SVG map vs minimal HTML table
    st.sidebar.caption(f"{rep.header.config_name} | level {rep.header.level} | seed {rep.header.seed}")

    frames = rep.select(Frame, phase, rnd)
    decisions = rep.select(DecisionRecord, phase, rnd)
    posts = rep.select(Post, phase, rnd)
    events = rep.select(Event, phase, rnd)
    # Practice: each agent plays its own game, all logged as ("practice", 0); show one at a time.
    players = [n for n in names if any(a.name == n for f in frames for a in f.agents)]
    if len(players) > 1 and all(len(f.agents) == 1 for f in frames):
        who = st.sidebar.selectbox("Practice of", players)
        frames = [f for f in frames if f.agents[0].name == who]
        decisions = [d for d in decisions if d.agent == who]
        events = [e for e in events if e.agent in (None, who)]
    if phase == "practice":   # practice is played in levels/practice.txt, not the header's level
        practice_map = Kitchen.load("practice", rep.header.bins).map_rows()
        rep = rep.model_copy(update={"header": rep.header.model_copy(update={"map": practice_map})})

    fs = bool(st.session_state.get("fs_on")) and bool(frames)   # full screen only makes sense with a map
    ctx = dict(rep=rep, frames=frames, decisions=decisions, posts=posts, agents=agents, graphics=graphics,
               hi=rep.header.round_s if phase == "round" else max((f.t for f in frames), default=0),
               key=f"t_{cond_label}_{season.file.name}_{level}_{epoch}_{phase}_{rnd}", fs=fs)   # per-phase key so each phase keeps its own position
    if st.session_state.get("_phase_key") != ctx["key"]:
        st.session_state["_phase_key"] = ctx["key"]
        st.session_state["playing"] = False   # never carry playback into another phase
    spd = current_speed()
    # run_every is fixed when the fragment is defined: only tick while playing (None = no timer).
    every = 1.0 / spd if st.session_state.get("playing") else None
    st.session_state["_armed"] = every is not None
    replay_fragment = st.fragment(run_every=every)(run_replay)

    if fs:
        st.markdown(FULLSCREEN_CSS, unsafe_allow_html=True)
        st.button("Exit full screen", key="fs_exit", on_click=st.session_state.update, kwargs={"fs_on": False})
        replay_fragment(**ctx, speed=spd)
        return

    tab_replay, tab_dec, tab_ev = st.tabs(["Replay", "Decisions", "Events"])
    with tab_replay:
        if not frames:
            show_conversation(rep, phase, rnd, agents)
        else:
            c1, c2 = st.columns([1, 3])
            c1.button("⛶ Full screen", key="fs_enter", on_click=st.session_state.update, kwargs={"fs_on": True}, help="Map, controls and a one-line ticker only")
            with c2:
                native_fullscreen_button()
            replay_fragment(**ctx, speed=spd)

    with tab_dec:
        shown = [d for d in decisions if d.agent in agents]
        st.dataframe(decisions_table(shown), use_container_width=True, hide_index=True,
                     column_config={"plan": st.column_config.TextColumn(width="large"),
                                    "post": st.column_config.TextColumn(width="medium")})
    with tab_ev:
        shown = [e for e in events if e.agent is None or e.agent in agents]
        st.dataframe(events_table(shown), use_container_width=True, hide_index=True)


main()
