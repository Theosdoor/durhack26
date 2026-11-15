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
