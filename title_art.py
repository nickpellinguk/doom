#!/usr/bin/env python3
"""The loading screen (docs/master_textured_spec.md section 9): DOOM's
TITLEPIC as a 128 x 160 Mode 2 picture, shown in the game's own 10K window
while the disc loads (src/master/mboot.s).

The picture is art/title/TITLE.txt: 160 rows of 128 characters, one a Mode 2
pixel, '.rgybmcw' the eight colours -- hand-editable, as the walls' and the
sprites' grids are. A missing grid is seeded from DOOM1.WAD: each pixel's
colour ramp chosen by its hue (the walls' house style: grey as blue / cyan
/ white), its brightness placed between the ramp's levels with a 2 x 2
ordered dither. Its last character row is black: mboot.s draws the loading
bar there (BAR_LINES), a step for each file loaded.

    python3 title_art.py            # seed if missing, write the preview
    python3 title_art.py --reseed
"""
import os, sys

import numpy as np

import master_assets as M

ROOT = M.ROOT
GRID = os.path.join(ROOT, 'art', 'title', 'TITLE.txt')
W, H = 128, 160
CH = '.rgybmcw'
K, R, G, Y, B, MG, C, WH = range(8)
RAMPS = {'red': [K, R, Y, WH], 'green': [K, G, Y, WH], 'blue': [K, B, C, WH],
         'grey': [K, B, C, WH]}
BAYER = np.array([[0.25, 0.75], [1.0, 0.5]])
BAR_ROW = 19                            # the last character row (lines 152-159):
BAR_LINES = (154, 157)                  #  black, the loading bar on these lines
LUMA = np.array([0.3, 0.59, 0.11])


def _ramp(c):
    if max(c) - min(c) < 40:
        return 'grey'
    r, g, b = c
    if r >= g and r >= b:
        return 'red'
    return 'green' if g >= b else 'blue'


def seed():
    w = M.Wad()
    small = M.scale_rgb(w.pal[w._patch('TITLEPIC')].astype(float), H, W)
    pal = np.array(M.PALETTE, float)
    out = np.zeros((H, W), int)
    for y in range(H):
        for x in range(W):
            c = small[y, x]
            rp = RAMPS[_ramp(c)]
            lum = min(1.0, (c @ LUMA) * 1.25 / 255)
            lv = [pal[k] @ LUMA / 255 for k in rp]
            i = max(0, min(len(rp) - 2, int(np.searchsorted(lv, lum)) - 1))
            t = (lum - lv[i]) / max(lv[i + 1] - lv[i], 1e-6)
            out[y, x] = rp[i + 1] if t > BAYER[y & 1, x & 1] - 0.125 else rp[i]
    out[BAR_ROW * 8:, :] = K            # the loading bar's row: black
    return out


def save(g, note='seeded by title_art.py'):
    os.makedirs(os.path.dirname(GRID), exist_ok=True)
    with open(GRID, 'w') as f:
        f.write(f'# TITLE {W}x{H} Mode 2 pixels: .rgybmcw = K R G Y B M C W ({note})\n')
        for r in g:
            f.write(''.join(CH[v] for v in r) + '\n')


def load():
    rows = [l.rstrip('\n') for l in open(GRID) if not l.startswith('#')]
    assert len(rows) == H and all(len(r) == W for r in rows), 'TITLE.txt is 128 x 160'
    return np.array([[CH.index(c) for c in r] for r in rows])


def screen_bytes():
    """The picture as the game's 10K window holds it: line y, byte column k
    at (y >> 3) * 512 + k * 8 + (y & 7), two pixels a byte."""
    g = load()
    out = bytearray(10240)
    for y in range(H):
        for k in range(64):
            out[(y >> 3) * 512 + k * 8 + (y & 7)] = M.mode2_byte((int(g[y, 2 * k]), int(g[y, 2 * k + 1])))
    return bytes(out)


def preview(path):
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    import pygame
    img = np.array(M.PALETTE, np.uint8)[load()].repeat(3, 0).repeat(6, 1)
    pygame.image.save(pygame.surfarray.make_surface(img.swapaxes(0, 1)), path)


if __name__ == '__main__':
    if '--reseed' in sys.argv or not os.path.exists(GRID):
        save(seed())
        print('seeded', GRID)
    p = os.path.join(ROOT, 'build', 'master', 'title.png')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    preview(p)
    print('preview', p)
