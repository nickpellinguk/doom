#!/usr/bin/env python3
"""Gate for fill_ref.py, the step-3 fill model (docs/master_textured_spec.md).

On the engine's own span pool (packed reference + py65 span clipper):
  - every on-map pose is filled completely (the span-diff rule leaves no
    gaps); the off-map poses are the known four
  - every byte is two of the fill's solid shades (Mode 2); fills
    are per line, so the two lines of a pair may differ at band edges
  - the surfaces agree with the float textured reference on at least 95%
    of drawn cells (wall / ceiling / floor / sky at each texel row)
Prints FILLREF: PASS.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import compare_renders as C
import master_assets as M
import fill_ref as Fm
import textured_ref as T

OFFMAP = {(192, -2368, 99), (3648, -2368, 35), (1500, -3700, 0), (3648, -4800, 131)}
F, R = Fm.FillRef(), T.TexturedRef()
SHADE_SET = {Fm.SH_SKY, Fm.SH_CEIL, Fm.SH_FLOOR, Fm.SH_WALL, Fm.SH_STEP, M.BLACK}
out = os.path.join(T.ROOT, 'build', 'master', 'fill')
os.makedirs(out, exist_ok=True)


def tkind(v):
    if v is None:
        return None
    if v[0] == 'w':
        return 'wall'
    return 'sky' if v[2] == T.SKY else ('ceil' if v[1] > R.vz else 'floor')


fails, n_all, a_all = [], 0, 0
for pose in C.POSITIONS:
    fb = F.render(*pose)
    R.render(*pose)
    hole = F.unfilled()
    if hole and pose not in OFFMAP:
        fails.append(f'{pose}: {hole} unfilled lines-cells on an on-map pose')
    # Fills are per LINE: every byte of the view is two of the shades.
    for y in range(Fm.LINES):                   # the view (the panel is pixel art)
        for k in range(64):
            b = fb[(y >> 3) * 512 + k * 8 + (y & 7)]
            if not set(M.mode2_pixels(b)) <= SHADE_SET:
                fails.append(f'{pose}: line {y} col {k}: ${b:02X} is not two shades')
                break
    fk, n, agree = F.kinds(), 0, 0
    for r in range(T.ROWS):
        for c in range(128):
            t, f = tkind(R.cell[r][c]), fk[r][c]
            if t is None and f is None:
                continue
            n += 1
            agree += t == f
    n_all += n
    a_all += agree
    tag = '_'.join(str(v) for v in pose).replace('-', 'm')
    open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
    T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
    print(f'  {pose}: unfilled {hole}, surface agreement '
          f'{100 * agree / max(n, 1):.1f}%')
pct = 100 * a_all / n_all
print(f'overall surface agreement with textured_ref: {pct:.2f}%')
if pct < 95.0:
    fails.append(f'surface agreement {pct:.2f}% < 95%')
for f in fails[:20]:
    print('FAIL:', f)
print('FILLREF: FAIL' if fails else 'FILLREF: PASS')
sys.exit(1 if fails else 0)
