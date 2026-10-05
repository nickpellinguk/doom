#!/usr/bin/env python3
"""Gate: the 6502 step-3 filler (src/master/mfill.s) draws EXACTLY what the
fill model (fill_ref.py) draws (docs/master_textured_spec.md, step 3).

Renders the frame-cycle poses with the MASTER engine in py65 (bank 6
poisoned, shadow RAM modelled behind ACCCON X, objects off) and compares
the 10K back buffer it wrote with fill_ref's bytes, byte for byte. Poses
that look off the map are compared too: the unfilled cells are zero on
both sides (the rig clears the buffer per frame). Prints MASTERFILL: PASS.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DOOM_CPU'] = '65c02'
import pygame
pygame.init()
import doom_wireframe as dw
import compare_renders as C
import fill_ref as Fm
from banked_bsp import MasterBspRender

F = Fm.FillRef()
R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                    dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
fails, tot = [], 0
for pose in C.POSITIONS:
    want = F.render(*pose)
    cyc = R.render_frame(*pose, dw.player_floor(*pose[:2]))
    got = R.framebuffer()
    tot += cyc
    bad = [i for i in range(10240) if got[i] != want[i]]
    if bad:
        i = bad[0]
        y = (i >> 9) * 8 + (i & 7)
        fails.append(f'{pose}: {len(bad)} bytes differ, first at line {y} '
                     f'byte column {(i & 511) >> 3}: 6502 ${got[i]:02X} model ${want[i]:02X}')
    print(f'  {pose}: {"same" if not bad else f"{len(bad)} bytes differ"}, {cyc:,} cycles')
print(f'total {tot:,} cycles over {len(C.POSITIONS)} poses')
for f in fails[:20]:
    print('FAIL:', f)
print('MASTERFILL: FAIL' if fails else 'MASTERFILL: PASS')
sys.exit(1 if fails else 0)
