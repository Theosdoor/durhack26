# Levels

One kitchen per file: `X` counter, ` ` floor, `O` onion crate, `D` dish rack, `P` pot,
`S` serving hatch, `1`..`9` start positions. Coordinates are (x = column, y = row), origin
top-left. A room is a connected area of floor; a counter that touches two rooms is a
pass-through counter, the only way items cross between rooms.

| Level | Source | Rooms | What it asks of the pair |
|---|---|---|---|
| `forced_coordination` | Overcooked-AI | 2 | Left room has the onions and dishes, right room the pots and hatch: everything crosses the middle counters. |
| `split_room` | ours | 2 | Left room (x 1-2) has the onions and the hatch, right room (x 4-5) the pots and the dish rack; the middle counters at x = 3 are the only crossing. A soup needs two crossings: onions left to right, then the plated soup right to left. |
| `cramped_room` | Overcooked-AI | 1 | One small room and one pot: stay out of each other's way. |
| `asymmetric_advantages` | Overcooked-AI | 2 | Two self-sufficient rooms (each with crate, rack and hatch) that share the two pots in the middle wall; the right room has its onions next to the pots, the left room its hatch and rack. |
| `coordination_ring` | Overcooked-AI | 1 | A ring around a centre counter; both pots are reached from the same tile. |
| `counter_circuit` | Overcooked-AI | 1 | A long circuit around a counter island; onions can be handed over the island. |
| `open_plan` | ours | 1 | Earlier test kitchen, two pots. |
| `practice` | ours | 1 | The one-player practice kitchen. |

Every level used in two-player rounds has start positions 1 and 2; `practice` has only 1.

## Credit

`forced_coordination`, `cramped_room`, `asymmetric_advantages`, `coordination_ring` and
`counter_circuit` are the layouts of the same names from
[Overcooked-AI](https://github.com/HumanCompatibleAI/overcooked_ai) (MIT licence), copied verbatim from the `grid` field of
`overcooked_ai_py/data/layouts/<name>.layout` in overcooked-ai 1.1.0 (`counter_circuit` is
`counter_circuit_o_1order`). Only the grids are reused: the rules, timings and orders are our
own engine's. `tests/test_levels.py` pins the copies.

`split_room`, `open_plan` and `practice` are ours.
