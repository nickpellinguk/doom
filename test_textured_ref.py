#!/usr/bin/env python3
"""Gate for textured_ref.py (step 2 of docs/master_textured_spec.md).

Renders the frame-cycle poses (compare_renders.POSITIONS) and the
ground-truth verify poses, writes the reference images to
build/master/ref/, and checks what any later 6502 frame will be held to:
  - every odd line is FLIP of the line above it (the cross-hatch rule)
  - every byte half is a valid shade's top row (one of the 10 pairs)
  - on-map poses leave NO cell undrawn; the poses that look off the map
    edge are exactly the known ones (OFFMAP), so a new hole fails
  - rendering is deterministic, and walls, floors and sky all appear
Prints TEXTUREDREF: PASS.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import compare_renders as C
import master_assets as M
import master_panel
import textured_ref as T

VERIFY = [(1792.34375, -3351.375, 108), (1056, -3616, 64), (1500, -3700, 0),
          (800, -3400, 96), (1056, -3328, 14), (1200, -3000, 129),
          (2112, -2368, 35), (1984, -2496, 67), (3648, -4800, 131),
          (-486, -3307, 243), (1301.5, -2586.09375, 0xB0)]
POSES = list(dict.fromkeys(list(C.POSITIONS) + VERIFY))
# poses whose view runs off the edge of the map (no seg on the ray at all):
# the two the cycle suite already knows see nothing, and two that look
# out past the level's outer wall
OFFMAP = {(192, -2368, 99), (3648, -2368, 35), (1500, -3700, 0),
          (3648, -4800, 131)}

R = T.TexturedRef()
out = os.path.join(T.ROOT, 'build', 'master', 'ref')
os.makedirs(out, exist_ok=True)
halves_l = {(M.wall_byte(s) << 2) & 0xCC for s in range(10)}
halves_r = {M.wall_byte(s) for s in range(10)}
fails, kinds = [], set()
for pose in POSES:
    fb = R.render(*pose)
    hole = R.unfilled()
    kinds |= {v[0] if v[0] == 'w' else ('sky' if v[2] == T.SKY else 'plane')
              for row in R.cell for v in row if v is not None}
    if R.render(*pose) != fb:
        fails.append(f'{pose}: not deterministic')
    if fb[master_panel.PANEL_OFFSET:] != master_panel.panel_bytes():
        fails.append(f'{pose}: the control panel (lines 136..159) is not master_panel')
    for y in range(0, 2 * T.ROWS, 2):                 # the view: lines 0..135
        for k in range(64):
            a = (y >> 3) * 512 + k * 8 + (y & 7)
            b, b2 = fb[a], fb[a + 1]
            if b2 != M.FLIP[b]:
                fails.append(f'{pose}: line {y + 1} col {k} is not FLIP of line {y}')
                break
            if (b & 0xCC) not in halves_l or (b & 0x33) not in halves_r:
                fails.append(f'{pose}: byte ${b:02X} at line {y} col {k} is not two shades')
                break
    if hole and pose not in OFFMAP:
        fails.append(f'{pose}: {hole} undrawn cells on an on-map pose')
    if not hole and pose in OFFMAP:
        fails.append(f'{pose}: listed OFFMAP but fully drawn -- update the list')
    tag = '_'.join(str(v) for v in pose).replace('-', 'm')
    open(os.path.join(out, f'{tag}.bin'), 'wb').write(fb)
    T.to_png(fb, os.path.join(out, f'{tag}.png'), M.PALETTE)
    print(f'  {pose}: undrawn {hole}')
if kinds != {'w', 'plane', 'sky'}:
    fails.append(f'expected walls, planes and sky; saw {sorted(kinds)}')
print(f'poses {len(POSES)}, images in {os.path.relpath(out, T.ROOT)}/')
for f in fails[:20]:
    print('FAIL:', f)
print('TEXTUREDREF: FAIL' if fails else 'TEXTUREDREF: PASS')
sys.exit(1 if fails else 0)
