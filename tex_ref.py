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
            Index = (u & (16*src_w - 1)) >> shift into a power-of-two
            column index of n entries (n = next power of two >= tw,
            shift = log2(16*src_w / n): every E1M1 source width is a power
            of two), logical column = index * tw // n: no multiply.
            ABOUT ONE DIVISION PER TWO BYTES (steps 5d, 5g): the left
            strip's d (x + 1) is exact on the seg's even bytes; on its odd
            bytes (counted from its first) it is the midpoint of the exact
            d either side, (d(x - 3) + d(x + 5)) >> 1, while x + 5 <= xh.
            The right strip's (x + 3) extrapolates from the previous byte's
            d when that byte computed one (it had wall rows):
                dr = d + ((d - d_prev) >> 1)
            else it is exact; past xh it is the left strip's d.
  bytes     the unit is the BYTE COLUMN (fill_ref): every write is a whole
            byte, a 4x2 fat pixel. A wall byte is two independent strips,
            left texel | (right texel >> 1) (Mode 2: one shift; texels are
            stored as left-pixel bytes): each strip has its own u (at its
            centre, x + 1 and x + 3) and its own v (its own T and B lines,
            at x and x + 2); the run extents, part and piece are the byte's.
            Step 5g: the right strip's lines are the midpoints of the
            byte's and the next byte's, Tr = T + ((T(x + 4) - T) >> 1), and
            its step is exact, K // (Br - Tr) (step 7g: 5g extrapolated it
            from the left steps, stepL + ((stepL - stepL_prev) >> 1), which
            combed the lower parts of close angled walls).
            Step 5n: a run whose two strips' v would differ by less than
            3/8 of a texel SHARES the left strip's v (T, B, step) -- each
            strip keeps its own u. The test is geometric and cheap: per
            seg s_lim = (384 * w - 1) // m (w its projected width, m the
            larger rise of its top and bottom lines; shared_limit), per
            run step <= s_lim. Nothing of the right strip's v is made.
            Step 7d: a run that does NOT share keeps the right strip's v
            only as a 5.3 delta from the left's, exact at the run's first
            line ys and stepped once per character row (4 wall pixels):
                dh0 = (vR(ys) >> 8) - (vL(ys) >> 8)           (mod 256)
                ddh = (stepR - stepL + 16) >> 5   (8 lines' worth, 5.3)
                dh(y) = dh0 + ddh * (y // 8 - ys // 8)         (mod 256)
            and the right row is ((vL >> 8) + dh(y)) mod 256 >> 3.
  v         5.11 fixed point, 5 integer bits = the texel row (wraps at 32
            for free), stepped per LINE PAIR (a texel is 2 lines of the same
            byte; a pair moves 2 * step):
                step = K // (B - T),  K = 2048 * th * (fc - fh) // src_h
                v(y) = Vtop + (y_even - T) * step         (mod 65536)
                Vtop = floor(2048 * th * (ztop - fc + yoff) / src_h)
            T, B are the filler's own floored line values at x (the even
            pixel); ztop is the
            pegged texture top (DOOM rules, as textured_ref); y_even is the
            even line of the pair. Texel row = (v >> 11) & (th - 1).
            Step 7s: a run whose first line ys is 512 or more lines below
            T starts from a step with 8 more fraction bits (near walls:
            see _v), and steps from there.

    python3 tex_ref.py      # render the regression poses into build/master/tex/
"""
import os

import fill_ref as Fm
import master_walls as MW
import master_assets as M

ROOT = Fm.ROOT


def shared_limit(w, m):
    """Step 5n: the largest left step for which a run shares one v.

    Between strips 2 pixels apart the v of a run differs by about the
    step times the lines' rise over those 2 pixels; within a seg the top
    and bottom lines are straight, rising at most m lines over w pixels.
    A run shares when step * m * 2 < 768 * w (3/8 of a texel, 2048 = one),
    i.e. step <= (384 * w - 1) // m: one division per seg, one compare
    per run. A zero-width or flat seg always shares."""
    if w <= 0 or m == 0:
        return 0xFFFF
    return min((384 * w - 1) // m, 0xFFFF)


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
        self.bands = {}                     # si -> x -> the span-diff bands (biased)
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
        d_prev = None                       # (x, d) of the last byte with d
        s_lim = shared_limit(sx2 - sx1, max(abs(ft2 - ft1), abs(fb2 - fb1)))
        for x in xs:
            o = self._span_at(before, x)
            if o is None:
                continue
            ot, ob = self.top(o, x), self.bot(o, x)
            n = self._span_at(after, x)
            bands = [(ot, ob, 'mid')] if n is None else [(ot, self.top(n, x) - 1, 'up'),
                                                          (self.bot(n, x) + 1, ob, 'lo')]
            self.bands.setdefault(si, {})[x] = bands
            T = Fm._floor_interp(x, sx1, ft1, sx2, ft2) + Bz
            B_ = Fm._floor_interp(x, sx1, fb1, sx2, fb2) + Bz
            # the right strip's own lines, for its own v (the extents of
            # every run are the byte's: T and B at x): midpoints with the
            # next byte's
            Tn = Fm._floor_interp(x + 4, sx1, ft1, sx2, ft2) + Bz
            Bn = Fm._floor_interp(x + 4, sx1, fb1, sx2, fb2) + Bz
            Tr, Br = T + ((Tn - T) >> 1), B_ + ((Bn - B_) >> 1)
            # perspective-correct d at the left strip's centre x + 1:
            # projective between the visible ends (8-bit weights; numerator
            # and denominator step by constants, one division per byte).
            # Only a byte with wall rows computes it (the 6502's lazy d).
            def dat(xc):
                dj, dk = xh - xc, xc - xl
                D = A * dj + B * dk
                return (dL * A * dj + dH * B * dk) // D if D else dL
            d = dr = 0                          # (no wall rows: d unused)
            if any(max(y0, Bz, T) <= min(y1, Bz + Fm.LINES - 1, B_)
                   for y0, y1, _ in bands):
                if ((x - xs[0]) >> 2) & 1 and x + 5 <= xh:
                    d = (dat(x - 3) + dat(x + 5)) >> 1  # an odd byte
                else:
                    d = dat(x + 1)
                if x + 3 > xh:
                    dr = d
                elif d_prev is not None and d_prev[0] == x - 4:
                    dr = d + ((d - d_prev[1]) >> 1)
                else:
                    dr = dat(x + 3)
                d_prev = (x, d)
            # the byte's piece is the LEFT strip's (a merged seg's joint
            # lands on a byte boundary): one part, one v, two columns
            start, (p_up, p_lo, p_mid, ubase) = W.dressing_at(si, d)
            u = ubase * 16 - start + d
            ur = ubase * 16 - start + dr
            c = x >> 1
            for y0, y1, which in bands:
                part = p_mid if solid else (p_up if which == 'up' else
                                            p_lo if which == 'lo' else MW.NONE)
                sr = None                       # the right strip's step
                share = False                   # one v for both strips
                if part != MW.NONE and max(y0, Bz, T) <= min(y1, Bz + Fm.LINES - 1, B_):
                    ys = max(y0, Bz, T)             # the run's first line
                    K = W.parts[part]['K']
                    sl = (K // (B_ - T)) & 0xFFFF if B_ > T else 0
                    # the right strip's own step, exact (step 7g: the 5g
                    # extrapolation from the previous byte's left step was
                    # off enough, times the distance from T, to comb the
                    # lower parts of close angled walls)
                    sr = (K // (Br - Tr)) & 0xFFFF if Br > Tr else 0
                    share = sl <= s_lim
                    if not share:
                        # step 7d: the right strip's row is the left v's
                        # high byte plus a 5.3 delta, exact at the run's
                        # first line and stepped by ddh (the steps'
                        # difference over 8 lines, 5.3 rounded) at each
                        # character row the run crosses
                        dh = ((self._v(part, ys, Tr, Br, sr, ys=ys) >> 8)
                              - (self._v(part, ys, T, B_, ys=ys) >> 8)) & 0xFF
                        df = (sr - sl) & 0xFFFF
                        df -= 0x10000 if df & 0x8000 else 0
                        ddh = ((df + 16) >> 5) & 0xFF
                        cr0 = (ys - Bz) >> 3
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
                        self.grid[yb - Bz][c] = ('t',) + self._texel(part, u, yb, T, B_, ys=ys)
                        self.grid[yb - Bz][c + 1] = ('t',) + (
                            self._texel(part, ur, yb, T, B_, ys=ys) if share
                            else self._texel(part, ur, yb, T, B_, ys=ys,
                                             dh=dh + ddh * (((yb - Bz) >> 3) - cr0)))
                        continue
                    self.grid[yb - Bz][c] = self.grid[yb - Bz][c + 1] = ('b', v)

    def _plane(self, si, kind, y, x, shade):
        """The cell for line y (unbiased) of byte column x's ceiling
        ('c') or floor ('f') run: step 4 draws the solid shade."""
        return ('b', shade)

    def _v(self, pi, yb, T, B, step=None, ys=None):
        """v (5.11) of line yb's pair, from lines T, B (or the given step).
        Step 7s: a run (first line ys) starting 512 or more lines below T
        takes its first v with 8 more bits of step, Vtop + (ys - T) * step
        + ((ys - T) * f >> 8), f = ((K % (B - T)) << 8) // (B - T), and
        steps from there: (ys - T) * step alone multiplies the step's
        truncation (a near wall: B - T in the thousands, step a few units)
        into rows of error, different in every column."""
        p = self.W.parts[pi]
        h = B - T
        if step is None:
            step = (p['K'] // h) & 0xFFFF if h > 0 else 0
        if ys is not None and h > 0 and (ys & ~1) - T >= 512:
            ma = (ys & ~1) - T
            q, r = divmod(p['K'], h)
            assert step == q & 0xFFFF
            f = (r << 8) // h                       # the step's next 8 bits
            v0 = p['vtop'] + ma * step + ((ma * f) >> 8)
            return (v0 + ((yb & ~1) - (ys & ~1)) * step) & 0xFFFF
        return (p['vtop'] + ((yb & ~1) - T) * step) & 0xFFFF

    def _texel(self, pi, u, yb, T, B, step=None, dh=None, ys=None):
        """The texel at column u, line yb; with dh (step 7d), the row is
        the high byte of the v from T, B plus dh (5.3), mod 256."""
        tp = self.W.tparams[self.W.parts[pi]['tid']]
        v = self._v(pi, yb, T, B, step, ys)
        if dh is not None:
            v = (((v >> 8) + dh) & 0xFF) << 8
        row = (v >> 11) & (tp['th'] - 1)
        col = ((u & tp['mask']) >> tp['shift']) * tp['tw'] // tp['n']
        return int(self.tex[tp['name']][0][row, col]), row, col, tp['name']

    def _compose(self):
        """The buffer bytes (Mode 2, step 6a: both lines of a wall texel row
        the same byte, no sector light; step 6d: a plane byte FLIP on odd
        lines, the floors' cross-hatch; the sky's solid cells as SKY_BYTE,
        cyan + white, likewise). Below the view, the control panel
        (master_panel)."""
        import master_panel
        fb = bytearray(10240)
        fb[master_panel.PANEL_OFFSET:] = master_panel.panel_bytes()
        sky = ('b', M.wall_byte(Fm.SH_SKY))
        for y in range(Fm.LINES):
            row = self.grid[y]
            for k in range(64):
                l, r = row[2 * k], row[2 * k + 1]
                if l == sky:
                    l = ('F', M.SKY_BYTE)           # the sky: cross-hatched
                if l is not None and l[0] == 'F':
                    b = l[1]                        # a whole plane byte,
                    if y & 1:                       #  pixels swapped on a
                        b = M.FLIP[b]               #  pair's odd line
                else:
                    b = M.wall_pair(l[1] if l is not None else 0,
                                    r[1] if r is not None else 0)
                fb[(y >> 3) * 512 + k * 8 + (y & 7)] = b
        return bytes(fb)


if __name__ == '__main__':
    import poses as C
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
