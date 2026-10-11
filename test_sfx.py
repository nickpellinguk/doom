#!/usr/bin/env python3
"""Gate (H9): the Tube host's sound effects over the music.

The resident music player (src/master/mmusic.s) and the effects player
(src/tube/hsfx.s, from the TUBE link's main RAM) on py65, as the split IRQ
runs them: MUS_TICK then sfx_tick, every 50 Hz tick, with effects asked
for (sfx_start, the main loop) at random -- one at a time, in bursts that
test the priorities, some while others play. Every tick's chip bytes must
be master_music.Player's then master_sfx.Sfx's, and the keyboard port as
the scan left it. Prints SFX: PASS / FAIL. Rebuilds the normal link after.
"""
import os, random, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
import abi
import asmbuild
import master_music as M
import master_sfx as X
from symmap import sym

asmbuild.build(force=True)
import test_master_music_mem as TM            # (the music gate's memory)


def main():
    res, stream, tab, lab = M.build()
    mem = TM.Mem()
    for i, b in enumerate(res):
        list.__setitem__(mem, abi.MUS_ORG + i, b)
    for i, b in enumerate(stream):
        list.__setitem__(mem, abi.MUS_STREAM + i, b)
    img = open(os.path.join(ROOT, 'engine_m.bin'), 'rb').read()    # MAIN, from $0F00
    lo, hi = sym('sfx_start'), sym('sx_e3') + 64
    for a in range(lo & 0xFF00, min(hi, 0x5600)):
        list.__setitem__(mem, a, img[a - 0x0F00])
    from py65.devices.mpu65c02 import MPU
    mpu = MPU(memory=mem)
    call = TM.caller(mem, mpu)
    model, fx = M.Player(stream, tab), X.Sfx()
    mem.latch[3] = 0
    call(abi.MUS_INIT)
    model.refill()
    rnd = random.Random(5)
    fails, nb, starts = [], 0, 0
    for n in range(6000):
        if n % 20 == 0:
            call(abi.MUS_REFILL)
            model.refill()
        r = rnd.random()
        asks = ([rnd.randrange(4)] if r < 0.04 else
                [rnd.randrange(4) for _ in range(3)] if r < 0.05 else [])
        for e in asks:
            mpu.a = e
            call(sym('sfx_start'), keep_a=True)
            fx.request(e)
            starts += 1
        key = rnd.randrange(0x80)
        mem.ddra, mem.ora, mem.latch[3] = 0x7F, key, 0
        mem.chip = []
        call(abi.MUS_TICK)
        call(sym('sfx_tick'))
        want = model.tick() + fx.tick(model)
        nb += len(want)
        if mem.chip != want:
            fails.append(f'tick {n}: 6502 {[hex(b) for b in mem.chip]} model {[hex(b) for b in want]}')
        if mem.ddra != 0x7F or mem.ora != key or mem.latch[3] != 0:
            fails.append(f'tick {n}: port left DDRA ${mem.ddra:02X} ORA ${mem.ora:02X}')
        if mem.bad:
            fails.append(f'tick {n}: {mem.bad[:3]}')
            mem.bad = []
        if len(fails) > 5:
            break
    print(f'  {n + 1} ticks, {starts} effects asked for, {nb} chip bytes')
    for f in fails[:6]:
        print('  FAIL', f)
    print('SFX: FAIL' if fails else 'SFX: PASS')
    return len(fails)


if __name__ == '__main__':
    bad = main()
    os.environ['DOOM_ASMDEFS'] = ''
    asmbuild.build()
    sys.exit(1 if bad else 0)
