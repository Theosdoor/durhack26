The board and the pre-round chat: the system prompt's board or chat section for each comms condition (comms.*, with planning_only_rule and chat_rules), then what an agent sees on each turn of the chat (chat_turn.*). See README.md.

## comms.full
THE BOARD
A message board shared by {us}. post(message) puts a message of at most {max_post_chars} characters on it. {board_read} read_board() is free: it takes no game time.
{chat_rules}

## comms.planning_only
THE CHAT
{planning_only_rule}
{chat_rules}

## comms.none


## planning_only_rule
You can exchange messages with your team only in the chat before each round: post(message) puts a message of at most {max_post_chars} characters on a board you share. There is no board during practice or rounds.

## chat_rules
In the chat before a round you take turns. On your turn you may call post(message) and/or ready() (you are ready to start the round), or reply without calling a tool to pass. You have up to {chat_posts} per chat. The chat ends when each of you has called ready() or used all your posts (until then you keep getting turns, even after ready()), or after {chat_turns} each, whichever comes first.

## chat_turn.first
Chat before round {round} of {rounds}.
The kitchen for round {round}:
{overview}
{orders_rules}

## chat_turn.notepad
Your notepad: "{notepad}"

## chat_turn.later
Your turn in the chat before round {round}.

## chat_turn.new_posts
New posts on the board:
{posts}

## chat_turn.no_new_posts
No new posts.

## chat_turn.ready_prefix
You have said you are ready.

## chat_turn.status
Posts left: {posts_left}. Call post(message) and/or ready(), or reply without a tool call to pass.
