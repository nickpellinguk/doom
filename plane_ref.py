#!/usr/bin/env python3
"""Step 5 model: textured floors and ceilings (docs/master_textured_spec.md).

tex_ref.py (step 4) fills each seg's byte columns -- ceiling above its front
ceiling line T, wall between, floor below its floor line B -- with the
planes as solid shades. This model textures the plane cells with the
16x16 flats, in integer arithmetic the 6502 reproduces exactly. Floors are
largely decorative, so the budget is ONE texel read per 4x2 fat pixel: a
plane byte is the flat's byte (both Mode 2 pixels the texel) at the byte
column's centre and the line PAIR's centre, written whole on both lines of
the pair.

  depth     a floor seen at line pair p (lines 2p, 2p+1, centre 2p+1) is
            k = 2p + 1 - 68 lines below the horizon (a ceiling k = 68 -
            (2p+1) above it), k odd in 1..67. From the engine's projection
            (focal 128, prescaled heights with the 1.2 aspect baked in) its
            depth is 1024 * D / k world units, D the eye's prescaled height
            above the plane -- the SAME prescaled heights the walls' floor
            and ceiling lines come from (vz - fh, ch - vz). So
                E = D * (2^20 // k)                     (= 1024 * depth)
  4.4       u, v in texels (a texel is 4 world units, a flat 16 texels):
            the row maths is 4.12 (16 bits); what is stepped is 4.4 (8
            bits, wrapping at 16 texels for free). With c, s the engine's
            8-bit cos / sin magnitudes (unity = 256) and signs:
                Pc = E * |c|,  Ps = E * |s|
                A  = sc * (Pc >> 8)    hV = sc * (Pc >> 14)
                Bs = ss * (Ps >> 8)    hU = ss * (Ps >> 14)
                Uc = Up + A + hU,      dU = 2*hU            (all mod 2^16)
                Vc = Vp - Bs + hV,     dV = 2*hV
            Uc/Vc are at byte column 32's centre (U0 + 32 dU, U0 at column
            0), dU/dV one byte column (4 pixels) on. Each is rounded to 4.4
            (its high byte, + 1 if the low byte >= $80), and byte column kb
            has u = Uc + (kb - 32) * dU (mod 2^8): along a line pair a plane
            is affine, so the 6502 starts a span with one 8x8 multiply per
            axis and steps u += dU, v += dV per byte -- two 8-bit adds, one
            texel read. Anchoring at the centre keeps the steps' rounding
            (1/32 texel a column) within a texel at the screen's edges.
            Up = 1024 * world x, Vp = -1024 * world y of the eye (DOOM flats
            run -y down), from the engine's 8.8 prescaled position.
  texel     flat[(v >> 4) * 16 + (u >> 4)] (NUKAGE1 is static: its
            animation frames were dropped for memory).
  shade     sky ceilings stay solid cyan; a plane the eye is not on the
            right side of (D <= 0) keeps its step-4 shade.

    python3 plane_ref.py     # render the regression poses into build/master/plane/
"""
import os

import fill_ref as Fm
import master_assets as M
import tex_ref as X

ROOT = Fm.ROOT
HORIZON = Fm.HORIZON                  # 68: the Master view's centre


def _name(b):
    return b.rstrip(b'\0').decode().upper()


class PlaneRef(X.TexRef):
    def __init__(self):
        super().__init__()
        self.flat = self.T.flat                     # name -> 16x16 bytes
        self.fid = {f['name']: f['id'] for f in self.T.A.man['flats']}

    def render(self, px, py, ab):
        self.rows = {}                              # (p, kind, D) -> row maths
        return super().render(px, py, ab)

    def frame_view(self):
        """The view terms of the row maths: Up, Vp and the signed 8-bit
        sin / cos (magnitude, negative?) exactly as the engine stages them."""
        import fp
        v = self.view
        s_mag, s_neg, s_one, c_mag, c_neg, c_one = fp.fp_sincos(v['ab'])
        dw = self.dw
        up = (dw.MAP_CENTER_X * 1024 + v['px88'] * 32) & 0xFFFF
        vp = -(dw.MAP_CENTER_Y * 1024 + v['py88'] * 32) & 0xFFFF
        return up, vp, (256 if s_one else s_mag), s_neg, (256 if c_one else c_mag), c_neg

    def row(self, p, kind, D):
        """(Uc, dU, Vc, dV), 4.4, for line pair p of a plane D above / below the eye."""
        key = (p, kind, D)
        if key in self.rows:
            return self.rows[key]
        k = 2 * p + 1 - HORIZON if kind == 'f' else HORIZON - (2 * p + 1)
        if D <= 0 or k <= 0:
            self.rows[key] = None
            return None
        up, vp, sm, sn, cm, cn = self.frame_view()
        E = D * ((1 << 20) // k)
        pc, ps = E * cm, E * sm
        sgn = lambda neg, v: -v if neg else v
        A, hV = sgn(cn, pc >> 8), sgn(cn, pc >> 14)
        Bs, hU = sgn(sn, ps >> 8), sgn(sn, ps >> 14)
        # 4.4: centre (byte column 32: U0 + 32 dU) and step, each rounded
        r8 = lambda x: ((x + 0x80) & 0xFFFF) >> 8
        r = (r8(up + A + hU), r8(2 * hU), r8(vp - Bs + hV), r8(2 * hV))
        self.rows[key] = r
        return r

    def _plane(self, si, kind, y, x, shade):
        dw = self.dw
        info = self.W.info[si]
        if kind == 'c' and info['sky']:
            return ('b', shade)
        svwh = dw.fp_segs_vwh[si]
        fh, ch = svwh[3], svwh[4]                   # prescaled s8 (the engine's)
        vz = self.view['vz']
        D = (vz - fh) if kind == 'f' else (ch - vz)
        r = self.row(y >> 1, kind, D)
        if r is None:
            return ('b', shade)
        u0, du, v0, dv = r
        kb = x >> 2                                 # byte column
        u = (u0 + (kb - 32) * du) & 0xFF
        v = (v0 + (kb - 32) * dv) & 0xFF
        sec = info['front']
        pic = _name(sec[2] if kind == 'f' else sec[3])
        return ('F', int(self.flat[pic][v >> 4, u >> 4]), kind, u >> 4, v >> 4, pic)


if __name__ == '__main__':
    import poses as C
    import textured_ref as T
    R = PlaneRef()
    out = os.path.join(ROOT, 'build', 'master', 'plane')
    os.makedirs(out, exist_ok=True)
    for (px, py, ab) in C.POSITIONS:
        fb = R.render(px, py, ab)
        tag = f'{px}_{py}_{ab}'.replace('-', 'm')
        open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
        T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
        print(f'({px},{py},{ab}): unfilled {R.unfilled()}')
