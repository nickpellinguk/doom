#!/usr/bin/env python3
"""Step 6b: the wall textures redrawn for clarity in Mode 2's 8 colours.

Each texture is art-directed (ART below) rather than nearest-colour matched.
At texel resolution (the box-scaled source, tiling), its brightness is split
into STRUCTURE and DETAIL:

  region   the local mean brightness (a 3x3 box, wrapping), banded by the
           texture's own percentiles (cuts) into its ramp: the large flat
           areas -- a panel, a board, a frame -- one calm colour each
  detail   the texel's brightness against that mean: a seam or recess
           (d < -kd * spread) takes the dark colour, a bevel or rim
           (d > kl * spread) the light colour
  accent   a strongly coloured texel (a lamp, a hazard stripe, a screen)
           takes its hue's colour from the texture's accent map

then a clean-up pass replaces a texel no 4-neighbour shares (and no
diagonal) with its neighbours' majority: no lone specks.

A texture with no entry keeps master_assets.quantise_tex.
"""
import numpy as np

import master_assets as M

K, R, G, Y, B, Mg, C, W = range(8)

# hues an accent can be: from the texel's RGB (see _hue)
HUES = ('red', 'yellow', 'green', 'blue')

# ramp: region colours dark -> light, cuts: the percentiles between them
# dark / light: detail colours (None: no detail of that sign)
# kd / kl: detail thresholds in units of the texture's detail spread
# accent: hue -> colour (hues not listed are not accents)
BROWN = dict(ramp=[K, R], cuts=[12], dark=K, light=Y, kd=1.0, kl=1.6,
             accent={'red': R})
GREY = dict(ramp=[K, B, C], cuts=[8, 55], dark=K, light=W, kd=1.0, kl=1.4,
            accent={'red': R, 'yellow': Y, 'green': G})
ART = {
    # brown: boards, panels, stone (red, black seams, yellow rims)
    'BROWN1':   dict(BROWN, ramp=[R], cuts=[], kd=1.2, kl=2.6),
    'BROWN96':  dict(BROWN, ramp=[K, R], cuts=[25], kl=2.0),
    'BROWN144': dict(BROWN, ramp=[K, R], cuts=[30], kl=2.4),
    'STARTAN1': dict(BROWN, ramp=[K, R, R], cuts=[6, 70], kl=1.2),
    'STARTAN3': dict(BROWN, ramp=[K, R, R], cuts=[6, 70], kl=1.2),
    'SW1STRTN': dict(BROWN, ramp=[K, R, R], cuts=[6, 70], kl=1.2,
                     accent={'red': R, 'green': G, 'yellow': Y}),
    'STEP6':    dict(BROWN, kl=1.2),
    'STEP1':    dict(BROWN, ramp=[K, R, Y], cuts=[30, 85], kl=9),
    'BIGDOOR4': dict(BROWN, ramp=[K, R], cuts=[10], kl=1.3,
                     accent={'red': R, 'yellow': Y}),
    'TEKWALL4': dict(BROWN, ramp=[K, R], cuts=[35], kd=0.8, kl=1.6),
    'EXITDOOR': dict(BROWN, ramp=[K, R, Y], cuts=[20, 92], kl=1.4),
    # grey metal: blue, black recesses, cyan / white rims
    'DOOR3':    dict(GREY, ramp=[B], cuts=[], light=C, kd=1.2, kl=1.2),
    'SUPPORT2': dict(GREY, ramp=[K, B, C], cuts=[10, 60]),
    'DOORSTOP': dict(GREY, ramp=[B, C], cuts=[50], kl=9, kd=9),
    'DOORTRAK': dict(GREY, ramp=[K, B], cuts=[50], kl=9, kd=9),
    'STARGR1':  dict(GREY, ramp=[K, B, C], cuts=[6, 60], kl=1.2),
    'BIGDOOR2': dict(GREY, ramp=[K, B, C], cuts=[8, 50], kl=1.3),
    'LITE3':    dict(GREY, ramp=[B, C, W], cuts=[30, 60], kl=9, kd=9),
    'COMPSPAN': dict(GREY, ramp=[K, B], cuts=[50], kl=9),
    'COMPTILE': dict(GREY, ramp=[K, B], cuts=[45], light=C, kl=1.0,
                     accent={}),
    'COMPTALL': dict(GREY, ramp=[K, B], cuts=[40], light=C, kl=1.2,
                     accent={'red': R, 'yellow': Y, 'green': G}),
    'COMPUTE2': dict(GREY, ramp=[K, B], cuts=[40], light=C, kl=1.2,
                     accent={'red': R, 'yellow': Y, 'green': G}),
    'PLANET1':  dict(GREY, ramp=[K, B, C], cuts=[35, 75], light=W, kl=1.6,
                     accent={'red': R, 'yellow': Y, 'green': G, 'blue': B}),
    'EXITSIGN': dict(GREY, ramp=[K, B], cuts=[30], light=C, kl=9,
                     accent={'red': R}),
    # green: slime, stone, nukage (green, black, yellow)
    'BROWNGRN': dict(ramp=[K, G], cuts=[20], dark=K, light=Y, kd=1.0, kl=2.0,
                     accent={}),
    'SLADWALL': dict(ramp=[K, G], cuts=[30], dark=K, light=Y, kd=1.0, kl=2.0,
                     accent={}),
    'STARG3':   dict(ramp=[K, G, G], cuts=[6, 70], dark=K, light=Y, kd=1.0,
                     kl=1.2, accent={}),
    'NUKE24':   dict(ramp=[R, K, G], cuts=[30, 55], dark=K, light=Y, kd=9,
                     kl=1.4, accent={'green': G}),
    'TEKWALL1': dict(ramp=[K, B], cuts=[45], dark=K, light=C, kd=1.0, kl=1.4,
                     accent={'green': G, 'red': R}),
}


def _box(a, r=1):
    """Mean over a (2r+1)^2 box, wrapping (textures tile)."""
    out = np.zeros_like(a)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out += np.roll(np.roll(a, dy, 0), dx, 1)
    return out / (2 * r + 1) ** 2


def _hue(rgb):
    """Per texel: the accent hue name, or '' (not strongly coloured)."""
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    out = np.full(rgb.shape[:2], '', dtype=object)
    vivid = (sat > 0.6) & (mx > 90)
    out[vivid & (r >= g) & (r >= b) & (g < 0.55 * r)] = 'red'
    out[vivid & (r >= b) & (g >= 0.55 * r) & (b < 0.6 * g)] = 'yellow'
    out[vivid & (g > r) & (g >= b)] = 'green'
    out[vivid & (b > r) & (b > g)] = 'blue'
    return out


def _clean(q):
    """A texel that no 4-neighbour or diagonal shares takes the 4-neighbour
    majority (ties: keep)."""
    h, w = q.shape
    out = q.copy()
    for y in range(h):
        for x in range(w):
            c = q[y, x]
            n4 = [q[(y + dy) % h, (x + dx) % w] for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))]
            nd = [q[(y + dy) % h, (x + dx) % w] for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1))]
            if c in n4 or c in nd:
                continue
            vals, cnt = np.unique(n4, return_counts=True)
            if cnt.max() >= 2 and (cnt == cnt.max()).sum() == 1:
                out[y, x] = vals[cnt.argmax()]
    return out


def convert_scaled(name, sc):
    """Texel colours (0-7) for wall texture `name` from its box-scaled RGB."""
    a = ART.get(name)
    if a is None:
        return M.quantise_tex(sc)
    lum = sc @ M.LUMA
    mean = _box(lum)
    d = lum - mean
    spread = max(np.percentile(np.abs(d), 75), 2.0)
    cuts = np.percentile(mean, a['cuts']) if a['cuts'] else []
    q = np.array(a['ramp'])[sum(mean > c for c in cuts)]
    if a['dark'] is not None:
        q = np.where(d < -a['kd'] * spread, a['dark'], q)
    if a['light'] is not None:
        q = np.where(d > a['kl'] * spread, a['light'], q)
    hue = _hue(sc)
    for h, col in a['accent'].items():
        q = np.where(hue == h, col, q)
    return _clean(q.astype(int))


def convert(name, rgb, th, tw):
    return convert_scaled(name, M.scale_rgb(rgb, th, tw))


# ── the hand-drawn grids (art/walls/NAME.txt): the art source ─────────
ART_DIR = __import__('os').path.join(M.ROOT, 'art', 'walls')
CH = '.rgybmcw' + 'nopqhtzx'            # K R G Y B M C W; cycling 8-15 (step 6e)


def grid_path(name):
    return __import__('os').path.join(ART_DIR, name + '.txt')


def load_grid(name):
    """The texture's grid (rows of CH letters), or None if not drawn."""
    import os
    p = grid_path(name)
    if not os.path.exists(p):
        return None
    rows = [l.rstrip('\n') for l in open(p) if l.strip() and not l.startswith('#')]
    return np.array([[CH.index(ch) for ch in r] for r in rows])


def save_grid(name, q, note=''):
    with open(grid_path(name), 'w') as f:
        f.write(f'# {name} {q.shape[1]}x{q.shape[0]}: .rgybmcw = K R G Y B M C W, '
                f'nopqhtzx = cycling 8-15{note}\n')
        for r in q:
            f.write(''.join(CH[c] for c in r) + '\n')


def wall_texels(name, rgb, th, tw):
    """The wall texture's texel colours: its hand-drawn grid, else the
    art-directed conversion."""
    g = load_grid(name)
    if g is not None:
        assert g.shape == (th, tw), f'{name}: grid {g.shape} != {(th, tw)}'
        return g
    return convert(name, rgb, th, tw)


if __name__ == '__main__':
    # seed: write the conversion as a grid for every texture not yet drawn
    import json, os, sys
    man = json.load(open(os.path.join(M.ROOT, 'build', 'master', 'assets.json')))
    wad = M.Wad()
    src = wad.textures()
    for t in man['textures']:
        if load_grid(t['name']) is None or '--force' in sys.argv:
            rgb = wad.pal[M.clipped(t['name'], src[t['name']])].astype(float)
            save_grid(t['name'], convert(t['name'], rgb, t['height'], t['width']),
                      ' (seeded by wall_art.convert)')
            print('seeded', t['name'])


# ── the flats (step 6b): hand-drawn tone grids, art/flats/NAME.txt ─────
# A flat texel is a TONE (master_assets.TONES): a solid colour, black + a
# colour, or white + a colour, cross-hatched on screen.
FLAT_DIR = __import__('os').path.join(M.ROOT, 'art', 'flats')
TCH = ('.rgybmcw' + 'RGYBMC' + '123456'    # solids; K + r g y b m c; W + r g y b m c
       + 'nopqhtzx' + 'NOPQ')                 # cycling solids; nukage wave pairs (6e)
assert len(TCH) == len(M.TONES)


def load_flat(name):
    import os
    p = os.path.join(FLAT_DIR, name + '.txt')
    if not os.path.exists(p):
        return None
    rows = [l.rstrip('\n') for l in open(p) if l.strip() and not l.startswith('#')]
    return np.array([[TCH.index(ch) for ch in r] for r in rows])


# ── the flats' far tone (step 7n): one tone per flat, art/flats/far.txt ──
# Lines 'NAME TONE' (TONE a TCH letter); a flat not listed is seeded: its
# mean colour matched to the nearest static tone (the nukage flat: its
# commonest tone, keeping the cycling).
FAR_FILE = __import__('os').path.join(FLAT_DIR, 'far.txt')


def far_tones():
    """{flat name: tone} as drawn."""
    import os
    if not os.path.exists(FAR_FILE):
        return {}
    out = {}
    for l in open(FAR_FILE):
        p = l.split('#')[0].split()
        if p:
            assert len(p) == 2 and len(p[1]) == 1, f'far.txt: {l!r}'
            out[p[0]] = TCH.index(p[1])
    return out


def seed_far(tones):
    """A flat's far tone from its 16x16 tones (the seed, for choosing by hand)."""
    blk = np.asarray(tones).ravel()
    if any(t >= 20 for t in blk):                 # cycling: the commonest tone
        return int(np.bincount(blk).argmax())
    static = range(8 + 12)                        # solids, black / white pairs
    trgb = np.array([M.tone_rgb(t) for t in static])
    c = np.mean([M.tone_rgb(t) for t in blk], 0)
    return int(np.argmin(((trgb - c) ** 2).sum(1)))


def save_flat(name, q, note=''):
    import os
    os.makedirs(FLAT_DIR, exist_ok=True)
    with open(os.path.join(FLAT_DIR, name + '.txt'), 'w') as f:
        f.write(f'# {name} 16x16 tones: .rgybmcw solid, RGYBMC black+colour, '
                f'123456 white+colour (r g y b m c), nopqhtzx cycling 8-15, '
                f'NOPQ nukage wave pairs{note}\n')
        for r in q:
            f.write(''.join(TCH[t] for t in r) + '\n')


def flat_tones(name, rgb):
    """A flat's tones: its hand-drawn grid, else the placeholder ramp."""
    g = load_flat(name)
    if g is not None:
        assert g.shape == (M.FLAT_N, M.FLAT_N), f'{name}: grid {g.shape}'
        return g
    return M.quantise_flat(M.scale_rgb(rgb, M.FLAT_N, M.FLAT_N))
