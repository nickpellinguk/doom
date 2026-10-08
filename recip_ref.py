#!/usr/bin/env python3
"""PROTOTYPE (not gated): the run's step from a reciprocal table (docs/master_textured_spec.md).

tex_ref's step is exact, K // (B - T): on the 6502 a 16-bit-quotient
shift-subtract divide per run (`tv_divm`, ~340-725 cycles). Here, for
h = B - T <= 255, it is one 8 x 16 multiply and a shift instead:

  part      K rounded to 8 significant bits, K ~ m << z, 128 <= m <= 255
            (every E1M1 K but two already is; COMPUTE2's 4/7 and NUKE24's
            2/3 scales move by -0.2%):
                z = bitlen(K) - 8,  m = (K + (1 << (z - 1))) >> z
            (z, m stored per part; a carry to 256 renormalises)
  table     h normalised the same way, L = bitlen(h) - 1, and
                RECIP[h] = (2^(16 + L) - 1) // h        (2^15 < R < 2^16)
            for h = 1..255 (512 bytes, lo / hi pages)
  step      P = m * RECIP[h]  (24 bits: 2 quarter-square 8 x 8s)
                step = (P + RND) >> s,  s = 16 + L - z  (4 <= s <= 16)
            RND = 1 << (s - 1) with ROUND, else 0
  h > 255   exact, as tex_ref (near walls; step 7s's fine first v stays)

    python3 recip_ref.py            # step error table + frame diffs vs tex_ref
"""
import os, sys
from collections import Counter

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import tex_ref as X

ROUND = True
HMAX = 255
RECIP = [0] + [((1 << (16 + h.bit_length() - 1)) - 1) // h for h in range(1, 256)]


def mz(K):
    """K -> (m, z), K ~ m << z, m 8 significant bits (rounded)."""
    z = max(K.bit_length() - 8, 0)
    m = (K + ((1 << (z - 1)) if z else 0)) >> z
    if m > 255:
        m, z = m >> 1, z + 1
    return m, z


def rstep(K, h, rnd=None):
    """The prototype's step for K over h lines (h <= HMAX)."""
    rnd = ROUND if rnd is None else rnd
    m, z = mz(K)
    L = h.bit_length() - 1
    s = 16 + L - z
    P = m * RECIP[h]
    if s <= 0:
        return (P << -s) & 0xFFFF
    return ((P + ((1 << (s - 1)) if rnd else 0)) >> s) & 0xFFFF


class RecipRef(X.TexRef):
    def _step(self, pi, h):
        if h <= 0:
            return 0
        K = self.W.parts[pi]['K']
        if h > HMAX:
            return (K // h) & 0xFFFF
        return rstep(K, h)


def step_table(W):
    Ks = sorted(set(p['K'] for p in W.parts if p['K']))
    print(f'{len(Ks)} distinct K; shift s range', end=' ')
    ss = [16 + h.bit_length() - 1 - mz(K)[1] for K in Ks for h in range(1, HMAX + 1)]
    print(min(ss), '..', max(ss))
    for rnd in (False, True):
        err = Counter(); rel = 0.0
        for K in Ks:
            for h in range(1, HMAX + 1):
                q = K // h
                if q > 0xFFFF:
                    continue
                e = rstep(K, h, rnd) - q
                err[e] += 1
                rel = max(rel, abs(rstep(K, h, rnd) - K / h) / (K / h))
        print(f'  round={rnd}: step - K//h', dict(sorted(err.items())),
              f'max |step - K/h| / (K/h) = {100 * rel:.3f}%')
    print('  parts moved by the rounding of K:',
          [(hex(K), f'{100 * ((mz(K)[0] << mz(K)[1]) - K) / K:+.2f}%') for K in Ks
           if (mz(K)[0] << mz(K)[1]) != K])


def frames():
    import poses as C
    E, Rp = X.TexRef(), RecipRef()
    off = {(192, -2368, 99), (3648, -2368, 35), (1500, -3700, 0), (3648, -4800, 131)}
    tot_t = tot_d = tot_row = 0
    for pose in C.POSITIONS:
        if pose in off:
            continue
        E.render(*pose); ge = [r[:] for r in E.grid]
        Rp.render(*pose); gr = Rp.grid
        nt = nd = nrow = 0
        for a, b in zip(ge, gr):
            for ca, cb in zip(a, b):
                if ca and ca[0] == 't':
                    nt += 1
                    if ca != cb:
                        nd += 1
                        if cb and cb[0] == 't' and abs(ca[2] - cb[2]) % 32 not in (0, 1, 31):
                            nrow += 1
        tot_t += nt; tot_d += nd; tot_row += nrow
        print(f'  {pose}: {nt} wall cells, {nd} differ ({100 * nd / max(nt, 1):.2f}%), '
              f'{nrow} by more than a row')
    print(f'total {tot_t} wall cells, {tot_d} differ ({100 * tot_d / tot_t:.2f}%), '
          f'{tot_row} by more than a row')


if __name__ == '__main__':
    R = X.TexRef()
    step_table(R.W)
    if 'table' not in sys.argv[1:]:
        frames()
