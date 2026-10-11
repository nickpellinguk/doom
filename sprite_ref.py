#!/usr/bin/env python3
"""Pickup sprites: the spec (docs/tube_master.md H6).

Step 1, the projection: where the engine puts a static object
(e1m1.fp_objects: pickups, floor lamps, barrels) on the screen. This is
`obj_project` (src/bsp/objects.s) in Python, on the Python engine's own
pieces, as the model's walls use them:

  view      the object's position through the vertex pipeline: the TRUE16
            totals (fp_to_view_totals_t16), behind iff vy < 16 counts
  recip     idx = vy >> 4 (fp_recip: near table, far by shifts)
  x         the centre: 128 + rns(counts*m9, S+5) (project_x_c's
            count-native entry; no shrink)
  y         top and bottom: fp_project_y of the s8 height deltas
            (zt - vz, zb - vz), + Y_BIAS (the engine's biased lines)

It is staged per subsector as the engine stages it: (depth key, centre x,
top y, bottom y, kind), in the subsector's table order, at the START of
the subsector (before its segs: nearer geometry is down, its own floor
is not). test_sprite_proj.py holds the 6502 to it.

    python3 sprite_ref.py        # the staged objects at the regression poses
"""
import math, os

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

import fill_ref as Fm


def s8(v):
    v &= 0xFF
    return v - 256 if v & 0x80 else v


def project(o, ctx, vz):
    """obj_project: (vy, cx, yt, yb, kind) or None (behind the near
    verdict). Under fill_ref.master_view (the Master's horizon)."""
    import fp
    tvx, tvy = fp.fp_to_view_totals_t16(o['x'], o['y'], ctx)
    if tvy < fp.T16_NEAR_VERDICT:
        return None
    from endpoint_spans import Y_BIAS
    m8, s = fp.fp_recip(tvy >> 4)
    cx = project_x_counts(tvx, m8, s)
    yt = fp.fp_project_y(s8(o['zt'] - vz), m8, s) + Y_BIAS
    yb = fp.fp_project_y(s8(o['zb'] - vz), m8, s) + Y_BIAS
    return (depth_key(tvy), cx, yt, yb, o['asp'])


def project_x_counts(counts, m8, s):
    """project_x_c on s16 view counts (project.s TRUE16 entry): the
    narrow body's P = floor(counts*m9/256) at net shift S-3, NO shrink
    (fp_project_x's shrink, for 8.8 inputs, differs by a pixel near).
    Net >= 1 is rns(counts*m9, S+5) exactly (floor composition); net <=
    0 is P scaled up. The s16 result wraps, +128 into the s16 store."""
    import fp
    P = (counts * (256 + m8)) >> 8
    n = s - 3
    r = fp.rns(P, n) if n >= 1 else P << -n
    return (r + 128 + 0x8000 & 0xFFFF) - 0x8000


def depth_key(vy):
    """The engine's staged depth: vy, except that a far object's (vy >=
    4096 counts, the reciprocal's far arm) low byte is the arm's scratch,
    vy_l >> 4 -- the same order, which is all it is used for (the
    nearest-first pass)."""
    return vy if vy < 0x1000 else (vy & 0xFF00) | ((vy & 0xFF) >> 4)


class Staging:
    """A render mixin: the objects of each subsector staged at its start,
    self.staged = [(ss, (vy, cx, yt, yb, kind), spans)], spans the clip
    pool's open spans then (endpoint_spans 8-tuples, biased lines)."""

    def render(self, px, py, ab):
        dw = self.dw
        by_ss = {}
        for o in dw.fp_objects:
            by_ss.setdefault(o['ss'], []).append(o)
        self.staged = []
        box = {}
        orig = dw._anim_ss_hook

        def ss_hook(idx):
            if orig is not None:
                orig(idx)
            for o in by_ss.get(idx, ()):
                p = project(o, box['ctx'], box['vz'])
                if p is not None:
                    self._stage((idx, p, list(box['clips'].spans)))
        dw._anim_ss_hook = ss_hook
        orig_bsp = dw.packed_render_bsp

        def bsp(nid, clips, ctx, vz, *a, **k):
            box.update(clips=clips, ctx=ctx, vz=vz)
            return orig_bsp(nid, clips, ctx, vz, *a, **k)
        dw.packed_render_bsp = bsp
        try:
            return super().render(px, py, ab)
        finally:
            dw._anim_ss_hook, dw.packed_render_bsp = orig, orig_bsp


    def _stage(self, e):
        self.staged.append(e)


class SpriteRef(Staging, Fm.FillRef):
    """FillRef's walk with the objects staged (the projection gate's)."""


# ---------------------------------------------------------------------------
# Step 3: the SPRITE records. Per staged object (farthest first; equal
# depths in staging order):
#
#   H     yb - yt, grown per kind (GROW, 256ths: pickups +15%, objects.s
#         obj_gtab) with the base anchored: yt = yb - H'. H <= 0: nothing.
#   dvl   (h << 8) // (H + 1): the texel step a LINE, 8.8 (h the art's
#         height; H + 1 keeps the last pair's row inside the art when yt
#         is odd, so no row is ever clamped).
#         The one divide: a byte column is 4 sx units and sx units are
#         line units (the projection's aspect), so the step a BYTE is
#         du = 4 * dvl and a line PAIR 2 * dvl.
#   u     at byte column k (sampled at x = 4k, the fill's byte sample):
#         (w << 7) + (4k - cx) * dvl, 8.8 -- the art's centre at cx. The
#         column is u >> 8, drawn while 0 <= u < w << 8.
#   lines at column k: [yt, yb) inside the span open at x = 4k when the
#         object's subsector began (read only: no tighten), and inside
#         the view. A line's row is its PAIR's, counted from yt's pair:
#         v(y) = ((y & ~1) - (yt & ~1)) * dvl, row v >> 8 (in [0, h)).
#
#   SPRITE k, y0, y1 (screen lines), kind, col, v0 (v at y0's pair),
#          step (dvl, the WALL record's form: a pair adds 2 * step);
#          line y reads row
#          (v0 + ((y & ~1) - (y0 & ~1)) * step) >> 8 of column col.
#          The byte as is on an even line, FLIP on an odd; TRANS skipped.
# ---------------------------------------------------------------------------
GROW = [0, 0, 38, 38, 38, 38, 38]


def records(staged, top, bot, bias, lines):
    """The frame's SPRITE records from the staged objects, drawing order."""
    import sprite_art as A
    order = sorted(range(len(staged)), key=lambda i: (-staged[i][1][0], i))
    out = []
    for i in order:
        _, (vy, cx, yt, yb, kind), spans = staged[i]
        H = yb - yt
        if H <= 0:
            continue
        H += (H * GROW[kind] + 128) >> 8
        yt = yb - H
        w, h = A.size(kind)
        dvl = (h << 8) // (H + 1)
        for k in range(64):
            x = 4 * k
            u = (w << 7) + (x - cx) * dvl
            if not 0 <= u < w << 8:
                continue
            o = Fm.FillRef._span_at(spans, x)
            if o is None:
                continue
            y0 = max(yt, top(o, x), bias)
            y1 = min(yb - 1, bot(o, x), bias + lines - 1)
            if y0 > y1:
                continue
            v0 = ((y0 & ~1) - (yt & ~1)) * dvl
            out.append(dict(kind='SPRITE', k=k, y0=y0 - bias, y1=y1 - bias,
                            spr=kind, col=u >> 8, v0=v0, step=dvl))
    return out


def draw_records(fb, recs):
    """SPRITE records onto a 10K buffer (bytearray), in order."""
    import sprite_art as A
    import master_assets as M
    art = {}
    for r in recs:
        if r['spr'] not in art:
            art[r['spr']] = A.texels(r['spr'])
        col = art[r['spr']][r['col']]
        for y in range(r['y0'], r['y1'] + 1):
            # the pair's row, from the record's first pair (biased lines
            # are even-offset: Y_BIAS 48, so parity is the screen's)
            v = r['v0'] + ((y & ~1) - (r['y0'] & ~1)) * r['step']
            b = col[v >> 8]
            if b != A.TRANS:
                fb[(y >> 3) * 512 + r['k'] * 8 + (y & 7)] = M.FLIP[b] if y & 1 else b
    return fb


class SpriteDL(Staging, __import__('tube_dl').DLRef):
    """The Tube frame with sprites: DLRef's frame and list, the SPRITE
    records appended (drawn last) and drawn over the frame."""

    def render(self, px, py, ab):
        fb = super().render(px, py, ab)
        self.sprites = records(self.staged, self.top, self.bot, self.bias, Fm.LINES)
        self.dl = self.dl + self.sprites
        return bytes(draw_records(bytearray(fb), self.sprites))


if __name__ == '__main__':
    import poses
    R = SpriteRef()
    for p in poses.POSITIONS[:5]:
        R.render(*p)
        print(p, [(ss, v) for ss, v, _ in R.staged])
