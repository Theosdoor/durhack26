# Messages during a season

User messages the code sends, by phase. Lines are joined with newlines in the order listed.

# Chat before each round (one message per turn)
First turn: `chat_start` (+ `chat_scores` once a round has been played), `chat_kitchen`, the
kitchen state, `notepad` or `notepad_empty`. Later turns: `chat_turn`. Every turn then:
`chat_new_posts` or `chat_no_posts`, then `chat_footer` (prefixed by `chat_ready_already`
if the agent has already called ready()).

## chat_start
Chat before round {round} of {rounds}.

## chat_scores
 Scores so far: {scores}.

## score_item
round {round}: {score}

## chat_kitchen
The kitchen at the start of the round:

## chat_turn
Your turn in the chat before round {round}.

## chat_new_posts
New posts on the board:
{posts}

## chat_no_posts
No new posts.

## chat_ready_already
You have said you are ready. 

## chat_footer
Posts left: {posts_left}. Call post(message) and/or ready(), or reply without a tool call to pass.

## notepad
Your notepad: "{notepad}"

## notepad_empty
Your notepad is empty.

# Round decisions (one message each time a cook has no plan)
`round_start` (first decision of the round only), the kitchen state, `unread` or `no_unread`,
`notepad` (if any), `last_plan_stopped` (if the last plan failed), `decide`.

## round_start
Round {round} of {rounds} starts now.

## unread
Unread posts on the board: {n}.

## no_unread
No unread posts.

## last_plan_stopped
Your last plan stopped early ({reason}).

## decide
You have no plan. Decide: call act(plan).

## cutoff_reask
Your last reply did not call act(plan), so you have no plan. Call act(plan) now.

# Debrief after each round

## debrief
Round {round} of {rounds} is over. Team score this round: {score} ({served} soups served). Scores so far: {scores}.
Your notepad: {notepad}
You may call note(text) to rewrite your notepad, or reply without a tool call.

## debrief_notepad
"{notepad}"

## debrief_notepad_empty
(empty)

# Board posts, as shown in the chat and by read_board()

## post_chat
[chat] {agent}: {text}

## post_round
[step {t}] {agent}: {text}

## board_empty
(no posts)
