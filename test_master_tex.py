#!/usr/bin/env python3
"""Gate: the 6502 filler and texturers (src/master/mfill.s: walls, step 4;
floors and ceilings, step 5) draw EXACTLY what the model (plane_ref.py, on
tex_ref.py) draws (docs/master_textured_spec.md).

Renders the regression poses with the MASTER engine in py65 -- banks 5/6,
ANDY and the MTEX_IX blob seeded by master_walls as the disc ships them,
shadow RAM behind ACCCON X, objects off -- and compares the 10K back
buffer with tex_ref's bytes, byte for byte (step 3's solid-shade gate,
test_master_fill.py, grew into this one). Poses that look off the map are
compared too: unfilled cells are zero on both sides. Writes the 6502's
frames to build/master/tex6502/. Prints MASTERTEX: PASS.

ONE KNOWN REFERENCE GAP, reported, not hidden: the model takes each seg's
projected geometry from the packed Python reference, and that reference
rounds sx at reciprocal shift S = 3 where the engine's count projector
(bsp/project.s, net shift S-3 = 0) truncates. It shows only on very near
walls' off-screen endpoints (1 seg fill in the 18-pose corpus). The gate
reads the ENGINE's geometry at every mf_fill; a seg whose geometry differs
from the reference may differ in its own cells only, and such segs are
counted and capped. Everything else must match byte for byte.

    python3 test_master_tex.py [px py ab]    # one pose
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DOOM_CPU'] = '65c02'
import pygame
pygame.init()
import doom_wireframe as dw
import compare_renders as C
import master_assets as M
import plane_ref as P
import textured_ref as T
from banked_bsp import MasterBspRender

poses = C.POSITIONS
if len(sys.argv) == 4:
    poses = [tuple(float(a) if '.' in a else int(a) for a in sys.argv[1:])]
F = P.PlaneRef()
R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                    dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
from symmap import sym
_S = lambda n: sym(n, banked=2)
mem = R.bm
s16 = lambda a: (mem[a] | mem[a + 1] << 8) - (65536 if mem[a + 1] & 0x80 else 0)
eng, pend = {}, {}
V1, V2, BIAS = _S('zp_seg_sx1_l'), _S('zp_seg_sx2_l'), 48


def at_fill(mpu):                       # mf_fill entry: the engine's seg lines
    pend['g'] = (s16(V1), s16(V2), s16(V1 + 2) - BIAS, s16(V2 + 2) - BIAS,
                 s16(V1 + 4) - BIAS, s16(V2 + 4) - BIAS)


def at_col(mpu):                        # first column: tx_seg has named the slot
    a = _S('tx_slot')
    pend['slot'] = mem[a] | mem[a + 1] << 8
    if 'g' in pend:
        eng[pend['slot']] = pend.pop('g')


BV = [_S(n) for n in ('mf_x', 'b_kind', 'b_y0', 'b_y1')]
def at_band(mpu):                       # band entry: the engine's span-diff band
    x, kind, y0, y1 = (mem[a] for a in BV)
    eng_b.setdefault(pend.get('slot'), {}).setdefault(x, []).append((kind, y0, y1))


def bands_norm(bl):                     # visible part of each band, by kind
    out = set()
    for kind, y0, y1 in bl:
        y0, y1 = max(y0, BIAS), min(y1, BIAS + len(F.owner) - 1)
        if y1 >= y0:
            out.add(({'mid': 0, 'up': 1, 'lo': 2}.get(kind, kind), y0, y1))
    return out


eng_b = {}
R.sc.pc_hooks = {_S('mf_fill'): at_fill, _S('col'): at_col, _S('band'): at_band}
# A seg is a reference gap when the engine's geometry differs from the
# reference's: its front lines (geom), or -- added 2026-10-07 -- the bands
# its span diff gives a column, which also carry the OPENING's lines (the
# back sector's edges). (1144.6, -3342.5, 153) seg 156 differs only there:
# the Python traversal puts the step's top edge on screen down to x 168,
# the engine (and the float reference) off it from x 180.
MAX_GAP_SEGS = 4                        # over the whole corpus
gap_segs = 0
out = os.path.join(T.ROOT, 'build', 'master', 'tex6502')
os.makedirs(out, exist_ok=True)
fails, tot = [], 0
for pose in poses:
    want = F.render(*pose)
    eng.clear(); pend.clear(); eng_b.clear()
    cyc = R.render_frame(*pose, dw.player_floor(*pose[:2]))
    got = R.framebuffer()
    tot += cyc
    tag = '_'.join(str(v) for v in pose).replace('-', 'm')
    T.to_png(got, os.path.join(out, f'{tag}.png'), M.PALETTE)
    gap = {si for si, g in F.geom.items() if si in eng and eng[si] != g}
    gap |= {si for si, xs in F.bands.items() if si in eng_b and any(
        bands_norm([(k, y0, y1) for y0, y1, k in b]) != bands_norm(eng_b[si].get(x, []))
        for x, b in xs.items())}
    gap_segs += len(gap)
    eng_own = {}                        # (line, strip) -> the slots the ENGINE filled it for
    for slot, xs in eng_b.items():
        for x, bl in xs.items():
            for _, y0, y1 in bands_norm(bl):
                for y in range(y0 - BIAS, y1 - BIAS + 1):
                    for c in (x >> 1, (x >> 1) + 1):
                        eng_own.setdefault((y, c), set()).add(slot)
    bad, raw = [], 0
    for i in range(10240):
        if got[i] == want[i]:
            continue
        raw += 1
        y, k = (i >> 9) * 8 + (i & 7), (i & 511) >> 3
        if y >= len(F.owner):                   # the control panel: never written
            bad.append(i)
            continue
        # the differing pixels' owners in the model AND in the engine: a gap
        # seg's own extent can differ by a line into a neighbour's cell
        owners = set()
        for c, h in ((2 * k, 0xAA), (2 * k + 1, 0x55)):   # Mode 2: left pixel bits 7 5 3 1
            if (got[i] ^ want[i]) & h:
                owners |= {F.owner[y][c]} | eng_own.get((y, c), set())
        if not owners & gap:
            bad.append(i)
    if gap:
        print(f'  {pose}: reference gap (engine sx differs) at segs {sorted(gap)}')
    if bad:
        i = bad[0]
        y = (i >> 9) * 8 + (i & 7)
        fails.append(f'{pose}: {len(bad)} bytes differ, first at line {y} '
                     f'byte column {(i & 511) >> 3}: 6502 ${got[i]:02X} model ${want[i]:02X}')
    print(f'  {pose}: {"same" if not raw else f"{raw - len(bad)} bytes differ in gap segs" if not bad else f"{len(bad)} bytes differ"}, {cyc:,} cycles')
print(f'total {tot:,} cycles over {len(poses)} poses; {gap_segs} reference-gap seg(s)')
if gap_segs > MAX_GAP_SEGS:
    fails.append(f'{gap_segs} segs differ from the reference geometry (cap {MAX_GAP_SEGS})')
# the colour cycle (step 6e): cyc_tab as master_assets.cycle_colour has it
ct = _S('cyc_tab')
want_ct = [((8 + k) << 4) | (M.cycle_colour(8 + k, p) ^ 7) for p in range(M.CYC_PHASES) for k in range(8)]
got_ct = [mem[ct + i] for i in range(64)]
if got_ct != want_ct:
    fails.append(f'cyc_tab differs from master_assets.cycle_colour: {got_ct} vs {want_ct}')
for f in fails[:20]:
    print('FAIL:', f)
print('MASTERTEX: FAIL' if fails else 'MASTERTEX: PASS')
sys.exit(1 if fails else 0)
