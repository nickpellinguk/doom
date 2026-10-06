#!/usr/bin/env python3
"""The Master's fake control panel: DOOM's status bar (STBAR, 320 x 32) as
the bottom 24 lines of both screen buffers (docs/master_textured_spec.md).

The 3D view is lines 0..135 (17 character rows, horizon at 68); the panel
is character rows 17..19, lines 136..159. It is drawn once, into both
buffers, and nothing writes there again (the Master never clears a
screen).

It is pixel art at the full Mode 1 resolution (256 x 24), not a texture
match: grey stone is a red / cyan cross-hatch, the big numbers pure red
with a 1-pixel black outline (panel_pixels).

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


DARK, LIGHT = 41, 140                   # grey levels: black below, white above
RED, CYAN, BLACK, WHITE = 1, 2, 0, 3    # logical colours (master_assets.PALETTE)


def _draw(w, bar, name, x, y, big=None):
    """V_DrawPatch onto the bar: at (x, y) less the patch's offsets. With
    `big`, a big-number glyph: only its red body is marked there (its own
    dark edge is left to the stone; the panel outlines the body itself)."""
    import struct
    _, _, lo, to = struct.unpack_from('<HHhh', w.lump(name))
    p = w._patch(name)
    ys, xs = np.nonzero(p >= 0)
    Y, X = ys + y - to, xs + x - lo
    ok = (X >= 0) & (X < 320) & (Y >= 0) & (Y < 32)
    if big is None:
        bar[Y[ok], X[ok]] = p[ys[ok], xs[ok]]
    else:
        body = w.pal[p[ys, xs]][:, 0] >= 90
        big[Y[ok & body], X[ok & body]] = True


def _num(w, bar, font, width, v, x, y, big=None):
    """STlib_drawNum: v right-aligned with its right edge at x."""
    for i, d in enumerate(reversed(str(v))):
        _draw(w, bar, f'{font}{d}', x - width * (i + 1), y, big)


def status_bar():
    """(32, 320) palette indices, and the big numbers' (32, 320) body mask:
    STBAR with a new game's single-player state as st_stuff.c places it
    (ammo 50, health 100%, the pistol, the straight face, armour 0%, the
    four ammo counts)."""
    w = M.Wad()
    bar = w._patch('STBAR')
    big = np.zeros(bar.shape, bool)
    _num(w, bar, 'STTNUM', 14, 50, 44, 3, big)          # ammo
    _num(w, bar, 'STTNUM', 14, 100, 90, 3, big)         # health
    _draw(w, bar, 'STTPRCNT', 90, 3, big)
    _draw(w, bar, 'STARMS', 104, 0)                     # arms box
    for i, wp in enumerate(range(2, 8)):                # the pistol owned
        _draw(w, bar, f'{"STYSNUM" if wp == 2 else "STGNUM"}{wp}',
              111 + (i % 3) * 12, 4 + (i // 3) * 10)
    _draw(w, bar, 'STFST01', 143, 0)                    # face
    _num(w, bar, 'STTNUM', 14, 0, 221, 3, big)          # armour
    _draw(w, bar, 'STTPRCNT', 221, 3, big)
    for y, have, most in ((5, 50, 200), (11, 0, 50), (23, 0, 300), (17, 0, 50)):
        _num(w, bar, 'STYSNUM', 4, have, 288, y)        # ammo counts
        _num(w, bar, 'STYSNUM', 4, most, 314, y)
    return w, bar, big


def panel_pixels():
    """(24, 256) logical colours, drawn as pixel art rather than matched:
    the bar sampled at each pixel's centre (320 x 32 -> 256 x 24), then
      grey stone (DARK..LIGHT)  red / cyan cross-hatch, by (x + y) parity;
                                a lone dark or light grey pixel in it
                                (the stone's own specks) is stone too
      dark grey                 black (dividers, shadows, the face box)
      light grey                white (labels, bevels)
      the big numbers           pure red, with a 1-pixel black outline
      anything coloured         the nearest palette colour (face, the
                                small yellow numbers)"""
    w, bar, big = status_bar()
    sy = (np.arange(PANEL_LINES) * 2 + 1) * 32 // (2 * PANEL_LINES)
    sx = (np.arange(256) * 2 + 1) * 320 // (2 * 256)
    rgb = w.pal[np.clip(bar[np.ix_(sy, sx)], 0, 255)]
    body = big[np.ix_(sy, sx)]
    pal = np.array(M.PALETTE, float)
    out = np.argmin(((rgb[..., None, :] - pal) ** 2).sum(-1), -1)
    grey = (rgb.max(-1) - rgb.min(-1)) < 24
    lum = rgb.mean(-1)
    yy, xx = np.mgrid[0:PANEL_LINES, 0:256]
    hatch = np.where((xx + yy) & 1, CYAN, RED)
    out = np.where(grey & (lum < DARK), BLACK, out)
    out = np.where(grey & (lum >= LIGHT), WHITE, out)
    stone = grey & (lum >= DARK) & (lum < LIGHT)
    for _ in range(2):                                  # the stone's own specks:
        n4 = np.pad(stone, 1, constant_values=True)    # a grey pixel with stone
        lone = (n4[:-2, 1:-1] & n4[2:, 1:-1]           # all round is stone
                & n4[1:-1, :-2] & n4[1:-1, 2:])
        stone |= grey & lone
    out = np.where(stone, hatch, out)
    ring = np.zeros_like(body)                          # 8-neighbours of a digit
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            ring |= np.roll(np.roll(np.pad(body, 1), dy, 0), dx, 1)[1:-1, 1:-1]
    out = np.where(ring & ~body, BLACK, out)
    return np.where(body, RED, out)


@functools.lru_cache(None)
def panel_bytes():
    """The panel as it sits in a buffer: 1536 bytes, character rows 17..19
    (offset (line >> 3 - 17) * 512 + k * 8 + (line & 7)), one Mode 1 byte
    per four pixels."""
    px = panel_pixels()
    out = bytearray(PANEL_SIZE)
    for y in range(PANEL_LINES):
        for k in range(64):
            out[(y >> 3) * 512 + k * 8 + (y & 7)] = M.mode1_byte(px[y, 4 * k:4 * k + 4])
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
