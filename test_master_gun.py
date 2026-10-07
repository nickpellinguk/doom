#!/usr/bin/env python3
"""Gate (step 6f): the Master's gun overlay. src/master/mgun_tab.s is what
master_gun.py generates from art/gun/PISGA0.txt, and gun_draw (bank 6), run
on a back buffer of random bytes, leaves exactly master_gun.apply's result
-- the art's pixels written, its transparent pixels and everything else
untouched. Prints MASTERGUN: PASS / FAIL."""
import os, random, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.init()
import abi
import e1m1 as dw
import master_gun as G
from banked_bsp import MasterBspRender
from symmap import sym

fails = []
if open(G.TAB).read() != G.source():
    fails.append('src/master/mgun_tab.s is stale: run python3 master_gun.py')
R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                    dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
bm = R.bm
rnd = random.Random(6)
for base in (abi.MSCREEN0, abi.MSCREEN1):
    before = bytes(rnd.randrange(256) for _ in range(0x2800))
    bm.shadow_store(0x3000, bytes(0x5000))
    bm.shadow_store(base, before)
    bm[abi.DV_BACKHI] = base >> 8
    bm.select(abi.BANK_C)
    cyc = R.sc._run(sym('gun_draw'))
    got = bm.shadow_bytes(base, base + 0x2800)
    want = G.apply(before)
    bad = [i for i in range(0x2800) if got[i] != want[i]]
    other = bm.shadow_bytes(0x3000, 0x8000)
    off = base - 0x3000
    stray = [i for i in range(0x5000) if not off <= i < off + 0x2800 and other[i]]
    print(f'buffer ${base:04X}: {cyc} cycles, {len(bad)} bytes differ, {len(stray)} written elsewhere')
    if bad:
        fails.append(f'${base:04X}: {len(bad)} bytes differ from master_gun.apply, first at +{bad[0]}')
    if stray:
        fails.append(f'${base:04X}: wrote outside its buffer')
    if bm[0xFE34] & 4:
        fails.append('ACCCON X left set')
for f in fails:
    print('FAIL:', f)
print('MASTERGUN: FAIL' if fails else 'MASTERGUN: PASS')
sys.exit(1 if fails else 0)
