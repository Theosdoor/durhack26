# Agent prompts

Everything the agents read comes from the Markdown files in this folder. Edit them here; the
code reads them when it builds each prompt (files are re-read when they change), so a running
season picks up an edit at its next prompt.

## How the files work
- Each `## name` heading starts a section; the section is the text up to the next heading,
  without leading or trailing blank lines. Leading/trailing spaces are kept (some sections
  start with a space or a comma because they are appended to another line).
- `# ` headings and the text under them are notes for editors and are never sent.
- `{placeholder}`s are filled in by the code (Python `str.format`). Write literal braces as
  `{{` and `}}`. A missing or misspelled placeholder raises an error naming the file and section.
- Not in these files (they are data, drawn by the code): the map grid, action syntax
  (`move_to(x, y)`, `wait(n)`, `interact(target)`), names of items (`onion`, `dish`, `soup`),
  pot ids and pot states (`empty`, `filling`, `cooking`, `ready`).

## Checking an edit
`uv run python -m tests.golden_prompts` renders every prompt for a grid of seasons (4 layouts x
2 rules modes, played by a scripted model) and compares them to `tests/golden/prompts.json`.
After an intentional edit, regenerate it and commit both:

    uv run python -m tests.golden_prompts --update

## Sections
| File | Section | When it is used | Placeholders |
|---|---|---|---|
| system.md | system | The system message, once per season | me, mate, rules, rounds, round_steps, max_plan_len, max_post_chars, chat_max_posts |
| system.md | rules_full | `{rules}` with `-T rules=full` (default) | cook_time, counters, walking, wait_limit |
| system.md | rules_minimal | `{rules}` with `-T rules=minimal` | walking, wait_limit |
| system.md | walking | `{walking}` in both rules | stuck_steps |
| system.md | counters_shared / counters_none | `{counters}` in rules_full: whether both cooks can reach a counter | shared / – |
| state.md | state | The kitchen at every round decision and at the start of each chat | t, score, served, map, cooks, pots, counters |
| state.md | legend, legend_cook | Line under the map; one legend_cook per cook | cooks; letter, name |
| state.md | cook, you, nothing | One line per cook | who, letter, x, y, holding |
| state.md | pot, pot_ready_in, pot_needs_start | One line per pot; extras appended (pot_needs_start: full rules only) | pot, x, y, onions, state, extra; n |
| state.md | counter_item, counters_empty | Items on counters | item, where |
| messages.md | chat_start, chat_scores, score_item, chat_kitchen | A cook's first chat turn before a round | round, rounds; scores; round, score |
| messages.md | chat_turn | Later chat turns | round |
| messages.md | chat_new_posts / chat_no_posts | Every chat turn | posts |
| messages.md | chat_ready_already, chat_footer | End of every chat turn | posts_left |
| messages.md | notepad / notepad_empty | Chat (both) and round decisions (notepad only) | notepad |
| messages.md | round_start | First decision of each round | round, rounds |
| messages.md | unread / no_unread, last_plan_stopped, decide | Every round decision | n; reason |
| messages.md | cutoff_reask | After a round reply with no act() (up to twice) | – |
| messages.md | debrief, debrief_notepad, debrief_notepad_empty | After each round | round, rounds, score, served, scores, notepad |
| messages.md | post_chat, post_round, board_empty | Posts shown in chat turns and by read_board() | agent, text, t |
| tools.md | act_description_full/minimal, act_plan_full/minimal | The act tool and its plan parameter, per rules mode | max_len |
| tools.md | act_rejected, act_accepted | What act() returns | error |
| tools.md | post_description, post_message, post_ignored, post_empty, post_ok | The post tool | max_chars |
| tools.md | read_board_description | The read_board tool (its result is the posts) | – |
| tools.md | note_description, note_text, note_ok | The note tool (debrief) | – |
| tools.md | ready_description, ready_ok | The ready tool (chat) | – |
| errors.md | unknown_action, needs_empty_hands, needs_item, empty_plan | act() rejections | action, actions; name, held, need |
| errors.md | move_not_floor, move_unreachable | move_to rejections | target |
| errors.md | no_such_pot, outside_kitchen, wrong_target, bad_target | Bad action targets | pot, pots; x, y, name; action, reason |
| errors.md | unreachable_target, unreachable_any | Target out of reach | action |
| errors.md | blocked, nothing_to_do_target, nothing_to_do | A running plan stopped (shown via last_plan_stopped) | action, mate, x, y, stuck_steps, wait_limit |
| errors.md | failed_at | Wraps the stop reason with its step | t, reason |
