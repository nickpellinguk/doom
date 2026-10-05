#!/usr/bin/env python3
"""Gate for master_assets.py (step 0 of docs/master_textured_spec.md).

Builds the assets twice into scratch directories, assembles the HAZEL
tables with ca65/ld65, then reads every texture back THROUGH THE BUILT
BYTES (directory -> header -> index byte -> interleaved column) and checks
it against the scaled, quantised source. Also checks the byte-format
invariants the 6502 drawers will rely on. Prints MASTERASSETS: PASS.
"""
import os, sys, tempfile
import numpy as np
import master_assets as M

fails = []


def check(ok, msg):
    if not ok:
        fails.append(msg)
        print('  FAIL:', msg)


def read_all(d):
    return {f: open(os.path.join(d, f), 'rb').read() for f in sorted(os.listdir(d))
            if f.endswith(('.bin', '.s', '.inc', '.json'))}


with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
    man = M.build(t1)
    M.build(t2)
    hz, lab = M.assemble_tables(t1)
    M.assemble_tables(t2)
    a, b = read_all(t1), read_all(t2)
    check(a.keys() == b.keys() and all(a[k] == b[k] for k in a
                                       if k not in ('textab.o',)),
          'build is not deterministic')
    A = M.Assets(t1, hz, 0xC000, lab)

    # ── byte formats ──
    check(all(M.FLIP[M.FLIP[x]] == x for x in range(256)), 'FLIP is not an involution')
    check(bytes(hz[lab['flip_tab'] - 0xC000:][:256]) == M.FLIP, 'assembled FLIP differs')
    check(lab['flip_tab'] & 0xFF == 0, 'FLIP table not page aligned')
    for s, (pa, pb) in enumerate(M.SHADES):
        w = M.wall_byte(s)
        check(w & ~0x33 & 0xFF == 0, f'wall byte {w:02X} outside $33')
        check(M.mode1_pixels(w)[2:] == [pa, pb], f'wall byte {w:02X} pixels')
        check(M.FLIP[w] & ~0x33 & 0xFF == 0, f'FLIP of wall byte {w:02X} leaves $33')
        f = M.floor_byte(s)
        p = M.mode1_pixels(f)
        check(p == [pa, pb, pa, pb], f'floor byte {f:02X} pixels {p}')
        check(M.mode1_pixels(M.FLIP[f]) == [pb, pa, pb, pa], f'FLIP of floor byte {f:02X}')
        for s2, (qa, qb) in enumerate(M.SHADES):     # (TEX1 << 2) OR TEX2
            comb = ((w << 2) & 0xFF) | M.wall_byte(s2)
            check(M.mode1_pixels(comb) == [pa, pb, qa, qb], f'strip combine {s},{s2}')

    # ── walls: every texel read back through the tables ──
    wad = M.Wad()
    src = wad.textures()
    for t in man['textures']:
        q = M.quantise(M.scale_rgb(wad.pal[M.clipped(t['name'], src[t['name']])], t['height'], t['width']))
        want = np.vectorize(M.wall_byte)(q)
        got = A.wall_bytes(t['id'])
        check(got.shape == want.shape and (got == want).all(), f"{t['name']} reads back wrong")
        ptr, bank, width, rowoff, _ = A.texture_header(t['id'])
        load, data = A.banks[bank]
        last = ptr + (max(t['index']) >> 3) * 256 + 255
        check(load <= ptr and last < load + len(data), f"{t['name']} leaves its bank image")
        check(t['height'] == M.TEX_H or t['max_exposed'] <= t['src_h'],
              f"{t['name']} stacked but repeats vertically")
        check(len(set(t['index'])) == t['unique'], f"{t['name']} index count")

    # stacked halves share storage and never overlap each other's rows
    for top, bot in M.STACKED:
        tt = next(x for x in man['textures'] if x['name'] == top)
        tb = next(x for x in man['textures'] if x['name'] == bot)
        check((tt['bank'], tt['ptr']) == (tb['bank'], tb['ptr']), f'{top}/{bot} not stacked')
        check((tt['rowoff'], tb['rowoff']) == (0, 128), f'{top}/{bot} row offsets')

    # ── flats ──
    for f in man['flats']:
        q = M.quantise(M.scale_rgb(wad.pal[wad.flat(f['name'])], M.FLAT_N, M.FLAT_N))
        check((A.flat_bytes(f['id']) == np.vectorize(M.floor_byte)(q)).all(),
              f"{f['name']} reads back wrong")
    names = [f['name'] for f in man['flats']]
    for grp in M.ANIM_FLATS:
        i = names.index(grp[0])
        pages = [man['flats'][i + k]['ptr'] for k in range(len(grp))]
        check(names[i:i + len(grp)] == grp, f'{grp} not consecutive')
        check(len({man['flats'][i + k]['bank'] for k in range(len(grp))}) == 1, f'{grp} split across banks')
    check(M.SKY_FLAT not in names, 'sky stored as a flat')

    # ── no two things share bytes ──
    used = {}
    for t in man['textures']:
        for i in set(t['index']):
            for r in range(t['height']):
                key = (t['bank'], t['ptr'] + (i >> 3) * 256 + (i & 7) + t['rowoff'] + 8 * r)
                prev = used.setdefault(key, t['name'])
                check(prev == t['name'], f"{t['name']} overlaps {prev} at {key}")
    for f in man['flats']:
        for k in range(256):
            key = (f['bank'], f['ptr'] + k)
            check(key not in used, f"{f['name']} overlaps {used.get(key)}")
            used[key] = f['name']

print(f"textures {len(man['textures'])}, flats {len(man['flats'])}, "
      f"HAZEL tables {len(hz)} B")
print('MASTERASSETS: FAIL' if fails else 'MASTERASSETS: PASS')
sys.exit(1 if fails else 0)
