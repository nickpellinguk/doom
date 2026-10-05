#!/usr/bin/env python3
"""Gate for plane_ref.py, the step-5 floor / ceiling model
(docs/master_textured_spec.md).

  - coverage unchanged: every on-map pose filled completely (the off-map
    poses are the known four)
  - the 4.12 integer maths is right: every plane cell's texel is, within
    one texel on both axes (16-texel wrap), the texel a float evaluation
    of the SAME geometry gives -- the engine's prescaled plane heights
    (the ones its floor and ceiling lines come from), exact trig, the
    byte column's and line pair's centre -- on at least 99% of cells
  - reported, not gated: agreement with the float textured reference,
    which uses WORLD heights. The prescaled eye height (41 -> 6 units =
    40 world) and plane heights (quantum ~6.7 world units) scale the
    depth by a few percent, which grows to whole texels with distance;
    the model keeps the floor consistent with the drawn floor lines.
Writes build/master/plane/*.png. Prints PLANEREF: PASS.
"""
import math, os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import compare_renders as C
import master_assets as M
import plane_ref as P
import textured_ref as T

OFFMAP = {(192, -2368, 99), (3648, -2368, 35), (1500, -3700, 0), (3648, -4800, 131)}
F, R = P.PlaneRef(), T.TexturedRef()


def ref_uv(r, c, h, pic):
    """textured_ref._plane_byte's texel coordinates for strip c, row r."""
    xs, ys = 4 * (c >> 1) + 2, 2 * r + 1
    d = ys - T.CY
    if d == 0 or (h - R.vz) * d > 0:
        d = 0.5 if h < R.vz else -0.5
    ey = (R.vz - h) * T.FY / d
    ex = (xs - T.CX) / T.FX * ey
    wx = R.px + ex * R.sin + ey * R.cos
    wy = R.py - ex * R.cos + ey * R.sin
    return int(math.floor(wx / 4)) & 15, int(math.floor(-wy / 4)) & 15


def self_uv(y, c, g, si):
    """Float evaluation of the model's own geometry for cell (y, c)."""
    import doom_wireframe as dw
    v = F.view
    a = dw.byte_to_radians(v['ab'])
    sn, cs = math.sin(a), math.cos(a)
    pxw = dw.MAP_CENTER_X + v['px88'] / 32
    pyw = dw.MAP_CENTER_Y + v['py88'] / 32
    p, kind = y >> 1, g[2]
    k = 2 * p + 1 - 80 if kind == 'f' else 80 - (2 * p + 1)
    fh, ch = dw.fp_segs_vwh[si][3:5]
    D = (v['vz'] - fh) if kind == 'f' else (ch - v['vz'])
    ey = 1024 * D / k
    ex = (4 * (c >> 1) + 2 - 128) / 128 * ey
    wx = pxw + ex * sn + ey * cs
    wy = pyw - ex * cs + ey * sn
    return int(math.floor(wx / 4)) & 15, int(math.floor(-wy / 4)) & 15


def near(d):
    d %= 16
    return min(d, 16 - d) <= 1


out = os.path.join(T.ROOT, 'build', 'master', 'plane')
os.makedirs(out, exist_ok=True)
fails, N, NEAR, EXACT, SN, SOK = [], 0, 0, 0, 0, 0
for pose in C.POSITIONS:
    fb = F.render(*pose)
    R.render(*pose)
    hole = F.unfilled()
    if hole and pose not in OFFMAP:
        fails.append(f'{pose}: {hole} unfilled cells on an on-map pose')
    for y in range(160):
        for c in range(0, 128, 2):
            g = F.grid[y][c]
            if g and g[0] == 'F':
                tu, tv = self_uv(y, c, g, F.owner[y][c])
                SN += 1
                SOK += near(g[3] - tu) and near(g[4] - tv)
    n = ne = ex = 0
    for r in range(80):
        for c in range(0, 128, 2):              # one cell per byte column
            g, t = F.grid[2 * r + 1][c], R.cell[r][c]
            if not (g and g[0] == 'F' and t and t[0] == 'p' and t[2] != T.SKY):
                continue
            pic = t[2]
            if pic.startswith('NUKAGE'):
                pic = 'NUKAGE1'
            if g[5] != pic:
                continue
            n += 1
            tu, tv = ref_uv(r, c, t[1], t[2])
            ne += near(g[3] - tu) and near(g[4] - tv)
            ex += (g[3], g[4]) == (tu, tv)
    if pose not in OFFMAP:
        N, NEAR, EXACT = N + n, NEAR + ne, EXACT + ex
    tag = '_'.join(str(v) for v in pose).replace('-', 'm')
    open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
    T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
    print(f'  {pose}: unfilled {hole}, plane cells {n}, within 1 texel '
          f'{100 * ne / max(n, 1):.1f}%, exact {100 * ex / max(n, 1):.1f}%')
pn, pe = 100 * NEAR / N, 100 * EXACT / N
ps = 100 * SOK / SN
print(f'4.12 maths vs float of the same geometry: {SN} plane cells, within one texel {ps:.2f}%')
print(f'vs the world-height reference (info): {N} cells, within one texel {pn:.2f}%, exact {pe:.2f}%')
if ps < 99.0:
    fails.append(f'4.12 maths within one texel {ps:.2f}% < 99%')
for f in fails[:20]:
    print('FAIL:', f)
print('PLANEREF: FAIL' if fails else 'PLANEREF: PASS')
sys.exit(1 if fails else 0)
