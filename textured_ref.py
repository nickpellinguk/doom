#!/usr/bin/env python3
"""Textured reference renderer for the BBC Master port (step 2 of
docs/master_textured_spec.md).

WHAT the 6502 textured renderer must draw, written for clarity: a classic
DOOM column renderer (front-to-back BSP walk, per-column top/bottom clip,
solid columns close) at the Master's resolution and in its exact bytes.

  Grid      128 wall strips (2 px) x 68 texel rows (2 lines); floors and
            ceilings are sampled per BYTE column (4 px), so they come out
            as 4x2 fat pixels while walls are 2x2.
  Camera    the engine's: 90 degree HFOV, focal 128 across / 153.6 down at
            256x160 (doom_wireframe's 1024x640 float camera scaled by 1/4),
            the view cut to its top 136 lines and centred on line 68 (the
            control panel, master_panel, is the bottom 24),
            eye at floor + 41, angle byte -> radians as the engine.
  Map       the engine's own tables (doom_wireframe: alternate BSP, segs,
            seg_sectors with its one-way-wall rule).
  Walls     upper / middle / lower with sidedef x/y offsets and DOOM's
            pegging (ML_DONTPEGTOP $08, ML_DONTPEGBOTTOM $10). Two-sided
            middle textures are drawn once (not tiled), back to front after
            the walk, clipped to the opening they were seen through.
            Texture coordinates are WAD units scaled to the stored texture
            (32 high, or 16 for the stacked short ones): rows wrap AND 31.
  Planes    the front sector's ceiling above / floor below every wall
            column; flats tile every 64 world units (16x16 texels); F_SKY1
            is solid cyan and a sky-to-sky upper wall is not drawn.
  Texels    read from the PACKED assets (bank images + assembled HAZEL
            tables via master_assets.Assets), i.e. the bytes the 6502 reads.
  Output    10240 bytes = one shadow buffer: byte column k, line y at
            (y>>3)*512 + k*8 + (y&7); lines 2r and 2r+1 = the texel byte
            (Mode 2). A byte is left strip OR (right strip >> 1) for walls
            and the floor byte's matching pixel for planes.

The 6502 port (steps 3-5) will add its own bit-exact Python mirror; this
file is the visual and semantic reference those are judged against.

    python3 textured_ref.py              # render the regression poses
"""
import math, os, sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
STRIPS, ROWS = 128, 68                  # the Master view: 136 lines (master_panel)
CX, CY = 128.0, 68.0                    # its centre (256 x 136)
FX, FY = 128.0, 153.6                   # focal lengths (px, lines)
NEAR = 1.0
DONTPEGTOP, DONTPEGBOTTOM = 0x08, 0x10
SKY = 'F_SKY1'
EYE = 41.0


def _name(b):
    return b.rstrip(b'\0').decode().upper()


class TexturedRef:
    def __init__(self, out=os.path.join(ROOT, 'build', 'master')):
        import master_assets as M
        self.M = M
        if not os.path.exists(os.path.join(out, 'assets.json')):
            M.build(out)
        hz, lab = M.assemble_tables(out)
        self.A = A = M.Assets(out, hz, 0xC000, lab)
        self.tex = {}
        for t in A.man['textures']:
            self.tex[t['name']] = (A.wall_bytes(t['id']), t['src_w'], t['src_h'])
        self.flat = {f['name']: A.flat_bytes(f['id']) for f in A.man['flats']}
        self.sky_byte = M.floor_byte(M.CYAN)        # solid cyan
        import doom_wireframe as dw
        self.dw = dw

    # ── per frame ────────────────────────────────────────────────────
    def render(self, px, py, angle_byte, vz=None, nukage=0):
        """Render one frame; returns the 10240-byte buffer image."""
        dw = self.dw
        if vz is None:
            vz = dw.player_floor(px, py) + EYE
        a = dw.byte_to_radians(angle_byte)
        self.cos, self.sin = math.cos(a), math.sin(a)
        self.px, self.py, self.vz = px, py, vz
        self.nukage = nukage
        self.top = [0] * STRIPS                       # first free row
        self.bot = [ROWS] * STRIPS                    # one past last free row
        self.open = STRIPS
        self.cell = [[None] * STRIPS for _ in range(ROWS)]
        self.masked = []
        self._bsp(len(dw.nodes) - 1)
        for rec in reversed(self.masked):             # back to front
            self._draw_masked(*rec)
        return self._compose()

    def _view(self, wx, wy):
        dx, dy = wx - self.px, wy - self.py
        return dx * self.sin - dy * self.cos, dx * self.cos + dy * self.sin

    def _bsp(self, nid):
        dw = self.dw
        if self.open == 0:
            return
        if nid & dw.NF_SUBSECTOR:
            ss = 0 if nid == 0xFFFF else nid & 0x7FFF
            cnt, first = dw.ssectors[ss]
            for si in range(first, first + cnt):
                self._seg(si)
            return
        node = dw.nodes[nid]
        side = dw.point_on_side(self.px, self.py, node)
        self._bsp(node[12 + side])
        self._bsp(node[12 + (side ^ 1)])

    def _rows(self, y_top, y_bot):
        """Rows whose sample line (2r+1) lies in [y_top, y_bot)."""
        r0 = max(0, math.ceil((y_top - 1) / 2))
        r1 = min(ROWS, math.ceil((y_bot - 1) / 2))
        return r0, r1

    def _row_of(self, y):
        return min(ROWS, max(0, math.ceil((y - 1) / 2)))

    def _seg(self, si):
        dw = self.dw
        s = dw.segs[si]
        ld = dw.linedefs[s[3]]
        lv1, lv2 = dw.vertexes[ld[0]], dw.vertexes[ld[1]]
        dot = (lv2[1] - lv1[1]) * (self.px - lv1[0]) - (lv2[0] - lv1[0]) * (self.py - lv1[1])
        if s[4] == 1:
            dot = -dot
        if dot <= 0:
            return                                    # back-facing
        v1, v2 = dw.vertexes[s[0]], dw.vertexes[s[1]]
        ex1, ey1 = self._view(*v1)
        ex2, ey2 = self._view(*v2)
        if ey1 < NEAR and ey2 < NEAR:
            return
        # screen x range after near clipping (parameter t along v1 -> v2)
        ta, tb = 0.0, 1.0
        if ey1 < NEAR:
            ta = (NEAR - ey1) / (ey2 - ey1)
        if ey2 < NEAR:
            tb = (NEAR - ey1) / (ey2 - ey1)
        def sx(t):
            ex, ey = ex1 + t * (ex2 - ex1), ey1 + t * (ey2 - ey1)
            return CX + ex * FX / ey
        xa, xb = sx(ta), sx(tb)
        if xb <= xa:
            return
        c0 = max(0, math.ceil((xa - 1) / 2))
        c1 = min(STRIPS, math.ceil((xb - 1) / 2))
        if c0 >= c1:
            return
        front_i, back_i = dw.seg_sectors(s)
        front = dw.sectors[front_i]
        back = dw.sectors[back_i] if back_i is not None else None
        sd = dw.sidedefs[ld[5] if s[4] == 0 else ld[6]]
        xoff, yoff = sd[0], sd[1]
        up_t, lo_t, mid_t = _name(sd[2]), _name(sd[3]), _name(sd[4])
        seglen = math.hypot(v2[0] - v1[0], v2[1] - v1[1])
        flags = ld[2]
        fh, fc = front[0], front[1]
        fpic, cpic = _name(front[2]), _name(front[3])
        if back is not None:
            bh, bc = back[0], back[1]
            sky_sky = cpic == SKY and _name(back[3]) == SKY
        for c in range(c0, c1):
            if self.top[c] >= self.bot[c]:
                continue
            k = (2 * c + 1 - CX) / FX                 # ray: ex = k * ey
            den = (ex2 - ex1) - k * (ey2 - ey1)
            if den == 0:
                continue
            t = min(1.0, max(0.0, (k * ey1 - ex1) / den))
            ey = ey1 + t * (ey2 - ey1)
            if ey < NEAR:
                ey = NEAR
            u = xoff + s[5] + t * seglen
            scale = FY / ey
            def yof(h):
                return CY - (h - self.vz) * scale
            y_fc, y_fh = yof(fc), yof(fh)
            top, bot = self.top[c], self.bot[c]
            # ceiling plane above the wall, floor plane below it
            rc = max(top, min(bot, self._row_of(y_fc)))
            rf = min(bot, max(top, self._row_of(y_fh)))
            for r in range(top, rc):
                self.cell[r][c] = ('p', fc, cpic)
            for r in range(rf, bot):
                self.cell[r][c] = ('p', fh, fpic)
            if back is None:
                # one-sided, or a two-sided line the ENGINE walls off (its
                # one-way rule): no middle texture there, so the lower one
                # (else the upper) dresses it
                wt = mid_t if mid_t != '-' else (lo_t if lo_t != '-' else up_t)
                if wt != '-':
                    ztop = fh + self.tex[wt][2] if flags & DONTPEGBOTTOM else fc
                    self._wall(c, rc, rf, wt, u, ztop + yoff, ey)
                else:
                    # the engine's ONE-WAY WINDOWS (doom_wireframe
                    # _ONEWAY_WALLED_SIDE): the back of each courtyard window
                    # ledge is solid seen from the room, and that side has no
                    # texture -- show sky, so the window reads as looking out
                    for r in range(rc, rf):
                        self.cell[r][c] = ('p', fc, SKY)
                self.top[c] = self.bot[c] = 0
                self.open -= 1
                continue
            ntop, nbot = rc, rf
            if bc < fc and not sky_sky:               # upper wall
                rb = max(rc, min(rf, self._row_of(yof(bc))))
                if up_t != '-':
                    ztop = fc if flags & DONTPEGTOP else bc + self.tex[up_t][2]
                    self._wall(c, rc, rb, up_t, u, ztop + yoff, ey)
                ntop = rb
            elif bc < fc:                             # sky over sky: the sky shows
                rb = max(rc, min(rf, self._row_of(yof(bc))))
                for r in range(rc, rb):
                    self.cell[r][c] = ('p', fc, cpic)
                ntop = rb
            if bh > fh:                               # lower wall
                rt = min(rf, max(ntop, self._row_of(yof(bh))))
                if lo_t != '-':
                    ztop = fc if flags & DONTPEGBOTTOM else bh
                    self._wall(c, rt, rf, lo_t, u, ztop + yoff, ey)
                nbot = rt
            if mid_t != '-':
                ztop = (max(fh, bh) + self.tex[mid_t][2] if flags & DONTPEGBOTTOM
                        else min(fc, bc))
                self.masked.append((c, ntop, nbot, mid_t, u, ztop + yoff, ey))
            self.top[c], self.bot[c] = ntop, nbot
            if ntop >= nbot:
                self.open -= 1

    def _wall(self, c, r0, r1, name, u, ztop, ey, tile=True):
        if r0 >= r1:
            return
        tb, sw, sh = self.tex[name]
        th, tw = tb.shape
        tc = int(math.floor(u * tw / sw)) % tw
        for r in range(r0, r1):
            z = self.vz + (CY - (2 * r + 1)) * ey / FY
            v = ztop - z
            if not tile and not (0 <= v < sh):
                continue
            tr = int(math.floor(v * th / sh)) % th
            self.cell[r][c] = ('w', int(tb[tr, tc]))

    def _draw_masked(self, c, r0, r1, name, u, ztop, ey):
        if name not in self.tex:
            return                  # not stored (master_assets.DROP)
        self._wall(c, r0, r1, name, u, ztop, ey, tile=False)

    def _plane_byte(self, c, r, h, pic):
        if pic == SKY:
            return self.sky_byte
        if pic.startswith('NUKAGE') and f'NUKAGE{self.nukage % 3 + 1}' in self.flat:
            pic = f'NUKAGE{self.nukage % 3 + 1}'
        xs, ys = 4 * (c >> 1) + 2, 2 * r + 1         # the byte column's centre
        d = ys - CY
        if d == 0 or (h - self.vz) * d > 0:
            d = 0.5 if h < self.vz else -0.5          # horizon rounding
        ey = (self.vz - h) * FY / d
        ex = (xs - CX) / FX * ey
        wx = self.px + ex * self.sin + ey * self.cos
        wy = self.py - ex * self.cos + ey * self.sin
        fu = int(math.floor(wx / 4)) & 15             # 64 world units = 16 texels
        fv = int(math.floor(-wy / 4)) & 15            # DOOM flats run -y down
        return int(self.flat[pic][fv, fu])

    def _compose(self):
        import master_assets as M
        import master_panel
        fb = bytearray(10240)
        fb[master_panel.PANEL_OFFSET:] = master_panel.panel_bytes()
        for r in range(ROWS):
            row = self.cell[r]
            for k in range(64):
                b = o = 0                             # even, odd line
                for half, c in ((0, 2 * k), (1, 2 * k + 1)):
                    v = row[c]
                    if v is None:
                        continue                      # never drawn: black
                    m = 0xAA if half == 0 else 0x55
                    if v[0] == 'w':
                        w = v[1] if half == 0 else v[1] >> 1
                        b |= w
                        o |= w
                    else:                             # odd line: FLIP
                        pb = self._plane_byte(c, r, v[1], v[2])
                        b |= pb & m
                        o |= M.FLIP[pb] & m
                for line, val in ((2 * r, b), (2 * r + 1, o)):
                    fb[(line >> 3) * 512 + k * 8 + (line & 7)] = val
        return bytes(fb)

    def unfilled(self):
        return sum(v is None for row in self.cell for v in row)


_LUT = None


def to_rgb(fb, palette):
    """Buffer image -> (160, 256, 3) uint8, as the display shows it: the
    view Mode 2, the control panel (lines 136+) Mode 1 in the split's
    palette (master_panel, step 6c)."""
    global _LUT
    import master_assets as M
    import master_panel as P
    if _LUT is None:
        _LUT = (np.array([[palette[c & 7] for c in M.mode2_pixels(b) for _ in (0, 1)]
                          for b in range(256)], np.uint8),        # 2 screen px per pixel
                np.array([[P.SPLIT_RGB[c] for c in P.mode1_pixels(b)]
                          for b in range(256)], np.uint8))
    a = np.frombuffer(fb, np.uint8).reshape(20, 64, 8)       # char row, column, line
    a = a.transpose(0, 2, 1).reshape(160, 64)                # line y, byte column
    out = _LUT[0][a].reshape(160, 256, 3)
    out[P.VIEW_LINES:] = _LUT[1][a[P.VIEW_LINES:]].reshape(-1, 256, 3)
    return out


def to_png(fb, path, palette):
    """Decode a buffer image into a 512x320 PNG (each pixel 2x2)."""
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    import pygame
    img = np.repeat(np.repeat(to_rgb(fb, palette), 2, 0), 2, 1)
    pygame.image.save(pygame.surfarray.make_surface(img.swapaxes(0, 1)), path)


if __name__ == '__main__':
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    import compare_renders as C
    import master_assets as M
    R = TexturedRef()
    out = os.path.join(ROOT, 'build', 'master', 'ref')
    os.makedirs(out, exist_ok=True)
    for (px, py, ab) in C.POSITIONS:
        fb = R.render(px, py, ab)
        tag = f'{px}_{py}_{ab}'.replace('-', 'm')
        open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
        to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
        print(f'({px},{py},{ab}): unfilled cells {R.unfilled()}')
