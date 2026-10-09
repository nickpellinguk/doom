#!/usr/bin/env python3
"""The Master's gun overlay (docs/master_textured_spec.md step 6f): the
spec the 6502's gun_draw reproduces byte for byte.

art/gun/PISGA0.txt is DOOM's idle pistol redrawn as Mode 2 pixel art: 24 x
50, each pixel one Mode 2 pixel by ONE line (full vertical resolution: the
first cut at two lines a pixel was too blocky to read as a hand), ' '
transparent. DOOM draws PISGA0 at (126, 106) on its 320 x 200 screen over a
168-line view; scaled 0.4 x 0.81 that is pixel 50, line 86 of our 128 x 136
view, so the grid's bottom row is line 135, the panel's edge.

Drawn into the back buffer after each frame, before the flip, as whole
bytes: a byte keeps the screen's bits of its transparent pixel(s) (mask) and
ORs in the art (data). Most of the gun's bytes have no transparent pixel, so
they are simply written. `python3 master_gun.py` compiles the table into
straight-line code, one copy per screen buffer (absolute addresses), in
src/master/mgun_tab.s (gun_b0 in shadow $3000, gun_b1 in bank 6): edge
bytes cleared with TRB, then each other value loaded once (one-byte INC /
DEC / ASL / LSR A from the last where possible) and stored (STA abs) or
ORed into the edge bytes (TSB abs); black with STZ. See source().
"""
import os

import master_assets as M

ROOT = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(ROOT, 'art', 'gun', 'PISGA0.txt')
TAB = os.path.join(ROOT, 'src', 'master', 'mgun_tab.s')
CH = '.rgybmcw'
X0 = 50                                 # the grid's first pixel (even: a byte edge)



def grid():
    rows = [l.rstrip('\n') for l in open(ART) if not l.startswith('#')]
    rows = [r for r in rows if r.strip()]
    w = max(len(r) for r in rows)
    return [[None if ch == ' ' else CH.index(ch) for ch in r.ljust(w)] for r in rows]


def table():
    """[(line, byte column, [(mask, data), ...]), ...] for the grid's rows."""
    out = []
    rows = grid()
    line0 = 136 - len(rows)                     # the bottom row on line 135
    for r, row in enumerate(rows):
        cells = []
        for k in range(0, len(row), 2):
            l, rt = row[k], row[k + 1] if k + 1 < len(row) else None
            mask = (0xAA if l is None else 0) | (0x55 if rt is None else 0)
            data = M.mode2_byte((l or 0, rt or 0)) & ~mask & 0xFF
            cells.append((X0 // 2 + k // 2, mask, data))
        cells = [c for c in cells if c[1] != 0xFF]
        if cells:
            first = cells[0][0]
            assert [c[0] for c in cells] == list(range(first, first + len(cells))), 'a gap in a gun row'
            out.append((line0 + r, first, [(m, d) for _, m, d in cells]))
    return out


def apply(fb):
    """The buffer with the gun drawn on it."""
    fb = bytearray(fb)
    for y, x, cells in table():
        for j, (m, d) in enumerate(cells):
            a = (y >> 3) * 512 + (x + j) * 8 + (y & 7)
            fb[a] = (fb[a] & m) | d
    return bytes(fb)


def cells():
    """[(buffer offset, mask, data), ...], every byte the gun touches."""
    return [((y >> 3) * 512 + (x + j) * 8 + (y & 7), m, d)
            for y, x, row in table() for j, (m, d) in enumerate(row)]


def _step(a, b):
    """A one-byte 65C02 instruction taking A from a to b, or None."""
    if b == (a + 1) & 0xFF:
        return 'INC A'
    if b == (a - 1) & 0xFF:
        return 'DEC A'
    if b == (a << 1) & 0xFF:
        return 'ASL A'
    if b == a >> 1:
        return 'LSR A'
    return None


def _order(start, values):
    """An order for the A values that maximises one-byte transitions
    (each saves the LDA #'s second byte): from the current A take the
    successor that starts the longest chain; when none, LDA a chain head
    (no unvisited predecessor) that starts the longest chain. Values are
    few (~25) and the step graph sparse, so chain lengths are searched
    exhaustively."""
    left = set(values)

    def chain(v, seen):
        best = 0
        for w in left:
            if w not in seen and _step(v, w):
                best = max(best, 1 + chain(w, seen | {w}))
        return best

    out, a = [], start
    while left:
        nxt = [w for w in left if a is not None and _step(a, w)]
        if not nxt:
            heads = [w for w in left if not any(_step(u, w) for u in left if u != w)]
            nxt = heads or list(left)
        v = max(sorted(nxt), key=lambda w: chain(w, {w}))
        out.append(v)
        left.discard(v)
        a = v
    return out


def source():
    """Straight-line 65C02 per buffer (2026-10-09: INC/DEC/ASL/LSR A between
    values, TRB/TSB for the edge bytes, STZ for black):
      1. edge bytes keep the screen's bits of their transparent pixel:
         TRB abs with A = ~mask clears the gun's pixel (masks: $55 / $AA)
      2. every other A value once, ordered by _order: STA abs for the opaque
         bytes of that value, TSB abs for the edge bytes whose data it is
         (after every TRB: the same addresses)
      3. opaque black: STZ abs (no A); edge data 0: TRB alone
    """
    s = ['; GENERATED by master_gun.py from art/gun/PISGA0.txt -- do not edit',
         '; the gun overlay, one routine per screen buffer (ACCCON X set)']
    cs = cells()
    clear = {}                                   # ~mask -> edge addresses
    put = {}                                     # value -> [(op, address)]
    zero = []
    for a, m, d in sorted(cs):
        if m == 0:
            if d == 0:
                zero.append(a)
            else:
                put.setdefault(d, []).append(('STA', a))
        else:
            clear.setdefault(~m & 0xFF, []).append(a)
            if d:
                put.setdefault(d, []).append(('TSB', a))
    for k, base in enumerate(('MSCREEN0', 'MSCREEN1')):
        # gun_b0 runs from shadow MGUN0 (freed by the shared panel; X is
        # set while it draws), gun_b1 from bank 6
        s.append('.segment "MGUN"' if k == 0 else '.segment "MB6C"')
        s.append(f'gun_b{k}:')
        acc = None

        def load(v):
            op = _step(acc, v) if acc is not None else None
            s.append(f'   {op}' if op else f'   LDA #${v:02X}')

        for v in _order(None, clear):
            load(v)
            acc = v
            for a in clear[v]:
                s.append(f'   TRB {base}+${a:04X}')
        for v in _order(acc, put):
            load(v)
            acc = v
            for op, a in put[v]:
                s.append(f'   {op} {base}+${a:04X}')
        for a in zero:
            s.append(f'   STZ {base}+${a:04X}')
        s.append('   RTS')
    return '\n'.join(s) + '\n'


if __name__ == '__main__':
    open(TAB, 'w').write(source())
    cs = cells()
    n0 = sum(1 for _, m, _ in cs if m == 0)
    print(f'{TAB}: {len(cs)} bytes, {n0} opaque, {len(cs) - n0} edge')
