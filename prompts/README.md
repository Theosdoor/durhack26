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
| `identity` | always, first line | `{me}` Kilo, `{mate}` Tango |
| `kitchen_rules`, `map_key`, `actions`, `interact_table`, `plans` | always (joined as the rules) | `{cook_s}` 20, `{every_s}` 30, `{max_open}` 3, `{due_s}` 60, `{reward}` 20, `{penalty}` 10, `{max_plan_len}` 5, `{wait_min}` 1, `{wait_max}` 10, `{blocked_limit}` 3 (the ones each uses are visible in its text) |
| `season` | always | `{practice}` (practice sentence + space, or empty), `{mate}`, `{rounds}` "3 rounds", `{round_s}` 180, `{between}` (a `between_*` snippet) |
| `deciding` | always | `{flat}` "2 seconds", `{posting}` (`posting_full` or empty), `{extras}` (`extras_*`), `{thinking_budget}` (space + `thinking_budget`, or empty) |
| (board or chat section) | from `communication.md`, between `deciding` and `notepad`; none for comms: none | |
| `notepad`, `goal` | always | none |
| `practice_intro`, `practice_intro_no_comms` | practice on (the latter when comms: none) | `{practice_s}` 60 |
| `between_chat_debrief`, `between_chat_score_only`, `between_no_chat_debrief`, `between_no_chat_score_only` | comms and debrief conditions | none |
| `posting_full`, `extras_full`, `extras_no_post` | comms: full / other | `{post_words_per_s}` 10 (`posting_full` only) |
| `board_read_pull`, `board_read_push` | comms: full, by the board setting | none |
| `thinking_budget` | reasoning: on | `{reasoning_tokens}` 1000 |

## `communication.md`: the board and the pre-round chat

| Section | Used when | Placeholders |
|---|---|---|
| `comms.full` | system prompt, comms: full ("THE BOARD") | `{mate}`, `{max_post_chars}` 200, `{board_read}`, `{chat_rules}` |
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
| `orders_rules` | chat, first turn | `{every_s}`, `{max_open}`, `{due_s}`, `{reward}`, `{penalty}`, `{round_s}` |
| `round_start.base`, `.turn_limit` | first decision of each round (`turn_limit` if the chat hit its cap) | `{round}`, `{rounds}`; `{turns}` "8 turns" |
| `cutoff_notice.notice`, `.post` | before the state after a reply that did not call act | `{seconds}` 4, `{post}` (space + `.post`, or empty); `{post_s}` 3 |
| `forced_why.*` | reason line of a forced-answer prompt (thinking budget used up, or reasoning off) | none |
| `forced.act` / `.chat` / `.debrief` | request of the forced-answer prompt | none |
| `round_result` | debrief: score_only, before the next message | `{round}`, `{rounds}`, `{score}`, `{served}` "2 orders", `{missed}` |
| `debrief.result` | debrief: full | `{round}`, `{rounds}`, `{score}`, `{served}`, `{missed}` |
| `debrief.scores_so_far`, `.score_item` | from round 2 | `{scores}` "round 1: 20, round 2: 30"; `{n}`, `{score}` |
| `debrief.notepad`, `.notepad_empty`, `.instruction` | debrief | `{notepad}` |
| `compaction.prompt`, `.posted` | Inspect's summarising prompt (`.posted` omitted when comms: none) | `{posted}`, `{addendums}`: Inspect fills `{addendums}` itself, keep it exactly, at the end |

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

## Not here

The engine's plan-result words (`Last plan: done`, `failed: <reason>`) and parse errors come from
`engine/game.py` and `engine/actions.py`; the map is drawn by `engine/text.py`; a board post is
formatted `[chat] Name: text` or `[t=12s] Name: text` by `board.py`. These stay in code.
