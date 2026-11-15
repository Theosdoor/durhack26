from pathlib import Path

import pytest
import yaml

from undercooked.config import MAP_SYMBOLS, NATO_NAMES, REPO_ROOT, AgentSpec, Config

CONFIGS = REPO_ROOT / "configs"
TEMPLATE = CONFIGS / "template.yaml"


def test_names_never_look_like_map_symbols():
    assert len(NATO_NAMES) == 20
    assert "Alpha" not in NATO_NAMES
    assert not {n[0] for n in NATO_NAMES} & MAP_SYMBOLS
    assert len({n[0] for n in NATO_NAMES}) == len(NATO_NAMES)  # letters are unique


def test_pilot_config_loads():
    cfg = Config.load("configs/split_room.yaml")
    assert cfg.level == "forced_coordination" and len(cfg.agents) == 2


def uncommented(path: Path) -> str:
    """The template with every option uncommented: "# " lines lose their "# ", "##" notes go."""
    out = []
    for line in path.read_text().splitlines():
        if line.startswith("##") or not line.strip():
            continue
        assert line.startswith("# "), f"template line is neither an option nor a note: {line!r}"
        out.append(line[2:])
    return "\n".join(out) + "\n"


def test_template_lists_every_option_with_its_default():
    data = yaml.safe_load(uncommented(TEMPLATE))
    cfg = Config.model_validate(data)                       # validates (extra keys forbidden)
    # the required and example-only fields; everything else must be the default
    defaults = Config(name=data["name"], level=data["level"],
                      agents=[AgentSpec(**a) for a in data["agents"]])
    assert cfg.model_dump(mode="json") == defaults.model_dump(mode="json")
    # every field, nested ones included, is written out (not just validated by default)
    def keys(model_dump: dict, given: dict, path: str = "") -> None:
        for k, v in model_dump.items():
            assert k in given, f"template misses {path}{k}"
            if isinstance(v, dict) and v and k not in ("level_overrides",):
                keys(v, given[k], f"{path}{k}.")
    keys(defaults.model_dump(mode="json"), data)


@pytest.mark.parametrize("path", sorted(p for p in CONFIGS.glob("*.yaml") if p != TEMPLATE),
                         ids=lambda p: p.name)
def test_configs_set_only_what_differs_from_the_default(path):
    """Slim configs: a top-level value equal to its default is left out, except the condition
    knobs (comms, debrief, rules, board, decision.reasoning), which a config may state."""
    data = yaml.safe_load(path.read_text())
    cfg = Config.model_validate(data)
    defaults = Config(name=cfg.name, level=cfg.level, levels=cfg.levels,
                      agents=cfg.agents).model_dump(mode="json")
    keep = {"name", "level", "levels", "agents", "comms", "debrief", "rules", "board", "scoring",
            "reflection"}
    for key, value in data.items():
        if key in keep:
            continue
        if isinstance(value, dict) and key in ("decision", "orders", "providers"):
            for sub, v in value.items():
                if (key, sub) != ("decision", "reasoning"):
                    assert v != defaults[key][sub], f"{path.name}: {key}.{sub} is the default"
        else:
            assert cfg.model_dump(mode="json")[key] != defaults[key], \
                f"{path.name}: {key} is the default"


def test_pilot_configs():
    rc = Config.load(CONFIGS / "pilot_rules_comms.yaml")
    assert rc.run_levels() == ["split_room", "cramped_room", "asymmetric_advantages"]
    assert (rc.rounds, rc.epochs, rc.practice) == (3, 2, True)
    assert {a.model for a in rc.agents} == {"openrouter/deepseek/deepseek-v4.1-flash"}
    cf = Config.load(CONFIGS / "pilot_cross_family.yaml")
    assert (cf.level, cf.rounds, cf.epochs, cf.orders.every_s) == ("split_room", 3, 2, 20)
    assert [(a.model, a.provider) for a in cf.agents] == [
        ("openrouter/deepseek/deepseek-v4.1-flash", "baidu/fp8"),
        ("openrouter/google/gemma-4-31b-it", "deepinfra/fp8")]
