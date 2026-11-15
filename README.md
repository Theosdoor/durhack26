# Undercooked

LLM agents play a cooperative kitchen game (Overcooked-like rules, our own engine) and talk on a
shared board. See CLAUDE.md for commands and the rules of the game.

## Replay site (docs/)

`docs/` is a static replay viewer for every season: a season browser with filters, and a player
with the kitchen, timeline, board, decisions, notepads and reflections. Plain HTML/JS/CSS, no
build step; the data is `docs/data/index.json` plus one gzipped JSON per season.

Regenerate it from a directory of Inspect `.eval` logs (finished logs only; no API calls):

    uv run undercooked site path/to/logs --out docs

Preview locally with `cd docs && uv run python -m http.server`, then open http://localhost:8000.

Deep links use the hash only, so they work from any subpath: `#<season id>` opens a season, and
`#<season id>--<phase>-t<time>` a moment, e.g. `#5Yqi6vJD-split-room-e2--r2-t45` (round 2 at
45 s). Phase codes: `p<k>` practice of agent k, `c<r>` chat before round r, `r<r>` round r,
`d<r>` private turn after round r, `x` reflection; in chat, private turn and reflection the
time is the turn index.

Deploy on GitHub Pages (once the repo is public): Settings → Pages → Source "Deploy from a
branch", branch `main`, folder `/docs`. The site is then served at
`https://<owner>.github.io/<repo>/`.

Sound (toggles in the player, remembered per browser): music (off by default) and sound effects
(on) are synthesized with the Web Audio API. Voices (off) play only clips listed in
`docs/audio/manifest.json`; its format is documented at the top of `docs/audio.js`. Add `?sfxlog`
to the URL to log every sound call.

Speaking: in every replay, when an agent posts, a speech bubble in its colour appears above its
chef (a bar above the transcript in chat, private turns and reflections) and the post is
highlighted on the Board, whatever the sound settings.

Highlight reel: the home page opens with narrated replays: whole seasons or, where credits ran out,
only the rounds in which every board post is voiced (the script refuses to include a phase with an unvoiced post). They are (intro, bridges, outro by a
narrator voice; board posts and some reflection lines voiced per agent). The clips are
ElevenLabs text-to-speech (model `eleven_v4_turbo`, premade voices) generated once by
`uv run python scripts/make_highlights.py` (`--plan`, `--test`, `--generate`; needs
`ELEVENLABS_API_KEY`, which is never written to `docs/`). It is cached and refuses to pass a
credit cap; `docs/audio/plan.json` lists every candidate line, its cost and whether it was
voiced, and `docs/audio/<season>/clips.json` the original and the spoken text.
