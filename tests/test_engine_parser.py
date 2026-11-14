"""Action parser: the forms real models write, and helpful errors for the rest."""

import pytest

from undercooked.engine import Kitchen, ParseError, parse_action, split_plan

K = Kitchen.load("split_room")


@pytest.mark.parametrize("text, canonical", [
    ("interact(onion_crate_1)", "interact(onion_crate_1)"),
    ("interact(onion_crate)", "interact(onion_crate)"),        # nearest, resolved later
    ("interact(pot_1)", "interact(pot_1)"),
    ("interact(pot)", "interact(pot)"),
    ("interact(hatch)", "interact(hatch)"),
    ("interact(dish_rack)", "interact(dish_rack)"),
    ("interact(counter(2,2))", "interact(counter_2_2)"),
    ("interact(counter_2_2)", "interact(counter_2_2)"),
    ("interact(counter 2 2)", "interact(counter_2_2)"),
    ("interact((2,2))", "interact(counter_2_2)"),
    ("interact(2, 1)", "interact(counter_2_1)"),
    ("interact(pot(3,0))", "interact(pot_1)"),
    ('Interact("Pot_1")', "interact(pot_1)"),
    ("`interact(pot_1)`", "interact(pot_1)"),
    ("1. interact(pot_1)", "interact(pot_1)"),
    ("interact(onion crate 2)", "interact(onion_crate_2)"),
    ("interact(target=pot_1)", "interact(pot_1)"),
    ("interact with pot 1", "interact(pot_1)"),
    ("interact(serving_hatch)", "interact(hatch)"),
    ("pick_up(onion_crate)", "interact(onion_crate)"),
    ("pick up onion", "interact(onion_crate)"),
    ("put_down(counter_2_2)", "interact(counter_2_2)"),
    ("serve(hatch)", "interact(hatch)"),
    ("pot_2", "interact(pot_2)"),
    ("move_to(onion_crate_1)", "move_to(onion_crate_1)"),
    ("move_to(counter_2_2)", "move_to(counter_2_2)"),
    ("go to pot_2", "move_to(pot_2)"),
    ("move_to(1,3)", "move_to(1, 3)"),
    ("move_to((1,3))", "move_to(1, 3)"),
    ("move_to(x=1, y=3)", "move_to(1, 3)"),
    ("move_to 1,3", "move_to(1, 3)"),
    ("move_to(2,2)", "move_to(counter_2_2)"),   # a non-floor tile: walk next to it
    ("move(left)", "move(left)"),
    ("up", "move(up)"),
    ("wait(3)", "wait(3)"),
    ("wait(3s)", "wait(3)"),
    ("wait(seconds=4)", "wait(4)"),
    ("wait", "wait(1)"),
    ("wait(100)", "wait(30)"),
    ("wait(0)", "wait(1)"),
])
def test_accepted_forms(text, canonical):
    assert parse_action(text, K).text() == canonical


@pytest.mark.parametrize("text, fragment", [
    ("interact(pot_9)", "pot_9 does not exist (this kitchen has: pot_1, pot_2)"),
    ("interact(counter)", "say which counter by position"),
    ("interact(1,1)", "(1,1) is floor"),
    ("interact(banana)", "unknown target 'banana'"),
    ("dance()", "unknown action 'dance'"),
    ("move_to(9,9)", "(9,9) is outside the kitchen"),
    ("interact(pot(2,1))", "(2,1) is counter_2_1, not a pot"),
    ("wait(abc)", "wait needs a number of seconds"),
    ("interact()", "needs a target"),
    ("", "empty action"),
])
def test_rejected_forms(text, fragment):
    with pytest.raises(ParseError) as e:
        parse_action(text, K)
    assert fragment in str(e.value)


def test_split_plan_flattens_joined_actions():
    assert split_plan(["interact(onion_crate), interact(pot_1)", "wait(2); move_to(1, 3)"]) == [
        "interact(onion_crate)", "interact(pot_1)", "wait(2)", "move_to(1, 3)"]
    assert split_plan("move_to 2, 2") == ["move_to 2, 2"]   # coordinates are not split
    assert split_plan(["", "  "]) == []
