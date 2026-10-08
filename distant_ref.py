#!/usr/bin/env python3
"""PROTOTYPE (not gated): a fast path for distant walls (docs/master_textured_spec.md).

A seg whose wall is shorter than DIST_H lines at both projected ends is
drawn without the exact per-seg and per-byte set-up of tex_ref:

  edges     T, B (its front ceiling / floor lines) linear in 8.8 from a
            1/width table instead of the exact floor interpolation (the
            6502's edge steppers and their divides):
                RW[w] = min(65535, 65536 // w), w = sx2 - sx1 (1..255)
                s     = ((y2 - y1) * RW[w]) >> 8          (8.8 lines per x)
                y(x)  = ((y1 << 8) + s * (x - sx1)) >> 8  (floor)
            so a byte column steps y by 4 s: one add per line per byte.
  u         d linear across the seg, from the same table: no perspective
            divide per byte and none at the visible ends:
                sd    = ((d2 - d1) * RW[w]) >> 8          (8.8 d per x)
                d(xc) = ((d1 << 8) + sd * (xc - sx1)) >> 8
            unless its ends' depths differ by more than DIST_RATIO (the
            engine's 1/depth weights wa, wb, as tex_ref): then ONE exact d
            at its midpoint xm = (sx1 + sx2) >> 1, tex_ref's formula,
                dm = L16 * b // (a + b),  a = wa (sx2 - xm), b = wb (xm - sx1)
            and d linear on each half (one divide per seg).
  v         as tex_ref (K // (B - T) per run: already cheap), and the two
            strips of a byte always share it (no shared_limit divide).

The span bands (what the seg fills) are unchanged: only where ceiling,
wall and floor meet inside them, and the texture's u, move.

    python3 distant_ref.py [H]     # compare poses -> build/master/distant/
"""
import os, sys

import fill_ref as Fm
import master_walls as MW
import plane_ref as P
import tex_ref as X

DIST_H = 48                          # lines: a seg shorter than this at both ends
DIST_RATIO = 1.1                     # end depths within this: d linear, no divide


def rw(w):
    return min(0xFFFF, 65536 // w)


def lin(x, x1, y1, x2, y2):
    """y at x on the seg's line, 8.8 from the 1/width table (floor)."""
    s = ((y2 - y1) * rw(x2 - x1)) >> 8
    return ((y1 << 8) + s * (x - x1)) >> 8


class DistantRef(P.PlaneRef):
    def __init__(self, h=DIST_H):
        super().__init__()
        self.h = h
        self.fast = set()                    # this frame's fast segs

    def render(self, px, py, ab):
        self.fast, self.split = set(), 0
        return super().render(px, py, ab)

    def is_fast(self, si, sx1, sx2, ft1, ft2, fb1, fb2):
        near = self.near[si]
        return (0 < sx2 - sx1 <= 255 and not near[0] and not near[1]
                and max(fb1 - ft1, fb2 - ft2) < self.h)

    def _fill(self, si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid):
        if not self.is_fast(si, sx1, sx2, ft1, ft2, fb1, fb2):
            return super()._fill(si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid)
        self.fast.add(si)
        W, Bz = self.W, self.bias
        info = W.info[si]
        self.geom[si] = (sx1, sx2, ft1, ft2, fb1, fb2)
        b_ceil = X.M.wall_byte(Fm.SH_SKY if info['sky'] else Fm.SH_CEIL)
        b_floor = X.M.wall_byte(Fm.SH_FLOOR)
        lo, hi = max(0, x_lo), min(255, x_hi)
        xs = range((lo + 3) & ~3, hi, 4)
        if not xs:
            return
        L16 = W.slot_len[si]
        d1, d2 = 0, L16
        m1, s1, m2, s2 = self.near[si][6:10]
        sx = max(s1, s2)
        wa, wb = (256 + m1) << (sx - s1), (256 + m2) << (sx - s2)
        xm = (sx1 + sx2) >> 1
        if 10 * max(wa, wb) <= 11 * min(wa, wb):       # DIST_RATIO = 1.1
            dfun = lambda xc: lin(xc, sx1, d1, sx2, d2)
        else:
            a, b = wa * (sx2 - xm), wb * (xm - sx1)
            dm = (d1 * a + d2 * b) // (a + b) if a + b else d1
            self.split += 1
            dfun = lambda xc: (lin(xc, sx1, d1, xm, dm) if xc < xm
                               else lin(xc, xm, dm, sx2, d2))
        for x in xs:
            o = self._span_at(before, x)
            if o is None:
                continue
            ot, ob = self.top(o, x), self.bot(o, x)
            n = self._span_at(after, x)
            bands = [(ot, ob, 'mid')] if n is None else [(ot, self.top(n, x) - 1, 'up'),
                                                          (self.bot(n, x) + 1, ob, 'lo')]
            self.bands.setdefault(si, {})[x] = bands
            T = lin(x, sx1, ft1, sx2, ft2) + Bz
            B_ = lin(x, sx1, fb1, sx2, fb2) + Bz
            d, dr = dfun(x + 1), dfun(x + 3)
            start, (p_up, p_lo, p_mid, ubase) = W.dressing_at(si, d)
            u = ubase * 16 - start + d
            ur = ubase * 16 - start + dr
            c = x >> 1
            for y0, y1, which in bands:
                part = p_mid if solid else (p_up if which == 'up' else
                                            p_lo if which == 'lo' else MW.NONE)
                for yb in range(max(y0, Bz), min(y1, Bz + Fm.LINES - 1) + 1):
                    self.owner[yb - Bz][c] = self.owner[yb - Bz][c + 1] = si
                    if yb < T:
                        cell = self._plane(si, 'c', yb - Bz, x, b_ceil)
                        self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = cell
                    elif yb > B_:
                        cell = self._plane(si, 'f', yb - Bz, x, b_floor)
                        self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = cell
                    elif part == MW.NONE:
                        self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = ('b', b_ceil)
                    else:
                        self.grid[yb - Bz][c] = ('t',) + self._texel(part, u, yb, T, B_)
                        self.grid[yb - Bz][c + 1] = ('t',) + self._texel(part, ur, yb, T, B_)


if __name__ == '__main__':
    import poses as C
    import textured_ref as T
    import master_assets as M
    h = int(sys.argv[1]) if len(sys.argv) > 1 else DIST_H
    A, Bf = P.PlaneRef(), DistantRef(h)
    out = os.path.join(Fm.ROOT, 'build', 'master', 'distant')
    os.makedirs(out, exist_ok=True)
    for (px, py, ab) in C.POSITIONS:
        fa, fb = A.render(px, py, ab), Bf.render(px, py, ab)
        tag = f'{px}_{py}_{ab}'.replace('-', 'm')
        T.to_png(fa, os.path.join(out, f'{tag}_a.png'), M.PALETTE)
        T.to_png(fb, os.path.join(out, f'{tag}_b.png'), M.PALETTE)
        nd = sum(x != y for x, y in zip(fa[:8704], fb[:8704]))
        print(f'({px},{py},{ab}): fast segs {len(Bf.fast)} ({Bf.split} split), '
              f'view bytes differing {nd}')
