#!/usr/bin/env python3
"""Gate: the 6502 music player (src/master/mmusic.s, py65) writes the same
chip bytes on every 50 Hz tick as master_music.Player, the spec, over two
and a bit loops of the tune with the main loop's refills at random frame
gaps (some long enough to drain the ring); and the packed tune replays
the BASIC listing's SOUND commands exactly (master_music.basic_events).

Also checked on every tick: the chip is only written with port A all
output and the keyboard off PA7 (latch bit 3 high), and DDRA, the port and
the latch are as the keyboard scan left them afterwards.
Prints MASTERMUSIC: PASS / FAIL."""
import os, random, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from py65.devices.mpu65c02 import MPU
import abi
import master_music as M

fails = []
tune = M.parse()
res, stream, tab, lab = M.build()

# 1. the packing replays the BASIC player
N = 4000
for k, ((U, ch, amp, pit, dur), r) in enumerate(zip(M.basic_events(tune, N), M.notes(stream, tab, N))):
    due = ((U + 1) >> 1) & 0xFFFF
    c = ch - 16
    want = [due & 0xFF, due >> 8, c | amp << 2, 0 if dur == 255 else 5 * dur]
    if c == 0:
        want += [0xE0 | pit, 0]
    elif amp:
        n = M.period(pit // 4)
        want += [M.CHIP_BASE[c] | n & 15, n >> 4]
    else:
        want += r[4:]                       # silent: the pitch is never written
    if want != r:
        fails.append(f'record {k}: BASIC {want} packed {r}')
        break
print(f'  BASIC listing vs packed tune: {N} SOUND commands '
      f'({N * 1.0 / len(M.basic_events(tune, 1452)):.1f} loops), '
      f'{"agree" if not fails else "DIFFER"}')


class Mem(list):
    """64K with System VIA port A, its latch and ROMSEL watched."""

    def __init__(self):
        super().__init__([0] * 0x10000)
        self.ddra, self.ora, self.latch = 0x7F, 0, [1] * 8
        self.chip, self.bad = [], []

    def __getitem__(self, a):
        if a == 0xFE43:
            return self.ddra
        if a == 0xFE4F:
            return self.ora & self.ddra     # (PA7 in: reads 0 here)
        return list.__getitem__(self, a)

    def __setitem__(self, a, v):
        if a == 0xFE43:
            self.ddra = v
        elif a == 0xFE4F:
            self.ora = v
        elif a == 0xFE40:
            bit, n = v >> 3 & 1, v & 7
            if n == 0 and bit == 0 and self.latch[0]:
                if self.ddra != 0xFF or not self.latch[3]:
                    self.bad.append(f'chip write with DDRA ${self.ddra:02X}, latch 3 = {self.latch[3]}')
                self.chip.append(self.ora)
            self.latch[n] = bit
        elif a == 0xFE30:
            if v != abi.BANK_WALK:
                self.bad.append(f'ROMSEL {v}')
        elif 0xFC00 <= a < 0xFF00:
            self.bad.append(f'write ${a:04X}')
        else:
            list.__setitem__(self, a, v)


mem = Mem()
for i, b in enumerate(res):
    list.__setitem__(mem, abi.MUS_ORG + i, b)
for i, b in enumerate(stream):
    list.__setitem__(mem, abi.MUS_STREAM + i, b)
mpu = MPU(memory=mem)
TRAMP = 0x0200
list.__setitem__(mem, TRAMP, 0x20)           # JSR target, then stop at +3


def call(addr):
    list.__setitem__(mem, TRAMP + 1, addr & 0xFF)
    list.__setitem__(mem, TRAMP + 2, addr >> 8)
    mpu.pc, mpu.sp = TRAMP, 0xFF
    c0 = mpu.processorCycles
    while mpu.pc != TRAMP + 3:
        mpu.step()
        if mpu.processorCycles - c0 > 200000:
            raise SystemExit(f'runaway at ${mpu.pc:04X}')
    return mpu.processorCycles - c0


model = M.Player(stream, tab)
mem.latch[3] = 0
call(abi.MUS_INIT)
model.refill()
init_bytes = mem.chip[:]
if init_bytes != [0x9F, 0xBF, 0xDF, 0xFF]:
    fails.append(f'init wrote {[hex(b) for b in init_bytes]}')
if mem.ddra != 0x7F or mem.latch[3]:
    fails.append('init left the keyboard port changed')

rnd = random.Random(7)
TICKS = 11000                                # 2.3 loops (4802 ticks each)
next_refill, worst_tick, worst_refill, nbytes, drained = 0, 0, 0, 0, 0
for n in range(TICKS):
    if n == next_refill:
        if mem[lab['m_rd']] == mem[lab['m_wr']]:
            drained += 1
        worst_refill = max(worst_refill, call(abi.MUS_REFILL))
        model.refill()
        next_refill = n + (rnd.choice((300, 600)) if rnd.random() < 0.01 else rnd.randint(1, 25))
    key = rnd.randrange(0x80)
    mem.ddra, mem.ora, mem.latch[3] = 0x7F, key, 0
    mem.chip = []
    worst_tick = max(worst_tick, call(abi.MUS_TICK))
    want = model.tick()
    nbytes += len(want)
    if mem.chip != want:
        fails.append(f'tick {n}: 6502 {[hex(b) for b in mem.chip]} model {[hex(b) for b in want]}')
    if mem.ddra != 0x7F or mem.ora != key or mem.latch[3] != 0:
        fails.append(f'tick {n}: port left DDRA ${mem.ddra:02X} ORA ${mem.ora:02X} latch3 {mem.latch[3]}')
    if mem.bad:
        fails.append(f'tick {n}: {mem.bad[:3]}')
        mem.bad = []
    if len(fails) > 5:
        break
print(f'  {n + 1} ticks, {nbytes} chip bytes, ring found empty at {drained} refills; '
      f'worst tick {worst_tick} cycles, worst refill {worst_refill} cycles')
for f in fails[:6]:
    print('  FAIL', f)
print('MASTERMUSIC: FAIL' if fails else 'MASTERMUSIC: PASS')
sys.exit(1 if fails else 0)
