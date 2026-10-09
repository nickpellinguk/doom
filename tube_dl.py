#!/usr/bin/env python3
"""The Tube display list (branch tube-master, docs/tube_master.md step 1).

On a Master with a 6502 second processor the geometry and the fill's
set-up maths move across the Tube; the host keeps only the loops that
write screen memory. What crosses is a DISPLAY LIST: per frame, records
that say exactly what to draw, with every per-run quantity the host's
loops need already worked out. This module defines it on the existing
spec models:

  DLRef     plane_ref.PlaneRef (walls: tex_ref, planes: plane_ref) that
            also RECORDS the frame's display list while it renders
  draw()    the host: a frame from the display list alone, the texture
            and flat bytes, and the control panel -- no geometry, no
            division, no multiply but the texel row's step

The gate (test_tube_dl.py) holds draw(dl) to PlaneRef's frame byte for
byte. Records (lines y 0..135, byte columns k 0..63):

  WALL  k, y0, y1: one byte column's wall rows in one band
        tid               the texture (its height th: row mask th - 1)
        cl, cr            the left / right strips' texture columns
        v0, step          v (5.11) at y0, and per line PAIR: line y's v is
                          v0 + ((y & ~1) - (y0 & ~1)) * step  (mod 2^16)
        share             the right strip reads the left's row, else
        dh0, ddh          its row is the high byte of v plus a 5.3 delta,
                          dh0 at y0 and ddh more each character row
                          (tex_ref step 7d):
                          dh(y) = hi((dh0 << 8) + $80 + ddh * (y//8 - y0//8))
        byte              wall_pair(left texel, right texel), every line
  SPAN  y, k0, k1: one line of floor or ceiling, byte columns k0..k1
        flat              the flat, or far: one byte (step 7n's far tone)
        u0, v0, du, dv    4.4 at k0 and per byte column (mod 2^8)
        byte              flat[(v >> 4) * 16 + (u >> 4)], FLIPped on odd y
  FILL  k, y0, y1: one byte column of one byte (sky, an unseen plane's
        shade, a sky-to-sky upper), FLIPped on odd lines if flip

Encoded sizes (for step 2's bandwidth estimate; the format is not final):
WALL 11 B (+3 when the right strip has its own row), SPAN 8 B (far 5 B),
FILL 5 B.
"""
import fill_ref as Fm
import master_assets as M
import plane_ref as P

WALL_B, WALL_DH_B, SPAN_B, SPAN_FAR_B, FILL_B = 11, 3, 8, 5, 5


class DLRef(P.PlaneRef):
    """PlaneRef, recording the frame's display list as it renders."""

    def render(self, px, py, ab):
        self.walls = []                              # WALL records
        self.pcells = {}                             # (y, k) -> plane cell params
        fb = super().render(px, py, ab)
        self.dl = self._display_list()
        return fb

    def _wall_run(self, si, x, part, u, ur, T, B, ys, ye, step, share, delta):
        Bz = self.bias
        p = self.W.parts[part]
        tp = self.W.tparams[p['tid']]
        col = lambda uu: ((uu & tp['mask']) >> tp['shift']) * tp['tw'] // tp['n']
        rec = dict(kind='WALL', k=x >> 2, y0=ys - Bz, y1=ye - Bz, tid=p['tid'],
                   cl=col(u), cr=col(ur), v0=self._v(part, ys, T, B, ys=ys),
                   step=step, share=share)
        if not share:
            rec['dh0'], rec['ddh'] = delta
        self.walls.append(rec)

    def _plane(self, si, kind, y, x, shade):
        cell = super()._plane(si, kind, y, x, shade)
        if cell[0] == 'F':
            info = self.W.info[si]
            svwh = self.dw.fp_segs_vwh[si]
            D = (self.view['vz'] - svwh[3]) if kind == 'f' else (svwh[4] - self.view['vz'])
            sec = info['front']
            pic = P._name(sec[2] if kind == 'f' else sec[3])
            far = self.level(y >> 1, kind, D)
            self.pcells[(y, x >> 2)] = (self.row(y >> 1, kind, D), pic, far, cell[1])
        return cell

    def _display_list(self):
        """WALL records as recorded; SPANs from the plane cells, a line's
        neighbouring byte columns with the same row maths, flat and tone
        joined; FILLs from the solid cells, a column's neighbouring lines
        with the same byte joined."""
        dl = list(self.walls)
        sky = ('b', M.wall_byte(Fm.SH_SKY))
        for y in range(Fm.LINES):
            k = 0
            while k < 64:
                c = self.pcells.get((y, k))
                if c is None:
                    k += 1
                    continue
                (uc, du, vc, dv), pic, far, _ = c
                k1 = k
                while k1 + 1 < 64 and (n := self.pcells.get((y, k1 + 1))) is not None \
                        and n[:3] == c[:3]:
                    k1 += 1
                rec = dict(kind='SPAN', y=y, k0=k, k1=k1,
                           u0=(uc + (k - 32) * du) & 0xFF, v0=(vc + (k - 32) * dv) & 0xFF,
                           du=du, dv=dv)
                if far:
                    rec['far'] = self.far[pic]
                else:
                    rec['flat'] = pic
                dl.append(rec)
                k = k1 + 1
        for k in range(64):
            y = 0
            while y < Fm.LINES:
                cell = self.grid[y][2 * k]
                if cell is None or cell[0] != 'b':
                    y += 1
                    continue
                assert self.grid[y][2 * k + 1] == cell
                if cell == sky:
                    byte, flip = M.SKY_BYTE, True
                else:
                    byte, flip = M.wall_pair(cell[1], cell[1]), False
                y1 = y
                while y1 + 1 < Fm.LINES and self.grid[y1 + 1][2 * k] == cell:
                    y1 += 1
                dl.append(dict(kind='FILL', k=k, y0=y, y1=y1, byte=byte, flip=flip))
                y = y1 + 1
        return dl


def draw(dl, tex, tparams, flats):
    """The host's frame from a display list: tex[name][0] (row, col)
    texels and tparams[tid] (name, th) as tex_ref has them, flats[name]
    16 x 16. Returns the 10K buffer (the panel below the view)."""
    import master_panel
    fb = bytearray(10240)
    fb[master_panel.PANEL_OFFSET:] = master_panel.panel_bytes()

    def put(y, k, b):
        fb[(y >> 3) * 512 + k * 8 + (y & 7)] = b

    for r in dl:
        if r['kind'] == 'WALL':
            tp = tparams[r['tid']]
            t, m = tex[tp['name']][0], tp['th'] - 1
            for y in range(r['y0'], r['y1'] + 1):
                v = (r['v0'] + ((y & ~1) - (r['y0'] & ~1)) * r['step']) & 0xFFFF
                left = int(t[(v >> 11) & m, r['cl']])
                if not r['share']:
                    dh = (((r['dh0'] << 8) + 0x80 + r['ddh'] * ((y >> 3) - (r['y0'] >> 3)))
                          & 0xFFFF) >> 8
                    v = (((v >> 8) + dh) & 0xFF) << 8
                put(y, r['k'], M.wall_pair(left, int(t[(v >> 11) & m, r['cr']])))
        elif r['kind'] == 'SPAN':
            u, v = r['u0'], r['v0']
            for k in range(r['k0'], r['k1'] + 1):
                b = r['far'] if 'far' in r else int(flats[r['flat']][v >> 4, u >> 4])
                put(r['y'], k, M.FLIP[b] if r['y'] & 1 else b)
                u, v = (u + r['du']) & 0xFF, (v + r['dv']) & 0xFF
        else:
            for y in range(r['y0'], r['y1'] + 1):
                put(y, r['k'], M.FLIP[r['byte']] if r['flip'] and y & 1 else r['byte'])
    return bytes(fb)


def size(dl):
    """The list's encoded bytes (module docstring sizes) and record counts."""
    n = {'WALL': 0, 'SPAN': 0, 'FILL': 0}
    b = 0
    for r in dl:
        n[r['kind']] += 1
        if r['kind'] == 'WALL':
            b += WALL_B + (0 if r['share'] else WALL_DH_B)
        elif r['kind'] == 'SPAN':
            b += SPAN_FAR_B if 'far' in r else SPAN_B
        else:
            b += FILL_B
    return b, n
