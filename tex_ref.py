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
            even pixel), as an exact rational in the endpoint weights:
                dj = sx2 - xc, dk = xc - sx1                 (clamped >= 0)
                den = a*dj + b*dk,  num = d1*a*dj + d2*b*dk
                (num, den shifted right together until den fits 16 bits)
                d = num // den
            Both sums step by a constant per strip, so the 6502 needs adds
            and one division per strip, no multiply. a, b are the engine's
            own reciprocals at the projected ends, (256 + M8) / 2^S, put on
            a common scale by shifting the NEARER one left by the S
            difference (the far end keeps all 9 bits) and then both right
            until they fit 16 bits. d1 = 0 and d2 = seg length, except at a
            NEAR-CLIPPED end, where d comes from the engine's own crossing
            fraction t (fp_cross_t16's 0.8 RN quotient).
            PIECES: the engine merges colinear neighbour segs with the same
            sectors, and all 20 merges in E1M1 join different linedefs
            (pillars, switches, light strips), so the texture and its u
            restart at each joint: the piece holding d gives the names,
            pegging and u = piece u base + (d - piece start).
            Texture column = floor(u * tw / (16 * src_w)) mod tw.
  v         5.11 fixed point, 5 integer bits = the texel row (wraps at 32
            for free), stepped per LINE PAIR (a texel is 2 lines: the byte,
            then FLIP of it):
                step = floor(2048 * th * (fc - fh) / (src_h * (B - T)))
                v(y) = Vtop + (y_even - T) * step         (mod 65536)
                Vtop = floor(2048 * th * (ztop - fc + yoff) / src_h)
            T, B are the filler's own floored line values at x (the even
            pixel); ztop is the
            pegged texture top (DOOM rules, as textured_ref); y_even is the
            even line of the pair. Texel row = (v >> 11) & (th - 1).

    python3 tex_ref.py      # render the regression poses into build/master/tex/
"""
import math, os

import fill_ref as Fm
import master_assets as M

ROOT = Fm.ROOT
DONTPEGTOP, DONTPEGBOTTOM = 0x08, 0x10


def _name(b):
    return b.rstrip(b'\0').decode().upper()


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
        self._info = {}

    def _seg_info(self, si):
        """The seg's sectors and its PIECES. The engine merges colinear
        neighbour segs with the same sectors (doom_wireframe
        _try_merge_svwh: 20 pairs, every one joining two different
        linedefs -- pillars, switches, light strips), so one engine seg
        can carry several textures with u restarting at each joint. Each
        piece: start (1/16 units from the engine seg's v1), u base (seg
        offset + sidedef x offset, x16), flags, y offset, texture names."""
        if si in self._info:
            return self._info[si]
        dw = self.dw
        seg, front, back = dw.fp_segs_vwh[si][:3]
        V1, V2 = dw.vertexes[seg[0]], dw.vertexes[seg[1]]
        dx, dy = V2[0] - V1[0], V2[1] - V1[1]
        L2 = dx * dx + dy * dy

        def along(v):                       # projection onto V1->V2, 0..L2
            return (v[0] - V1[0]) * dx + (v[1] - V1[1]) * dy

        raw = []
        for s in dw.segs:
            if s[2] != seg[2]:
                continue
            a, b = dw.vertexes[s[0]], dw.vertexes[s[1]]
            if any((p[0] - V1[0]) * dy != (p[1] - V1[1]) * dx for p in (a, b)):
                continue
            if 0 <= along(a) < L2 and 0 < along(b) <= L2:
                raw.append((along(a), s))
        raw.sort()
        assert raw and raw[0][0] == 0, f'seg {si}: no raw seg at v1'
        pieces = []
        for at, s in raw:
            ld = dw.linedefs[s[3]]
            sd = dw.sidedefs[ld[5] if s[4] == 0 else ld[6]]
            pieces.append(dict(start=int(round(16 * at / math.sqrt(L2))),
                               ubase=(s[5] + sd[0]) * 16, flags=ld[2], yoff=sd[1],
                               up=_name(sd[2]), lo=_name(sd[3]), mid=_name(sd[4])))
        info = dict(pieces=pieces, length=math.sqrt(L2),
                    front=dw.sectors[front],
                    back=dw.sectors[back] if back is not None and back >= 0 else None)
        self._info[si] = info
        return info

    def _fill(self, si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid):
        dw, Bz = self.dw, self.bias
        # grid cells: ('b', byte) for a solid shade, ('t', byte, row, col, name)
        # for a wall texel
        info = self._seg_info(si)
        front, back = info['front'], info['back']
        fh, fc = front[0], front[1]
        sky = _name(front[3]) == Fm.SKY
        sky_sky = (not solid) and back is not None and sky and _name(back[3]) == Fm.SKY
        b_ceil = M.wall_byte(Fm.SH_SKY if sky else Fm.SH_CEIL)
        b_floor = M.wall_byte(Fm.SH_FLOOR)
        # the textures of each piece's parts
        for pc in info['pieces']:
            if solid:
                mid = pc['mid'] if pc['mid'] != '-' else (
                    pc['lo'] if pc['lo'] != '-' else pc['up'])
                pc['parts'] = {'mid': self._part(info, pc, mid, 'mid')}
            else:
                pc['parts'] = {'up': self._part(info, pc, pc['up'], 'up') if not sky_sky else None,
                               'lo': self._part(info, pc, pc['lo'], 'lo')}
        # distance along the seg (x16) at the projected endpoints; a
        # near-clipped endpoint from the engine's crossing fraction t
        L16 = int(round(info['length'] * 16))
        u1, u2 = 0, L16
        near = self.near.get(si)
        if near is not None:
            c1, c2, tvx1, tvy1, tvx2, tvy2 = near[:6]
            if c1 and not c2:
                u1 = (L16 * cross_t(tvy1, tvy2, self.near_cross) + 128) >> 8
            elif c2 and not c1:
                u2 = L16 - ((L16 * cross_t(tvy2, tvy1, self.near_cross) + 128) >> 8)
        # perspective weights: the engine's 1/depth at each projected end,
        # (256 + M8) / 2^S, on a common scale (the NEARER end shifted left
        # by the S difference, so the far end keeps all 9 bits), then
        # together right until both fit 16 bits
        m1, s1, m2, s2 = near[6:10]
        sx = max(s1, s2)
        wa, wb = (256 + m1) << (sx - s1), (256 + m2) << (sx - s2)
        while max(wa, wb) > 0xFFFF:
            wa >>= 1
            wb >>= 1
        lo, hi = max(0, x_lo), min(255, x_hi)
        for x in range((lo + 1) & ~1, hi, 2):
            o = self._span_at(before, x)
            if o is None:
                continue
            ot, ob = self.top(o, x), self.bot(o, x)
            n = self._span_at(after, x)
            bands = [(ot, ob, 'mid')] if n is None else [(ot, self.top(n, x) - 1, 'up'),
                                                          (self.bot(n, x) + 1, ob, 'lo')]
            T = Fm._floor_interp(x, sx1, ft1, sx2, ft2) + Bz
            B = Fm._floor_interp(x, sx1, fb1, sx2, fb2) + Bz
            # perspective-correct distance along the seg at the strip's
            # centre xc = x + 1: exact rational in the endpoint weights
            xc = x + 1
            dj, dk = max(0, sx2 - xc), max(0, xc - sx1)
            den = wa * dj + wb * dk
            num = u1 * wa * dj + u2 * wb * dk
            while den > 0xFFFF:
                den >>= 1
                num >>= 1
            d = num // den if den else u1
            pc = info['pieces'][0]
            for p in info['pieces'][1:]:            # the piece holding d
                if d >= p['start']:
                    pc = p
            u = pc['ubase'] + d - pc['start']
            parts = pc['parts']
            c = x >> 1
            for y0, y1, which in bands:
                part = parts.get('mid' if solid else which)
                for yb in range(max(y0, Bz), min(y1, Bz + Fm.LINES - 1) + 1):
                    if yb < T:
                        v = b_ceil
                    elif yb > B:
                        v = b_floor
                    elif part is None:
                        v = b_ceil                  # sky-to-sky upper: sky shows
                    else:
                        self.grid[yb - Bz][c] = ('t',) + self._texel(part, u, yb, T, B, fc, fh)
                        continue
                    self.grid[yb - Bz][c] = ('b', v)

    def _part(self, info, pc, name, which):
        if name == '-' or name not in self.tex:
            return None
        tb, sw, sh = self.tex[name]
        th = tb.shape[0]
        front, back, flags = info['front'], info['back'], pc['flags']
        fh, fc = front[0], front[1]
        if which == 'mid':
            ztop = fh + sh if flags & DONTPEGBOTTOM else fc
        elif which == 'up':
            ztop = fc if flags & DONTPEGTOP else back[1] + sh
        else:
            ztop = fc if flags & DONTPEGBOTTOM else back[0]
        vtop = (2048 * th * (ztop - fc + pc['yoff'])) // sh
        return dict(name=name, tb=tb, sw=sw, sh=sh, th=th, tw=tb.shape[1], vtop=vtop)

    def _texel(self, p, u, yb, T, B, fc, fh):
        h = B - T
        step = (2048 * p['th'] * (fc - fh)) // (p['sh'] * h) if h > 0 and fc > fh else 0
        ye = yb & ~1
        v = (p['vtop'] + (ye - T) * step) & 0xFFFF
        row = (v >> 11) & (p['th'] - 1)
        col = (u * p['tw'] // (16 * p['sw'])) % p['tw']
        return int(p['tb'][row, col]), row, col, p['name']

    def _compose(self):
        fb = bytearray(10240)
        for y in range(Fm.LINES):
            row = self.grid[y]
            for k in range(64):
                l, r = row[2 * k], row[2 * k + 1]
                b = (((l[1] << 2) & 0xCC) if l is not None else 0) | \
                    (r[1] if r is not None else 0)
                fb[(y >> 3) * 512 + k * 8 + (y & 7)] = b if y % 2 == 0 else M.FLIP[b]
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
