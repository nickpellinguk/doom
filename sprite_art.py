#!/usr/bin/env python3
"""Pickup sprites (docs/tube_master.md H6 step 2): the art.

Each billboard kind's DOOM sprite as TEXEL BYTES, the floors' format: a
texel is a TONE (master_assets.TONES: a solid colour, or black / white +
a colour), one Mode 2 byte, written as is on a line pair's even line and
FLIP (its pixels swapped) on the odd line -- the cross-hatch comes free
with the byte. A transparent texel is TRANS, a byte no tone makes; the
drawer skips it.

The grids are hand-editable, as the flats' are: art/sprites/NAME.txt, one
character a texel (wall_art.TCH, ' ' transparent), the sprite's own size
(one texel a DOOM pixel: a near pickup is a few dozen bytes wide, so a
texel is about a byte there). A missing grid is seeded from DOOM1.WAD by
the quantiser below.

    python3 sprite_art.py            # seed missing grids, write the preview
    python3 sprite_art.py --reseed   # reseed every grid from the WAD
"""
import os, sys

import numpy as np

import master_assets as M
import wall_art as W

ROOT = M.ROOT
ART_DIR = os.path.join(ROOT, 'art', 'sprites')

# kind index (e1m1 K_*) -> sprite lump. Green and blue armour share K_VEST
# (one geometry, one art): ARM1A0.
K_HEX, K_LAMP, K_POTION, K_HELMET, K_BOXS, K_BOXM, K_VEST = range(7)
SPRITES = ['BAR1A0', 'COLUA0', 'BON1A0', 'BON2A0', 'STIMA0', 'MEDIA0', 'ARM1A0']

TRANS = 0x80                     # transparent: no tone byte, nor its FLIP
_TONE_BYTES = {M.tone_byte(t) for t in range(len(M.TONES))}
assert TRANS not in _TONE_BYTES and M.FLIP[TRANS] not in _TONE_BYTES



VIVID = list(range(1, 7)) + list(range(8, 20))   # hues, K + hue, W + hue


def _ramp(px):
    """A pixel's tone ramp (master_assets.TONE_RAMPS) by its own hue:
    a low-saturation pixel is grey; else the dominant channel's ramp
    (red-dominant browns and reds split by how much green they carry)."""
    r, g, b = px
    if max(px) - min(px) < 48:
        return 'grey'
    if r >= g and r >= b:
        return 'red' if g < 0.45 * r else 'brown'
    return 'green' if g >= b else 'blue'


def seed(wad, name):
    """Tones (h, w), -1 transparent, from the WAD: the flats' tone ramps
    (master_assets.TONE_RAMPS) chosen PER PIXEL by hue (one ramp a sprite,
    as a flat takes, turns a white box with a red cross brown), the
    brightness normalised over the sprite's opaque pixels (5..95%)."""
    idx = wad._patch(name)
    ok = idx >= 0
    rgb = wad.pal[np.maximum(idx, 0)]
    lum = rgb @ M.LUMA
    lo, hi = np.percentile(lum[ok], 5), np.percentile(lum[ok], 95)
    t = np.clip((lum - lo) / max(hi - lo, 1), 0, 1)
    q = np.full(idx.shape, -1)
    for y, x in zip(*np.nonzero(ok)):
        ramp = M.TONE_RAMPS[_ramp(rgb[y, x])]
        tl = [M.tone_rgb(k) @ M.LUMA / 255 for k in ramp]
        q[y, x] = ramp[int(np.argmin([abs(t[y, x] - v) for v in tl]))]
        c = rgb[y, x]
        if c.max() - c.min() > 64 and c.max() > 96:     # VIVID: a red cross,
            g = np.clip(c * 1.5, 0, 255)                 # a green glow keep
            q[y, x] = min(VIVID, key=lambda k: ((M.tone_rgb(k) - g) ** 2).sum())
    return q


def grid_path(name):
    return os.path.join(ART_DIR, name + '.txt')


def save_grid(name, q):
    os.makedirs(ART_DIR, exist_ok=True)
    with open(grid_path(name), 'w') as f:
        f.write(f'# {name} {q.shape[1]}x{q.shape[0]} tones (wall_art.TCH), '
                f'space transparent; seeded by sprite_art.py\n')
        for r in q:
            f.write(''.join(' ' if t < 0 else W.TCH[t] for t in r).rstrip() + '\n')


def load_grid(name):
    """Tones (h, w), -1 transparent, as drawn in art/sprites/NAME.txt."""
    rows = [l.rstrip('\n') for l in open(grid_path(name)) if not l.startswith('#')]
    w = max(len(r) for r in rows)
    return np.array([[-1 if ch == ' ' else W.TCH.index(ch) for ch in r.ljust(w)]
                     for r in rows])


def texels(kind):
    """The kind's art, column-major: [column][row] texel bytes (TRANS
    transparent). This is what the drawer reads."""
    g = load_grid(SPRITES[kind])
    return [[TRANS if t < 0 else M.tone_byte(t) for t in g[:, c]]
            for c in range(g.shape[1])]


def size(kind):
    """(width, height) in texels."""
    t = texels(kind)
    return len(t), len(t[0])


def build(reseed=False):
    wad = M.Wad()
    for name in SPRITES:
        if reseed or not os.path.exists(grid_path(name)):
            save_grid(name, seed(wad, name))
            print('seeded', name)


def preview(path, scale=4):
    """Every kind decoded from its texel bytes, cross-hatched as drawn (a
    texel two lines: the byte, then its FLIP), on grey."""
    import pygame
    pal = M.palette16(0)
    cols = []
    for k in range(len(SPRITES)):
        t = texels(k)
        w, h = len(t), len(t[0])
        img = np.full((2 * h, 2 * w, 3), 96, np.uint8)
        for c in range(w):
            for r in range(h):
                b = t[c][r]
                if b == TRANS:
                    continue
                for dy, bb in ((0, b), (1, M.FLIP[b])):
                    l, rt = M.mode2_pixels(bb)
                    img[2 * r + dy, 2 * c] = pal[l]
                    img[2 * r + dy, 2 * c + 1] = pal[rt]
        cols.append(img)
    H = max(i.shape[0] for i in cols)
    gap = np.full((H, 4, 3), 160, np.uint8)
    row = np.concatenate(sum(([np.pad(i, ((H - i.shape[0], 0), (0, 0), (0, 0)),
                                      constant_values=96), gap] for i in cols), []), 1)
    row = row.repeat(scale, 0).repeat(scale, 1)
    surf = pygame.surfarray.make_surface(row.swapaxes(0, 1))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pygame.image.save(surf, path)


if __name__ == '__main__':
    build('--reseed' in sys.argv)
    for k, n in enumerate(SPRITES):
        print(f'  {k} {n}: {size(k)[0]}x{size(k)[1]} texels')
    p = os.path.join(ROOT, 'build', 'master', 'sprites.png')
    preview(p)
    print('preview', p)
