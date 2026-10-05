#!/usr/bin/env python3
"""Gate for the MASTER engine build (step 1 of docs/master_textured_spec.md).

The Master build is the Model B engine with bank C's content laid linear in
main RAM (CBITS_M) and no rasteriser: plot_h / plot_v / RASTER_ENTRY are RTS
emit stubs. Both rigs trap those entries and record the staged line, so for
every pose the Master build must emit EXACTLY the Model B build's line list,
in the same order. Bank 6 is poisoned in the Master rig, so a bank-C read
that missed the rebase shows up here as a different list.

Billboard objects are off on both sides (the Master build has none yet).

Poses: the 17 frame-cycle positions (compare_renders.POSITIONS) plus the 11
ground-truth verify positions from run_regression. Also reports the Master
cycle total against the Model B C02 build. Prints MASTERENGINE: PASS.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DOOM_CPU'] = '65c02'        # compare against the Model B C02 link:
                                        # same CPU as the Master's 65C12
import pygame
pygame.init()
import doom_wireframe as dw
import compare_renders as C
from banked_bsp import BankedBspRender, MasterBspRender

VERIFY = [(1792.34375, -3351.375, 108), (1056, -3616, 64), (1500, -3700, 0),
          (800, -3400, 96), (1056, -3328, 14), (1200, -3000, 129),
          (2112, -2368, 35), (1984, -2496, 67), (3648, -4800, 131),
          (-486, -3307, 243), (1301.5, -2586.09375, 0xB0)]
POSES = list(C.POSITIONS) + VERIFY


def lines_for(R):
    """Every emitted line per pose, across the whole render (the rig's
    last_lines is per _run call, so gather them through a wrapper)."""
    r = R(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
          dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    sc = r.sc
    # Billboard objects are OFF in the Master build (they apply span lines
    # outside any seg's fill window; no sprites yet), so compare the two
    # engines with objects off on both sides.
    from symmap import sym as _sy
    anyb = _sy('OBJ_ANYB', banked=R.VAR)
    for i in range(32):
        r.bm[anyb + i] = 0
    raw = sc._run
    acc = []
    def run(entry, *a, **k):
        cyc = raw(entry, *a, **k)
        acc.extend(sc.last_lines)
        return cyc
    sc._run = run
    out = []
    for (px, py, ab) in POSES:
        acc.clear()
        cyc = r.render_frame(px, py, ab, dw.player_floor(px, py))
        out.append((list(acc), cyc))
    return out


mb = lines_for(BankedBspRender)
ms = lines_for(MasterBspRender)
fails = []
tb = tm = 0
for (px, py, ab), (lb, cb), (lm, cm) in zip(POSES, mb, ms):
    tb += cb; tm += cm
    tag = 'same' if lb == lm else 'DIFFERENT'
    if lb != lm:
        k = next((i for i, (a, b) in enumerate(zip(lb, lm)) if a != b), min(len(lb), len(lm)))
        fails.append(f'({px},{py},{ab}): {len(lb)} vs {len(lm)} lines, first difference at #{k}')
    print(f'  ({px},{py},{ab}): {len(lb)} lines, {tag}; cycles B {cb:,} / Master {cm:,}')
total_lines = sum(len(l) for l, _ in mb)
print(f'poses {len(POSES)}, lines {total_lines:,}; cycles Model B C02 {tb:,}, '
      f'Master {tm:,} ({(tm - tb) / tb:+.2%})')
check = total_lines > 1000                   # never pass on an empty walk
if not check:
    fails.append('vacuous: almost nothing emitted')
for f in fails:
    print('FAIL:', f)
print('MASTERENGINE: FAIL' if fails else 'MASTERENGINE: PASS')
sys.exit(1 if fails else 0)
