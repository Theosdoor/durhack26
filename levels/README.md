# Levels

One kitchen per file: `X` counter, ` ` floor, `O` onion crate, `D` dish rack, `P` pot,
`S` serving hatch, `B` bin (trash: interact while holding anything to throw it away),
`1`..`9` start positions. Coordinates are (x = column, y = row), origin
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

## Bins

Every level has one bin per room an agent can stand in (`B`, ids `bin_1`, `bin_2`, ... in reading
order). Each replaces a plain wall counter that touches floor, never a shared (pass-through)
counter and never a tile that blocks a path or the only access to an object, placed next to the
dish rack or hatch where possible. `bins: false` in the config turns every `B` back into a
counter (the layouts of runs before bins; 213 earlier seasons ran so).

| Level | Bins (x, y) | Where |
|---|---|---|
| `forced_coordination` | `bin_1` (4,3), `bin_2` (1,4) | right room under the pot column, by the hatch; left room below the rack |
| `split_room` | `bin_1` (1,4), `bin_2` (5,4) | left room by the hatch; right room by the dish rack |
| `cramped_room` | `bin_1` (2,3) | bottom wall, between the rack and the hatch |
| `asymmetric_advantages` | `bin_1` (2,4), `bin_2` (6,4) | bottom wall, beside each room's dish rack |
| `coordination_ring` | `bin_1` (3,4) | bottom wall, beside the hatch |
| `counter_circuit` | `bin_1` (7,1) | right wall, beside the hatch |
| `open_plan` | `bin_1` (6,2) | right wall, between the rack and the hatch |
| `practice` | `bin_1` (2,2) | bottom wall, beside the rack |

With bins, `forced_coordination`, `cramped_room`, `asymmetric_advantages`, `coordination_ring` and
`counter_circuit` differ from the Overcooked-AI grids by those `B` tiles (each is an `X` there).

Every level used in two-player rounds has start positions 1 and 2; `practice` has only 1.

## Credit

`forced_coordination`, `cramped_room`, `asymmetric_advantages`, `coordination_ring` and
`counter_circuit` are the layouts of the same names from
[Overcooked-AI](https://github.com/HumanCompatibleAI/overcooked_ai) (MIT licence), copied (apart from the bins above, `X` in the originals) from the `grid` field of
`overcooked_ai_py/data/layouts/<name>.layout` in overcooked-ai 1.1.0 (`counter_circuit` is
`counter_circuit_o_1order`). Only the grids are reused: the rules, timings and orders are our
own engine's. `tests/test_levels.py` pins the copies with the bins turned back into counters.

`split_room`, `open_plan` and `practice` are ours.
