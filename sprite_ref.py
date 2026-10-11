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


class SpriteRef(Fm.FillRef):
    """FillRef's walk, with the objects of each subsector staged at its
    start: self.staged = [(ss, (vy, cx, yt, yb, kind), spans)], spans
    the clip pool's open spans then."""

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
                    self.staged.append((idx, p, list(box['clips'].spans)))
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


if __name__ == '__main__':
    import poses
    R = SpriteRef()
    for p in poses.POSITIONS[:5]:
        R.render(*p)
        print(p, [(ss, v) for ss, v, _ in R.staged])
