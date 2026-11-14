from undercooked.config import MAP_SYMBOLS, NATO_NAMES, Config


def test_names_never_look_like_map_symbols():
    assert len(NATO_NAMES) == 20
    assert "Alpha" not in NATO_NAMES
    assert not {n[0] for n in NATO_NAMES} & MAP_SYMBOLS
    assert len({n[0] for n in NATO_NAMES}) == len(NATO_NAMES)  # letters are unique


def test_pilot_config_loads():
    cfg = Config.load("configs/split_room.yaml")
    assert cfg.level == "split_room" and len(cfg.agents) == 2
