# Undercooked: notes for agents working in this repo

LLM agents play a cooperative kitchen game (Overcooked-like rules, our own engine) and talk on a
shared board. Seasons run as Inspect AI samples; logs are `.eval` files; a Streamlit viewer replays them.
The design lives in the "Implementation plan" tab of the team's Claude Doc (summarised in README.md).

## Commands
- Tests (the gate; fast, no network): `uv run pytest -q`
- Run a season: `uv run undercooked run configs/split_room.yaml` (every option, with its default:
  configs/template.yaml; several levels: `levels:` in the config or `--levels a,b`)
- Bot tuning per level: `uv run python analysis/bot_tuning.py`
- Viewer: `uv run undercooked view logs/<file>.eval`
- Always `uv run`, never bare `python`.

## Contracts (don't change without updating every reader)
- `undercooked/config.py`: run config (YAML in `configs/`).
- `undercooked/replay.py`: replay records. Writers log them with
  `transcript().info(rec.model_dump(mode="json"), source="kitchen")`; the viewer reads `Replay`.
- `levels/*.txt`: `X` counter, ` ` floor, `O` onion crate, `D` dish rack, `P` pot,
  `S` serving hatch, `1`..`9` start positions. Coordinates are (x=column, y=row), origin top-left.
  levels/README.md lists them and credits the Overcooked-AI layouts.

## Rules of the game (MVP)
1 tick = 1 s = 1 tile, no diagonals. Interactions take 1 s. Per tick: interactions first (every
give, then every take), then movement (if two agents would collide or swap, neither moves), then
pots cook, then orders update. No agent index has priority: other ties use a per-second order from
the game's seeded RNG. Seats (start digits) and each chat's first speaker are seeded per season.
Onion soup = 3 onions in a pot, cooks `cook_s` seconds by itself, plate with a dish, serve at the hatch.

## Never
- Never print or commit `.env` or API keys. Real API calls cost money: tests must use
  Inspect's `mockllm/model` or scripted bots, never OpenRouter.
