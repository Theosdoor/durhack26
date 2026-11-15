/* Undercooked replay site: a season browser and a replay player, no build step.
   Data: data/index.json (one row per season) and data/seasons/<id>.json.gz (written by
   `uv run undercooked site <logs>`). All paths are relative, so the site works from a subpath.

   Deep links (hash only, ASCII letters, digits, hyphens):
     #<season id>                       the season, on its first round
     #<season id>--<phase>[-t<time>]    a moment: phase codes
        p<k>  practice of agent k (1-based, header order)   c<r>  chat before round r
        r<r>  round r                                        d<r>  private turn after round r
        x     reflection                                     b     briefing
     time is game seconds in practice and rounds, the turn index in chat, private turn, reflection.
   e.g. #5Yqi6vJD-split-room-e2--r2-t45
   Sound (music, voices, effects) lives in audio.js; this file only calls UCAudio.advance while playing. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const app = $("#app");
const SPEEDS = [1, 2, 4, 8, 16];
const RULES = { full: "full rules", bare: "bare rules", open: "no goal" };
const RULES_HELP = {
  full: "the system prompt explains the recipe, scoring and every action",
  bare: "only the goal and the tool syntax: the agents must discover how the kitchen works",
  open: "as bare, but no goal at all (\"do whatever you like\"); the score is still shown",
};
const TILE_COLOURS = { X: "#c08a52", " ": "#f2e3c4", O: "#e8a13a", D: "#7aa7c7", P: "#3b3b42", S: "#2f9e6b" };
const FEED_TYPES = new Set(["pickup", "putdown", "add_to_pot", "cook_start", "cook_done", "plate", "serve", "fail",
  "order_new", "order_missed", "compaction", "practice_finished", "cutoff"]);

const S = {
  index: null, maps: {}, cache: new Map(),
  home: { team: "", rules: "", n: "", level: "", pair: "", sort: "score-desc" },
  season: null, phase: null, t: 0, playing: false, speed: 1, timer: null, openDetails: new Set(),
};

/* ------------------------------------------------------------ theme */
(function theme() {
  const saved = localStorage.getItem("uc-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("uc-theme", next);
  });
})();

/* ------------------------------------------------------------ data */
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const buf = new Uint8Array(await res.arrayBuffer());
  let text;
  if (buf[0] === 0x1f && buf[1] === 0x8b) {   // gzip bytes (a server may already have decoded them)
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    text = await new Response(stream).text();
  } else {
    text = new TextDecoder().decode(buf);
  }
  return JSON.parse(text);
}

async function loadIndex() {
  if (!S.index) {
    const idx = await fetchJSON("data/index.json");
    S.index = idx.seasons;
    S.maps = idx.maps || {};
  }
  return S.index;
}

async function loadSeason(id) {
  if (!S.cache.has(id)) S.cache.set(id, fetchJSON(`data/seasons/${encodeURIComponent(id)}.json.gz`).then(prepare));
  return S.cache.get(id);
}

/* ------------------------------------------------------------ helpers */
const levelName = (lv) => lv.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const shortModel = (m) => m.split("/").pop();
const fmt = (v) => (v === null || v === undefined ? "–" : Number.isInteger(v) ? String(v) : v.toFixed(1));

function miniMarkdown(text) {
  // escape, then **bold**, headings, bullet lines; paragraphs on blank lines
  const lines = esc(text).split("\n");
  let html = "", list = false;
  for (let line of lines) {
    line = line.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s.,;:!?)]|$)/g, "$1<i>$2</i>");
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) { if (!list) { html += "<ul>"; list = true; } html += `<li>${bullet[1]}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    const h = line.match(/^#{1,4}\s+(.*)$/);
    if (h) html += `<h4>${h[1]}</h4>`;
    else if (line.trim()) html += `<p>${line}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function thumb(map) {
  if (!map) return "";
  const cols = Math.max(...map.map((r) => r.length));
  let cells = "";
  for (const row of map) for (const ch of row.padEnd(cols)) cells += `<span style="background:${TILE_COLOURS[ch] || TILE_COLOURS[" "]}"></span>`;
  return `<div class="thumb" style="grid-template-columns:repeat(${cols},9px)" aria-hidden="true">${cells}</div>`;
}

/* ------------------------------------------------------------ router */
function parseHash() {
  let h = location.hash.replace(/^#/, "");
  try { h = decodeURIComponent(h); } catch { /* keep it raw */ }
  if (!h) return null;
  const [id, rest] = h.split("--");
  const m = (rest || "").match(/^([a-z])(\d*)(?:-t(\d+))?$/);
  return { id, code: m ? m[1] + m[2] : null, t: m && m[3] !== undefined ? Number(m[3]) : null };
}

function momentHash() {
  if (!S.season || !S.phase) return "";
  return `#${S.season.id}--${S.phase.code}-t${S.t}`;
}

function writeHash() {
  const h = momentHash();
  if (h && location.hash !== h) history.replaceState(null, "", h);
}

async function route() {
  stop();
  const target = parseHash();
  try {
    if (!target) { await loadIndex(); S.season = null; renderHome(); return; }
    if (!S.season || S.season.id !== target.id) {
      app.innerHTML = `<p class="loading">Loading season ${esc(target.id)}&hellip;</p>`;
      const [season] = await Promise.all([loadSeason(target.id), loadIndex().catch(() => null)]);
      S.season = season;
      renderSeasonShell();
    }
    const ph = S.season.phases.find((p) => p.code === target.code) || S.season.defaultPhase;
    selectPhase(ph, target.t ?? 0, false);
  } catch (err) {
    console.error(err);
    app.innerHTML = `<p class="error">Could not load this page: ${esc(err.message)}. <a href="#">All seasons</a></p>`;
  }
}
addEventListener("hashchange", route);

/* ------------------------------------------------------------ home */
function renderHome() {
  document.title = "Undercooked: replays";
  const rows = S.index;
  const uniq = (f) => [...new Set(rows.map(f))].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
  const teams = uniq((r) => r.team), levels = uniq((r) => r.level), sizes = uniq((r) => r.n_agents);
  const rules = uniq((r) => r.rules);
  const f = S.home;
  const opt = (v, label, cur) => `<option value="${esc(v)}"${String(cur) === String(v) ? " selected" : ""}>${esc(label)}</option>`;
  const seg = (key, items) => `<div class="seg" role="group">${items.map(([v, l]) =>
    `<button type="button" data-k="${key}" data-v="${esc(v)}" aria-pressed="${String(f[key]) === String(v)}">${esc(l)}</button>`).join("")}</div>`;
  app.innerHTML = `
  <section class="intro">
    <div class="card">
      <h1>LLM chefs, no assigned roles</h1>
      <p>In <b>Undercooked</b>, 2&ndash;4 language-model agents share an Overcooked-style kitchen. Nobody is told who
      does what: they plan on a shared message board, then act in real time. Every tick is one second; a decision takes
      a few seconds to land, so the agents commit to short plans (<code>move_to</code>, <code>interact</code>, <code>wait</code>).</p>
      <p><b>Onion soup:</b> 3 onions in a pot &rarr; it cooks for 20 s by itself &rarr; plate it with a dish &rarr; serve it at the
      hatch for 20 points. A season is practice (each agent alone), then 3 rounds of 180 s, each preceded by a chat and
      followed by a private turn to update a notepad, and a final written reflection.</p>
    </div>
    <div class="card">
      <p><b>Conditions</b></p>
      <ul>
        <li><span class="tag rules-full">full rules</span> ${esc(RULES_HELP.full)}</li>
        <li><span class="tag rules-bare">bare rules</span> ${esc(RULES_HELP.bare)}</li>
        <li><span class="tag rules-open">no goal</span> ${esc(RULES_HELP.open)}</li>
      </ul>
      <p class="muted">Teams are self-play (one model) or mixed pairs; layouts come from Overcooked-AI.
      In the reflection each agent also predicts the score it would have got with full instructions.</p>
    </div>
  </section>
  <section class="card filters" aria-label="Filters">
    <label>Team <select data-k="team">${opt("", "All teams", f.team)}${teams.map((t) => opt(t, t, f.team)).join("")}</select></label>
    <label>Rules ${seg("rules", [["", "All"], ...rules.map((r) => [r, RULES[r] || r])])}</label>
    <label>Team size ${seg("n", [["", "All"], ...sizes.map((n) => [n, `${n} agents`])])}</label>
    <label>Pairing ${seg("pair", [["", "All"], ["self", "Self-play"], ["mixed", "Mixed"]])}</label>
    <label>Layout <select data-k="level">${opt("", "All layouts", f.level)}${levels.map((l) => opt(l, levelName(l), f.level)).join("")}</select></label>
    <label>Sort <select data-k="sort">${[["score-desc", "Score, high to low"], ["score-asc", "Score, low to high"],
      ["level", "Layout"], ["team", "Team"], ["gap", "Predicted minus actual"]].map(([v, l]) => opt(v, l, f.sort)).join("")}</select></label>
    <button type="button" id="reset" class="ghost">Reset</button>
  </section>
  <p class="summary" id="summary"></p>
  <section class="grid" id="grid" aria-label="Seasons"></section>`;
  app.querySelectorAll("select[data-k]").forEach((el) => el.addEventListener("change", () => { f[el.dataset.k] = el.value; renderHome(); }));
  app.querySelectorAll(".seg button").forEach((el) => el.addEventListener("click", () => { f[el.dataset.k] = el.dataset.v; renderHome(); }));
  $("#reset").addEventListener("click", () => { Object.assign(f, { team: "", rules: "", n: "", level: "", pair: "", sort: "score-desc" }); renderHome(); });
  renderGrid();
}

function predictedMean(r) {
  const p = r.agents.map((a) => a.predicted).filter((v) => v !== null && v !== undefined);
  return p.length ? p.reduce((a, b) => a + b, 0) / p.length : null;
}

function renderGrid() {
  const f = S.home;
  let rows = S.index.filter((r) => (!f.team || r.team === f.team) && (!f.rules || r.rules === f.rules)
    && (!f.n || String(r.n_agents) === String(f.n)) && (!f.level || r.level === f.level)
    && (!f.pair || (f.pair === "mixed") === r.mixed));
  const by = {
    "score-desc": (a, b) => b.total - a.total, "score-asc": (a, b) => a.total - b.total,
    level: (a, b) => a.level.localeCompare(b.level) || b.total - a.total,
    team: (a, b) => a.team.localeCompare(b.team) || b.total - a.total,
    gap: (a, b) => ((predictedMean(b) ?? -1e9) - b.total) - ((predictedMean(a) ?? -1e9) - a.total),
  }[f.sort];
  rows = rows.slice().sort((a, b) => by(a, b) || a.id.localeCompare(b.id));
  const mean = rows.length ? rows.reduce((s, r) => s + r.total, 0) / rows.length : 0;
  $("#summary").textContent = `${rows.length} of ${S.index.length} seasons` + (rows.length ? ` · mean total score ${mean.toFixed(1)}` : "");
  const maxRound = Math.max(20, ...S.index.flatMap((r) => r.rounds.filter((x) => x !== null)));
  $("#grid").innerHTML = rows.map((r) => {
    const pm = predictedMean(r);
    const bars = r.rounds.map((v, i) => `<div class="b" title="Round ${i + 1}: ${fmt(v)}"><span>${fmt(v)}</span><i style="height:${Math.max(4, ((v || 0) / maxRound) * 100)}%"></i></div>`).join("");
    return `<a class="card season-card" href="#${esc(r.id)}">
      <div class="row">
        <div><div class="title">${esc(levelName(r.level))} <span class="muted">· season ${r.epoch}</span></div>
          <div class="team">${esc(r.team)}</div></div>
        <div class="big">${fmt(r.total)}<small>points</small></div>
      </div>
      <div class="row">
        <div class="tags"><span class="tag rules-${esc(r.rules)}">${esc(RULES[r.rules] || r.rules)}</span>
          <span class="tag">${r.n_agents} agents</span>${r.mixed ? '<span class="tag">mixed</span>' : ""}
          <span class="tag">${r.served} soups</span></div>
        ${thumb(S.maps[r.level])}
      </div>
      <div class="bars" aria-label="Score per round">${bars}</div>
      <div class="pred">${pm === null ? "no prediction" : `predicted with full instructions: <b>${fmt(pm)}</b>`}</div>
    </a>`;
  }).join("") || `<p class="empty-note">No season matches these filters.</p>`;
}

/* ------------------------------------------------------------ season model */
function phaseLabel(p) {
  if (p.phase === "practice") return { k: "Practice", l: p.agent };
  if (p.phase === "chat") return { k: "Chat", l: `before R${p.round}` };
  if (p.phase === "round") return { k: "Round", l: String(p.round) };
  if (p.phase === "debrief") return { k: "Private", l: `after R${p.round}` };
  if (p.phase === "reflection") return { k: "", l: "Reflection" };
  return { k: "", l: p.phase };
}

function prepare(season) {
  const h = season.header;
  const recs = season.records;
  recs.forEach((r, i) => { r.i = i; });
  season.colour = {};
  h.agents.forEach((a, i) => { season.colour[a.name] = season.sprites.colours[i % season.sprites.colours.length]; });
  season.agentInfo = Object.fromEntries(h.agents.map((a) => [a.name, a]));
  // (phase, round) groups in order of first appearance
  const groups = new Map();
  for (const r of recs) {
    const key = `${r.phase}|${r.round}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  const phases = [];
  let gi = 0;
  for (const [key, rs] of groups) {
    const [phase, round] = [key.split("|")[0], Number(key.split("|")[1])];
    const base = { phase, round, gi: gi++ };
    if (phase === "practice") {
      h.agents.forEach((a, k) => {
        const mine = rs.filter((r) => r.agent === a.name || (r.kind === "frame" && r.agents[0]?.name === a.name));
        if (!mine.some((r) => r.kind === "frame")) return;
        phases.push(makePhase(season, { ...base, agent: a.name, code: `p${k + 1}` }, mine));
      });
    } else if (phase === "briefing") {
      if (rs.some((r) => r.kind === "post" || r.kind === "decision")) phases.push(makePhase(season, { ...base, code: "b" }, rs));
    } else {
      const code = { chat: "c", round: "r", debrief: "d", reflection: "x" }[phase] + (phase === "reflection" ? "" : round);
      phases.push(makePhase(season, { ...base, code }, rs));
    }
  }
  season.phases = phases;
  season.defaultPhase = phases.find((p) => p.phase === "round") || phases[0];
  season.posts = recs.filter((r) => r.kind === "post");
  season.notes = recs.filter((r) => r.kind === "decision" && r.note && r.landed !== false);
  season.roundScores = [];
  for (const p of phases) if (p.phase === "round") season.roundScores[p.round - 1] = p.frames.length ? p.frames[p.frames.length - 1].score : null;
  return season;
}

function makePhase(season, p, rs) {
  p.records = rs;
  p.frames = rs.filter((r) => r.kind === "frame").sort((a, b) => a.t - b.t);
  p.decisions = rs.filter((r) => r.kind === "decision");
  p.events = rs.filter((r) => r.kind === "event");
  p.posts = rs.filter((r) => r.kind === "post");
  p.game = p.frames.length > 0;
  if (p.game) {
    const last = p.frames[p.frames.length - 1].t;
    p.hi = p.phase === "round" ? Math.max(season.header.round_s, last) : last;
    p.byT = [];
    let cur = p.frames[0];
    let j = 0;
    for (let t = 0; t <= p.hi; t++) {
      while (j < p.frames.length && p.frames[j].t <= t) cur = p.frames[j++];
      p.byT[t] = cur;
    }
    p.map = p.phase === "practice" ? season.header.practice_map : season.header.map;
    p.bgKey = p.phase === "practice" ? "practice" : "level";
  } else {
    p.hi = Math.max(0, ...rs.filter((r) => r.kind === "decision" || r.kind === "post").map((r) => r.t));
  }
  const lab = phaseLabel(p);
  p.k = lab.k; p.l = lab.l;
  p.title = p.phase === "practice" ? `Practice · ${p.agent}` : p.phase === "chat" ? `Chat before round ${p.round}`
    : p.phase === "round" ? `Round ${p.round}` : p.phase === "debrief" ? `Private turn after round ${p.round}`
      : p.phase === "reflection" ? "Reflection" : p.phase;
  return p;
}

/* a record is in the past of (phase, t) if its phase group came earlier, or it is in the same
   group at time <= t (decisions: when they landed) */
function isPast(r, p, t, useLanding = false) {
  const gi = S.season.groupIndex(r);
  if (gi !== p.gi) return gi < p.gi;
  const rt = useLanding && r.lands_t !== undefined ? r.lands_t : r.t;
  return rt <= t;
}

/* ------------------------------------------------------------ season page */
function renderSeasonShell() {
  const s = S.season, h = s.header;
  const groupOf = new Map();
  for (const p of s.phases) for (const r of p.records) groupOf.set(r.i, p.gi);
  const keyGi = new Map(s.phases.map((p) => [`${p.phase}|${p.round}`, p.gi]));
  s.groupIndex = (r) => groupOf.get(r.i) ?? keyGi.get(`${r.phase}|${r.round}`) ?? -1;
  const row = (S.index || []).find((r) => r.id === s.id);
  document.title = `${levelName(h.level)} · season ${h.epoch} · Undercooked`;
  const legend = h.agents.map((a) => `<span title="${esc(a.model)}${a.provider ? " via " + esc(a.provider) : ""}"><span class="swatch" style="background:${s.colour[a.name]}"></span><b>${esc(a.name)}</b> <span class="muted">${esc(shortModel(a.model))}</span></span>`).join("");
  // phase buttons, grouped: practice | chat, round, private per round | reflection
  const groups = [];
  for (const p of s.phases) {
    const g = p.phase === "practice" ? "practice" : p.phase === "reflection" ? "reflection" : p.phase === "briefing" ? "briefing" : `r${p.round}`;
    if (!groups.length || groups[groups.length - 1].g !== g) groups.push({ g, items: [] });
    groups[groups.length - 1].items.push(p);
  }
  const phaseBtns = groups.map((g) => `<div class="grp">${g.items.map((p) =>
    `<button type="button" data-code="${p.code}" title="${esc(p.title)}">${p.k ? `<span class="k">${esc(p.k)}</span>` : ""}${esc(p.l)}</button>`).join("")}</div>`).join("");
  app.innerHTML = `
  <div class="season-head">
    <a class="back" href="#">&larr; All seasons</a>
    <h1>${esc(levelName(h.level))} <span class="muted">· season ${h.epoch}</span></h1>
    <div class="tags"><span class="tag rules-${esc(h.rules)}">${esc(RULES[h.rules] || h.rules)}</span>
      <span class="tag">${h.agents.length} agents</span>${row?.mixed ? '<span class="tag">mixed</span>' : ""}
      <span class="tag">${h.rounds} × ${h.round_s} s</span><span class="tag">scoring: ${esc(h.scoring)}</span></div>
    <div class="agents-legend">${legend}</div>
    <button type="button" id="copy" class="ghost" title="Copy a link to this exact moment">Copy link</button>
    ${window.UCAudio ? UCAudio.controlsHTML() : ""}
  </div>
  <nav class="phases" aria-label="Phases">${phaseBtns}</nav>
  <div class="layout">
    <section class="card stage" id="stage"></section>
    <aside class="side" id="side"></aside>
  </div>`;
  app.querySelectorAll(".phases button").forEach((b) => b.addEventListener("click", () => {
    selectPhase(s.phases.find((p) => p.code === b.dataset.code), 0, true);
  }));
  if (window.UCAudio) UCAudio.bind(app);
  $("#copy").addEventListener("click", async () => {
    writeHash();
    try { await navigator.clipboard.writeText(location.href); $("#copy").textContent = "Copied"; }
    catch { $("#copy").textContent = "Link in address bar"; }
    setTimeout(() => { const b = $("#copy"); if (b) b.textContent = "Copy link"; }, 1500);
  });
}

function selectPhase(p, t) {
  stop();
  S.phase = p;
  S.t = Math.max(0, Math.min(p.hi, t || 0));
  app.querySelectorAll(".phases button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.code === p.code)));
  const btn = app.querySelector(`.phases button[data-code="${p.code}"]`);
  if (btn) btn.scrollIntoView({ block: "nearest", inline: "nearest" });
  renderStage();
  renderSide();
  update();
  writeHash();
}

function renderStage() {
  const p = S.phase, s = S.season;
  const stage = $("#stage");
  const unit = p.game ? "s" : "turn";
  const marks = [];
  if (p.game) {
    for (const e of p.events) if (e.type === "serve") marks.push(`<i class="serve" style="left:${(e.t / p.hi) * 100}%" title="serve t=${e.t}"></i>`);
    for (const e of p.events) if (e.type === "fail") marks.push(`<i class="fail" style="left:${(e.t / p.hi) * 100}%"></i>`);
    for (const r of p.posts) marks.push(`<i class="post" style="left:${(r.t / p.hi) * 100}%" title="post t=${r.t}"></i>`);
  }
  stage.innerHTML = `
    <div class="stage-bar"><span class="ph">${esc(p.title)}</span><span id="stats"></span></div>
    ${p.game ? kitchenSVG(p) : `<div class="talk" id="talk">${talkHTML(p)}</div>`}
    <div class="controls">
      <button type="button" id="prevph" title="Previous phase (P)" aria-label="Previous phase">&laquo;</button>
      <button type="button" id="back" title="Back one ${unit} (Left)">&#9664;</button>
      <button type="button" id="play" class="primary" title="Play / pause (Space)">&#9654; Play</button>
      <button type="button" id="fwd" title="Forward one ${unit} (Right)">&#9654;</button>
      <button type="button" id="nextph" title="Next phase (N)" aria-label="Next phase">&raquo;</button>
      <select id="speed" title="Playback speed" aria-label="Playback speed">${SPEEDS.map((v) => `<option value="${v}"${v === S.speed ? " selected" : ""}>${v}×</option>`).join("")}</select>
      <div class="scrub"><div class="marks">${marks.join("")}</div>
        <input type="range" id="scrub" min="0" max="${p.hi}" step="1" value="${S.t}" aria-label="Time"></div>
      <span class="time" id="time"></span>
    </div>
    ${p.game ? `<div class="legend-marks"><span><i style="background:var(--good)"></i>soup served</span><span><i style="background:#3b82f6"></i>board post</span><span><i style="background:var(--bad);opacity:.55"></i>failed action</span></div>
    <div class="feed" id="feed" aria-label="Kitchen log"></div>` : ""}
    <p class="kbd">Keys: Space play/pause · &larr;/&rarr; step (Shift: 10) · N/P next/previous phase</p>`;
  $("#play").addEventListener("click", toggle);
  $("#back").addEventListener("click", () => seek(S.t - 1));
  $("#fwd").addEventListener("click", () => seek(S.t + 1));
  $("#prevph").addEventListener("click", () => stepPhase(-1));
  $("#nextph").addEventListener("click", () => stepPhase(1));
  $("#speed").addEventListener("change", (e) => { S.speed = Number(e.target.value); if (S.playing) { stop(); play(); } });
  $("#scrub").addEventListener("input", (e) => { stop(); seek(Number(e.target.value)); });
  S.chefs = new Map();
  S.dynKey = null;
}

function stepPhase(d) {
  const ph = S.season.phases;
  const i = ph.indexOf(S.phase) + d;
  if (i >= 0 && i < ph.length) selectPhase(ph[i], 0, true);
}

/* ------------------------------------------------------------ kitchen drawing */
function kitchenSVG(p) {
  const sp = S.season.sprites, h = S.season.header;
  const cols = Math.max(...p.map.map((r) => r.length));
  const top = h.scoring === "orders" ? sp.strip : 0;
  const w = cols * sp.T, ht = top + p.map.length * sp.T;
  p.top = top;
  return `<svg class="kitchen" id="kitchen" viewBox="0 0 ${w} ${ht}" role="img" aria-label="Kitchen map" preserveAspectRatio="xMidYMid meet">
    ${sp.defs}<g id="orders"></g><g transform="translate(0 ${top})">${sp.bg[p.bgKey] || ""}</g>
    <g id="dyn"></g><g id="chefs"></g></svg>`;
}

function potKey(p) { return `${p.onions}|${p.state}|${p.ready_in ?? ""}`; }

function drawFrame(p, f, moveMs) {
  const sp = S.season.sprites, T = sp.T, top = p.top;
  const svg = $("#kitchen");
  if (!svg || !f) return;
  // pots and counter items: redraw only when they change
  const dynKey = JSON.stringify([f.pots?.map(potKey), f.counters || {}]);
  if (dynKey !== S.dynKey) {
    S.dynKey = dynKey;
    let html = "";
    for (const pot of f.pots || []) html += `<g transform="translate(${pot.pos[0] * T} ${top + pot.pos[1] * T})">${sp.pots[potKey(pot)] || ""}</g>`;
    for (const [xy, item] of Object.entries(f.counters || {})) {
      const [x, y] = xy.split(",").map(Number);
      if (!"XOD".includes(p.map[y]?.[x])) continue;
      html += `<g transform="translate(${x * T} ${top + y * T})">${sp.items[item] || ""}</g>`;
    }
    $("#dyn").innerHTML = html;
  }
  // chefs: one group each, moved with a CSS transition so playback glides
  const layer = $("#chefs");
  const present = new Set();
  for (const a of f.agents || []) {
    present.add(a.name);
    let c = S.chefs.get(a.name);
    if (!c) {
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", "chef");
      layer.appendChild(g);
      c = { g, key: null };
      S.chefs.set(a.name, c);
    }
    const key = `${a.name}|${a.holding || ""}|${a.status === "deciding" ? 1 : 0}`;
    if (key !== c.key) { c.g.innerHTML = sp.chefs[key] || sp.chefs[`${a.name}||0`] || ""; c.key = key; }
    c.g.style.setProperty("--move", `${moveMs}ms`);
    c.g.style.transform = `translate(${a.pos[0] * T}px, ${top + a.pos[1] * T}px)`;
  }
  for (const [name, c] of S.chefs) if (!present.has(name)) { c.g.remove(); S.chefs.delete(name); }
  // order strip (scoring "orders" only), as in undercooked/sprites.py order_strip
  if (top) {
    const W = Math.max(...p.map.map((r) => r.length)) * T;
    let s = `<rect width="${W}" height="${top}" fill="#3b2a1e"/>`;
    let x = 96;
    for (const o of f.orders || []) {
      const left = Math.max(0, o.due_t - f.t), urgent = left < 10;
      if (x + 126 > W) break;
      s += `<rect x="${x}" y="8" width="126" height="${top - 16}" rx="6" fill="${urgent ? "#d63b3b" : "#fbf1d9"}" stroke="#1c130c" stroke-width="2"/>
        <text x="${x + 10}" y="${top / 2 + 5}" font-size="13" font-weight="700" fill="${urgent ? "#fff" : "#3a2616"}" font-family="Helvetica, Arial, sans-serif">#${o.id} soup</text>
        <text x="${x + 116}" y="${top / 2 + 5}" font-size="16" font-weight="800" text-anchor="end" fill="${urgent ? "#fff" : "#3a2616"}" font-family="Helvetica, Arial, sans-serif">${left}s</text>`;
      x += 134;
    }
    s += `<text x="14" y="${top / 2 + 5}" font-size="13" font-weight="800" fill="#f6e7c8" font-family="Helvetica, Arial, sans-serif">ORDERS</text>`;
    $("#orders").innerHTML = s;
  }
}

/* ------------------------------------------------------------ talk phases */
function talkHTML(p) {
  const s = S.season;
  const items = p.decisions.length ? p.decisions : p.posts;
  if (!items.length) return `<p class="empty-note">Nothing was said in this phase.</p>`;
  const actual = s.roundScores.reduce((a, b) => a + (b || 0), 0);
  return items.map((d) => {
    const c = s.colour[d.agent];
    const info = s.agentInfo[d.agent];
    const flags = [d.ready && '<span class="flag ok">ready</span>', d.cutoff && '<span class="flag bad">cut off</span>',
      d.forced && '<span class="flag warn">forced answer</span>', d.landed === false && '<span class="flag bad">not landed</span>',
      d.read_board && '<span class="flag info">read board</span>'].filter(Boolean).join("");
    let body = "";
    if (d.kind === "post") body = `<div class="body">${esc(d.text)}</div>`;
    else {
      if (p.phase === "reflection") {
        const pred = d.predicted_score;
        const delta = pred === undefined || pred === null ? "" : pred - actual;
        body += `<div class="predict"><div class="box"><small>Predicted with full instructions</small><b>${fmt(pred)}</b></div>
          <div class="box"><small>Actual season total</small><b>${fmt(actual)}</b></div>
          ${delta === "" ? "" : `<div class="box"><small>Prediction minus actual</small><b class="delta ${delta >= 0 ? "up" : "down"}">${delta >= 0 ? "+" : ""}${fmt(delta)}</b></div>`}</div>`;
        if (d.text) body += `<div class="md">${miniMarkdown(d.text)}</div>`;
      } else {
        if (d.post) body += `<div class="say"><b>Posted:</b> ${esc(d.post)}</div>`;
        if (d.text) body += `<div class="say"><b>Said:</b> <span class="body">${esc(d.text)}</span></div>`;
        if (d.note) body += `<details${S.openDetails.has("n" + d.i) ? " open" : ""} data-key="n${d.i}"><summary>Notepad after this turn</summary><div class="notepad" style="--c:${c}">${esc(d.note)}</div></details>`;
        if (!d.post && !d.text && !d.note) body += `<div class="empty-note">passed</div>`;
      }
      if (d.reasoning) body += `<details${S.openDetails.has("r" + d.i) ? " open" : ""} data-key="r${d.i}"><summary>Reasoning (${d.reasoning.length.toLocaleString()} chars)</summary><div class="reasoning">${esc(d.reasoning)}</div></details>`;
    }
    return `<article class="turn" data-t="${d.t}" style="--c:${c}">
      <div class="hd"><span><span class="swatch" style="background:${c}"></span><span class="name">${esc(d.agent)}</span>
        <span class="muted">${esc(shortModel(info?.model || ""))}</span></span>
        <span>${flags}<span class="muted mono">turn ${d.t}</span></span></div>${body}</article>`;
  }).join("");
}

/* ------------------------------------------------------------ side panels */
function renderSide() {
  const s = S.season, p = S.phase;
  const names = p.phase === "practice" ? [p.agent] : s.header.agents.map((a) => a.name);
  const rs = s.roundScores;
  const total = rs.reduce((a, b) => a + (b || 0), 0);
  const scoreChips = rs.map((v, i) => `<span class="sc${p.phase === "round" && p.round === i + 1 ? " cur" : ""}">R${i + 1} <b>${fmt(v)}</b></span>`).join("")
    + `<span class="sc total">Total ${fmt(total)}</span>`;
  const reflections = s.phases.find((x) => x.phase === "reflection");
  const preds = reflections ? reflections.decisions.map((d) => `<span class="sc"><span class="swatch" style="background:${s.colour[d.agent]}"></span>${esc(d.agent)} predicts <b>${fmt(d.predicted_score)}</b></span>`).join("") : "";
  $("#side").innerHTML = `
    <section class="card panel"><h2>Season score</h2><div class="scores">${scoreChips}</div>
      ${preds ? `<div class="scores" style="margin-top:6px">${preds}<a href="#${s.id}--x-t0" class="sc">reflections &rarr;</a></div>` : ""}</section>
    ${p.game ? `<section class="card panel"><h2>Agents <span class="muted" style="text-transform:none;letter-spacing:0">latest decision at this time</span></h2><div id="agents">${names.map((n) => `<div class="agent" data-agent="${esc(n)}" style="--c:${s.colour[n]}"></div>`).join("")}</div></section>` : ""}
    <section class="card panel"><h2>Board <span class="muted" id="boardcount" style="text-transform:none;letter-spacing:0"></span></h2><div class="board" id="board"></div></section>
    <section class="card panel"><h2>Notepads</h2><div id="notepads"></div></section>`;
  S.agentKeys = new Map();
  S.boardKey = null;
  S.noteKey = null;
}

function agentCard(name, p, t, f) {
  const s = S.season;
  const info = s.agentInfo[name];
  // the plan running now comes from the latest decision that has landed; a newer one still in
  // flight is flagged as "thinking"
  const mine = p.decisions.filter((d) => d.agent === name && d.t <= t);
  const landed = mine.filter((d) => d.lands_t === undefined || d.lands_t <= t);
  const pending = mine.length && mine[mine.length - 1].lands_t > t ? mine[mine.length - 1] : null;
  const d = landed[landed.length - 1] || pending;
  const st = f?.agents?.find((a) => a.name === name);
  const fails = p.events.filter((e) => e.agent === name && e.type === "fail" && e.t <= t && (!d || e.t >= d.t)).slice(-3);
  const key = `${d ? d.i : -1}|${pending ? pending.i : -1}|${d && d.lands_t > t}|${fails.length}|${st?.plan?.join(";")}|${st?.holding}|${st?.status}`;
  if (S.agentKeys.get(name) === key) return null;
  S.agentKeys.set(name, key);
  let html = `<div class="hd"><span><span class="name">${esc(name)}</span> <span class="model">${esc(shortModel(info.model))}</span></span>
    <span class="meta">${st ? `(${st.pos[0]},${st.pos[1]}) · ${esc(st.holding || "empty hands")}${st.status === "deciding" ? " · thinking" : ""}` : ""}</span></div>`;
  if (!d) return html + `<div class="empty-note">no decision yet</div>`;
  const inflight = d.lands_t !== undefined && d.lands_t > t;
  const flags = [inflight && `<span class="flag info">thinking, lands t=${d.lands_t}</span>`,
    pending && pending !== d && `<span class="flag info">next decision: thinking since t=${pending.t}, lands t=${pending.lands_t}</span>`,
    d.cutoff && '<span class="flag bad">cut off</span>', d.forced && '<span class="flag warn">forced answer</span>',
    d.landed === false && '<span class="flag bad">never landed</span>', d.read_board && '<span class="flag info">read board</span>',
    d.finish_practice && '<span class="flag ok">finished practice</span>'].filter(Boolean).join("");
  html += `<div class="meta">decided t=${d.t}${d.lands_t !== undefined ? ` → acts t=${d.lands_t}` : ""} ${flags}</div>`;
  if (d.plan?.length) {
    // remaining plan (from the frame) marks the steps already done
    const remaining = !inflight && st ? st.plan?.length || 0 : d.plan.length;
    const doneN = inflight ? 0 : Math.max(0, d.plan.length - remaining);
    html += `<div class="plan" title="Plan: steps done are struck through">${d.plan.map((a, i) =>
      `<span class="${i < doneN ? "done" : i === doneN && !inflight ? "cur" : ""}">${esc(a)}</span>`).join("")}</div>`;
  } else html += `<div class="empty-note">no plan</div>`;
  if (d.plan_sent && d.plan_sent.join("|") !== (d.plan || []).join("|")) html += `<div class="meta">sent: ${esc(d.plan_sent.join(" | "))}</div>`;
  if (d.post) html += `<div class="say"><b>Posted:</b> ${esc(d.post)}</div>`;
  if (d.text) html += `<div class="say"><b>Said:</b> ${esc(d.text)}</div>`;
  if (fails.length) html += `<div class="fails">${fails.map((e) => `t=${e.t} ✗ ${esc(e.data?.action || "")}: ${esc(e.data?.reason || "failed")}`).join("<br>")}</div>`;
  if (pending && pending !== d && pending.reasoning) html += `<details${S.openDetails.has("r" + pending.i) ? " open" : ""} data-key="r${pending.i}"><summary>Reasoning of the decision in flight (${pending.reasoning.length.toLocaleString()} chars)</summary><div class="reasoning">${esc(pending.reasoning)}</div></details>`;
  if (d.reasoning) html += `<details${S.openDetails.has("r" + d.i) ? " open" : ""} data-key="r${d.i}"><summary>Reasoning (${d.reasoning.length.toLocaleString()} chars)</summary><div class="reasoning">${esc(d.reasoning)}</div></details>`;
  return html;
}

document.addEventListener("toggle", (e) => {
  const k = e.target?.dataset?.key;
  if (!k) return;
  if (e.target.open) S.openDetails.add(k); else S.openDetails.delete(k);
}, true);

function phaseTitleOf(r) {
  const p = S.season.phases.find((x) => x.phase === r.phase && x.round === r.round);
  return p ? (p.phase === "practice" ? "Practice" : p.title) : r.phase;
}

function updateSide() {
  const s = S.season, p = S.phase, t = S.t;
  const f = p.game ? p.byT[t] : null;
  if (p.game) {
    for (const el of app.querySelectorAll("#agents .agent")) {
      const html = agentCard(el.dataset.agent, p, t, f);
      if (html !== null) el.innerHTML = html;
    }
  }
  // board: every post up to now, grouped by phase
  const posts = s.posts.filter((r) => isPast(r, p, t));
  const bkey = posts.length + "|" + t;
  if (bkey !== S.boardKey) {
    S.boardKey = bkey;
    let last = null, html = "";
    for (const r of posts) {
      const title = phaseTitleOf(r);
      if (title !== last) { html += `<div class="sep">${esc(title)}</div>`; last = title; }
      const now = r.phase === p.phase && r.round === p.round && r.t === t;
      html += `<div class="post${now ? " now" : ""}" style="--c:${s.colour[r.agent]}"><span class="who">${esc(r.agent)}</span><span class="at">${r.phase === "round" || r.phase === "practice" ? "t=" : "turn "}${r.t}</span><div>${esc(r.text)}</div></div>`;
    }
    const board = $("#board");
    board.innerHTML = html || `<p class="empty-note">No posts yet.</p>`;
    board.scrollTop = board.scrollHeight;
    $("#boardcount").textContent = `${posts.length} post${posts.length === 1 ? "" : "s"} so far`;
  }
  // notepads: each agent's latest landed note
  const names = p.phase === "practice" ? [p.agent] : s.header.agents.map((a) => a.name);
  const latest = {};
  for (const d of s.notes) if (isPast(d, p, t, true)) latest[d.agent] = d;
  const nkey = names.map((n) => latest[n]?.i ?? -1).join(",");
  if (nkey !== S.noteKey) {
    S.noteKey = nkey;
    $("#notepads").innerHTML = names.map((n) => {
      const d = latest[n];
      return `<div class="np-name"><span class="swatch" style="background:${s.colour[n]}"></span>${esc(n)}${d ? ` <span class="muted mono" style="font-weight:400;font-size:.75rem">written in ${esc(phaseTitleOf(d).toLowerCase())}, t=${d.lands_t ?? d.t}</span>` : ""}</div>
        <div class="notepad${d ? "" : " empty"}" style="--c:${s.colour[n]}">${d ? esc(d.note) : "empty"}</div>`;
    }).join("");
  }
}

/* ------------------------------------------------------------ timeline */
function update(moveMs = 0) {
  const p = S.phase, t = S.t;
  const scrub = $("#scrub");
  if (scrub) scrub.value = t;
  const total = p.game ? p.hi : p.hi;
  $("#time").textContent = p.game ? `t ${t} / ${total} s` : `turn ${t} / ${total}`;
  if (p.game) {
    const f = p.byT[t];
    drawFrame(p, f, moveMs);
    const deciding = f.agents.filter((a) => a.status === "deciding").length;
    $("#stats").innerHTML = `<span class="stat">Score <b>${f.score}</b></span> <span class="stat">Soups served <b>${f.served || 0}</b></span>`
      + (S.season.header.scoring === "orders" ? ` <span class="stat">Missed <b>${f.missed || 0}</b></span>` : "")
      + ` <span class="stat muted">${deciding ? `${deciding} thinking` : ""}</span>`;
    const ev = p.events.filter((e) => FEED_TYPES.has(e.type) && e.t <= t).slice(-8);
    $("#feed").innerHTML = ev.length ? ev.map((e) => `<div class="${e.t === t ? "now" : ""}"><span class="mono muted">t=${e.t}</span> ${feedText(e)}</div>`).join("")
      : `<span class="muted">Kitchen log: nothing has happened yet.</span>`;
    $("#feed").scrollTop = 1e6;
  } else {
    const talk = $("#talk");
    let cur = null;
    for (const el of talk.querySelectorAll(".turn")) {
      const tt = Number(el.dataset.t);
      el.classList.toggle("future", tt > t);
      el.classList.toggle("now", tt === t);
      if (tt === t && !cur) cur = el;
    }
    $("#stats").innerHTML = `<span class="stat muted">${p.phase === "chat" ? "agents take turns posting on the board; ready() ends the chat"
      : p.phase === "debrief" ? "each agent alone: sees the round result and may rewrite its notepad"
        : p.phase === "reflection" ? "free-text answers to the reflection questions" : ""}</span>`;
    if (cur) talk.scrollTo({ top: cur.offsetTop - talk.offsetTop - 4, behavior: S.playing ? "smooth" : "auto" });
  }
  updateSide();
  const play = $("#play");
  if (play) play.innerHTML = S.playing ? "&#10074;&#10074; Pause" : "&#9654; Play";
}

function feedText(e) {
  const who = e.agent ? `<b style="color:inherit">${esc(e.agent)}</b> ` : "";
  const d = e.data || {};
  switch (e.type) {
    case "pickup": return `${who}picked up ${esc(d.item)} at (${esc(d.at)})`;
    case "putdown": return `${who}put ${esc(d.item)} down at (${esc(d.at)})`;
    case "add_to_pot": return `${who}added ${esc(d.item)} to ${esc(d.pot)} (${d.onions}/3)`;
    case "cook_start": return `${who}${esc(d.pot)} started cooking`;
    case "cook_done": return `${esc(d.pot)} soup is ready`;
    case "plate": return `${who}plated soup from ${esc(d.pot)}`;
    case "serve": return `${who}<b style="color:var(--good)">served a soup +${d.points ?? ""}</b>`;
    case "fail": return `${who}<span style="color:var(--bad)">failed ${esc(d.action || "")}</span>: ${esc(d.reason || "")}`;
    case "compaction": return `${who}context compacted`;
    case "practice_finished": return `${who}finished practice`;
    case "order_new": return `new order #${esc(d.order)}`;
    case "order_missed": return `<span style="color:var(--bad)">order #${esc(d.order)} expired ${d.points ?? ""}</span>`;
    case "cutoff": return `${who}reply had no action`;
    default: return `${who}${esc(e.type)}`;
  }
}

function seek(t, moveMs = 0) {
  S.t = Math.max(0, Math.min(S.phase.hi, t));
  update(moveMs);
  writeHash();
}

function interval() { return (S.phase.game ? 1000 : 1600) / S.speed; }

function play() {
  if (S.playing) return;
  if (S.t >= S.phase.hi) S.t = 0;
  S.playing = true;
  const ms = interval();
  S.timer = setInterval(() => {
    if (S.t >= S.phase.hi) { stop(); update(); return; }
    const from = S.t;
    seek(S.t + 1, Math.min(ms * 0.9, 400));
    if (window.UCAudio) { try { UCAudio.advance(S.season, S.phase, from, S.t, S.speed); } catch (e) { console.warn(e); } }
  }, ms);
  update();
}

function stop() {
  if (S.timer) clearInterval(S.timer);
  S.timer = null;
  if (S.playing) { S.playing = false; const b = $("#play"); if (b) b.innerHTML = "&#9654; Play"; }
}

function toggle() { if (S.playing) stop(); else play(); update(); }

document.addEventListener("keydown", (e) => {
  if (!S.season || !S.phase || !location.hash) return;
  if (e.target.matches("input[type=text], textarea, select")) return;
  if (e.key === " ") {
    if (e.target.closest("button, a, summary")) return;   // let Space press the focused control
    e.preventDefault(); toggle();
  }
  else if (e.key === "ArrowRight") { e.preventDefault(); stop(); seek(S.t + (e.shiftKey ? 10 : 1)); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); stop(); seek(S.t - (e.shiftKey ? 10 : 1)); }
  else if (e.key === "n" || e.key === "N") stepPhase(1);
  else if (e.key === "p" || e.key === "P") stepPhase(-1);
});

route();
