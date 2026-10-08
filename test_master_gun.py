#!/usr/bin/env python3
"""Gate (step 6f): the Master's gun overlay. src/master/mgun_tab.s is what
master_gun.py generates from art/gun/PISGA0.txt, and gun_draw (bank 6;
gun_b0 in shadow $3000, gun_b1 in bank 6), run
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
VIEW = 0x2200                                   # a buffer's 136-line view
gun0 = open('engine_gun_m.bin', 'rb').read()   # gun_b0: shadow MGUN0
for base in (abi.MSCREEN0, abi.MSCREEN1):
    before = bytes(rnd.randrange(256) for _ in range(VIEW))
    bm.shadow_store(0x3000, bytes(0x5000))
    bm.shadow_store(abi.MGUN0, gun0)
    bm.shadow_store(base, before)
    bm[abi.DV_BACKHI] = base >> 8
    bm.select(abi.BANK_C)
    cyc = R.sc._run(sym('gun_draw'))
    got = bm.shadow_bytes(base, base + VIEW)
    want = G.apply(before + bytes(0x2800 - VIEW))[:VIEW]
    bad = [i for i in range(VIEW) if got[i] != want[i]]
    other = bm.shadow_bytes(0x3000, 0x8000)
    off, g = base - 0x3000, abi.MGUN0 - 0x3000
    stray = [i for i in range(0x5000) if not off <= i < off + VIEW
             and not g <= i < g + len(gun0) and other[i]]
    print(f'buffer ${base:04X}: {cyc} cycles, {len(bad)} bytes differ, {len(stray)} written elsewhere')
    if bad:
        fails.append(f'${base:04X}: {len(bad)} bytes differ from master_gun.apply, first at +{bad[0]}')
    if stray:
        fails.append(f'${base:04X}: wrote outside its view')
    if other[g:g + len(gun0)] != gun0:
        fails.append(f'${base:04X}: gun_b0 (shadow ${abi.MGUN0:04X}) overwritten')
    if bm[0xFE34] & 4:
        fails.append('ACCCON X left set')
for f in fails:
    print('FAIL:', f)
print('MASTERGUN: FAIL' if fails else 'MASTERGUN: PASS')
sys.exit(1 if fails else 0)
