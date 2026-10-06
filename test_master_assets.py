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

    # ── byte formats (Mode 2, step 6a) ──
    for x in range(256):
        check(M.mode2_byte(M.mode2_pixels(x)) == x, f'mode2 pixels round trip {x:02X}')
    for s, (pa, _) in enumerate(M.SHADES):
        w = M.wall_byte(s)
        check(w & ~0xAA & 0xFF == 0, f'wall byte {w:02X} outside the left pixel ($AA)')
        check(M.mode2_pixels(w) == [pa, 0], f'wall byte {w:02X} pixels')
        f = M.floor_byte(s)
        check(M.mode2_pixels(f) == [pa, pa], f'floor byte {f:02X} pixels')
        for s2, (qa, _) in enumerate(M.SHADES):      # TEX1 OR (TEX2 >> 1)
            comb = M.wall_pair(w, M.wall_byte(s2))
            check(comb == w | (M.wall_byte(s2) >> 1), f'wall_pair {s},{s2}')
            check(M.mode2_pixels(comb) == [pa, qa], f'strip combine {s},{s2}')

    # ── floor cross-hatch (step 6d): FLIP swaps the pixels; only TONES ──
    for x in range(256):
        a, b = M.mode2_pixels(x)
        check(M.mode2_pixels(M.FLIP[x]) == [b, a], f'FLIP {x:02X}')
    check(len(M.PAIRS) == 12 and len(set(M.TONES)) == 32, 'tone set')   # + cycling (6e)
    for a, b in M.PAIRS:
        check(a in (M.BLACK, M.WHITE) and b not in (M.BLACK, M.WHITE),
              f'pair {a},{b} is not black or white + a colour')
    check(M.mode2_pixels(M.SKY_BYTE) == [M.CYAN, M.WHITE], 'sky byte')
    ok_tones = {M.tone_byte(t) for t in range(len(M.TONES))}

    # ── walls: every texel read back through the tables ──
    wad = M.Wad()
    src = wad.textures()
    for t in man['textures']:
        import wall_art
        q = wall_art.wall_texels(t['name'], wad.pal[M.clipped(t['name'], src[t['name']])].astype(float),
                                 t['height'], t['width'])
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
        q = wall_art.flat_tones(f['name'], wad.pal[wad.flat(f['name'])].astype(float))
        got = A.flat_bytes(f['id'])
        check((got == np.vectorize(M.tone_byte)(q)).all(), f"{f['name']} reads back wrong")
        check(set(got.ravel().tolist()) <= ok_tones, f"{f['name']} uses a pair outside TONES")
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
