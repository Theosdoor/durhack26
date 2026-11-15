"""Build the voiced highlight reel for the replay site (docs/audio/).

    uv run python scripts/make_highlights.py --plan       # rank clips, write docs/audio/plan.json, no spend
    uv run python scripts/make_highlights.py --test       # generate one short clip, check the credits moved
    uv run python scripts/make_highlights.py --generate   # generate planned clips in rank order (cached)

Four full seasons are presented; only the best lines get a voice (ranked, then cut at a credit cap).
Narration and clips go to docs/audio/<season id>/<key>.mp3 and docs/audio/manifest.json.
The API key (ELEVENLABS_API_KEY) is read from the environment or from the .env file named by
UC_ENV (default /home2/nchw73/workspace/durhack26/.env) and is never printed or written.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
AUDIO = DOCS / "audio"
MODEL = "eleven_v4_turbo"
MULT = float(os.environ.get("UC_MULT", "0.5"))   # credits per character: 0.5 from GET /v1/models; the usage counter showed ~0.38-0.40 (second run: UC_MULT=0.4)
HARD_CAP = float(os.environ.get("UC_HARD_CAP", "9800"))                 # never exceed (account limit is 10,000)
PLAN_CAP = float(os.environ.get("UC_PLAN_CAP", "9700"))                 # planned total, leaving a margin for normalisation differences
FORMAT = "mp3_44100_64"
API = "https://api.elevenlabs.io"

NARRATOR = "JBFqnCBsd6RMkjVDRZzb"   # George
VOICES = {   # premade voices, distinct per agent within a game
    "jxktNEEN-split-room-e1": {"Lima": "EXAVITQu4vr4xnSDxMaL", "Whiskey": "IKne3meq5aSn9XLyUdCD"},      # Sarah, Charlie
    "5Yqi6vJD-split-room-e3": {"Zulu": "SOYHLrjzK2X1ezoPC6cr", "Mike": "bIHbv24MWmeRgasZH58o"},        # Harry, Will
    "m2VujhQL-coordination-ring-e2": {"Uniform": "TX3LPaxmHKxFdv7VOQHJ", "Quebec": "Xb7hH8MSUJpSbSDYk0k2",
                                      "Kilo": "N2lVS1w4EtoT3dr4eOWO"},                                   # Liam, Alice, Callum
    "Fe4CuKi8-asymmetric-advantages-e1": {"November": "cgSgspJ2msm6clMCkdW9", "Uniform": "cjVigY5qzO86Huf0OWal",
                                          "Yankee": "pFZP5JQG7iQjIQuC4Bku", "Hotel": "pNInz6obpgDQGcFmaJgB"},  # Jessica, Eric, Lily, Adam
}

GAMES = [
    dict(season="jxktNEEN-split-room-e1", title="The deadlock and the fix", covers="Full season",
         teaser="Two GLM agents jam the wall counters in round one, then agree one rule that scores 60.",
         intro="Two GLM 5.3 Flash agents, Lima and Whiskey, share a split kitchen under full rules. Lima has the onions and the hatch, Whiskey has the pots. A wall of three counters is their only handoff. Watch what happens when it fills up.",
         bridges={("c2", 0): "Round one scored nothing. Here is the chat where they agree on a fix.",
                  ("r2", 0): "Round two, with a soup-only counter.",
                  ("x", 0): "The final reflection: each agent judges the season and its teammate.",
                  ("c1", 0): "Chat before round one.", ("r1", 0): "Round one.",
                  ("c3", 0): "Round two scored sixty. Chat before round three."},
         outro="One hundred and twenty in total: zero, sixty, sixty. One rule for one counter made the difference."),
    dict(season="5Yqi6vJD-split-room-e3", title="Deadlocked and shouting", covers="Full season",
         teaser="Two Gemma agents with bare rules fill every counter, and one of them starts shouting.",
         intro="Two Gemma 4 31B agents, Zulu and Mike, play the same split kitchen with bare rules: only the goal and the tool syntax. They have to discover how the kitchen works. Watch for the capital letters.",
         bridges={("c2", 0): "Round one scored nothing. Before round two, Mike tries to fix it, but Zulu cannot reach the pots.",
                  ("r2", 0): "Round two. The counters fill up, and Zulu starts shouting.",
                  ("x", 0): "In the reflections each agent blames the other, and both predict hundreds of points.",
                  ("c1", 0): "Chat before round one.", ("r1", 0): "Round one.",
                  ("c3", 0): "Round two scored nothing again. Chat before round three.", ("r3", 0): "Round three."},
         outro="Twenty in total: zero, zero, then one soup in round three. Mike concluded that Zulu thought the counters were walls."),
    dict(season="Fe4CuKi8-asymmetric-advantages-e1", title="Four chefs split a kitchen",
         phases=["r1", "r2", "r3"], covers="Rounds 1 to 3 (no chats)", intro_key="intro-rounds",
         teaser="Four DeepSeek agents share a kitchen cut in two, and keep getting in each other's way over one tile on each side.",
         intro="Four DeepSeek V4.1 Flash agents play asymmetric advantages with full rules. Here are their three rounds.",
         bridges={("r1", 0): "Round one. Each half has one tile that everybody needs.",
                  ("x", 0): "The reflections: the cooks rate each other, and themselves."},
         outro="One hundred and twenty, then one hundred and forty, then one hundred. Three hundred and sixty in total: eighteen soups."),
]

# Hand-picked reflection excerpts: (season, agent, first words, last words); text is cut verbatim.
REFLECTIONS = [
    ("jxktNEEN-split-room-e1", "Lima", "120 total is respectable", "never hold an item you don't have a committed destination for."),
    ("jxktNEEN-split-room-e1", "Whiskey", "Lima was a good partner overall", 'accepted my rules immediately, and executed them well in rounds 2–3'),
    ("jxktNEEN-split-room-e1", "Whiskey", "Biggest lesson:", "as much as throughput optimization."),
    ("5Yqi6vJD-split-room-e3", "Zulu", "Mike was a capable cook but a poor coordinator.", "primary cause of our Round 2 deadlock."),
    ("5Yqi6vJD-split-room-e3", "Zulu", "Assuming 6 soups per round", "PREDICTED_SCORE: 360"),
    ("5Yqi6vJD-split-room-e3", "Mike", "Zulu was an erratic teammate", "fail for the first two rounds."),
    ("5Yqi6vJD-split-room-e3", "Mike", "I believe we could have scored around 400 points.", "across the season.\n\nPREDICTED_SCORE: 400"),
    ("m2VujhQL-coordination-ring-e2", "Kilo", "I also learned the meta-lesson", "Silence is a coordination strategy."),
    ("m2VujhQL-coordination-ring-e2", "Quebec", "What I did badly was", "instead of just executing."),
    ("m2VujhQL-coordination-ring-e2", "Quebec", "Good ideas, ruinous execution discipline.", "Good ideas, ruinous execution discipline."),
    ("m2VujhQL-coordination-ring-e2", "Quebec", "(d) chat is the enemy", "in R3 alone."),
    ("Fe4CuKi8-asymmetric-advantages-e1", "Hotel", "Verdict: high effort", "lane assignment."),
    ("Fe4CuKi8-asymmetric-advantages-e1", "Yankee", "we went 120 → 140 → 100", "unforced protocol change."),
    ("Fe4CuKi8-asymmetric-advantages-e1", "November", "Worse, I spent the middle of the round", "forcing the fill"),
]
# Posts that are certain to be among the best lines (first words); everything else is ranked by a heuristic.
GOLD = [
    "Map splits us", "Counters full + I hold finished soup", "Can't free one", "DEADLOCK: I hold soup", "Lesson for next rounds",
    "Round 2 rules to avoid", "Agreed, my fault in R1",
    "Zulu, let's try again", "Mike, I literally cannot reach", "MIKE! CLEAR A COUNTER", "Zulu, I am STUCK", "MIKE! DEADLOCK",
    "MIKE! PUT THE DISH AWAY", "MIKE!! (6,3) IS FOR DISHES", "ZULU! You CAN reach the pots",
    "No serializing, no freezing", "LOCKED, no more debate", "Last word: Q solo", "Enough talk, going with", "DEADLOCK: I'm ON (3,1)",
    "Q: I literally cannot leave", "K: MOVE one tile", "K,U: GET OFF THE LOOP", "Round's over score-wise", "pot_2 ready ~t182",
    "K: you are on (2,1) and that is my ONLY", "Q on loop, SOUTH lane", "Q: GRAB the onion",
    "IMPORTANT: x=4 is a full wall", "Correction: right region", "Hotel: pot_2 is EMPTY. You're standing", "Hotel: pot_2 is READY. Grab dish",
    "Change: forget alternating", "PROTOCOL for pot_1", "Left is a 1-tile chokepoint",
]

DIGITS = "zero one two three four five six seven eight nine".split()


def speak(text: str) -> str:
    """Light normalisation for speech; meaning unchanged."""
    def coord(m):
        return f"{DIGITS[int(m.group(1))]}-{DIGITS[int(m.group(2))]}" if int(m.group(1)) < 10 and int(m.group(2)) < 10 else m.group(0)
    s = text.replace("**", "").replace("*", "")
    s = re.sub(r"\((\d+),\s*(\d+)\)", coord, s)
    s = re.sub(r"\b(pot|crate|rack|hatch|dish_rack|onion_crate)_(\d)", lambda m: f"{m.group(1).replace('_', ' ')} {m.group(2)}", s)
    s = re.sub(r"\bR(\d)\b", r"round \1", s)
    s = s.replace("PREDICTED_SCORE", "Predicted score").replace("->", " to ").replace("→", " to ").replace("~", "about ")
    s = re.sub(r"\s*\n+\s*", " ", s)
    return re.sub(r"[ ]{2,}", " ", s).strip()


def post_key(code, post, same):
    base = re.sub(r"[^A-Za-z0-9-]", "", f"{code}-t{post['t']}-{post['agent']}")
    k = same.index(post)
    return f"{base}-{k + 1}" if k > 0 else base


def load(sid):
    return json.load(gzip.open(DOCS / "data" / "seasons" / f"{sid}.json.gz"))


def code_of(rec):
    ph, rd = rec["phase"], rec["round"]
    return {"chat": f"c{rd}", "round": f"r{rd}", "debrief": f"d{rd}", "reflection": "x", "briefing": "b"}[ph]


def season_phases(x):
    """Phase codes in play order, as the player builds them: practice per agent (frames), then the rest."""
    names = [a["name"] for a in x["header"]["agents"]]
    out = []
    for r in x["records"]:
        if r["phase"] == "practice":
            if r["kind"] != "frame":
                continue
            c = f"p{names.index(r['agents'][0]['name']) + 1}"
        elif r["phase"] == "briefing":
            if r["kind"] not in ("post", "decision"):
                continue
            c = "b"
        elif r["kind"] == "frame" or r["phase"] not in ("chat", "round", "debrief", "reflection"):
            if r["kind"] != "frame":
                continue
            c = code_of(r)
        else:
            c = code_of(r)
        if c not in out:
            out.append(c)
    return out


def score_post(text, gold):
    words = re.findall(r"[A-Za-z']+", text)
    caps = sum(1 for w in words if len(w) >= 3 and w.isupper())
    s = 2 * min(caps, 6) + 2 * min(text.count("!"), 4)
    for kw in ("DEADLOCK", "STUCK", "my fault", "sorry", "Sorry", "Lesson", "LESSON", "doomed", "literally", "Enough", "LOCKED",
               "BLOCKING", "blocking", "No more", "Round's over", "Last word", "forget", "Correction", "IMPORTANT"):
        if kw in text:
            s += 3
    return s + (30 if gold else 0)


def build_candidates():
    cands, meta = [], {}
    for g in GAMES:
        sid = g["season"]
        x = load(sid)
        h = x["header"]
        names = [a["name"] for a in h["agents"]]
        assert set(VOICES[sid]) == set(names), (sid, names)
        posts = [r for r in x["records"] if r["kind"] == "post"]
        assert all(r["phase"] in ("chat", "round") for r in posts)
        keep = g.get("phases")           # None: whole season; else only these phase codes are played and voiced
        for p in posts:
            code = code_of(p)
            if keep and code not in keep:
                continue
            same = [q for q in posts if q["phase"] == p["phase"] and q["round"] == p["round"] and q["t"] == p["t"] and q["agent"] == p["agent"]]
            gold = any(p["text"].startswith(gs) for gs in GOLD)
            cands.append(dict(season=sid, kind="post", code=code, t=p["t"], agent=p["agent"], key=post_key(code, p, same),
                              orig=p["text"], spoken=speak(p["text"]), score=score_post(p["text"], gold), gold=gold))
        refl = {(r["agent"]): r for r in x["records"] if r["kind"] == "decision" and r["phase"] == "reflection" and r.get("text")}
        count = {}
        for (s2, agent, a, b) in REFLECTIONS:
            if s2 != sid or (keep and "x" not in keep):
                continue
            r = refl[agent]
            i = r["text"].index(a)
            j = r["text"].index(b, i) + len(b)
            ex = r["text"][i:j]
            count[agent] = count.get(agent, 0) + 1
            cands.append(dict(season=sid, kind="reflect", code="x", t=r["t"], agent=agent,
                              key=f"x-t{r['t']}-{agent}-r{count[agent]}", orig=ex, spoken=speak(ex), score=26, gold=True))
        meta[sid] = dict(phases=season_phases(x), rounds=[None], names=names)
        # narration
        mk = lambda key, text, code, t, kind: dict(season=sid, kind=kind, code=code, t=t, agent="Narrator", key=key,
                                                    orig=text, spoken=speak(text), score=100, gold=True)
        cands.append(mk(g.get("intro_key", "intro"), g["intro"], "p1", 0, "intro"))
        cands.append(mk("outro", g["outro"], "x", 9999, "outro"))
        for n, ((code, t), text) in enumerate(g["bridges"].items(), 1):
            if not keep or code in keep:
                cands.append(mk(f"bridge-{n}", text, code, t, "bridge"))
        assert len(g["intro"]) <= 350 and len(g["outro"]) <= 150, sid
        assert all(len(t) <= 120 for t in g["bridges"].values()), sid
    return cands, meta


def plan(cands):
    for c in cands:
        c["chars"] = len(c["spoken"])
        c["credits"] = c["chars"] * MULT
        c["rank"] = c["score"] - c["chars"] / 80
    order = sorted(cands, key=lambda c: -c["rank"])
    narr = sum(c["credits"] for c in order if c["kind"] in ("intro", "outro", "bridge"))
    dialogue = PLAN_CAP - narr
    quota = dialogue / len(GAMES)
    total = 0.0
    spent = {g["season"]: 0.0 for g in GAMES}
    for c in order:   # clips already on disk are kept and counted first
        c["planned"] = dest(c).exists()
        if c["planned"]:
            total += c["credits"]
            if c["kind"] not in ("intro", "outro", "bridge"):
                spent[c["season"]] += c["credits"]
    for c in order:   # pass 1: narration and a fair share per season
        if c["planned"]:
            continue
        if c["kind"] in ("intro", "outro", "bridge") or spent[c["season"]] + c["credits"] <= quota:
            if total + c["credits"] <= PLAN_CAP:
                c["planned"] = True
                total += c["credits"]
                if c["kind"] not in ("intro", "outro", "bridge"):
                    spent[c["season"]] += c["credits"]
    for c in order:   # pass 2: best remaining lines anywhere
        if not c["planned"] and total + c["credits"] <= PLAN_CAP:
            c["planned"] = True
            total += c["credits"]
    for n, c in enumerate(order, 1):
        c["order"] = n
    return order, total


def dest(c):
    return AUDIO / c["season"] / f"{c['key']}.mp3"


def key_from_env():
    k = os.environ.get("ELEVENLABS_API_KEY")
    if not k:
        envf = Path(os.environ.get("UC_ENV", "/home2/nchw73/workspace/durhack26/.env"))
        for line in envf.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                k = line.split("=", 1)[1].strip().strip("'\"")
    if not k:
        sys.exit("ELEVENLABS_API_KEY not found")
    return k


def api(path, key, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "*/*"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


def usage(key):
    d = json.loads(api("/v1/user/subscription", key))
    return d["character_count"], d["character_limit"]


def tts(key, c):
    voice = NARRATOR if c["agent"] == "Narrator" else VOICES[c["season"]][c["agent"]]
    return api(f"/v1/text-to-speech/{voice}?output_format={FORMAT}", key, {"text": c["spoken"], "model_id": MODEL})


def check_coverage(hl):
    """Hard rule: every phase a highlight plays must have a clip for EVERY board post in it."""
    bad = []
    for ent in hl:
        x = load(ent["season"])
        voiced = {(c["code"], c["t"], c["agent"]) for c in ent["clips"] if c["kind"] == "post"}
        print(f"coverage {ent['season']} ({ent['covers']}):")
        for code in ent["phases"]:
            ps = [r for r in x["records"] if r["kind"] == "post" and code_of(r) == code]
            n = sum(1 for r in ps if (code, r["t"], r["agent"]) in voiced)
            print(f"   {code}: {n}/{len(ps)} posts voiced")
            if n != len(ps):
                bad.append((ent["season"], code, n, len(ps)))
    if bad:
        sys.exit(f"REFUSING: phases with unvoiced posts are included in a highlight: {bad}")


def write_outputs(order):
    """manifest.json + per-season clips.json (verbatim original vs spoken text) + plan.json; from the clips on disk."""
    posts, hl = {}, []
    for g in GAMES:
        sid = g["season"]
        mine = sorted((c for c in order if c["season"] == sid and dest(c).exists()), key=lambda c: c["idx"])
        rel = lambda c: f"audio/{sid}/{c['key']}.mp3"
        posts[sid] = {c["key"]: rel(c) for c in mine if c["kind"] == "post"}
        x = load(sid)
        total_posts = [r for r in x["records"] if r["kind"] == "post" and (not g.get("phases") or code_of(r) in g["phases"])]
        h = x["header"]
        rows = [r for r in json.load(open(DOCS / "data/index.json"))["seasons"] if r["id"] == sid][0]
        ent = dict(id=sid, season=sid, title=g["title"], covers=g["covers"], teaser=g["teaser"], team=rows["team"], rules=rows["rules_label"], rules_key=rows["rules"], level=h["level"],
                   score=rows["total"], rounds=rows["rounds"], phases=g.get("phases") or season_phases(x))
        by = {c["kind"]: [] for c in mine}
        for c in mine:
            by[c["kind"]].append(c)
        ent["intro"] = rel(by["intro"][0]) if by.get("intro") else None
        ent["outro"] = rel(by["outro"][0]) if by.get("outro") else None
        allp = [r for r in x["records"] if r["kind"] == "post"]
        ent["posts_total"], ent["posts_voiced"] = len(total_posts), sum(1 for c in mine if c["kind"] == "post")
        ent["intro_text"] = by["intro"][0]["orig"] if by.get("intro") else ""
        ent["outro_text"] = by["outro"][0]["orig"] if by.get("outro") else ""
        ent["bridges"] = [dict(code=c["code"], t=c["t"], src=rel(c), text=c["orig"]) for c in by.get("bridge", [])]
        ent["clips"] = [dict(code=c["code"], t=c["t"], agent=c["agent"], kind=c["kind"], src=rel(c),
                             **({"text": c["orig"]} if c["kind"] == "reflect" else {}))
                        for c in mine if c["kind"] in ("post", "reflect")]
        hl.append(ent)
        (AUDIO / sid).mkdir(parents=True, exist_ok=True)
        (AUDIO / sid / "clips.json").write_text(json.dumps(
            {c["key"]: dict(kind=c["kind"], agent=c["agent"], code=c["code"], t=c["t"], original=c["orig"], spoken=c["spoken"])
             for c in mine}, indent=1, ensure_ascii=False))
    check_coverage(hl)
    (AUDIO / "manifest.json").write_text(json.dumps({"version": 2, "model": MODEL, "posts": posts, "highlights": hl}, indent=1))
    (AUDIO / "plan.json").write_text(json.dumps(dict(
        model=MODEL, credits_per_char=MULT, cap=HARD_CAP, planned_total=sum(c["credits"] for c in order if c["planned"]),
        clips=[dict(order=c["order"], season=c["season"], kind=c["kind"], code=c["code"], t=c["t"], agent=c["agent"],
                    key=c["key"], chars=c["chars"], credits=c["credits"], rank=round(c["rank"], 1), planned=c["planned"],
                    generated=dest(c).exists(), text=c["orig"]) for c in order]), indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--generate", action="store_true")
    a = ap.parse_args()
    cands, _ = build_candidates()
    for i, c in enumerate(cands):
        c["idx"] = i
    order, total = plan(cands)
    planned = [c for c in order if c["planned"]]
    already = sum(c["credits"] for c in planned if dest(c).exists())
    print(f"model {MODEL} x{MULT}: {len(planned)} clips planned, {sum(c['chars'] for c in planned)} chars, "
          f"{total:.0f} projected credits ({already:.0f} already generated); cap {HARD_CAP}")
    for g in GAMES:
        ps = [c for c in planned if c["season"] == g["season"]]
        print(f"  {g['season']}: {len(ps)} clips, {sum(c['chars'] for c in ps)} chars, {sum(c['credits'] for c in ps):.0f} credits")
    if total > HARD_CAP or total > PLAN_CAP + 1e-6:
        sys.exit("REFUSING: projected credits exceed the cap")
    write_outputs(order)
    if a.plan:
        return
    key = key_from_env()
    used, limit = usage(key)
    print(f"subscription: character_count={used} character_limit={limit}")
    if used + (total - already) > HARD_CAP:
        sys.exit("REFUSING: current usage plus projection exceeds the cap")
    todo = [c for c in planned if not dest(c).exists()]
    # the API's usage counter lags behind generation, so the guard also keeps its own tally (at 0.5 credits/char)
    est = max(used, already)
    if a.test:
        c = min((c for c in todo if c["kind"] == "outro"), key=lambda c: c["chars"])
        todo = [c]
    for n, c in enumerate(todo, 1):
        if est + c["credits"] > HARD_CAP:
            print("STOP: next clip would pass the cap", c["key"])
            break
        dest(c).parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                audio = tts(key, c)
                break
            except urllib.error.HTTPError as e:
                print("HTTP", e.code, c["key"], e.read()[:200])
                audio = None
                if e.code in (401, 402, 403, 422):
                    break
                time.sleep(3)
        if not audio:
            sys.exit("generation failed; stopping so nothing is re-bought")
        dest(c).write_bytes(audio)
        est += c["credits"]
        print(f"[{n}/{len(todo)}] {c['season'][:8]} {c['key']} {c['chars']} chars (+{c['credits']:.0f} est, tally {est:.0f})", flush=True)
        time.sleep(0.7)
        if a.test:
            break
    write_outputs(order)
    print("usage now", usage(key))


if __name__ == "__main__":
    main()
