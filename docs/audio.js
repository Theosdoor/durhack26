/* Undercooked replay site: sound. Everything is synthesized with the Web Audio API (no audio
   files, no network) except optional voice clips listed in audio/manifest.json.

   Three toggles, remembered in localStorage:
   - music (default off): an original, procedurally scheduled loop: oom-pah bass, accordion-ish
     chord stabs and a marimba-like melody over a short chord progression;
   - voices (default off): plays the mp3 the manifest lists for a board post when that post
     appears during playback; does nothing when there is no manifest or no entry;
   - sfx (default on): short sounds for game events during playback, rate-limited.

   Voice manifest (docs/audio/manifest.json, version 2), every path relative to docs/:
   {
     "version": 2,
     "posts": { "<season id>": { "<post key>": "audio/<season id>/<post key>.mp3" } },
     "highlights": [
       { "id": "<season id>", "season": "<season id>", "title": "...", "teaser": "...", "team": "GLM x2",
         "rules": "full rules", "rules_key": "full", "score": 120, "rounds": [0, 60, 60],
         "phases": ["p1", "p2", "c1", "r1", ...],            // the whole season, in play order
         "intro": "audio/<season id>/intro.mp3", "outro": "audio/<season id>/outro.mp3",
         "bridges": [ { "code": "c2", "t": 0, "src": "audio/..." } ],   // narrator, spoken as that moment begins
         "clips":   [ { "code": "r1", "t": 96, "agent": "Whiskey", "kind": "post|reflect", "src": "audio/..." } ] }
     ]
   }
   A post key is "<phase code>-t<time>-<agent>" (phase codes as in the URL hash: c2, r2, d2, p1),
   with "-2", "-3"... appended when one agent posts more than once at the same time, e.g.
   "r2-t80-Lima". UCAudio.postKey(season, post) computes it. "posts" holds the voiced board posts
   and is what the Voices toggle plays during normal replay. Only the best lines of a season have a
   clip; a post without one plays silently (its text is shown as usual). "reflect" clips are
   excerpts of a reflection answer (phase x, t = turn index). docs/audio/<season id>/clips.json
   gives the original and the spoken text of every clip; docs/audio/plan.json the ranked plan.
   The highlight player (app.js) uses UCAudio.speak(src) / stopSpeak() and setHighlight(on): in
   highlight mode the saved Voices setting is untouched and ignored, so clips never overlap.

   Test mode: add ?sfxlog to the URL: every sound call is console.logged as "[sfx] <name> ..."
   and pushed to window.__sfxLog. */
"use strict";

(function () {
  const store = {
    get(k, d) { try { const v = localStorage.getItem("uc-audio-" + k); return v === null ? d : v === "1"; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("uc-audio-" + k, v ? "1" : "0"); } catch { /* private mode */ } },
  };
  const on = { music: store.get("music", false), voices: store.get("voices", false), sfx: store.get("sfx", true) };
  const LOG = /[?&]sfxlog\b/.test(location.search);
  window.__sfxLog = [];
  let ctx = null, master = null, sfxBus = null, musicBus = null;
  let inReplay = false;   // music plays only while a replay is open, never on the home page

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try {
        ctx = new AC();
        master = ctx.createGain(); master.gain.value = 0.9; master.connect(ctx.destination);
        sfxBus = ctx.createGain(); sfxBus.gain.value = 0.35; sfxBus.connect(master);
        musicBus = ctx.createGain(); musicBus.gain.value = 0.10; musicBus.connect(master);
      } catch { ctx = null; return null; }
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }
  // browsers allow audio only after a user gesture: start (or resume) the context on the first one
  for (const ev of ["pointerdown", "keydown"]) {
    document.addEventListener(ev, () => { ensureCtx(); if (on.music && inReplay) startMusic(); }, { capture: true });
  }

  /* ---------------------------------------------------------- building blocks */
  function tone(freq, t0, dur, { type = "sine", gain = 0.5, bus = sfxBus, attack = 0.005, slideTo = null } = {}) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t0);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(gain, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(bus);
    o.start(t0); o.stop(t0 + dur + 0.02);
  }
  function noise(t0, dur, { gain = 0.3, filter = 2000, q = 0.7, type = "bandpass", bus = sfxBus } = {}) {
    const len = Math.max(1, Math.floor(ctx.sampleRate * dur));
    const buf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = buf; f.type = type; f.frequency.value = filter; f.Q.value = q;
    g.gain.setValueAtTime(gain, t0); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f); f.connect(g); g.connect(bus);
    src.start(t0); src.stop(t0 + dur + 0.02);
  }
  const midi = (n) => 440 * Math.pow(2, (n - 69) / 12);

  /* ---------------------------------------------------------- sound effects */
  const SFX = {
    onion(t) { tone(330, t, 0.12, { type: "triangle", gain: 0.5, slideTo: 140 }); noise(t + 0.02, 0.08, { gain: 0.15, filter: 900 }); },
    cook(t) { noise(t, 0.7, { gain: 0.22, filter: 5000, q: 0.4, type: "highpass" }); tone(110, t, 0.25, { type: "sine", gain: 0.2 }); },
    ready(t) { tone(1568, t, 0.9, { gain: 0.35 }); tone(2093, t + 0.004, 0.7, { gain: 0.12 }); },
    plate(t) { tone(1200, t, 0.08, { type: "square", gain: 0.08 }); tone(1800, t + 0.03, 0.12, { gain: 0.15 }); },
    serve(t) { [72, 76, 79, 84].forEach((n, i) => tone(midi(n), t + i * 0.07, 0.35, { type: "triangle", gain: 0.35 })); },
    fail(t) { tone(150, t, 0.16, { type: "sine", gain: 0.45, slideTo: 70 }); noise(t, 0.06, { gain: 0.1, filter: 300, type: "lowpass" }); },
    post(t) { tone(520, t, 0.07, { gain: 0.35, slideTo: 980 }); },
  };
  const EVENT_SFX = { add_to_pot: "onion", cook_start: "cook", cook_done: "ready", plate: "plate", serve: "serve",
    fail: "fail", bump: "fail", order_missed: "fail" };
  let recent = [];   // wall-clock times of recent sounds, for the rate limit

  function play(name, detail, speed = 1) {
    if (!on.sfx) return false;
    const now = performance.now();
    recent = recent.filter((x) => now - x < 1000);
    // above 4x only serves sound; never more than 4 sounds a second
    if ((speed > 4 && name !== "serve") || recent.length >= 4) return false;
    recent.push(now);
    window.__sfxLog.push({ name, ...detail });
    if (LOG) console.log(`[sfx] ${name}`, JSON.stringify(detail));
    const c = ensureCtx();
    if (!c || c.state !== "running") return true;
    try { SFX[name](c.currentTime + 0.01); } catch (e) { console.warn("sfx", e); }
    return true;
  }

  /* ---------------------------------------------------------- music: an original loop */
  // 8 bars in F major, 4/4 at 116 bpm: F  Dm  Bb  C  |  F  Am  Bb  C (chord roots, MIDI)
  const BARS = [[53, [65, 69, 72]], [50, [62, 65, 69]], [46, [62, 65, 70]], [48, [64, 67, 72]],
    [53, [65, 69, 72]], [45, [64, 69, 72]], [46, [62, 65, 70]], [48, [64, 67, 70]]];
  // melody: [bar, eighth (0-7), midi, eighths long]; written for this site
  const MELODY = [[0, 0, 77, 1], [0, 1, 79, 1], [0, 2, 81, 2], [0, 5, 79, 1], [0, 6, 77, 2],
    [1, 0, 74, 2], [1, 3, 77, 1], [1, 4, 81, 3],
    [2, 0, 82, 1], [2, 1, 81, 1], [2, 2, 79, 2], [2, 4, 77, 1], [2, 5, 74, 1], [2, 6, 77, 2],
    [3, 0, 76, 3], [3, 4, 79, 1], [3, 5, 76, 1], [3, 6, 72, 2],
    [4, 0, 72, 1], [4, 1, 77, 1], [4, 2, 81, 1], [4, 3, 84, 2], [4, 6, 81, 2],
    [5, 0, 76, 2], [5, 2, 81, 1], [5, 3, 79, 1], [5, 4, 76, 3],
    [6, 0, 74, 1], [6, 1, 77, 1], [6, 2, 82, 2], [6, 4, 81, 1], [6, 5, 79, 1], [6, 6, 77, 2],
    [7, 0, 79, 2], [7, 2, 76, 1], [7, 3, 72, 1], [7, 4, 76, 2], [7, 6, 79, 2]];
  const BPM = 116, EIGHTH = 60 / BPM / 2;
  let musicTimer = null, nextStep = 0, step = 0;

  function scheduleStep(s, t) {
    const bar = Math.floor(s / 8) % BARS.length, e = s % 8;
    const [root, chord] = BARS[bar];
    if (e === 0 || e === 4) tone(midi(e === 0 ? root - 12 : root - 5), t, EIGHTH * 1.6, { type: "triangle", gain: 0.55, bus: musicBus });
    if (e === 2 || e === 6) {   // off-beat "pah": a short, slightly buzzy chord (accordion-ish)
      for (const n of chord) {
        tone(midi(n - 12), t, EIGHTH * 0.9, { type: "sawtooth", gain: 0.06, bus: musicBus, attack: 0.01 });
        tone(midi(n - 12) * 1.004, t, EIGHTH * 0.9, { type: "square", gain: 0.03, bus: musicBus, attack: 0.01 });
      }
    }
    for (const [b, at, n, len] of MELODY) {
      if (b === bar && at === e) {   // marimba-ish: sine plus a quick high partial
        tone(midi(n), t, EIGHTH * len + 0.15, { gain: 0.3, bus: musicBus, attack: 0.003 });
        tone(midi(n) * 4, t, 0.05, { gain: 0.05, bus: musicBus, attack: 0.002 });
      }
    }
    if (e % 2 === 1) noise(t, 0.03, { gain: 0.05, filter: 8000, type: "highpass", bus: musicBus });   // shaker
  }
  function startMusic() {
    const c = ensureCtx();
    if (!c || musicTimer) return;
    nextStep = c.currentTime + 0.1;
    musicTimer = setInterval(() => {
      if (c.state !== "running") return;
      while (nextStep < c.currentTime + 0.2) { scheduleStep(step++, nextStep); nextStep += EIGHTH; }
    }, 40);
    if (LOG) console.log("[sfx] music start");
  }
  function stopMusic() {
    if (musicTimer) clearInterval(musicTimer);
    musicTimer = null;
    if (LOG) console.log("[sfx] music stop");
  }

  /* ---------------------------------------------------------- voices (manifest only) */
  let manifest = null, manifestTried = false, voice = null, hlMode = false, spkFin = null;
  async function loadManifest() {
    if (manifestTried) return manifest;
    manifestTried = true;
    try {
      const res = await fetch("audio/manifest.json");
      if (res.ok) manifest = await res.json();
    } catch { /* no manifest: voices stay silent */ }
    api.highlights = manifest?.highlights || [];
    return manifest;
  }
  function postKey(season, post, code) {
    const base = `${code}-t${post.t}-${post.agent}`.replace(/[^A-Za-z0-9-]/g, "");
    const same = season.posts.filter((p) => p.phase === post.phase && p.round === post.round && p.t === post.t && p.agent === post.agent);
    const k = same.indexOf(post);
    return k > 0 ? `${base}-${k + 1}` : base;
  }
  async function playVoice(season, post, code) {
    if (!on.voices || hlMode) return;
    const m = await loadManifest();
    const key = postKey(season, post, code);
    const src = m?.posts?.[season.id]?.[key];
    if (LOG) console.log(`[sfx] voice ${key} ${src || "(none)"}`);
    if (!src) return;
    try { if (voice) voice.pause(); voice = new Audio(src); voice.volume = 0.9; await voice.play(); } catch { /* blocked or missing */ }
  }

  // highlight player: speak one clip and resolve when it ends (or fails, or after 90 s)
  function speak(src) {
    return new Promise((resolve) => {
      stopSpeak();
      const a = new Audio(src);
      let done = false;
      const fin = () => { if (done) return; done = true; clearTimeout(to); if (voice === a) voice = null; if (spkFin === fin) spkFin = null; resolve(); };
      const to = setTimeout(fin, 90000);
      a.addEventListener("ended", fin); a.addEventListener("error", fin);
      a.volume = 0.95; voice = a; spkFin = fin;
      if (LOG) { console.log(`[sfx] speak ${src}`); window.__sfxLog.push({ name: "speak", src }); }
      a.play().catch(fin);
    });
  }
  function stopSpeak() { const f = spkFin; if (voice) { try { voice.pause(); } catch { /* gone */ } } if (f) f(); }
  function setHighlight(v) { hlMode = !!v; if (!v) stopSpeak(); }

  /* ---------------------------------------------------------- player hooks */
  // called by app.js while playing, after time moved from `from` to `to` in phase `p`
  function advance(season, p, from, to, speed) {
    if (to <= from) return;
    const hit = (r) => r.t > from && r.t <= to;
    if (p.game) {
      for (const e of p.events) {
        if (!hit(e) || !EVENT_SFX[e.type]) continue;
        play(EVENT_SFX[e.type], { type: e.type, t: e.t, agent: e.agent || null }, speed);
      }
    }
    for (const r of p.posts) {
      if (!hit(r)) continue;
      play("post", { type: "post", t: r.t, agent: r.agent }, speed);
      playVoice(season, r, p.code);
    }
  }

  function svgIcon(kind, active) {
    const wave = active ? '<path d="M15 9a4 4 0 0 1 0 6M17.5 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
      : '<path d="M16 9l5 6M21 9l-5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>';
    return `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9h4l5-4v14l-5-4H3z" fill="currentColor"/>${wave}</svg>`;
  }
  const LABEL = { music: "Music", voices: "Voices", sfx: "Effects" };
  function controlsHTML() {
    return `<div class="sound" role="group" aria-label="Sound">${Object.keys(LABEL).map((k) =>
      `<button type="button" class="ghost snd" data-snd="${k}" aria-pressed="${on[k]}" title="${LABEL[k]}: ${on[k] ? "on" : "off"}">${svgIcon(k, on[k])}<span>${LABEL[k]}</span></button>`).join("")}</div>`;
  }
  function bind(root) {
    root.querySelectorAll("button[data-snd]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.snd;
      on[k] = !on[k];
      store.set(k, on[k]);
      ensureCtx();
      if (k === "music") { if (on.music && inReplay) startMusic(); else stopMusic(); }
      if (k === "voices" && on.voices) loadManifest();
      if (k === "voices" && !on.voices && voice) voice.pause();
      b.setAttribute("aria-pressed", String(on[k]));
      b.title = `${LABEL[k]}: ${on[k] ? "on" : "off"}`;
      b.innerHTML = `${svgIcon(k, on[k])}<span>${LABEL[k]}</span>`;
    }));
  }

  function setReplay(v) {
    inReplay = !!v;
    if (!inReplay) stopMusic();
    else if (on.music && ctx) startMusic();
  }
  const api = { setReplay, advance, controlsHTML, bind, postKey, play, speak, stopSpeak, setHighlight, highlights: [], state: on };
  window.UCAudio = api;
})();
