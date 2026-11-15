# Undercooked

LLM agents play a cooperative kitchen game and talk on a shared board; see CLAUDE.md for the
commands and `configs/template.yaml` for every option.

## Models

Agents are Inspect model names. Most runs go through OpenRouter (`openrouter/<author>/<model>`,
`OPENROUTER_API_KEY` in `.env`).

### Gemini (Google's API)

1. Put `GOOGLE_API_KEY=...` in `.env` (key from https://aistudio.google.com/apikey).
2. Install the optional extra: `uv sync --extra gemini` (adds `google-genai`; the default install
   is unchanged).
3. Run `uv run undercooked run configs/gemini_example.yaml`, or any config with
   `--model google/gemini-2.5-flash` (self-play) or `--agents "google/gemini-2.5-flash,openrouter/deepseek/deepseek-v4.1-flash@baidu/fp8"` (mixed team).

`google/...` models take no `provider` pin. `decision.reasoning_tokens` is the Gemini thinking
budget and `reasoning: off` sets it to 0; thinking-only models (e.g. `gemini-2.5-pro`) cannot turn
thinking off, so write `google/gemini-2.5-pro!reasoning`.

No new key? Use Gemini through OpenRouter: `openrouter/google/gemini-2.5-flash` (the usual
OpenRouter endpoint pinning and `!reasoning` rules apply).

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

Live at https://theosdoor.github.io/durhack26/ (GitHub Pages: deploy from branch `main`, folder
`/docs`).

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
voiced, and `docs/audio/<season>/clips.json` the original and the spoken text. Seasons with no
ElevenLabs clips are read by the browser's own text-to-speech when Voices is on (never mixed
within a season); `docs/audio/browser-highlights.json` lists the browser-voiced highlights.
