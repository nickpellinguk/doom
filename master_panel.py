#!/usr/bin/env python3
"""The Master's fake control panel: DOOM's status bar (STBAR, 320 x 32) as
the bottom 24 lines of both screen buffers (docs/master_textured_spec.md).

The 3D view is lines 0..135 (17 character rows, horizon at 68); the panel
is character rows 17..19, lines 136..159. It is drawn once, into both
buffers, and nothing writes there again (the Master never clears a
screen).

The bar is scaled to 128 strips x 24 lines (each strip two Mode 1 pixels,
as the walls), quantised to the same shade pairs as the textures, and
written as the walls are: the byte on even lines, its FLIP on odd lines.

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


GAIN, CHROMA = 1.3, 0.6                 # the bar's own match (M.quantise's)


def _draw(w, bar, name, x, y):
    """V_DrawPatch onto the bar: at (x, y) less the patch's offsets."""
    import struct
    _, _, lo, to = struct.unpack_from('<HHhh', w.lump(name))
    p = w._patch(name)
    ys, xs = np.nonzero(p >= 0)
    Y, X = ys + y - to, xs + x - lo
    ok = (X >= 0) & (X < 320) & (Y >= 0) & (Y < 32)
    bar[Y[ok], X[ok]] = p[ys[ok], xs[ok]]


def _num(w, bar, font, width, v, x, y):
    """STlib_drawNum: v right-aligned with its right edge at x."""
    for i, d in enumerate(reversed(str(v))):
        _draw(w, bar, f'{font}{d}', x - width * (i + 1), y)


def status_bar():
    """(32, 320) palette indices: STBAR with a new game's single-player
    state drawn on it as st_stuff.c places it (ammo 50, health 100%,
    the pistol, the straight face, armour 0%, the four ammo counts)."""
    w = M.Wad()
    bar = w._patch('STBAR')
    _num(w, bar, 'STTNUM', 14, 50, 44, 3)               # ammo
    _num(w, bar, 'STTNUM', 14, 100, 90, 3)              # health
    _draw(w, bar, 'STTPRCNT', 90, 3)
    _draw(w, bar, 'STARMS', 104, 0)                     # arms box
    for i, wp in enumerate(range(2, 8)):                # the pistol owned
        _draw(w, bar, f'{"STYSNUM" if wp == 2 else "STGNUM"}{wp}',
              111 + (i % 3) * 12, 4 + (i // 3) * 10)
    _draw(w, bar, 'STFST01', 143, 0)                    # face
    _num(w, bar, 'STTNUM', 14, 0, 221, 3)               # armour
    _draw(w, bar, 'STTPRCNT', 221, 3)
    for y, have, most in ((5, 50, 200), (11, 0, 50), (23, 0, 300), (17, 0, 50)):
        _num(w, bar, 'STYSNUM', 4, have, 288, y)        # ammo counts
        _num(w, bar, 'STYSNUM', 4, most, 314, y)
    return w, bar


def panel_shades():
    """(24, 128) shade indices: the status bar scaled to 128 strips x 24
    lines, matched with its own gain and colour weight (it is mostly mid
    grey stone, which the texture settings turn into cyan speckle)."""
    w, bar = status_bar()
    rgb = w.pal[np.clip(bar, 0, 255)]
    g, c = M.GAIN, M.CHROMA
    M.GAIN, M.CHROMA = GAIN, CHROMA
    try:
        return M.quantise(M.scale_rgb(rgb, PANEL_LINES, 128))
    finally:
        M.GAIN, M.CHROMA = g, c


@functools.lru_cache(None)
def panel_bytes():
    """The panel as it sits in a buffer: 1536 bytes, character rows 17..19
    (offset (line >> 3 - 17) * 512 + k * 8 + (line & 7))."""
    sh = panel_shades()
    out = bytearray(PANEL_SIZE)
    for y in range(PANEL_LINES):
        for k in range(64):
            b = (((M.wall_byte(sh[y, 2 * k]) << 2) & 0xCC)
                 | M.wall_byte(sh[y, 2 * k + 1]))
            out[(y >> 3) * 512 + k * 8 + (y & 7)] = b if y % 2 == 0 else M.FLIP[b]
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
