#!/usr/bin/env python3
"""Gate (step 7h): the Master fill's exact divide. div32 (m_p 32 / m_b 16
-> quotient m_p, remainder m_r) picks its own path: an 8-bit divisor <= 128
the unrolled byte steps (dv8f / d8_fast, step 7h), a larger 8-bit one the
patched byte loop, a 16-bit one the unrolled dq_core (7f), a quotient of
2^16 or more the 32-step loop. Run on the 6502 against Python over every
8-bit divisor, the 16-bit band and the slow path, with random dividends and
the edges (zero, quotient bytes of 0, remainders of d - 1). Prints
MASTERDIV: PASS / FAIL."""
import os, random, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.init()
import abi
import e1m1 as dw
from banked_bsp import MasterBspRender
from symmap import sym

R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                    dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
bm = R.bm
S = lambda n: sym(n)
MP, MB, MR, DIV = S('m_p'), S('m_b'), S('m_r'), S('div32')
bm.select(abi.BANK_C)                   # div32's byte steps live in bank 6


def div(n, d):
    for k in range(4):
        bm[MP + k] = (n >> (8 * k)) & 0xFF
    bm[MB], bm[MB + 1] = d & 0xFF, d >> 8
    cyc = R.sc._run(DIV)
    q = sum(bm[MP + k] << (8 * k) for k in range(4))
    r = bm[MR] | bm[MR + 1] << 8
    return q, r, cyc


rnd = random.Random(7)
cases = []
for d in range(1, 256):                 # every 8-bit divisor, quotient < 2^16
    top = d << 16
    cases += [(0, d), (d - 1, d), (d, d), (top - 1, d), ((d - 1) << 8, d),
              ((d - 1) << 16 | 0xFFFF, d) if (d - 1) << 16 | 0xFFFF < top else (top - 1, d)]
    cases += [(rnd.randrange(top), d) for _ in range(12)]
for _ in range(400):                    # 16-bit divisors, quotient < 2^16
    d = rnd.randrange(256, 65536)
    cases += [(rnd.randrange(d << 16), d)]
for _ in range(100):                    # quotient >= 2^16: the 32-step loop
    d = rnd.randrange(1, 65536)
    cases += [(rnd.randrange(d << 16, 1 << 32), d)]
fails, cyc8, n8 = [], 0, 0
for n, d in cases:
    q, r, cyc = div(n, d)
    if (q, r) != (n // d, n % d):
        fails.append(f'{n} / {d}: got q={q} r={r}, want q={n // d} r={n % d}')
    if d <= 128 and n < d << 16:
        cyc8 += cyc; n8 += 1
print(f'{len(cases)} divisions; divisor <= 128: {n8}, mean {cyc8 // max(n8, 1)} cycles')
for f in fails[:10]:
    print('FAIL:', f)
print('MASTERDIV: FAIL' if fails else 'MASTERDIV: PASS')
sys.exit(1 if fails else 0)
