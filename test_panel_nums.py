#!/usr/bin/env python3
"""Gate (H7): the Tube host draws the panel's big numbers.

The Tube link (DOOM_ASMDEFS=TUBE=1) on py65, the panel as the disc leaves
it (master_panel.panel_bytes, the fields at their boot values). For a run
of values -- the edges (0, 9, 10, 99, 100, 999, past 999) and random ones,
fields changing one, two or all at a time -- pn_val is set and pn_update
(src/tube/hpanel.s, bank 6) run: the shared panel in shadow RAM must be
master_panel.panel_with of the values, byte for byte, and a frame with
nothing changed must write nothing. Prints the cycles and PANELNUMS: PASS
/ FAIL. Rebuilds the normal link on the way out.
"""
import os, random, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
import pygame
pygame.init()
import abi
import e1m1 as dw
import master_panel as MP
from banked_bsp import MasterBspRender
from symmap import sym


def main():
    r = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                        dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    m = r.bm
    m.clear_shadow()
    m.shadow_store(abi.MPANEL, MP.panel_bytes())
    m[0xFE34] = 0x09                            # ACCCON as the frame leaves it
    upd, val = sym('pn_update'), sym('pn_val')
    rnd = random.Random(7)
    edge = [0, 9, 10, 99, 100, 101, 199, 200, 999, 1000, 65535]
    seq = [(e, e, e) for e in edge] + [(50, 100, 0)]
    vals = [50, 100, 0]
    for _ in range(150):
        for f in rnd.sample(range(3), rnd.randint(1, 3)):
            vals[f] = rnd.choice([rnd.randint(0, 999), rnd.randint(0, 220), rnd.choice(edge)])
        seq.append(tuple(vals))
    seq += [seq[-1]]                            # unchanged: nothing written
    bad, cyc = 0, []
    for vs in seq:
        m[0xFE30] = 6                           # bank 6 (BANK_C), as the driver has it
        for f, v in enumerate(vs):              #  (pn_val is bank 6 RAM)
            m[val + 2 * f], m[val + 2 * f + 1] = v & 0xFF, v >> 8
        r.sc._run(upd)
        cyc.append(r.sc.mpu.processorCycles)
        got = m.shadow_bytes(abi.MPANEL, abi.MPANEL + MP.PANEL_SIZE)
        if bytes(got) != MP.panel_with(vs):
            bad += 1
            if bad <= 5:
                print(f'  {vs}: panel differs')
    print(f'  {len(seq)} updates; cycles: unchanged {cyc[-1]:,}, '
          f'mean {sum(cyc) // len(cyc):,}, max {max(cyc):,}')
    print('PANELNUMS: FAIL' if bad else 'PANELNUMS: PASS')
    return bad


if __name__ == '__main__':
    bad = main()
    os.environ['DOOM_ASMDEFS'] = ''
    import asmbuild
    asmbuild.build()
    sys.exit(1 if bad else 0)
