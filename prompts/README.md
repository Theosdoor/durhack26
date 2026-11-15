# Agent-facing prompts

Every text the LLM agents read that the engine does not draw lives in these five Markdown files,
grouped by when the agent sees it. Each text is a `## <name>` section; the code
(`undercooked/prompts.py`, `engine/text.py`, `tools.py`) loads the files once, fills the
`{placeholders}` and chooses which section applies to the experimental condition (comms,
reasoning, debrief, board, practice).

## Editing guide

- **The goal sentence is pinned**: the last line of `system.md` section `goal` must equal `GOAL`
  in `tests/test_prompts_and_records.py`. Change both on purpose, or neither.
- **Keep the facts accurate.** The interact table, the action syntax (`interact(<id>)`,
  `move_to(x, y)`, `wait(<seconds>)`) and the object ids (`onion_crate_1`, `pot_1`,
  `counter_2_1`, ...) in `system.md` and `tools.md` describe what the engine really accepts.
  Wording may change; behaviour may not. The example plan in `tools.md` `act.description` must
  stay a valid plan.
- **Placeholders** are `str.format` style (`{me}`, `{round_s}`, ...). Use only those listed
  below for that section; an unknown one fails with an error naming the file and section.
  Literal braces must be doubled (`{{`, `}}`).
- **Plain text only**: no templating beyond `{placeholders}`. Conditions (reasoning on or off,
  board pull or push, comms, "1 turn" vs "2 turns") stay in `prompts.py`, which picks a section.
  Counted nouns arrive finished (`{rounds}` = "3 rounds").
- **Whitespace**: newlines around a section are ignored; spaces inside count. Where the code
  joins two pieces it adds the single space or newline itself, so do not start or end a snippet
  with a space. Text above a file's first `## ` heading is a note for you, never shown to agents.
- Do not rename `## ` headings (the code looks them up) and do not use `## ` inside a text.
- Do not name models or providers in a prompt (a test greps for them).
- After editing run `uv run pytest -q`. `tests/test_prompt_files.py` compares every rendered
  text with `tests/fixtures/prompts_golden.json`. After an INTENTIONAL change, review the diff
  of the rendered texts, then regenerate the golden file with
  `uv run python -m tests.make_prompt_golden` and commit it with the edit.

Examples: 3 rounds of 180 s, 60 s practice, 6 posts per chat, Kilo with teammate Tango.

## `system.md`: the system prompt (once, at the start)

| Section | Used when | Placeholders |
|---|---|---|
| `identity` | rules full/recipe/minimal*, bare, open: always the first line | `{me}` Kilo, `{cooks}` "two" (a word up to nine), `{team}` (`identity.one_mate` or `identity.many_mates`) |
| `identity.one_mate`, `identity.many_mates` | inside `identity`: one other agent / several | `{names}` "Tango" or "Tango, Lima and Golf" |
| `kitchen_rules`, `recipe`, `scoring.orders` / `scoring.soups`, `map_key`, `actions`, `interact_table`, `plans` | rules full (joined as the rules; `scoring.*` by config `scoring`; for the other rules values see the last section) | `{hatch_soup}` (`hatch_soup.orders` or `hatch_soup.soups`, in `interact_table`), `{cook_s}` 20, `{every_s}` 30, `{max_open}` 3, `{due_s}` 60, `{reward}` 20, `{penalty}` 10, `{max_plan_len}` 5, `{wait_min}` 1, `{wait_max}` 10, `{blocked_limit}` 3, `{bin_key}`, `{bin_row}`, `{bin_ids}`, `{bin_kind}` (empty unless config `bins` is true, then the `bin.map_key`, `bin.interact_row`, `bin.ids` and `bin.kind` sections; `map_key`, `interact_table` and `actions` use them) (the ones each uses are visible in its text) |
| `season` | always | `{practice}` (practice sentence + space, or empty), `{us}` "you and Tango" ("you, Tango and Lima"), `{rounds}` "3 rounds", `{round_s}` 180, `{afresh}` (`afresh.orders` or `afresh.soups`), `{between}` (a `between_*` snippet) |
| `hatch_soup.orders`, `hatch_soup.soups` | inside `interact_table`, by config `scoring` | `{reward}` (soups only) |
| `afresh.orders`, `afresh.soups` | inside `season`, by config `scoring` | none |
| `deciding` | always | `{flat}` "2 seconds", `{posting}` (`posting_full` or empty), `{extras}` (`extras_*`), `{thinking_budget}` (space + `thinking_budget`, or empty) |
| (board or chat section) | from `communication.md`, between `deciding` and `notepad`; none for comms: none | |
| `notepad`, `goal` | always | none |
| `practice_intro`, `practice_intro_no_comms` | practice on (the latter when comms: none) | `{practice_s}` 60 |
| `between_chat_private`, `between_chat_score_only`, `between_no_chat_private`, `between_no_chat_score_only` | comms and debrief conditions | none |
| `posting_full`, `extras_full`, `extras_no_post` | comms: full / other | `{post_words_per_s}` 10 (`posting_full` only) |
| `board_read_pull`, `board_read_push` | comms: full, by the board setting | none |
| `thinking_budget` | reasoning: on | `{reasoning_tokens}` 1000 |

## `communication.md`: the board and the pre-round chat

| Section | Used when | Placeholders |
|---|---|---|
| `comms.full` | system prompt, comms: full ("THE BOARD") | `{us}`, `{max_post_chars}` 200, `{board_read}`, `{chat_rules}` |
| `comms.planning_only` | system prompt, comms: planning_only ("THE CHAT") | `{planning_only_rule}`, `{chat_rules}` |
| `comms.none` | system prompt, comms: none; deliberately empty (text here would appear in that place) | none |
| `planning_only_rule` | inside `comms.planning_only` | `{max_post_chars}` |
| `chat_rules` | inside both other comms sections | `{chat_posts}` "6 posts", `{chat_turns}` "8 turns" |
| `chat_turn.first` | an agent's first turn in a chat | `{round}`, `{rounds}`, `{overview}` (drawn map + `kitchen_overview.*` lines), `{orders_rules}` |
| `chat_turn.notepad` | first turn, if the notepad is set | `{notepad}` |
| `chat_turn.later` | later turns | `{round}` |
| `chat_turn.new_posts`, `chat_turn.no_new_posts` | every turn | `{posts}` "[chat] Tango: hi" |
| `chat_turn.ready_prefix` | after the agent called ready() | none |
| `chat_turn.status` | every turn, last line | `{posts_left}` 4 |

## `messages.md`: what is sent during a season

| Section | Used when | Placeholders |
|---|---|---|
| `season_intro.practice` / `.chat` / `.no_chat` | the first user message | none |
| `practice_start.with_comms` / `.no_comms` | first decision of practice | `{practice_s}` |
| `kitchen_overview.start_you` / `.start_mate` | chat overview, under the map | `{name}`, `{letter}`, `{x}`, `{y}` |
| `kitchen_overview.objects`, `.counters` | chat overview | `{objects}`, `{counters}` |
| `kitchen_overview.object_item`, `.counter_item` | list items of the above | `{id}` pot_1, `{x}`, `{y}` |
| `orders_rules` | chat, first turn, only with rules full and scoring orders | `{every_s}`, `{max_open}`, `{due_s}`, `{reward}`, `{penalty}`, `{round_s}` |
| `round_start.base`, `.turn_limit` | first decision of each round (`turn_limit` if the chat hit its cap) | `{round}`, `{rounds}`; `{turns}` "8 turns" |
| `cutoff_notice.notice`, `.post` | before the state after a reply that did not call act | `{seconds}` 4, `{post}` (space + `.post`, or empty); `{post_s}` 3 |
| `forced_why.*` | reason line of a forced-answer prompt (thinking budget used up, or reasoning off) | none |
| `forced.act` / `.chat` / `.debrief` | request of the forced-answer prompt | none |
| `round_result` | debrief: score_only, before the next message | `{round}`, `{rounds}`, `{score}`, `{result}` (`result.soups` "2 soups served" or `result.orders` "2 orders served, 1 missed") |
| `result.soups`, `result.orders` | inside `round_result` / `debrief.result`, by config `scoring` | `{served}` "2 soups"; `{served}`, `{missed}` |
| `debrief.result` | debrief: private (the default; "full" is an alias), after each round including the last | `{round}`, `{rounds}`, `{score}`, `{result}` |
| `debrief.scores_so_far`, `.score_item` | from round 2 | `{scores}` "round 1: 20, round 2: 30"; `{n}`, `{score}` |
| `debrief.notepad`, `.notepad_empty`, `.instruction` | debrief | `{notepad}` |
| `debrief.next_chat`, `.next_round`, `.last` | last line of the private turn (chat before the next round / next round / after the last round) | `{round}` (the next round) |
| `reflection.intro` | after the last round (config `reflection`, default on): one free-text reply per agent, no tools | `{scores}` "round 1: 20, round 2: 30", `{total}` 50 |
| `reflection.questions` | after `reflection.intro`; question 5 asks for `PREDICTED_SCORE: <points>` on the last line, which `prompts.predicted_score` parses | none |
| `reflection.open_question` | after the questions, rules: open only | none |
| `compaction.prompt`, `.posted` | Inspect's summarising prompt (`.posted` omitted when comms: none) | `{posted}`, `{results}` (`compaction.results_soups` "scores, soups served" or `compaction.results_orders`), `{addendums}`: Inspect fills `{addendums}` itself, keep it exactly, at the end |
| `compaction.results_soups`, `.results_orders` | inside `compaction.prompt`, by config `scoring` | none |

## `state.md`: the state at every decision

The map and the order of lines are code. Sections: `header` (`{t}`, `{duration}`, `{score}`,
`{where}`), `where_practice`, `where_round` (`{round}`, `{rounds}`), `orders` (`{orders}`),
`order_item` (`{due_t}`), `orders_none`, `you` (`{name}`, `{letter}`, `{x}`, `{y}`, `{item}`,
`{result}`), `teammate` (same minus `{result}`), `item.nothing|onion|dish|soup`, `pot` (`{id}`,
`{x}`, `{y}`, `{what}`), `pot.ready`, `pot.cooking` (`{ready_in}`), `pot.empty`, `pot.onions`
(`{onions}` "2 onions"), `counters` (`{counters}`), `counter` (`{x}`, `{y}`, `{what}`),
`counter.empty`, `counters_none`, `board_unread` (`{unread}` "2 unread posts"), `board_none`,
`notepad` (`{notepad}`), `push_new_posts` (`{posts}`), `push_none`. The code removes any line
starting with `Board:` when there is no board, so keep that prefix on `board_unread` and
`board_none`.

## `tools.md`: tool descriptions and results

`act.description`, `act.plan_param` (`{max_len}` 5); `act.rejected` (`{error}`); `act.too_long`
(`{n}` 7, `{max_len}`); `post.description`, `post.posted_cut` (`{max_post_chars}`);
`post.not_posted` (`{reason}`); `post.cap_reached` (`{cap}` 6). The rest (`act.ignored`,
`act.accepted`, `post.message_param`, `post.ignored`, `post.empty`, `post.posted`,
`post.lands_later`, `read_board.*`, `note.*`, `ready.*`) have no placeholders.

## Rendered example

`EXAMPLE.md` is generated (not a prompt file, nothing loads it): every message one agent sees in
a short fake season, each piece of text marked with the file and section it comes from. It is
stale after any edit here; regenerate with `uv run python -m analysis.render_prompts` (add
`--plain` to drop the markers, which writes `EXAMPLE.plain.md`).

## Not here

The engine's plan-result words (`Last plan: done`, `failed: <reason>`) and parse errors come from
`engine/game.py` and `engine/actions.py`; the map is drawn by `engine/text.py`; a board post is
formatted `[chat] Name: text` or `[t=12s] Name: text` by `board.py`. These stay in code.

## Sections added on the `levels` branch

Appended at the end of their files (the tables above are unchanged; this list completes them).

| File | Section | Used when | Placeholders |
|---|---|---|---|
| `system.md` | `practice_finish_note` | practice on (after the practice sentence in `season`) | none |
| `system.md` | `rules_recipe`, `rules_minimal`, `rules_minimal_hint` | config `rules` other than full: replace the recipe and orders paragraphs of `kitchen_rules` and the `interact_table` | none |
| `system.md` | `thinking_budget` (changed) | reasoning: on; now "Think briefly, then act." | none (no more `{reasoning_tokens}`) |
| `messages.md` | `forced_notice` | the next decision after a forced answer | `{seconds}` 1 |
| `messages.md` | `round_start.unseen_chat` | comms planning_only: first round message, if the teammate posted after your last chat turn | `{posts}` |
| `tools.md` | `finish_practice.description`, `finish_practice.result` | practice decisions only | none |
| `tools.md` | `act.description_no_recipe` | config `rules` other than full (example plan without the recipe) | `{max_len}` |
| `state.md` | `order_item_minimal` | rules minimal / minimal_hint: the order line names no dish | `{due_t}` |

With `rules` other than full, the chat's first turn leaves out `orders_rules`.

## Sections added for scoring, bare/open rules, the private turn, the reflection and N agents

| File | Section | Used when | Placeholders |
|---|---|---|---|
| `system.md` | `rules_recipe_soups` | rules recipe with scoring soups (`rules_recipe` mentions orders) | none |
| `system.md` | `bare.goal`, `bare.season_chat_full`, `bare.season_planning_only`, `bare.season_no_chat` | rules bare (and `bare.season_*` for open too): the whole prompt is `identity`, the goal line, one season line | `{rounds}` "3 rounds" |
| `system.md` | `open.invitation` | rules open: replaces the goal (no score is mentioned) | `{names}` |
| `tools.md` | `act.description_bare` | rules bare / open: the action syntax only | `{max_len}` |
| `messages.md` | `reflection.*`, `debrief.next_*`, `debrief.last`, `result.*`, `compaction.results_*` | see the `messages.md` table above | |

With more than two agents the teammate wording becomes the team: `{us}` ("you, Tango and Lima"),
`{names}`, `{cooks}`. The `{mate}` placeholder no longer exists. `state.md` has no order line
when scoring is soups (the `orders*` sections are used only with scoring orders).
