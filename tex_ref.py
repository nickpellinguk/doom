#!/usr/bin/env python3
"""Step 4 model: textured walls on the engine's spans (docs/master_textured_spec.md).

fill_ref.py (step 3) decides WHAT each seg fills -- the bands its span
updates remove, split into ceiling / wall / floor at the seg's front ceiling
(T) and floor (B) lines. This model textures the wall runs, in integer
arithmetic chosen so the 6502 can reproduce it exactly:

  part      solid seg: middle texture (a walled two-sided line has none:
            lower, else upper, else sky, as textured_ref); portal: the top
            band's wall rows are the UPPER texture, the bottom band's the
            LOWER (a sky-to-sky upper is not drawn: those rows are sky)
  u         d, the distance along the engine seg (1/16 world units), is
            perspective-correct at the strip's centre xc = x + 1 (x is its
            even pixel). The weights a, b are the engine's own reciprocals
            at the projected ends, (256 + M8) / 2^S, the NEARER shifted left
            by the S difference (the far end keeps all 9 bits), both to 16
            bits. Per SEG, d is exact at the first and last visible strip
            centres xl, xh:
                pa = wa*(sx2 - xc), pb = wb*(xc - sx1)   (raw: pa + pb)
                (pa, pb shifted right together until both < $8000)
                d  = (d1*pa + d2*pb) // (pa + pb)
            d1 = 0 and d2 = round(16 * seg length), except at a NEAR-CLIPPED
            end, where d comes from the engine's own crossing fraction t
            (fp_cross_t16's 0.8 RN quotient). Per STRIP, a projective map
            between those ends with their raw weights shifted together to
            8 bits (A, B):
                d = (dL*A*(xh-xc) + dH*B*(xc-xl)) // (A*(xh-xc) + B*(xc-xl))
            numerator and denominator step by constants: adds and one
            32/16 division per strip on the 6502, no multiply.
            PIECES: the engine merges colinear neighbour segs with the same
            sectors, and all 20 merges in E1M1 join different linedefs
            (pillars, switches, light strips), so the texture and its u
            restart at each joint: the piece holding d gives the dressing
            and u = 16 * u base - piece start + d (master_walls.py).
            Column = ((u & (16*src_w - 1)) * R) >> 16, R = 4096*tw // src_w
            (every E1M1 source width is a power of two).
  bytes     the unit is the BYTE COLUMN (fill_ref): every write is a whole
            byte, a 4x2 fat pixel. A wall byte is two independent strips,
            (left texel << 2) | right texel: each strip has its own u (at its
            centre, x + 1 and x + 3) and its own v (its own T and B lines,
            at x and x + 2); the run extents, part and piece are the byte's.
  v         5.11 fixed point, 5 integer bits = the texel row (wraps at 32
            for free), stepped per LINE PAIR (a texel is 2 lines: the byte,
            then FLIP of it; a pair moves 2 * step):
                step = K // (B - T),  K = 2048 * th * (fc - fh) // src_h
                v(y) = Vtop + (y_even - T) * step         (mod 65536)
                Vtop = floor(2048 * th * (ztop - fc + yoff) / src_h)
            T, B are the filler's own floored line values at x (the even
            pixel); ztop is the
            pegged texture top (DOOM rules, as textured_ref); y_even is the
            even line of the pair. Texel row = (v >> 11) & (th - 1).

    python3 tex_ref.py      # render the regression poses into build/master/tex/
"""
import os

import fill_ref as Fm
import master_walls as MW
import master_assets as M

ROOT = Fm.ROOT


def cross_t(vy_clip, vy_other, near):
    """The engine's crossing fraction (fp.fp_cross_t16, the TRUE16 view
    totals in counts): t in 0..256, from the CLIPPED endpoint toward the
    other, round to nearest."""
    d = vy_other - vy_clip
    n = near - vy_clip
    while d > 255:
        d >>= 1
        n >>= 1
    if d <= 0:
        return 256
    return min(256, ((n << 8) + (d >> 1)) // d)


class TexRef(Fm.FillRef):
    def __init__(self):
        super().__init__()
        import textured_ref as T
        import fp
        self.near_cross = fp.T16_NEAR_CROSS
        self.T = T.TexturedRef()                    # packed texel bytes by name
        self.tex = self.T.tex
        self.W = MW.Walls(self.dw, self.tex, self.T.A.man)
        self.dbg = {}

    def render(self, px, py, ab):
        self.geom = {}                      # si -> the hook's (sx1 sx2 ft1 ft2 fb1 fb2)
        self.owner = [[None] * Fm.STRIPS for _ in range(Fm.LINES)]   # cell -> si
        return super().render(px, py, ab)

    def _fill(self, si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid):
        W, Bz = self.W, self.bias
        # grid cells: ('b', byte) for a solid shade, ('t', byte, row, col, name)
        # for a wall texel
        info = W.info[si]
        self.geom[si] = (sx1, sx2, ft1, ft2, fb1, fb2)
        b_ceil = M.wall_byte(Fm.SH_SKY if info['sky'] else Fm.SH_CEIL)
        b_floor = M.wall_byte(Fm.SH_FLOOR)
        lo, hi = max(0, x_lo), min(255, x_hi)
        xs = range((lo + 3) & ~3, hi, 4)    # the byte columns this seg owns
        if not xs:
            return
        # d (1/16 world units along the seg) at the projected ends; a
        # near-clipped end from the engine's crossing fraction t
        L16 = W.slot_len[si]
        d1, d2 = 0, L16
        near = self.near[si]
        c1, c2, tvx1, tvy1, tvx2, tvy2 = near[:6]
        if c1 and not c2:
            d1 = (L16 * cross_t(tvy1, tvy2, self.near_cross) + 128) >> 8
        elif c2 and not c1:
            d2 = L16 - ((L16 * cross_t(tvy2, tvy1, self.near_cross) + 128) >> 8)
        # endpoint weights: the engine's 1/depth, (256 + M8) / 2^S, the
        # nearer end shifted left by the S difference, both to 16 bits
        m1, s1, m2, s2 = near[6:10]
        sx = max(s1, s2)
        wa, wb = (256 + m1) << (sx - s1), (256 + m2) << (sx - s2)
        while max(wa, wb) > 0xFFFF:
            wa >>= 1
            wb >>= 1

        def at(xc):                         # (1/depth weight, exact d) at xc
            a, b = wa * (sx2 - xc), wb * (xc - sx1)
            raw = a + b
            while max(a, b) > 0x7FFF:       # (a + b fits 16 bits)
                a >>= 1
                b >>= 1
            return raw, ((d1 * a + d2 * b) // (a + b) if a + b else d1)

        # the map's ends: the first owned strip centre, and the last strip
        # centre inside [lo, hi) (<= sx2); a byte's right strip past it
        # takes its left strip's d
        xl, xh = xs[0] + 1, ((hi - 1) & ~1) + 1
        A, dL = at(xl)
        B, dH = at(xh)
        while max(A, B) > 255:
            A >>= 1
            B >>= 1
        self.dbg[si] = dict(slot=si, l16=L16, d1=d1, d2=d2, wa=wa, wb=wb, xl=xl, xh=xh,
                            dl=dL, dh=dH, A=A, B=B)   # (the 6502 debug view)
        for x in xs:
            o = self._span_at(before, x)
            if o is None:
                continue
            ot, ob = self.top(o, x), self.bot(o, x)
            n = self._span_at(after, x)
            bands = [(ot, ob, 'mid')] if n is None else [(ot, self.top(n, x) - 1, 'up'),
                                                          (self.bot(n, x) + 1, ob, 'lo')]
            T = Fm._floor_interp(x, sx1, ft1, sx2, ft2) + Bz
            B_ = Fm._floor_interp(x, sx1, fb1, sx2, fb2) + Bz
            # the right strip's own lines, for its own v (the extents of
            # every run are the byte's: T and B at x)
            Tr = Fm._floor_interp(x + 2, sx1, ft1, sx2, ft2) + Bz
            Br = Fm._floor_interp(x + 2, sx1, fb1, sx2, fb2) + Bz
            # perspective-correct d at each strip centre (x+1, x+3):
            # projective between the visible ends (8-bit weights; numerator
            # and denominator step by constants, one division per strip)
            def dat(xc):
                dj, dk = xh - xc, xc - xl
                D = A * dj + B * dk
                return (dL * A * dj + dH * B * dk) // D if D else dL
            d = dat(x + 1)
            dr = dat(x + 3) if x + 3 <= xh else d
            # the byte's piece is the LEFT strip's (a merged seg's joint
            # lands on a byte boundary): one part, one v, two columns
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
                        continue
                    elif yb > B_:
                        cell = self._plane(si, 'f', yb - Bz, x, b_floor)
                        self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = cell
                        continue
                    elif part == MW.NONE:
                        v = b_ceil                  # no texture (sky-to-sky upper)
                    else:
                        self.grid[yb - Bz][c] = ('t',) + self._texel(part, u, yb, T, B_)
                        self.grid[yb - Bz][c + 1] = ('t',) + self._texel(part, ur, yb, Tr, Br)
                        continue
                    self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = ('b', v)

    def _plane(self, si, kind, y, x, shade):
        """The cell for line y (unbiased) of byte column x's ceiling
        ('c') or floor ('f') run: step 4 draws the solid shade."""
        return ('b', shade)

    def _texel(self, pi, u, yb, T, B):
        p = self.W.parts[pi]
        tp = self.W.tparams[p['tid']]
        h = B - T
        step = (p['K'] // h) & 0xFFFF if h > 0 else 0
        v = (p['vtop'] + ((yb & ~1) - T) * step) & 0xFFFF
        row = (v >> 11) & (tp['th'] - 1)
        col = ((u & tp['mask']) * tp['R']) >> 16
        return int(self.tex[tp['name']][0][row, col]), row, col, tp['name']

    def _compose(self):
        """The buffer bytes, lit: every byte of a seg is ANDed with its
        front sector's light masks (maskEven on even lines, maskOdd on odd
        lines, after the FLIP); sky is never masked."""
        fb = bytearray(10240)
        sky = M.wall_byte(Fm.SH_SKY)
        for y in range(Fm.LINES):
            row = self.grid[y]
            for k in range(64):
                si = self.owner[y][2 * k]
                cell = row[2 * k]
                if si is None or (cell is not None and cell[0] == 'b' and cell[1] == sky):
                    mask = 0xFF
                else:
                    mask = MW.LIGHT_MASKS[self.W.info[si]['level']][y & 1]
                l, r = row[2 * k], row[2 * k + 1]
                if l is not None and l[0] == 'F':
                    b = l[1]                        # a whole plane byte
                else:
                    b = (((l[1] << 2) & 0xCC) if l is not None else 0) | \
                        (r[1] if r is not None else 0)
                fb[(y >> 3) * 512 + k * 8 + (y & 7)] = (b if y % 2 == 0 else M.FLIP[b]) & mask
        return bytes(fb)


if __name__ == '__main__':
    import compare_renders as C
    import textured_ref as T
    R = TexRef()
    out = os.path.join(ROOT, 'build', 'master', 'tex')
    os.makedirs(out, exist_ok=True)
    for (px, py, ab) in C.POSITIONS:
        fb = R.render(px, py, ab)
        tag = f'{px}_{py}_{ab}'.replace('-', 'm')
        open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
        T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
        print(f'({px},{py},{ab}): unfilled {R.unfilled()}')
