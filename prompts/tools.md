# Tools

Tool descriptions (what the model sees in its tool list) and what each tool call returns.
`act` has one description per rules mode.

## act_description_full
Set your plan: 1 to {max_len} actions done in order, e.g. ["get_onion", "put_in_pot(pot_1)"]. Your next decision comes when the plan is done or fails. Every round decision must call act.

## act_plan_full
1 to {max_len} actions, each one string such as "put_in_pot(pot_2)" or "wait(3)".

## act_description_minimal
Set your plan: 1 to {max_len} actions done in order, e.g. ["interact(0, 1)", "interact(pot_1)"]. Your next decision comes when the plan is done or fails. Every round decision must call act.

## act_plan_minimal
1 to {max_len} actions, each one string such as "interact(pot_2)" or "wait(3)".

## act_rejected
Plan rejected: {error}. Call act(plan) again with a valid plan.

## act_accepted
Plan accepted. It starts now.

## post_description
Post a message (at most {max_chars} characters) on the board you share with your teammate. One post per turn.

## post_message
The message.

## post_ignored
Ignored: one post per turn.

## post_empty
Not posted: empty message.

## post_ok
Posted.

## read_board_description
Read the board: every post since the chat before this round, newest last. Free: takes no game time.

## note_description
Replace the text of your private notepad (only you can see it).

## note_text
The new notepad text.

## note_ok
Notepad updated.

## ready_description
Say you are ready to start the round (chat only).

## ready_ok
Noted: you are ready to start the round.
