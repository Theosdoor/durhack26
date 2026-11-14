# Kitchen state

The kitchen as an agent sees it: at every round decision and at the start of each chat. The
map rows themselves are drawn by the code from the layout.

## state
Step {t}. Team score {score} ({served} soups served).
{map}
{cooks}
{pots}
Items on counters: {counters}.

## legend
Legend: X counter, O onion crate, D dish rack, P pot, S serving hatch, space floor, {cooks}. Coordinates are (x, y) = (column, row).

## legend_cook
{letter} {name}

## cook
{who} ({letter}) at ({x},{y}), holding {holding}.

## you
You

## nothing
nothing

## pot
{pot} at ({x},{y}): {onions} onions, {state}{extra}.

## pot_ready_in
, ready in {n}

## pot_needs_start
 (not cooking: needs start_cooking)

## counter_item
{item} at ({where})

## counters_empty
none
