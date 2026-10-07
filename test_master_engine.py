#!/usr/bin/env python3
"""Gate for the Master ENGINE (the BSP walk, transform, seg emission and span
clipper): at every pose it must emit exactly the recorded line list, in the
same order (golden/master_engine_lines.json.gz).

The rig PC-traps the plot entries (plot_h / plot_v / RASTER_ENTRY, RTS emit
stubs on the Master), so the list is everything the engine emits. Billboard
objects are off, as in the Master build.

The golden lists were recorded from the Master link on 2026-10-07, when the
Model B build (which this gate used to compare against) was excised; the
Model B and Master engines emitted identical lines at Model B's 160-line
view at every pose until then. A deliberate engine change re-records them:

    python3 test_master_engine.py --record

Poses: poses.POSITIONS plus poses.VERIFY. Prints MASTERENGINE: PASS.
"""
import gzip, json, os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.init()
import e1m1 as dw
import poses
from banked_bsp import MasterBspRender
from symmap import sym

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'golden', 'master_engine_lines.json.gz')
POSES = list(poses.POSITIONS) + list(poses.VERIFY)


def lines_for():
    """Every emitted line per pose, across the whole render (the rig's
    last_lines is per _run call, so gather them through a wrapper)."""
    r = MasterBspRender(dw.packed_layout, dw.packed_rom_main,
                        dw.packed_rom_detail, dw.packed_bbox_table,
                        dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    anyb = sym('OBJ_ANYB')
    for i in range(32):
        r.bm[anyb + i] = 0
    sc = r.sc
    raw = sc._run
    acc = []
    def run(entry, *a, **k):
        cyc = raw(entry, *a, **k)
        acc.extend(sc.last_lines)
        return cyc
    sc._run = run
    out = []
    for p in POSES:
        acc.clear()
        r.render_frame(*p, dw.player_floor(*p[:2]))
        out.append([list(l) for l in acc])
    return out


got = lines_for()
if '--record' in sys.argv:
    json.dump({'poses': [{'pose': list(p), 'lines': l} for p, l in zip(POSES, got)]},
              gzip.open(GOLDEN, 'wt'))
    print(f'recorded {len(POSES)} poses, {sum(len(l) for l in got):,} lines')
    sys.exit(0)

want = {tuple(e['pose']): e['lines'] for e in json.load(gzip.open(GOLDEN, 'rt'))['poses']}
fails = []
for p, lm in zip(POSES, got):
    lg = want.get(tuple(p))
    if lg is None:
        fails.append(f'{p}: no golden list (re-record)')
        continue
    tag = 'same' if lg == lm else 'DIFFERENT'
    if lg != lm:
        k = next((i for i, (a, b) in enumerate(zip(lg, lm)) if a != b), min(len(lg), len(lm)))
        fails.append(f'{p}: {len(lg)} golden vs {len(lm)} lines, first difference at #{k}')
    print(f'  {p}: {len(lm)} lines, {tag}')
total = sum(len(l) for l in got)
print(f'poses {len(POSES)}, lines {total:,}')
if total < 1000:
    fails.append('vacuous: almost nothing emitted')
for f in fails:
    print('FAIL:', f)
print('MASTERENGINE: FAIL' if fails else 'MASTERENGINE: PASS')
sys.exit(1 if fails else 0)
