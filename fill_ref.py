#!/usr/bin/env python3
"""Step 3 fill model for the BBC Master port (docs/master_textured_spec.md).

The rule the 6502 filler implements, stated on the ENGINE'S OWN span pool:
the clip spans are the still-unfilled screen; every span update a seg makes
(mark_solid for a solid wall, the fused top/bottom walk for a portal)
removes area from them, and THAT area is what the seg fills. Diffing the
pool before and after the seg's updates gives, per column, the bands to
fill; the seg's front ceiling/floor lines split each band into ceiling,
wall and floor. Coverage is exact by construction: every on-screen pixel
is filled once, in front-to-back order, with no overdraw and no gaps.

  columns   strip c = pixels 2c, 2c+1 belongs to whatever range contains
            its EVEN pixel 2c (the engine's ranges are half-open [lo, hi))
  bands     old span (top, bot) vs new at x = 2c, evaluated exactly as the
            engine evaluates span lines (endpoint_spans._span_top/_bot);
            closed column -> [top, bot]; narrowed -> [ot, nt-1], [nb+1, ob]
  split     T, B = the seg's front ceiling / floor lines at x (floor
            interpolation between its projected endpoints): y < T ceiling,
            y > B floor, otherwise wall (upper / lower inside a portal)
  shades    step 3 fills SOLID shades: sky ceilings are cyan
  output    per screen LINE: even lines the byte, odd lines FLIP[byte], so
            band edges may fall on any line without breaking the hatch

The engine's span pool is reached through the packed Python reference with
the py65 6502 span clipper behind it (doom_wireframe.Instrumented6502Spans),
the same pairing the regression's traversal gates use.

    python3 fill_ref.py      # render the regression poses into build/master/fill/
"""
import math, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

import master_assets as M

STRIPS, LINES = 128, 160
SKY = 'F_SKY1'
# step-3 shades (indices into master_assets.SHADES)
SH_SKY = M.SHADES.index((2, 2))        # cyan
SH_CEIL = M.SHADES.index((0, 2))       # black / cyan
SH_FLOOR = M.SHADES.index((0, 1))      # black / red
SH_WALL = M.SHADES.index((1, 3))       # red / white
SH_STEP = M.SHADES.index((2, 3))       # cyan / white: upper and lower walls
KIND = {SH_SKY: 'sky', SH_CEIL: 'ceil', SH_FLOOR: 'floor', SH_WALL: 'wall', SH_STEP: 'wall'}


def sky_bitmap():
    """32 bytes, one bit per subsector: its ceiling is F_SKY1. The 6502
    filler's mf_skymap (master/mfill.s) is seeded with this; the subsector's
    sector is the front sector of its (packed) segs, as fill_ref uses."""
    import doom_wireframe as dw
    out = bytearray(32)
    for ss, (cnt, first) in enumerate(dw.fp_ssectors):
        secs = {dw.fp_segs_vwh[j][1] for j in range(first, first + cnt)}
        if not secs:
            continue
        assert len(secs) == 1, f'subsector {ss} spans sectors {secs}'
        if dw.sectors[secs.pop()][3].rstrip(b'\0').decode().upper() == SKY:
            out[ss >> 3] |= 1 << (ss & 7)
    return bytes(out)


def _floor_interp(x, x0, y0, x1, y1):
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) // (x1 - x0)


class FillRef:
    def __init__(self):
        import pygame
        pygame.init()
        import doom_wireframe as dw
        from endpoint_spans import _span_top, _span_bot, Y_BIAS
        self.dw, self.top, self.bot, self.bias = dw, _span_top, _span_bot, Y_BIAS

    def render(self, px, py, ab):
        import fp, pygame
        from wad_packed import spans_init_full
        dw = self.dw
        self.grid = [[None] * STRIPS for _ in range(LINES)]
        self.near = {}
        ctx_box = {}
        def hook(si, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid, near=None):
            ctx_box[si] = (x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid)
            self.near[si] = near
        orig_seg = dw.packed_render_seg
        def seg(si, clips, *a, **k):
            before = list(clips.spans)
            orig_seg(si, clips, *a, **k)
            c = ctx_box.pop(si, None)
            if c is not None:
                self._fill(si, before, list(clips.spans), *c)
        clips = dw.Instrumented6502Spans()
        dw._seg_fill_hook, dw.packed_render_seg = hook, seg
        orig_use, orig_ab = dw._USE_ANGLE_BBOX, dw._VIEW_AB
        dw._USE_ANGLE_BBOX, dw._VIEW_AB = True, ab
        try:
            px_88 = int((px - dw.MAP_CENTER_X) * 256 / dw.PRESCALE)
            py_88 = int((py - dw.MAP_CENTER_Y) * 256 / dw.PRESCALE)
            ctx = fp.fp_view_context(px_88, py_88, fp.fp_sincos(ab))
            vz = dw._prescale_height(dw.player_floor(px, py) + 41)
            a = ab * 2 * math.pi / 256
            ram = bytearray(dw.packed_layout['ram_size'])
            spans_init_full(ram, dw.packed_layout['ram_spans'], dw.FP_RENDER_W, dw.FP_RENDER_H - 1)
            dw.packed_render_bsp(len(dw.nodes) - 1, clips, ctx, vz, px, py,
                                 math.cos(a), math.sin(a), pygame.Surface((256, 160)), ram)
        finally:
            dw._seg_fill_hook, dw.packed_render_seg = None, orig_seg
            dw._USE_ANGLE_BBOX, dw._VIEW_AB = orig_use, orig_ab
        return self._compose()

    @staticmethod
    def _span_at(spans, x):
        for s in spans:
            if s[0] <= x < s[1]:
                return s
        return None

    def _fill(self, si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid):
        dw, Bz = self.dw, self.bias
        front = dw.sectors[dw.fp_segs_vwh[si][1]]
        sky = front[3].rstrip(b'\0').decode().upper() == SKY
        sh_ceil = SH_SKY if sky else SH_CEIL
        sh_wall = SH_WALL if solid else SH_STEP
        lo, hi = max(0, x_lo), min(255, x_hi)
        for x in range((lo + 1) & ~1, hi, 2):               # even pixels in [lo, hi)
            o = self._span_at(before, x)
            if o is None:
                continue
            ot, ob = self.top(o, x), self.bot(o, x)
            n = self._span_at(after, x)
            bands = [(ot, ob)] if n is None else [(ot, self.top(n, x) - 1),
                                                  (self.bot(n, x) + 1, ob)]
            T = _floor_interp(x, sx1, ft1, sx2, ft2) + Bz
            B = _floor_interp(x, sx1, fb1, sx2, fb2) + Bz
            c = x >> 1
            for y0, y1 in bands:
                for yb in range(max(y0, Bz), min(y1, Bz + LINES - 1) + 1):
                    sh = sh_ceil if yb < T else (SH_FLOOR if yb > B else sh_wall)
                    self.grid[yb - Bz][c] = sh

    def _compose(self):
        fb = bytearray(10240)
        for y in range(LINES):
            row = self.grid[y]
            for k in range(64):
                l, r = row[2 * k], row[2 * k + 1]
                b = (((M.wall_byte(l) << 2) & 0xCC) if l is not None else 0) | \
                    (M.wall_byte(r) if r is not None else 0)
                fb[(y >> 3) * 512 + k * 8 + (y & 7)] = b if y % 2 == 0 else M.FLIP[b]
        return bytes(fb)

    def unfilled(self):
        return sum(v is None for row in self.grid for v in row)

    def kinds(self):
        """Per (strip, texel row) surface kind at the row's sample line 2r+1."""
        return [[KIND.get(self.grid[2 * r + 1][c]) for c in range(STRIPS)] for r in range(80)]


if __name__ == '__main__':
    import compare_renders as C
    import textured_ref as T
    F = FillRef()
    out = os.path.join(ROOT, 'build', 'master', 'fill')
    os.makedirs(out, exist_ok=True)
    for (px, py, ab) in C.POSITIONS:
        fb = F.render(px, py, ab)
        tag = f'{px}_{py}_{ab}'.replace('-', 'm')
        open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
        T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
        print(f'({px},{py},{ab}): unfilled {F.unfilled()}')
