#!/usr/bin/env python3
"""The Master's fake control panel: DOOM's status bar (STBAR, 320 x 32) as
the bottom 24 lines of both screen buffers (docs/master_textured_spec.md).

The 3D view is lines 0..135 (17 character rows, horizon at 68); the panel
is character rows 17..19, lines 136..159. It is drawn once, into both
buffers, and nothing writes there again (the Master never clears a
screen).

It is Mode 2 pixel art (128 x 24, step 6a): the bar's stone in the grey
material ramp (black, blue, cyan), big red numbers with a 1-pixel black
outline, white labels on black, the face redrawn at 10 x 22 and the ammo
counts in yellow (panel_pixels).

    python3 master_panel.py     # writes build/master/panel.bin and .png
"""
import functools, os

import numpy as np

import master_assets as M

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEW_LINES = 136                        # the 3D view: lines 0..135
PANEL_LINES = 24                        # the panel: lines 136..159
PANEL_ROW = VIEW_LINES // 8             # its first character row (17)
PANEL_OFFSET = PANEL_ROW * 512          # its offset in a 10K buffer ($2200)
PANEL_SIZE = PANEL_LINES // 8 * 512     # 1536 bytes


PANEL_W = 128                           # Mode 2 pixels across (2 per byte)
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)


def _draw(w, bar, name, x, y):
    """V_DrawPatch onto the bar: at (x, y) less the patch's offsets."""
    import struct
    _, _, lo, to = struct.unpack_from('<HHhh', w.lump(name))
    p = w._patch(name)
    ys, xs = np.nonzero(p >= 0)
    Y, X = ys + y - to, xs + x - lo
    ok = (X >= 0) & (X < 320) & (Y >= 0) & (Y < 32)
    bar[Y[ok], X[ok]] = p[ys[ok], xs[ok]]


def status_bar():
    """(32, 320) palette indices: STBAR with the arms box drawn on it (the
    numbers, face and lettering are the panel's own pixel art)."""
    w = M.Wad()
    bar = w._patch('STBAR')
    _draw(w, bar, 'STARMS', 104, 0)
    return w, bar


def stone():
    """(24, 128): the bar's stone and frames in the grey material ramp
    (black, blue, cyan), sampled at each Mode 2 pixel's centre."""
    w, bar = status_bar()
    sy = (np.arange(PANEL_LINES) * 2 + 1) * 32 // (2 * PANEL_LINES)
    sx = (np.arange(PANEL_W) * 2 + 1) * 320 // (2 * PANEL_W)
    lum = w.pal[np.clip(bar[np.ix_(sy, sx)], 0, 255)].mean(-1)
    return np.where(lum < 45, BLACK, np.where(lum < 125, BLUE, CYAN))


# The panel's small font: 3 x 5 capitals and digits ('#' lit)
FONT = {
    'A': ['.#.', '#.#', '###', '#.#', '#.#'], 'E': ['###', '#..', '##.', '#..', '###'],
    'H': ['#.#', '#.#', '###', '#.#', '#.#'], 'L': ['#..', '#..', '#..', '#..', '###'],
    'M': ['#.#', '###', '###', '#.#', '#.#'], 'O': ['.#.', '#.#', '#.#', '#.#', '.#.'],
    'R': ['##.', '#.#', '##.', '#.#', '#.#'], 'S': ['.##', '#..', '.#.', '..#', '##.'],
    'T': ['###', '.#.', '.#.', '.#.', '.#.'], '/': ['..#', '..#', '.#.', '#..', '#..'],
    '0': ['###', '#.#', '#.#', '#.#', '###'], '1': ['.#.', '##.', '.#.', '.#.', '###'],
    '2': ['###', '..#', '###', '#..', '###'], '3': ['###', '..#', '###', '..#', '###'],
    '4': ['#.#', '#.#', '###', '..#', '..#'], '5': ['###', '#..', '###', '..#', '###'],
    '6': ['###', '#..', '###', '#.#', '###'], '7': ['###', '..#', '..#', '..#', '..#'],
    '8': ['###', '#.#', '###', '#.#', '###'], '9': ['###', '#.#', '###', '..#', '###'],
}
# The big numbers: 4 x 10, seven segments with 2-line horizontals
_SEG = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fbgc', '5': 'afgcd',
        '6': 'afgecd', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg'}


def big_glyph(c):
    if c == '%':
        return ['#..#', '#..#', '...#', '..#.', '..#.', '.#..', '.#..', '#...', '#..#', '#..#']
    g = [['.'] * 4 for _ in range(10)]
    on = _SEG[c]
    for seg, rows, cols in (('a', (0, 1), range(4)), ('g', (4, 5), range(4)),
                            ('d', (8, 9), range(4)), ('f', (0, 1, 2, 3, 4, 5), (0,)),
                            ('b', (0, 1, 2, 3, 4, 5), (3,)), ('e', (4, 5, 6, 7, 8, 9), (0,)),
                            ('c', (4, 5, 6, 7, 8, 9), (3,))):
        if seg in on:
            for r in rows:
                for x in cols:
                    g[r][x] = '#'
    return [''.join(r) for r in g]


def text_width(t):
    return sum(len(FONT[c][0]) + 1 for c in t) - 1


def _glyphs(px, rows_of, t, x, y, colour):
    for c in t:
        g = rows_of(c)
        for r, row in enumerate(g):
            for i, b in enumerate(row):
                if b == '#':
                    px[y + r, x + i] = colour
        x += len(g[0]) + 1


def _text(px, t, x, y, colour=WHITE):
    _glyphs(px, FONT.__getitem__, t, x, y, colour)


def _big(px, t, right, y=1):
    """Big red numbers ending at column right - 1, with a 1-pixel black
    outline (the 8 neighbours)."""
    body = np.zeros(px.shape, bool)
    x = right - 5 * len(t) + 1
    _glyphs(body.view(np.uint8), big_glyph, t, x, y, 1)
    ring = np.zeros_like(body)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            ring |= np.roll(np.roll(np.pad(body, 1), dy, 0), dx, 1)[1:-1, 1:-1]
    px[ring & ~body] = BLACK
    px[body] = RED


def _box(px, x0, y0, x1, y1):
    px[y0:y1 + 1, x0:x1 + 1] = BLACK


# The labels: (text, its first column), each in a black box just around it
# (DOOM's ARMS label is dropped: at 0.4 scale it would touch HEALTH, and
# the box above it shows the weapon numbers)
LABELS = [('AMMO', 1), ('HEALTH', 19), ('ARMOR', 73)]
LABEL_Y = 17
# The ammo table: the counts only (have / most), yellow as DOOM's
AMMO = [(50, 200), (0, 50), (0, 50), (0, 300)]
# The face (DOOM's STFST01, straight ahead) at 10 x 22 Mode 2 pixels:
# '.' black, 'r' red (hair, shadow), 'y' yellow (skin), 'W' white (lit
# brow and cheeks, eyes, teeth); the broad head, the bar ears
FACE = [
    '..rrrrrr..',
    '.rrrrrrrr.',
    '.rrrrrrrr.',
    '.rr.rr.rr.',
    '.rrrrrrrr.',
    '.ryyyyyyr.',
    '.yyyyyyyy.',
    '.yWyyyyWy.',
    '.y..yy..y.',
    'ryW.yy.Wyr',
    'ry..yy..yr',
    'ryyyyyyyyr',
    'ryyyrryyyr',
    'ryWyyyyWyr',
    'ryyyyyyyyr',
    '.yr....ry.',
    '.yr.WW.ry.',
    '.yr....ry.',
    '.ryyyyyyr.',
    '..ryyyyr..',
    '..rryyrr..',
    '...rrrr...',
]
FACE_BOX = (57, 1, 70, 23)


def panel_pixels():
    """(24, 128) logical colours: the panel as Mode 2 pixel art."""
    px = stone()
    _big(px, '50', 18)                                  # ammo
    _big(px, '100%', 41)                                # health
    _big(px, '0%', 93)                                  # armour
    for t, x in LABELS:
        _box(px, x - 1, LABEL_Y - 1, x + text_width(t), LABEL_Y + 5)
        _text(px, t, x, LABEL_Y)
    _box(px, 42, 1, 56, 22)                             # the arms numbers
    for i, wp in enumerate(range(2, 8)):
        _text(px, str(wp), 43 + (i % 3) * 5, 2 + (i // 3) * 7,
              YELLOW if wp == 2 else RED)
    x0, y0, x1, y1 = FACE_BOX
    _box(px, x0, y0, x1, y1)
    fx = x0 + (x1 - x0 + 1 - len(FACE[0])) // 2
    for r, row in enumerate(FACE):
        for i, c in enumerate(row):
            px[y0 + r, fx + i] = {'.': BLACK, 'r': RED, 'y': YELLOW, 'W': WHITE}[c]
    _box(px, 99, 0, PANEL_W - 1, PANEL_LINES - 1)       # the ammo table
    for r, (have, most) in enumerate(AMMO):
        t = f'{have}/{most}'
        _text(px, t, PANEL_W - text_width(t), 1 + 6 * r, YELLOW)
    return px


@functools.lru_cache(None)
def panel_bytes():
    """The panel as it sits in a buffer: 1536 bytes, character rows 17..19
    (offset (line >> 3 - 17) * 512 + k * 8 + (line & 7)), one Mode 2 byte
    per two pixels."""
    px = panel_pixels()
    out = bytearray(PANEL_SIZE)
    for y in range(PANEL_LINES):
        for k in range(64):
            out[(y >> 3) * 512 + k * 8 + (y & 7)] = M.mode2_byte(px[y, 2 * k:2 * k + 2])
    return bytes(out)


if __name__ == '__main__':
    import textured_ref as T
    out = os.path.join(ROOT, 'build', 'master')
    os.makedirs(out, exist_ok=True)
    pb = panel_bytes()
    open(os.path.join(out, 'panel.bin'), 'wb').write(pb)
    fb = bytearray(10240)
    fb[PANEL_OFFSET:] = pb
    T.to_png(bytes(fb), os.path.join(out, 'panel.png'), M.PALETTE)
    print(f'panel: {len(pb)} bytes -> build/master/panel.bin, panel.png')
