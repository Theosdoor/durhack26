# Errors

Why a plan was rejected (returned by act) or why a running plan stopped (shown at the next
decision through `last_plan_stopped`, and logged as the reason of a `fail` event).

## unknown_action
unknown action {action!r}; use one of {actions}

## needs_empty_hands
{name} needs empty hands, but you hold {held}

## needs_item
{name} needs you to hold {need}, but you hold {held}

## empty_plan
empty plan

## move_not_floor
move_to{target}: not a floor tile

## move_unreachable
move_to{target}: you can't get there from your side of the kitchen

## no_such_pot
there is no {pot} (pots: {pots})

## outside_kitchen
({x},{y}) is outside the kitchen

## wrong_target
({x},{y}) is not something you can {name}

## bad_target
{action}: {reason}

## unreachable_target
{action}: you can't reach there from your side of the kitchen

## unreachable_any
{action}: you can't reach anywhere to do that from your side of the kitchen

## blocked
{action}: {mate} at ({x},{y}) blocked your way for {stuck_steps} steps. Ask them to move, go another way, or move_to somewhere else

## nothing_to_do_target
{action}: nothing to do there for {wait_limit} steps

## nothing_to_do
{action}: nothing to do yet for {wait_limit} steps

## failed_at
step {t}: {reason}
