The system prompt. With rules: full, in reading order: identity, the kitchen rules (kitchen_rules, recipe, scoring.*, map_key, actions, interact_table, plans), season, deciding, the board or chat section (from communication.md), notepad, goal. With rules: bare: identity, bare.goal, one bare.season_* line. With rules: open: identity, open.invitation, one bare.season_* line (no goal and no score in the system prompt; the game's state, round results and private turns still show the score). The other sections are snippets the code inserts depending on the experimental condition. See README.md.

## identity
You are {me}, one of {cooks} cooks in a kitchen game. {team}

## identity.one_mate
Your teammate is {names}.

## identity.many_mates
Your teammates are {names}.

## kitchen_rules
HOW THE KITCHEN WORKS
Time runs in game seconds. In one second you either walk one tile (up, down, left or right; no
diagonals) or do one interaction. You can only walk on floor tiles; counters, crates, racks,
pots and the hatch block movement, and so do other players.

## recipe
Recipe (the only dish): onion soup. Put 3 onions in a pot; it starts cooking by itself when the
3rd onion goes in and is ready {cook_s} s later. Take a dish to the ready pot to plate the
soup, then carry the soup to the serving hatch. You can hold one item at a time: an onion, a
dish or a soup. Onions and dishes never run out.

## scoring.orders
Orders: Each order is due {due_s} s after it arrives. Serving a soup
fills the open order that is due soonest: +{reward} points. An order not served by its due
time expires: -{penalty} points.

## scoring.soups
Scoring: every soup served at the serving hatch scores +{reward} points.

## map_key
MAP KEY
X counter (a wall; any counter next to the floor can hold one item)   ' ' floor
O onion crate   D dish rack   P pot   S serving hatch
Capital letters on the floor are players (the first letter of their name).
Coordinates are (x, y): x is the column (left to right), y is the row (top to bottom),
both starting at 0, as labelled around the map.

## actions
ACTIONS (your plan is a list of 1 to {max_plan_len} of these, done in order)
interact(<id>)    walk next to the object (shortest path), then interact with it (1 s).
move_to(x, y)     walk to that floor tile.
move_to(<id>)     walk next to that object without interacting.
wait(<seconds>)   stand still for {wait_min} to {wait_max} s.
Object ids are numbered in reading order (row by row, left to right): onion_crate_1,
dish_rack_1, pot_1, pot_2, hatch_1, ... Counters are named by position: counter_2_1 is the
counter at (2,1). An unnumbered id (onion_crate, dish_rack, pot, hatch) means the nearest one
you can reach. You interact from any floor tile beside the object; facing does not matter.

## interact_table
INTERACT TABLE (anything else does nothing and your plan fails, with the reason)
onion crate + empty hands  -> you take an onion
dish rack   + empty hands  -> you take a dish
counter     + holding item -> you put it on the counter (if the counter is empty)
counter     + empty hands  -> you pick up the item on the counter
pot         + onion        -> the onion goes in (max 3; not while cooking or ready)
pot         + dish         -> if the soup is ready, you now hold the soup and the pot is empty
hatch       + soup         -> {hatch_soup}

## hatch_soup.orders
served: fills the open order due soonest

## hatch_soup.soups
served: +{reward} points

## plans
PLANS
Your plan runs step by step until it is done or fails. If another player is in your way you
wait for them; if you are blocked for {blocked_limit} seconds in a row the plan fails ("blocked by <name>").
A plan also fails if an action cannot be understood, its target does not exist or cannot be
reached from where you are, or an interaction does nothing. A new plan replaces the old one.

## season
THE SEASON
{practice}Then {us} play {rounds} together in the same kitchen. Each round lasts {round_s} s and starts afresh ({afresh}). {between}

## afresh.orders
empty pots, new orders, everyone back at the start

## afresh.soups
empty pots, everyone back at the start

## deciding
DECIDING
During practice and rounds, whenever you have no plan (at the start, or when your plan is done or has failed) you decide: call act(plan) with your next plan. Every decision must end with act(plan). While you decide you stand still, and deciding costs game time: {flat} per decision{posting}. {extras}{thinking_budget}

## notepad
YOUR NOTEPAD
A private note that only you can see. It is shown to you with your state at every decision and kept for the whole season. note(text) replaces it.

## goal
GOAL
Get the highest team score.

## practice_intro
First you practise alone in a small practice kitchen for {practice_s} s (no board, and the score does not count).

## practice_intro_no_comms
First you practise alone in a small practice kitchen for {practice_s} s (the score does not count).

## between_chat_private
Before each round there is a chat on the board. After each round you get a private turn: you see the round's score and may update your notepad.

## between_chat_score_only
Before each round there is a chat on the board; after each round you are told the round's score.

## between_no_chat_private
After each round you get a private turn: you see the round's score and may update your notepad.

## between_no_chat_score_only
After each round you are told the round's score.

## posting_full
, plus 1 s for every {post_words_per_s} words you post (rounded up)

## extras_full
Your plan, post and note take effect when your decision lands. In a decision you may also call post(message), read_board() and note(text).

## extras_no_post
Your plan and note take effect when your decision lands. In a decision you may also call note(text).

## board_read_pull
During a round your state shows how many posts you have not read yet; call read_board() to read them.

## board_read_push
During a round, new posts are shown to you with your state.

## thinking_budget
Think briefly, then act.

## practice_finish_note
When you have practised enough you may call finish_practice() to end your practice early.

## rules_recipe
THE KITCHEN
Your team scores by serving onion soup at the serving hatch. A soup needs onions cooked in a pot and a dish to carry it. Orders come in over time; serving a soup fills one and scores points, and an order left too long costs points. You can hold one item at a time. How the objects work is for you to find out by playing.

## rules_recipe_soups
THE KITCHEN
Your team scores by serving onion soup at the serving hatch. A soup needs onions cooked in a pot and a dish to carry it. You can hold one item at a time. How the objects work is for you to find out by playing.

## rules_minimal
THE KITCHEN
Your team scores points by delivering things at the serving hatch. How the kitchen works is for you to find out by playing.

## rules_minimal_hint
THE KITCHEN
Your team scores points by delivering things at the serving hatch. How the kitchen works is for you to find out by playing. Practice is a good time to try interacting with everything and see what happens.

## bare.season_chat_full
You play {rounds}, with a chat on a shared board before each round; during the rounds you can keep posting to it.

## bare.season_planning_only
You play {rounds}, with a chat on a shared board before each round.

## bare.season_no_chat
You play {rounds}.

## bare.goal
Get the highest team score by serving soups at the serving hatch.

## open.invitation
You are in a kitchen with {names}. Do whatever you like: explore, play, talk.
