The fixed wording of the per-decision state view. The map drawing and the order of the lines are code (undercooked/engine/text.py). A line starting "Board:" is removed by the code when there is no board, so keep that prefix. Plurals of counted nouns ("1 unread post", "2 onions") are built in code.

## header
Time {t} / {duration} s · Score {score} · {where}

## where_practice
Practice

## where_round
Round {round} of {rounds}

## orders
Orders: {orders}

## order_item
onion soup (due {due_t} s)

## orders_none
Orders: none open.

## you
You ({name}, {letter}): at ({x},{y}), holding {item}. Last plan: {result}.

## teammate
{name} ({letter}): at ({x},{y}), holding {item}.

## item.nothing
nothing

## item.onion
an onion

## item.dish
a dish

## item.soup
a soup

## pot
{id} ({x},{y}): {what}.

## pot.ready
soup ready to plate

## pot.cooking
cooking, ready in {ready_in} s

## pot.empty
empty

## pot.onions
{onions}, not cooking

## counters
Counters: {counters}.

## counter
({x},{y}) {what}

## counter.empty
empty

## counters_none
Counters: all empty.

## board_unread
Board: {unread}.

## board_none
Board: no unread posts.

## notepad
Your notepad: "{notepad}"

## push_new_posts
New posts on the board:
{posts}

## push_none
Board: no new posts.

## order_item_minimal
an order (open until t = {due_t} s)
