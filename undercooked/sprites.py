"""Illustrated kitchen map for the replay viewer, drawn as one inline SVG string.

`render_svg(header, frame)` is pure (no Streamlit): it turns a Header (the level map) and a Frame
(agents, pots, counter items, orders) into an SVG. All art is original and built from basic shapes.
The scene paints its own backgrounds and uses explicit colours, so it looks the same in Streamlit's
light and dark themes. Every piece of text sits on a dark or light "pill" so it stays legible.

Layout: an order strip (STRIP px high) across the top, then the grid of tiles (T px square each).
Each helper below draws one element and takes the tile's top-left corner (x0, y0) in SVG pixels.
"""

from __future__ import annotations

import html
from functools import lru_cache

from undercooked.engine.kitchen import Kitchen
from undercooked.replay import AgentState, Frame, Header, OrderState, PotState

T = 88            # tile size in SVG pixels (the SVG scales to its container via the viewBox)
STRIP = 56        # height of the order strip above the map
AGENT_COLOURS = ["#0072B2", "#E69F00", "#CC79A7", "#009E73"]   # Okabe-Ito: blue, orange, pink, green
INK = "#3a2616"   # dark brown used for outlines
ONION = "#d98c3f"
SOUP = "#f2a93b"
FONT = "Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------- small drawing helpers

def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def pill(cx: float, cy: float, text: str, size: int = 12, fg: str = "#ffffff", bg: str = "#2b2118") -> str:
    """Text on a rounded backing, centred on (cx, cy), so it is readable on any background."""
    w = len(text) * size * 0.62 + 12
    h = size + 8
    return (f"<rect x='{cx - w / 2:.1f}' y='{cy - h / 2:.1f}' width='{w:.1f}' height='{h}' rx='{h / 2}' "
            f"fill='{bg}' fill-opacity='0.9'/>"
            f"<text x='{cx}' y='{cy + size * 0.35:.1f}' font-size='{size}' font-weight='700' fill='{fg}' "
            f"text-anchor='middle' font-family='{FONT}'>{esc(text)}</text>")


DEFS = ("<defs>"
        "<radialGradient id='uc-onion' cx='0.35' cy='0.35' r='0.85'>"
        "<stop offset='0' stop-color='#f3c26b'/><stop offset='0.55' stop-color='#d9913f'/>"
        "<stop offset='1' stop-color='#a85f24'/></radialGradient>"
        "<linearGradient id='uc-steel' x1='0' y1='0' x2='0' y2='1'>"
        "<stop offset='0' stop-color='#f1f5f8'/><stop offset='1' stop-color='#c3ced8'/></linearGradient>"
        "</defs>")


def onion(cx: float, cy: float, r: float, angle: float = 0) -> str:
    """One onion: golden teardrop bulb with a pointed top, 3 green sprouts, root tuft, skin lines, highlight."""
    sw = max(1.2, r * 0.14)
    body = (f"M{cx:.1f} {cy - 1.25 * r:.1f} C{cx + 0.2 * r:.1f} {cy - 0.85 * r:.1f} {cx + 1.05 * r:.1f} {cy - 0.55 * r:.1f} "
            f"{cx + 1.05 * r:.1f} {cy + 0.15 * r:.1f} C{cx + 1.05 * r:.1f} {cy + 0.8 * r:.1f} {cx + 0.5 * r:.1f} {cy + r:.1f} "
            f"{cx:.1f} {cy + r:.1f} C{cx - 0.5 * r:.1f} {cy + r:.1f} {cx - 1.05 * r:.1f} {cy + 0.8 * r:.1f} "
            f"{cx - 1.05 * r:.1f} {cy + 0.15 * r:.1f} C{cx - 1.05 * r:.1f} {cy - 0.55 * r:.1f} {cx - 0.2 * r:.1f} "
            f"{cy - 0.85 * r:.1f} {cx:.1f} {cy - 1.25 * r:.1f} Z")
    ty = cy - 1.25 * r
    g = f"<g transform='rotate({angle} {cx:.1f} {cy:.1f})'>"
    g += (f"<path d='M{cx - 0.25 * r:.1f} {cy + 0.95 * r:.1f} l{-0.2 * r:.1f} {0.3 * r:.1f} "
          f"M{cx:.1f} {cy + r:.1f} l0 {0.35 * r:.1f} M{cx + 0.25 * r:.1f} {cy + 0.95 * r:.1f} l{0.2 * r:.1f} {0.3 * r:.1f}' "
          f"stroke='{INK}' stroke-width='{sw * 0.8:.1f}' stroke-linecap='round' fill='none'/>")
    for dx, dy in [(-0.45, -0.55), (0.0, -0.75), (0.45, -0.55)]:
        leaf = f"M{cx:.1f} {ty + 0.1 * r:.1f} q{dx * r * 0.2:.1f} {dy * r * 0.8:.1f} {dx * r:.1f} {dy * r:.1f}"
        g += (f"<path d='{leaf}' stroke='{INK}' stroke-width='{sw * 2.2:.1f}' stroke-linecap='round' fill='none'/>"
              f"<path d='{leaf}' stroke='#5fa33a' stroke-width='{sw * 1.2:.1f}' stroke-linecap='round' fill='none'/>")
    g += f"<path d='{body}' fill='url(#uc-onion)' stroke='{INK}' stroke-width='{sw:.1f}' stroke-linejoin='round'/>"
    g += (f"<path d='M{cx:.1f} {ty + 0.15 * r:.1f} C{cx - 0.5 * r:.1f} {cy - 0.4 * r:.1f} {cx - 0.55 * r:.1f} {cy + 0.5 * r:.1f} "
          f"{cx - 0.2 * r:.1f} {cy + 0.95 * r:.1f} M{cx:.1f} {ty + 0.15 * r:.1f} C{cx + 0.45 * r:.1f} {cy - 0.4 * r:.1f} "
          f"{cx + 0.55 * r:.1f} {cy + 0.5 * r:.1f} {cx + 0.2 * r:.1f} {cy + 0.95 * r:.1f} M{cx:.1f} {ty + 0.2 * r:.1f} "
          f"C{cx:.1f} {cy - 0.2 * r:.1f} {cx:.1f} {cy + 0.4 * r:.1f} {cx:.1f} {cy + 0.9 * r:.1f}' "
          f"stroke='#8a4e1c' stroke-opacity='0.6' stroke-width='{max(0.8, sw * 0.6):.1f}' fill='none' stroke-linecap='round'/>")
    g += (f"<ellipse cx='{cx - 0.5 * r:.1f}' cy='{cy - 0.15 * r:.1f}' rx='{0.2 * r:.1f}' ry='{0.38 * r:.1f}' "
          f"fill='#fff3cf' fill-opacity='0.75' transform='rotate(18 {cx - 0.5 * r:.1f} {cy - 0.15 * r:.1f})'/>")
    return g + "</g>"


def plate(cx: float, cy: float, r: float, soup: bool = False) -> str:
    """A white plate (two rings); with soup it holds an orange blob."""
    s = (f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='#fdfdfd' stroke='{INK}' stroke-width='1.5'/>"
         f"<circle cx='{cx}' cy='{cy}' r='{r * 0.68:.1f}' fill='#e9eef2' stroke='#b9c4cc' stroke-width='1'/>")
    if soup:
        s += (f"<circle cx='{cx}' cy='{cy}' r='{r * 0.55:.1f}' fill='{SOUP}'/>"
              f"<circle cx='{cx - r * 0.18:.1f}' cy='{cy - r * 0.18:.1f}' r='{r * 0.14:.1f}' fill='#fff2c4'/>")
    return s


def item_icon(item: str, cx: float, cy: float, r: float) -> str:
    """Draw a held / carried item: onion, dish (plate) or soup (plate with soup)."""
    if item == "onion":
        return onion(cx, cy, r * 0.85)
    return plate(cx, cy, r, soup=(item == "soup"))


# ---------------------------------------------------------------- tiles

def floor_tile(x: int, y: int, x0: int, y0: int) -> str:
    """Light floor with a checker tint and grout lines."""
    fill = "#f6e9cc" if (x + y) % 2 == 0 else "#e9d4aa"
    return (f"<rect x='{x0}' y='{y0}' width='{T}' height='{T}' fill='{fill}' stroke='#c9b184' stroke-width='2'/>"
            f"<rect x='{x0 + 7}' y='{y0 + 7}' width='{T - 14}' height='{T - 14}' rx='8' fill='#ffffff' "
            f"fill-opacity='0.18'/>"
            f"<path d='M{x0 + 12} {y0 + 14} l10 -2' stroke='#ffffff' stroke-opacity='0.6' stroke-width='3' "
            f"stroke-linecap='round'/>")   # little shine mark


def counter_tile(x0: int, y0: int) -> str:
    """Wooden worktop: pale top face, light edge highlight, darker front lip and a few grain lines."""
    return (f"<rect x='{x0}' y='{y0}' width='{T}' height='{T}' fill='#b9814a'/>"
            f"<rect x='{x0 + 2}' y='{y0 + 2}' width='{T - 4}' height='{T - 14}' rx='5' fill='#d9a56a' "
            f"stroke='{INK}' stroke-width='2'/>"
            f"<rect x='{x0 + 6}' y='{y0 + 5}' width='{T - 12}' height='5' rx='2.5' fill='#f0cc98'/>"
            f"<rect x='{x0 + 2}' y='{y0 + T - 14}' width='{T - 4}' height='12' rx='4' fill='#8a5a30' "
            f"stroke='{INK}' stroke-width='2'/>"
            f"<path d='M{x0 + 14} {y0 + 30} h{T - 40} M{x0 + 26} {y0 + 46} h{T - 44} M{x0 + 12} {y0 + 60} h{T - 50}' "
            f"stroke='#c28f58' stroke-width='1.5' stroke-linecap='round'/>")


def pass_counter_tile(x0: int, y0: int) -> str:
    """Pass-through counter between rooms: cool steel top, darker lip and a two-way arrow mark."""
    cx, cy = x0 + T / 2, y0 + 61   # mark sits low on the top face so an item on the counter doesn't hide it
    return ("<g data-tile='pass'>"
            f"<rect x='{x0}' y='{y0}' width='{T}' height='{T}' fill='#8997a4'/>"
            f"<rect x='{x0 + 2}' y='{y0 + 2}' width='{T - 4}' height='{T - 14}' rx='5' fill='url(#uc-steel)' "
            f"stroke='{INK}' stroke-width='2'/>"
            f"<rect x='{x0 + 6}' y='{y0 + 5}' width='{T - 12}' height='4' rx='2' fill='#ffffff' fill-opacity='0.8'/>"
            f"<rect x='{x0 + 2}' y='{y0 + T - 14}' width='{T - 4}' height='12' rx='4' fill='#6f7f8d' "
            f"stroke='{INK}' stroke-width='2'/>"
            f"<path d='M{cx - 16} {cy - 4} h30 m-6 -4 l6 4 l-6 4 M{cx + 16} {cy + 4} h-30 m6 -4 l-6 4 l6 4' "
            f"fill='none' stroke='#4f6272' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/>"
            "</g>")


@lru_cache(maxsize=64)
def _pass_counters(rows: tuple[str, ...]) -> frozenset[tuple[int, int]]:
    """Counters reachable from two or more rooms (the engine's shared counters)."""
    return frozenset(o.pos for o in Kitchen("map", list(rows)).shared_counters())


def is_pass_through(rows: list[str], x: int, y: int) -> bool:
    """A counter that touches floor in two different rooms: the only way items cross between
    rooms. A counter with floor on both sides inside one room (an island, as in counter_circuit)
    is an ordinary counter."""
    return (x, y) in _pass_counters(tuple(rows))


def crate_tile(x0: int, y0: int) -> str:
    """Onion crate: a wooden box with slats, full of onions."""
    s = counter_tile(x0, y0)
    s += (f"<rect x='{x0 + 9}' y='{y0 + 9}' width='{T - 18}' height='{T - 24}' rx='7' fill='#a8703a' "
          f"stroke='{INK}' stroke-width='2.5'/>"
          f"<rect x='{x0 + 15}' y='{y0 + 15}' width='{T - 30}' height='{T - 36}' rx='5' fill='#6b4423'/>")
    for dx, dy, r, ang in [(27, 34, 11, -20), (60, 33, 11, 18), (44, 30, 11.5, 4), (34, 49, 10.5, 24), (55, 49, 10.5, -16)]:
        s += onion(x0 + dx, y0 + dy, r, ang)
    return s


def rack_tile(x0: int, y0: int) -> str:
    """Dish rack: a tray holding a stack of white plates (offset circles give the stacked look)."""
    s = counter_tile(x0, y0)
    s += (f"<rect x='{x0 + 9}' y='{y0 + 9}' width='{T - 18}' height='{T - 24}' rx='8' fill='#7aa7c7' "
          f"stroke='{INK}' stroke-width='2.5'/>")
    for i, (dx, dy) in enumerate([(0, 8), (0, 4), (0, 0)]):
        s += plate(x0 + T / 2 + dx, y0 + 40 - dy * 1.5, 22 - i)
    return s


def hatch_tile(x0: int, y0: int) -> str:
    """Serving hatch: counter with a green mat, a bell and an arrow pointing out."""
    s = "<g data-tile='hatch'>" + counter_tile(x0, y0)
    s += (f"<rect x='{x0 + 8}' y='{y0 + 8}' width='{T - 16}' height='{T - 22}' rx='8' fill='#2f9e6b' "
          f"stroke='{INK}' stroke-width='2.5'/>"
          f"<rect x='{x0 + 14}' y='{y0 + 14}' width='{T - 28}' height='{T - 34}' rx='5' fill='none' "
          f"stroke='#bfeed7' stroke-width='2' stroke-dasharray='5 4'/>"
          # bell: dome, rim and knob
          f"<path d='M{x0 + 30} {y0 + 50} a14 14 0 0 1 28 0 z' fill='#f6d35b' stroke='{INK}' stroke-width='2'/>"
          f"<rect x='{x0 + 27}' y='{y0 + 50}' width='34' height='5' rx='2.5' fill='#e0b53a' stroke='{INK}' "
          f"stroke-width='1.5'/>"
          f"<circle cx='{x0 + 44}' cy='{y0 + 33}' r='3.5' fill='#f6d35b' stroke='{INK}' stroke-width='1.5'/>"
          # arrow
          f"<path d='M{x0 + 66} {y0 + 30} h10 m-4 -4 l4 4 l-4 4' fill='none' stroke='#ffffff' stroke-width='3' "
          f"stroke-linecap='round' stroke-linejoin='round'/>")
    return s + "</g>"


def pot_tile(x0: int, y0: int, pot: PotState | None, cook_s: int = 20) -> str:
    """Pot from above: counter base, dark pot with handles, onions inside, and a state overlay."""
    s = counter_tile(x0, y0)
    cx, cy, r = x0 + T / 2, y0 + 40, 27
    state = pot.state if pot else "empty"
    if state == "ready":   # green glow behind the pot
        s += f"<circle cx='{cx}' cy='{cy}' r='{r + 10}' fill='#3ddc84' fill-opacity='0.55'/>"
    s += (f"<rect x='{cx - r - 9}' y='{cy - 5}' width='{2 * r + 18}' height='10' rx='5' fill='#2a2a2e' "
          f"stroke='{INK}' stroke-width='1.5'/>"   # side handles
          f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='#3b3b42' stroke='{INK}' stroke-width='3'/>"
          f"<circle cx='{cx}' cy='{cy}' r='{r - 6}' fill='{SOUP if state == 'ready' else '#1d1d22'}'/>")
    if state == "ready":
        s += (f"<circle cx='{cx - 8}' cy='{cy - 8}' r='5' fill='#fff2c4'/>"
              f"<path d='M{cx - 10} {cy + 1} l7 8 l13 -16' fill='none' stroke='#14804a' stroke-width='5' "
              f"stroke-linecap='round' stroke-linejoin='round'/>")
    elif pot and pot.onions:
        spots = [(-9, -1, -18), (9, -1, 16), (0, 9, 4)]
        for dx, dy, ang in spots[:min(pot.onions, 3)]:
            s += onion(cx + dx, cy + dy, 11.5, ang)
    if state == "cooking" and pot:
        frac = max(0.0, min(1.0, 1 - (pot.ready_in or 0) / max(1, cook_s)))
        circ = 2 * 3.14159 * (r + 4)
        s += (f"<circle cx='{cx}' cy='{cy}' r='{r + 4}' fill='none' stroke='#2b2118' stroke-opacity='0.55' "
              f"stroke-width='5'/>"
              f"<circle cx='{cx}' cy='{cy}' r='{r + 4}' fill='none' stroke='#ffb020' stroke-width='5' "
              f"stroke-linecap='round' stroke-dasharray='{circ * frac:.1f} {circ:.1f}' "
              f"transform='rotate(-90 {cx} {cy})'/>")
        for dx, dy, pr in [(-10, -34, 6), (4, -42, 8), (14, -30, 5)]:   # steam puffs above the pot
            s += f"<circle cx='{cx + dx}' cy='{cy + dy}' r='{pr}' fill='#ffffff' fill-opacity='0.8'/>"
        s += pill(cx, y0 + T - 20, f"{pot.ready_in}s", 14, "#ffffff", "#c2410c")
    elif state == "ready":
        s += pill(cx, y0 + T - 20, "READY", 12, "#ffffff", "#14804a")
    return s


def counter_item(item: str, x0: int, y0: int) -> str:
    """An item lying on a counter, drawn on a small white disc so it stands out from the wood."""
    cx, cy = x0 + T / 2, y0 + 34
    return (f"<circle cx='{cx}' cy='{cy}' r='20' fill='#ffffff' fill-opacity='0.55' stroke='{INK}' "
            f"stroke-opacity='0.5' stroke-width='1.5'/>" + item_icon(item, cx, cy, 15))


# ---------------------------------------------------------------- chefs

def chef(agent: AgentState, letter: str, colour: str, x0: int, y0: int) -> str:
    """Top-down chef: shadow, coloured shoulders, face under a puffy 3-bump hat with a lettered band, two hands."""
    cx, cy = x0 + T / 2, y0 + 48
    skin = "#f7d3b0"
    s = (f"<ellipse cx='{cx}' cy='{cy + 24}' rx='34' ry='11' fill='#000000' fill-opacity='0.22'/>"   # drop shadow
         f"<ellipse cx='{cx}' cy='{cy + 17}' rx='32' ry='16' fill='{colour}' stroke='{INK}' stroke-width='2.5'/>"
         f"<path d='M{cx - 22} {cy + 12} q22 -8 44 0' stroke='#ffffff' stroke-opacity='0.35' stroke-width='3' "
         f"fill='none' stroke-linecap='round'/>"
         f"<circle cx='{cx}' cy='{cy + 3}' r='16' fill='{skin}' stroke='{INK}' stroke-width='2.5'/>"
         # hat: three bumps, then a coloured band carrying the letter
         f"<circle cx='{cx - 11}' cy='{cy - 11}' r='9' fill='#ffffff' stroke='{INK}' stroke-width='2'/>"
         f"<circle cx='{cx + 11}' cy='{cy - 11}' r='9' fill='#ffffff' stroke='{INK}' stroke-width='2'/>"
         f"<circle cx='{cx}' cy='{cy - 16}' r='10' fill='#ffffff' stroke='{INK}' stroke-width='2'/>"
         f"<rect x='{cx - 18}' y='{cy - 12}' width='36' height='10' fill='#ffffff'/>"
         f"<rect x='{cx - 18}' y='{cy - 9}' width='36' height='11' rx='4' fill='{colour}' stroke='{INK}' stroke-width='2'/>"
         f"<text x='{cx}' y='{cy - 0.5}' font-size='11' font-weight='800' fill='#ffffff' text-anchor='middle' "
         f"font-family='{FONT}'>{esc(letter)}</text>"
         # face under the brim: eyes, rosy cheeks, small smile
         f"<circle cx='{cx - 6}' cy='{cy + 7}' r='2' fill='{INK}'/><circle cx='{cx + 6}' cy='{cy + 7}' r='2' fill='{INK}'/>"
         f"<circle cx='{cx - 11}' cy='{cy + 11}' r='3.2' fill='#f08a8a' fill-opacity='0.65'/>"
         f"<circle cx='{cx + 11}' cy='{cy + 11}' r='3.2' fill='#f08a8a' fill-opacity='0.65'/>"
         f"<path d='M{cx - 4} {cy + 11} q4 4 8 0' stroke='{INK}' stroke-width='1.8' fill='none' stroke-linecap='round'/>")
    if agent.holding:   # carried item sits between the two hands, at the front
        s += item_icon(agent.holding, cx, cy + 25, 10)
    for hx in (cx - 20, cx + 20):   # two small hands at the front
        s += f"<circle cx='{hx}' cy='{cy + 25}' r='6' fill='{skin}' stroke='{INK}' stroke-width='2'/>"
    s += pill(cx, y0 + 11, agent.name, 11, "#ffffff", colour)
    if agent.status == "deciding":   # thought bubble: big bubble with dots and two small trail circles
        bx, by = cx + 26, y0 + 28
        s += (f"<circle cx='{bx - 12}' cy='{by + 14}' r='2.5' fill='#ffffff' stroke='{INK}' stroke-width='1.2'/>"
              f"<circle cx='{bx - 6}' cy='{by + 8}' r='3.5' fill='#ffffff' stroke='{INK}' stroke-width='1.2'/>"
              f"<ellipse cx='{bx + 4}' cy='{by - 4}' rx='15' ry='10' fill='#ffffff' stroke='{INK}' stroke-width='2'/>"
              f"<text x='{bx + 4}' y='{by + 2}' font-size='16' font-weight='800' fill='{INK}' text-anchor='middle' "
              f"font-family='{FONT}'>...</text>")
    return s


# ---------------------------------------------------------------- order strip

def order_strip(orders: list[OrderState], t: int, width: int) -> str:
    """Dark strip with one ticket per open order; a ticket turns red when under 10 s are left."""
    s = f"<rect x='0' y='0' width='{width}' height='{STRIP}' fill='#3b2a1e'/>"
    s += pill(46, STRIP / 2, "ORDERS", 12, "#3b2a1e", "#f6e7c8")
    if not orders:
        return s + pill(width / 2, STRIP / 2, "no open orders", 12, "#f6e7c8", "#5a4331")
    tw, gap, x = 126, 8, 96
    room = max(1, (width - x) // (tw + gap))
    for o in orders[:room]:
        left = max(0, o.due_t - t)
        urgent = left < 10
        bg, fg = ("#d63b3b", "#ffffff") if urgent else ("#fbf1d9", "#3a2616")
        s += (f"<rect x='{x}' y='8' width='{tw}' height='{STRIP - 16}' rx='6' fill='{bg}' stroke='#1c130c' "
              f"stroke-width='2'/>"
              f"<text x='{x + 10}' y='{STRIP / 2 + 5}' font-size='13' font-weight='700' fill='{fg}' "
              f"font-family='{FONT}'>#{o.id} soup</text>"
              f"<text x='{x + tw - 10}' y='{STRIP / 2 + 5}' font-size='16' font-weight='800' fill='{fg}' "
              f"text-anchor='end' font-family='{FONT}'>{left}s</text>")
        x += tw + gap
    if len(orders) > room:
        s += pill(width - 24, STRIP / 2, f"+{len(orders) - room}", 12, "#f6e7c8", "#5a4331")
    return s


# ---------------------------------------------------------------- entry point

def map_tiles(rows: list[str], pots: list[PotState], counters: dict[str, str], cook_s: int = 20,
              top: int = STRIP) -> list[str]:
    """Every tile of the map (with its pot and counter item), the grid starting `top` px down.
    With no pots and no counters it is the static background the static site draws on."""
    cols = max(len(r) for r in rows)
    at = {tuple(p.pos): p for p in pots}
    parts = []
    for y, line in enumerate(rows):
        for x, ch in enumerate(line.ljust(cols)):
            x0, y0 = x * T, top + y * T
            if ch == "X":
                parts.append(pass_counter_tile(x0, y0) if is_pass_through(rows, x, y) else counter_tile(x0, y0))
            elif ch == "O":
                parts.append(crate_tile(x0, y0))
            elif ch == "D":
                parts.append(rack_tile(x0, y0))
            elif ch == "S":
                parts.append(hatch_tile(x0, y0))
            elif ch == "P":
                parts.append(pot_tile(x0, y0, at.get((x, y)), cook_s))
            else:
                parts.append(floor_tile(x, y, x0, y0))
            item = counters.get(f"{x},{y}")
            if item and ch in "XOD":
                parts.append(counter_item(item, x0, y0))
    return parts


def render_svg(header: Header, frame: Frame) -> str:
    """The whole scene as an SVG string (no newlines or indentation, so Markdown leaves it alone)."""
    rows = header.map
    cols = max(len(r) for r in rows)
    width, height = cols * T, STRIP + len(rows) * T
    parts = [DEFS, order_strip(frame.orders, frame.t, width)]
    parts += map_tiles(rows, frame.pots, frame.counters, header.cook_s)
    names = [a.name for a in header.agents]
    letters = {a.name: a.letter for a in header.agents}
    for a in frame.agents:   # chefs last so they draw on top of tiles
        colour = AGENT_COLOURS[names.index(a.name) % len(AGENT_COLOURS)] if a.name in names else AGENT_COLOURS[0]
        parts.append(chef(a, letters.get(a.name, a.name[:1]), colour, a.pos[0] * T, STRIP + a.pos[1] * T))
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='100%' "
            f"style='max-width:{max(width, 760)}px;display:block;margin:auto;border-radius:12px' "
            f"role='img' aria-label='Kitchen map'>" + "".join(parts) + "</svg>")
