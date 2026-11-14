Tool descriptions (what the model's tool list shows) and tool results (what a call answers). One section per text. Keep the example plan in act.description valid.

## act.description
Set your plan: a list of 1 to {max_len} actions done in order, e.g. ["interact(onion_crate)", "interact(pot_1)"]. Every decision must end with act. A new plan replaces your old one.

## act.plan_param
1 to {max_len} actions, each one string such as "interact(pot_1)", "move_to(2, 3)" or "wait(3)".

## act.ignored
Ignored: you already sent a plan in this decision.

## act.rejected
Plan rejected: {error}. You stand still and decide again when this decision lands.

## act.accepted
Plan accepted. It starts when this decision lands.

## act.too_long
Your plan has {n} actions. Only the first {max_len} actions are kept.

## post.description
Post a message (at most {max_post_chars} characters) on the board you share with your teammate. One post per turn.

## post.message_param
The message.

## post.ignored
Ignored: one post per turn.

## post.not_posted
Not posted: {reason}

## post.empty
Not posted: the message is empty.

## post.cap_reached
you have used all {cap} posts for this chat

## post.posted
Posted.

## post.posted_cut
Posted (cut to {max_post_chars} characters).

## post.lands_later
It appears on the board when this decision lands.

## read_board.description
Read the board: every post since the chat before this round, newest last. Free: takes no game time. Marks the posts as read.

## read_board.empty
The board is empty.

## note.description
Replace the text of your private notepad (only you can see it).

## note.text_param
The new notepad text.

## note.result
Notepad updated.

## ready.description
Say that you are ready to start the round (chat only).

## ready.result
Noted: you are ready to start the round.
