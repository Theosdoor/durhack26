/* Undercooked replay site: sound. Everything is synthesized with the Web Audio API (no audio
   files, no network) except optional voice clips listed in audio/manifest.json.

   Three toggles, remembered in localStorage:
   - music (default off): an original café-musette waltz (3/4): musette accordion in A minor,
     then a Neapolitan mandolin-tremolo half in C major; plays only while a replay is playing,
     at its own tempo whatever the replay speed, with a volume slider (also remembered);
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

   Browser voices: a season with NO entry in the manifest (no key under "posts", not a highlight)
   is read aloud by the browser's own speech synthesis (speechSynthesis) when Voices is on. The two
   sources are never mixed within a season: any ElevenLabs entry means ElevenLabs only (posts without
   a clip stay silent). Each agent seat gets a fixed voice (spread over the installed English voices)
   plus its own pitch and rate. Posts are cleaned of markdown and cut to ~300 characters. Queue policy
   while playing: up to 2x speed a post is spoken as it appears; during the live game at 1x a new post
   waits for the current one to finish (only the latest waiting post is kept), at 2x it replaces the one
   being spoken; above 2x nothing is spoken. In chat and other turn-based phases the playhead waits for
   the speech to end (UCAudio.holdClock). Speech is cancelled on pause, seek, phase/season change, leaving
   the replay and Voices off. Without speechSynthesis everything stays silent. Browser-voice highlights
   (docs/audio/browser-highlights.json) use UCAudio.speakText(), which resolves when the speech ends.

   Test mode: add ?sfxlog to the URL: every sound call is console.logged as "[sfx] <name> ..."
   and pushed to window.__sfxLog. */
"use strict";

(function () {
  const store = {
    get(k, d) { try { const v = localStorage.getItem("uc-audio-" + k); return v === null ? d : v === "1"; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("uc-audio-" + k, v ? "1" : "0"); } catch { /* private mode */ } },
  };
  const on = { music: store.get("music", false), voices: store.get("voices", false), sfx: store.get("sfx", true) };
  // music volume, 0..1 on the slider (squared, so the low end is fine-grained); 0.6 is the default
  const MUSIC_MAX = 0.5;
  let musicVol = (() => { try { const v = parseFloat(localStorage.getItem("uc-audio-musicvol")); return v >= 0 && v <= 1 ? v : 0.6; } catch { return 0.6; } })();
  const musicGain = () => MUSIC_MAX * musicVol * musicVol;
  const LOG = /[?&]sfxlog\b/.test(location.search);
  window.__sfxLog = []; window.__musicLog = [];
  let ctx = null, master = null, sfxBus = null, musicBus = null;
  let inReplay = false, playing = false;   // music plays only while a replay is open AND playing

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try {
        ctx = new AC();
        master = ctx.createGain(); master.gain.value = 0.9; master.connect(ctx.destination);
        sfxBus = ctx.createGain(); sfxBus.gain.value = 0.35; sfxBus.connect(master);
        musicBus = ctx.createGain(); musicBus.gain.value = musicGain(); musicBus.connect(master);
      } catch { ctx = null; return null; }
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }
  // browsers allow audio only after a user gesture: start (or resume) the context on the first one
  for (const ev of ["pointerdown", "keydown"]) {
    document.addEventListener(ev, () => { ensureCtx(); if (musicWanted()) startMusic(); }, { capture: true });
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
  // An original café-musette waltz written for this site (3/4, 160 bpm), in two halves:
  //  A (Paris): A minor, musette accordion (three reeds tuned apart, so they beat), oom-pah-pah bass.
  //  B (Napoli): C major, mandolin tremolo melody over the same waltz, tambourine jingles.
  const BPM = 160, EIGHTH = 60 / BPM / 2, STEPS = 6;   // a bar = 6 eighths
  const CH = { Am: [45, 40, [57, 60, 64]], Dm: [38, 45, [57, 62, 65]], E7: [40, 47, [56, 59, 62, 64]],
    C: [36, 43, [55, 60, 64]], G7: [43, 38, [55, 59, 62, 65]], F: [41, 48, [57, 60, 65]] };
  const PROG = ["Am", "Dm", "E7", "Am", "Am", "Dm", "E7", "Am",      // A: Paris
    "C", "G7", "G7", "C", "F", "C", "G7", "C"];                    // B: Napoli
  // melody: [bar, eighth (0-5), midi, eighths long]
  const MELODY = [
    [0, 0, 76, 2], [0, 2, 75, 1], [0, 3, 76, 1], [0, 4, 81, 2],
    [1, 0, 77, 3], [1, 3, 76, 1], [1, 4, 74, 2],
    [2, 0, 71, 1], [2, 1, 72, 1], [2, 2, 74, 1], [2, 3, 76, 1], [2, 4, 80, 2],
    [3, 0, 81, 4], [3, 4, 76, 2],
    [4, 0, 72, 2], [4, 2, 71, 1], [4, 3, 72, 1], [4, 4, 76, 2],
    [5, 0, 77, 2], [5, 2, 81, 2], [5, 4, 77, 2],
    [6, 0, 76, 1], [6, 1, 74, 1], [6, 2, 72, 1], [6, 3, 71, 1], [6, 4, 68, 2],
    [7, 0, 69, 4], [7, 4, 64, 1], [7, 5, 68, 1],
    [8, 0, 79, 3], [8, 3, 76, 3],
    [9, 0, 77, 3], [9, 3, 74, 3],
    [10, 0, 71, 2], [10, 2, 74, 2], [10, 4, 77, 2],
    [11, 0, 76, 6],
    [12, 0, 77, 2], [12, 2, 81, 2], [12, 4, 84, 2],
    [13, 0, 84, 3], [13, 3, 79, 3],
    [14, 0, 77, 2], [14, 2, 76, 2], [14, 4, 74, 2],
    [15, 0, 72, 4], [15, 4, 71, 1], [15, 5, 75, 1]];
  const NAPOLI = (bar) => bar >= 8;
  let musicTimer = null, nextStep = 0, step = 0;

  // accordion: three detuned reeds (musette tuning) through a soft low-pass, with bellows swell
  function accordion(n, t, dur, gain) {
    const f = ctx.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = 2600; f.Q.value = 0.8;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.03);
    g.gain.setValueAtTime(gain, t + Math.max(0.04, dur - 0.06));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    f.connect(g); g.connect(musicBus);
    for (const [ratio, type, lvl] of [[1, "sawtooth", 0.5], [1.0075, "sawtooth", 0.35], [0.9926, "square", 0.25]]) {
      const o = ctx.createOscillator(), og = ctx.createGain();
      o.type = type; o.frequency.setValueAtTime(midi(n) * ratio, t); og.gain.value = lvl;
      o.connect(og); og.connect(f); o.start(t); o.stop(t + dur + 0.05);
    }
  }
  // mandolin: a pair of plucked, slightly detuned strings, repeated as a tremolo
  function mandolin(n, t, dur, gain) {
    const step = EIGHTH / 3;
    for (let x = 0; x < dur - 0.01; x += step) {
      const g2 = gain * (x === 0 ? 1 : 0.7);
      tone(midi(n), t + x, step * 1.6, { type: "triangle", gain: g2, bus: musicBus, attack: 0.002 });
      tone(midi(n) * 1.003, t + x + 0.004, step * 1.4, { type: "sawtooth", gain: g2 * 0.18, bus: musicBus, attack: 0.002 });
    }
  }
  function scheduleStep(s, t) {
    const bar = Math.floor(s / STEPS) % PROG.length, e = s % STEPS;
    const [root, fifth, chord] = CH[PROG[bar]];
    if (e === 0) {   // oom: bass alternates root and fifth bar by bar
      const b = bar % 2 ? fifth : root;
      tone(midi(b), t, EIGHTH * 1.8, { type: "triangle", gain: 0.6, bus: musicBus });
      tone(midi(b) * 2, t, EIGHTH * 1.2, { type: "sine", gain: 0.15, bus: musicBus });
    }
    if (e === 2 || e === 4) for (const n of chord) accordion(n - 12, t, EIGHTH * 0.8, 0.045);   // pah, pah
    for (const [b, at, n, len] of MELODY) {
      if (b !== bar || at !== e) continue;
      if (NAPOLI(bar)) mandolin(n, t, EIGHTH * len, 0.2);
      else accordion(n, t, EIGHTH * len * 0.95, 0.11);
    }
    if (NAPOLI(bar) && (e === 2 || e === 4)) noise(t, 0.09, { gain: 0.07, filter: 7000, q: 2, type: "bandpass", bus: musicBus });   // tambourine
    if (!NAPOLI(bar) && e === 0) noise(t, 0.04, { gain: 0.04, filter: 3000, type: "highpass", bus: musicBus });   // brush
  }
  // The waltz keeps its own clock (the audio context's), whatever the replay speed. Playback stops and
  // restarts at every phase change, so stopping waits a moment (a restart cancels it), and a restart
  // within a bar of a stop picks up the old beat grid instead of laying a second one over the notes
  // already scheduled.
  let stopTimer = null;
  function startMusic() {
    if (stopTimer) { clearTimeout(stopTimer); stopTimer = null; }
    const c = ensureCtx();
    if (!c || musicTimer) return;
    if (!(nextStep > c.currentTime && nextStep < c.currentTime + 1)) nextStep = c.currentTime + 0.1;
    musicTimer = setInterval(() => {
      if (c.state !== "running") return;
      while (nextStep < c.currentTime + 0.2) { if (LOG) window.__musicLog.push(nextStep); scheduleStep(step++, nextStep); nextStep += EIGHTH; }
    }, 40);
    if (LOG) console.log("[sfx] music start");
  }
  function stopMusic(now = false) {
    if (stopTimer) { clearTimeout(stopTimer); stopTimer = null; }
    if (!musicTimer) return;
    const halt = () => { stopTimer = null; clearInterval(musicTimer); musicTimer = null; if (LOG) console.log("[sfx] music stop"); };
    if (now) halt(); else stopTimer = setTimeout(halt, 500);
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
  const usesEleven = (m, id) => !!m && (Object.keys(m.posts?.[id] || {}).length > 0 || (m.highlights || []).some((h) => h.season === id));
  async function playVoice(season, post, code, speed = 1, game = true) {
    if (!on.voices || hlMode) return;
    const m = await loadManifest();
    if (!on.voices || hlMode || !playing || !inReplay) return;   // state changed while the manifest loaded
    if (!usesEleven(m, season.id)) { speakPost(season, post, speed, game); return; }   // never mix sources in a season
    const key = postKey(season, post, code);
    const src = m?.posts?.[season.id]?.[key];
    if (LOG) console.log(`[sfx] voice ${key} ${src || "(none)"}`);
    if (!src) return;
    try { if (voice) voice.pause(); voice = new Audio(src); voice.volume = 0.9; await voice.play(); } catch { /* blocked or missing */ }
  }

  /* ---------------------------------------------------------- browser voices (speechSynthesis) */
  const synth = (() => { try { return window.speechSynthesis && typeof SpeechSynthesisUtterance !== "undefined" ? window.speechSynthesis : null; } catch { return null; } })();
  const SEAT_PITCH = [1.0, 0.75, 1.25, 0.9, 1.15, 0.8, 1.1, 0.95];
  const SEAT_RATE = [1.0, 1.06, 0.94, 1.1, 0.98, 1.04, 0.92, 1.08];
  let pool = [], ttsGen = 0, ttsBusy = false, ttsPending = null, ttsDone = null, ttsDog = null;

  function refreshVoices() {
    if (!synth) return;
    let all = [];
    try { all = synth.getVoices() || []; } catch { all = []; }
    const en = all.filter((v) => /^en([-_]|$)/i.test(v.lang || ""));
    const seen = new Set();
    pool = (en.length ? en : all).filter((v) => !seen.has(v.name) && seen.add(v.name))
      .sort((a, b) => (a.lang || "").localeCompare(b.lang || "") || a.name.localeCompare(b.name));
  }
  if (synth) {
    refreshVoices();
    try { synth.addEventListener("voiceschanged", refreshVoices); } catch { synth.onvoiceschanged = refreshVoices; }
  }
  const seatOf = (season, agent) => {
    const i = (season.header?.agents || []).findIndex((a) => a.name === agent);
    return i < 0 ? 0 : i;
  };
  // the voice for a seat: seats are spread over the pool (stride) so neighbours differ
  function voiceFor(season, seat) {
    if (!pool.length) return null;
    const n = Math.max(1, season.header?.agents?.length || 1);
    return pool[(seat * Math.max(1, Math.floor(pool.length / n))) % pool.length];
  }
  // strip what reads badly, cap the length at a word boundary
  function speakable(text) {
    let t = String(text || "")
      .replace(/```[\s\S]*?```/g, " ").replace(/`([^`]*)`/g, "$1")
      .replace(/\*\*?([^*]+)\*\*?/g, "$1").replace(/^\s*#{1,6}\s+/gm, "").replace(/^\s*[-*>]\s+/gm, "")
      .replace(/^\s*[A-Za-z]{1,2}:\s+/, "")                                   // "R: " / "C: " speaker initials
      .replace(/\(\s*(\d+)\s*,\s*(\d+)\s*\)/g, "$1 $2")                    // (3,1) -> 3 1
      .replace(/(?:->|=>|\u2192)/g, ", then ").replace(/&/g, " and ").replace(/~/g, " about ")
      .replace(/(\w)_(\w)/g, "$1 $2").replace(/_/g, " ")
      .replace(/[\u{1F000}-\u{1FAFF}\u2600-\u27BF\uFE0F]/gu, " ")             // emoji and dingbats
      .replace(/[#*=|<>{}\[\]\\^]/g, " ")
      .replace(/([!?.])\1+/g, "$1").replace(/\s+/g, " ").trim();
    if (t.length > 300) {
      const cut = t.slice(0, 300), k = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
      t = k > 150 ? cut.slice(0, k + 1) : cut.slice(0, cut.lastIndexOf(" ") > 150 ? cut.lastIndexOf(" ") : 300);
    }
    return t;
  }
  function ttsCancel() {
    ttsGen++; ttsPending = null; ttsBusy = false;
    if (ttsDog) { clearTimeout(ttsDog); ttsDog = null; }
    const f = ttsDone; ttsDone = null;
    if (synth) { try { synth.cancel(); } catch { /* nothing to cancel */ } }
    if (f) f();
  }
  // speak one item now (caller has cancelled whatever was speaking); done() runs once when it ends
  function utterNow(item, done) {
    const text = speakable(item.text);
    const gen = ++ttsGen;
    if (!text) { if (done) done(); return; }
    const seat = seatOf(item.season, item.agent);
    if (LOG) { console.log(`[sfx] tts ${item.agent}: ${text}`); window.__sfxLog.push({ name: "tts", agent: item.agent, seat, text }); }
    const finish = () => {
      if (gen !== ttsGen) return;               // cancelled or superseded: stale event
      ttsBusy = false; ttsDone = null;
      if (ttsDog) { clearTimeout(ttsDog); ttsDog = null; }
      if (done) done();
      const next = ttsPending; ttsPending = null;
      if (next && on.voices && playing) { ttsGen++; utterNow(next); }   // gen bump: nothing older can fire
    };
    ttsBusy = true; ttsDone = done || null;
    const est = 3000 + text.length * 120;       // watchdog: some browsers never fire end events
    if (!synth) { ttsDog = setTimeout(finish, Math.min(est, 2500 + text.length * 55)); return; }
    try {
      const u = new SpeechSynthesisUtterance(text);
      const v = voiceFor(item.season, seat);
      if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "en-GB";
      u.pitch = SEAT_PITCH[seat % SEAT_PITCH.length]; u.rate = SEAT_RATE[seat % SEAT_RATE.length]; u.volume = 1;
      u.onend = finish; u.onerror = finish;
      ttsDog = setTimeout(finish, est);
      synth.speak(u);
    } catch { finish(); }
  }
  function speakPost(season, post, speed, game) {
    if (!synth || !on.voices || hlMode) return;
    const item = { text: post.text, agent: post.agent, season };
    if (speed > 2) { ttsCancel(); return; }    // too fast to follow: say nothing
    if (!ttsBusy) { utterNow(item); return; }
    if (game && speed > 1) { ttsCancel(); utterNow(item); return; }   // 2x: the newest post replaces the stale one
    ttsPending = item;                          // 1x: wait for the current one; only the latest waiting post survives
  }
  // highlight player: speak a post and resolve when it has ended (or was cancelled)
  function speakText(text, agent, season) {
    return new Promise((resolve) => { ttsCancel(); utterNow({ text, agent, season }, resolve); });
  }
  // app.js asks before ticking a turn-based phase (chat...): wait while a post is being read aloud
  function holdClock(phase, speed) {
    return !!(synth && ttsBusy && on.voices && !hlMode && phase && !phase.game && speed <= 2);
  }

  // highlight player: speak one clip and resolve when it ends (or fails, or after 90 s)
  function speak(src) {
    return new Promise((resolve) => {
      stopSpeak();
      const a = new Audio(src);
      let done = false;
      const fin = () => { if (done) return; done = true; clearTimeout(to); if (voice === a) voice = null; if (spkFin === fin) spkFin = null; resolve(); };
      const to = setTimeout(fin, 90000);
      spkTo = to;
      a.addEventListener("ended", fin); a.addEventListener("error", fin);
      a.volume = 0.95; voice = a; spkFin = fin;
      if (LOG) { console.log(`[sfx] speak ${src}`); window.__sfxLog.push({ name: "speak", src }); }
      a.play().catch(fin);
    });
  }
  // highlight pause: hold the ElevenLabs clip where it is; browser speech is cut (the player says that post again)
  let spkTo = null;
  function pauseSpeak() {
    if (voice) { try { voice.pause(); } catch { /* gone */ } if (spkTo) { clearTimeout(spkTo); spkTo = null; } }
    ttsCancel();
  }
  function resumeSpeak() {
    if (voice && spkFin) { const f = spkFin; spkTo = setTimeout(f, 90000); voice.play().catch(f); }
  }
  function stopSpeak() { ttsCancel(); const f = spkFin; if (voice) { try { voice.pause(); } catch { /* gone */ } } if (f) f(); }
  function setHighlight(v) { hlMode = !!v; if (!v) stopSpeak(); }

  addEventListener("pagehide", () => { if (synth) { try { synth.cancel(); } catch { /* gone */ } } });

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
      playVoice(season, r, p.code, speed, !!p.game);
    }
  }

  function svgIcon(kind, active) {
    const wave = active ? '<path d="M15 9a4 4 0 0 1 0 6M17.5 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
      : '<path d="M16 9l5 6M21 9l-5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>';
    return `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9h4l5-4v14l-5-4H3z" fill="currentColor"/>${wave}</svg>`;
  }
  const LABEL = { music: "Music", voices: "Voices", sfx: "Effects" };
  const HELP = { voices: " (ElevenLabs voices on narrated seasons; every other season is read aloud by your browser's own, more robotic, voice)" };
  const tip = (k) => `${LABEL[k]}: ${on[k] ? "on" : "off"}${HELP[k] || ""}`;
  function controlsHTML() {
    return `<div class="sound" role="group" aria-label="Sound">${Object.keys(LABEL).map((k) =>
      `<button type="button" class="ghost snd" data-snd="${k}" aria-pressed="${on[k]}" title="${tip(k)}">${svgIcon(k, on[k])}<span>${LABEL[k]}</span></button>` +
      (k === "music" ? `<input type="range" class="vol" id="music-vol" min="0" max="1" step="0.05" value="${musicVol}" aria-label="Music volume" title="Music volume">` : "")).join("")}</div>`;
  }
  function bind(root) {
    const vol = root.querySelector("#music-vol");
    if (vol) vol.addEventListener("input", () => {
      musicVol = Math.max(0, Math.min(1, Number(vol.value)));
      try { localStorage.setItem("uc-audio-musicvol", String(musicVol)); } catch { /* private mode */ }
      if (musicBus) musicBus.gain.setTargetAtTime(musicGain(), ctx.currentTime, 0.05);
    });
    root.querySelectorAll("button[data-snd]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.snd;
      on[k] = !on[k];
      store.set(k, on[k]);
      ensureCtx();
      if (k === "music") { if (musicWanted()) startMusic(); else stopMusic(true); }
      if (k === "voices" && on.voices) loadManifest();
      if (k === "voices" && !on.voices) { if (voice) voice.pause(); ttsCancel(); }
      b.setAttribute("aria-pressed", String(on[k]));
      b.title = tip(k);
      b.innerHTML = `${svgIcon(k, on[k])}<span>${LABEL[k]}</span>`;
    }));
  }

  function musicWanted() { return on.music && inReplay && playing; }
  function syncMusic() { if (musicWanted() && ctx) startMusic(); else stopMusic(); }
  function setReplay(v) { inReplay = !!v; if (!inReplay) { playing = false; if (!hlMode) ttsCancel(); stopMusic(true); } syncMusic(); }
  function setPlaying(v) { playing = !!v; if (playing) ensureCtx(); else if (!hlMode) ttsCancel(); syncMusic(); }
  const api = { setReplay, setPlaying, advance, controlsHTML, bind, postKey, play, speak, speakText, stopSpeak, pauseSpeak, resumeSpeak, holdClock, canSpeak: !!synth, usesEleven: (id) => usesEleven(manifest, id), setHighlight, highlights: [], state: on };
  window.UCAudio = api;
})();
