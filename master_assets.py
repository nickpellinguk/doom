#!/usr/bin/env python3
"""Step 0 of the BBC Master textured port: E1M1 textures -> Master formats.

The spec is docs/master_textured_spec.md; this file is its executable form.

    python3 master_assets.py              # build into build/master/
    python3 master_assets.py --out DIR    # build elsewhere (the test does)

Outputs (all deterministic):
  tex_bank<N>.bin   wall column data + flats for sideways bank N, covering
                    region start .. last used byte (load address in
                    assets.json and textab.inc)
  textab.s          ca65 source for the HAZEL tables: texture directory,
                    texture headers + column index bytes, flat tables,
                    and the 256-byte cross-hatch FLIP table
  textab.inc        TEX_* / FLAT_* ids and bank-image equates
  assets.json       manifest for the Python reference renderer (step 2)
  walls.png, flats.png   previews decoded back from the packed bytes
  report.txt        memory report

Byte formats (Mode 1: pixel k of a byte uses bits 7-k (colour bit 1) and
3-k (colour bit 0); logical colours 0 black, 1 magenta, 2 cyan, 3 white):
  shade         one of 10 pixel pairs (a, b), a <= b: 4 solid + 6 mixes.
                Textures hold only the TOP row of the cross-hatch; the
                second screen line is FLIP[byte] (each pair swapped).
  wall texel    pair in pixels 2,3 (the $33 positions); the drawer writes
                (TEX1 << 2) OR TEX2, two strips per byte.
  floor texel   a full byte, pixels a b a b (#0 == #2, #1 == #3).
  wall column   32 bytes, one texel per byte, top row first. A 256-byte
                page holds 8 columns interleaved: byte = row*8 + slot.
  texture       header in HAZEL: ptr lo, ptr hi, bank, width, rowoff, then
                one index byte per column. Column c lives at
                ptr + (idx >> 3)*256 + (idx & 7), rows step +8 from
                Y = rowoff (0, or 128 for the bottom half of a stack).
                Columns are deduplicated within each texture only, and a
                texture's columns never cross a sideways bank.
  flat          16x16, one byte per texel, one page: byte = row*16 + col.

Every texture is 32 texels high, keeping its proportions, except the four
short ones, which are 16 high and stacked in pairs (STACKED).
"""
import argparse, json, os, struct, subprocess, sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
WAD = os.path.join(ROOT, 'DOOM1.WAD')

# ── palette and shades ────────────────────────────────────────────────
PALETTE = [(0, 0, 0), (255, 0, 255), (0, 255, 255), (255, 255, 255)]
PHYSICAL = [0, 5, 6, 7]          # VDU 19 physical colours for logical 0-3
SHADES = [(a, a) for a in range(4)] + [(a, b) for a in range(4)
                                       for b in range(a + 1, 4)]
GAIN = 2.0                       # source brightness boost before matching
CHROMA = 0.0                     # colour-difference weight (0 = brightness only)
LUMA = np.array([.299, .587, .114])

TEX_H = 32                       # stored texture height (texels)
SHORT_H = 16                     # height of a stacked half
STACKED = [('NUKE24', 'STEP6'), ('EXITSIGN', 'STEP1')]   # (top, bottom)
FLAT_N = 16                      # flats are FLAT_N x FLAT_N
ANIM_FLATS = [['NUKAGE1', 'NUKAGE2', 'NUKAGE3']]          # stored in order
SKY_FLAT = 'F_SKY1'

DEFAULT_REGIONS = [(5, 0x8000, 0xC000), (6, 0x8000, 0xC000)]


def mode1_byte(pixels):
    """Four logical colours (left to right) -> one Mode 1 byte."""
    b = 0
    for k, c in enumerate(pixels):
        b |= ((c >> 1) & 1) << (7 - k) | (c & 1) << (3 - k)
    return b


def mode1_pixels(b):
    return [((b >> (7 - k)) & 1) << 1 | ((b >> (3 - k)) & 1) for k in range(4)]


def wall_byte(shade):
    a, b = SHADES[shade]
    return mode1_byte((0, 0, a, b))


def floor_byte(shade):
    a, b = SHADES[shade]
    return mode1_byte((a, b, a, b))


FLIP = bytes(mode1_byte((p[1], p[0], p[3], p[2])) for p in map(mode1_pixels, range(256)))
SHADE_RGB = np.array([(np.array(PALETTE[a]) + PALETTE[b]) / 2 for a, b in SHADES])


# ── WAD reading ───────────────────────────────────────────────────────
class Wad:
    def __init__(self, path=WAD):
        self.d = open(path, 'rb').read()
        n, off = struct.unpack_from('<4xII', self.d)
        self.dir = []
        for i in range(n):
            p, s, nm = struct.unpack_from('<II8s', self.d, off + 16 * i)
            self.dir.append((nm.rstrip(b'\0').decode().upper(), p, s))
        self.first = {}
        for i, (nm, p, s) in enumerate(self.dir):
            self.first.setdefault(nm, i)
        pal = self.lump('PLAYPAL')[:768]
        self.pal = np.frombuffer(pal, np.uint8).reshape(256, 3).astype(float)

    def lump(self, name):
        _, p, s = self.dir[self.first[name]]
        return self.d[p:p + s]

    def flat(self, name):
        a, b = self.first['F_START'], self.first['F_END']
        for nm, p, s in self.dir[a:b]:
            if nm == name:
                return np.frombuffer(self.d[p:p + 4096], np.uint8).reshape(64, 64)
        raise KeyError(name)

    def _patch(self, name):
        b = self.lump(name)
        w, h = struct.unpack_from('<HH', b)
        img = np.full((h, w), -1, int)
        for x in range(w):
            o = struct.unpack_from('<I', b, 8 + 4 * x)[0]
            while b[o] != 255:
                top, ln = b[o], b[o + 1]
                img[top:top + ln, x] = list(b[o + 3:o + 3 + ln])
                o += ln + 4
        return img

    def textures(self):
        """name -> palette-index image (h, w), composited from patches."""
        pn = self.lump('PNAMES')
        pnames = [pn[4 + 8 * i:12 + 8 * i].rstrip(b'\0').decode().upper()
                  for i in range(struct.unpack_from('<I', pn)[0])]
        out, cache = {}, {}
        t1 = self.lump('TEXTURE1')
        for k in range(struct.unpack_from('<I', t1)[0]):
            o = struct.unpack_from('<I', t1, 4 + 4 * k)[0]
            nm, _, w, h, _, pc = struct.unpack_from('<8sIHHIH', t1, o)
            img = np.zeros((h, w), int)
            for j in range(pc):
                ox, oy, pi = struct.unpack_from('<hhH', t1, o + 22 + 10 * j)
                if pnames[pi] not in cache:
                    cache[pnames[pi]] = self._patch(pnames[pi])
                p = cache[pnames[pi]]
                ys, xs = np.nonzero(p >= 0)
                Y, X = ys + oy, xs + ox
                ok = (X >= 0) & (X < w) & (Y >= 0) & (Y < h)
                img[Y[ok], X[ok]] = p[ys[ok], xs[ok]]
            out[nm.rstrip(b'\0').decode().upper()] = img
        return out


# ── scaling and quantisation ──────────────────────────────────────────
def _cells(n, m):
    """m output cells over n source texels: box when shrinking, nearest
    when enlarging."""
    return [(i * n // m, max((i + 1) * n // m, i * n // m + 1)) for i in range(m)]


def scale_rgb(rgb, th, tw):
    h, w = rgb.shape[:2]
    out = np.zeros((th, tw, 3))
    for i, (y0, y1) in enumerate(_cells(h, th)):
        for j, (x0, x1) in enumerate(_cells(w, tw)):
            out[i, j] = rgb[y0:y1, x0:x1].reshape(-1, 3).mean(0)
    return out


def quantise(rgb):
    """RGB (.., 3) -> shade indices, matched mainly on brightness."""
    s = np.clip(rgb * GAIN, 0, 255)
    dl = (s @ LUMA)[..., None] - (SHADE_RGB @ LUMA)
    cost = dl ** 2
    if CHROMA:
        cost = cost + CHROMA * ((s[..., None, :] - SHADE_RGB) ** 2).sum(-1)
    return np.argmin(cost, -1)


# ── map usage ─────────────────────────────────────────────────────────
def _name(b):
    return b.rstrip(b'\0').decode().upper()


def map_usage():
    """Textures, flats and the stacking check from the engine's own map
    data (doom_wireframe) and mover rules (anim_sectors.Mover)."""
    import doom_wireframe as dw
    secs, sides, lines = dw.sectors, dw.sidedefs, dw.linedefs
    nb = {}
    for ld in lines:
        ss = [sides[s][5] for s in (ld[5], ld[6]) if s != 0xFFFF]
        for a in ss:
            nb.setdefault(a, set()).update(x for x in ss if x != a)
    # height ranges each sector can take (doors: ceiling; lifts: floor)
    rng = {}
    for i, s in enumerate(secs):
        f, c = (s[0], s[0]), (s[1], s[1])
        kind = dw.ANIM_SECTORS.get(i)
        if kind == 'ceil':
            c = (s[0], max(s[1], min(secs[n][1] for n in nb[i]) - 4))
        elif kind == 'floor':
            f = (min(s[0], min(secs[n][0] for n in nb[i])), s[0])
        rng[i] = (f, c)
    walls, exposure = set(), {}
    def use(tex, h):
        t = _name(tex)
        if t != '-':
            walls.add(t)
            exposure[t] = max(exposure.get(t, 0), h)
    for ld in lines:
        r, l = ld[5], ld[6]
        if l == 0xFFFF:
            s = sides[r]; (f, c) = rng[s[5]]
            use(s[4], c[1] - f[0])
            continue
        for a, b in ((r, l), (l, r)):
            s = sides[a]; (mf, mc), (of, oc) = rng[s[5]], rng[sides[b][5]]
            use(s[2], max(0, mc[1] - oc[0]))
            use(s[3], max(0, of[1] - mf[0]))
            use(s[4], max(0, min(mc[1], oc[1]) - max(mf[0], of[0])))
    flats = set()
    for s in secs:
        flats.update((_name(s[2]), _name(s[3])))
    flats.discard(SKY_FLAT)
    for grp in ANIM_FLATS:
        if flats & set(grp):
            flats |= set(grp)
    return sorted(walls), exposure, sorted(flats)


# ── building ──────────────────────────────────────────────────────────
class Region:
    def __init__(self, bank, start, end):
        self.bank, self.start, self.end = bank, start, end
        self.slot = 0                  # next free column slot from start
        self.mem = bytearray(end - start)
        self.top = 0                   # high-water mark (bytes)

    def slots_free(self):
        return (self.end - self.start) // 256 * 8 - self.slot


def build(out, regions=DEFAULT_REGIONS, wad_path=WAD):
    wad = Wad(wad_path)
    tex_src = wad.textures()
    walls, exposure, flat_names = map_usage()

    # 1. scale + quantise every wall texture
    stacked = {t: (i, half) for i, pair in enumerate(STACKED) for half, t in enumerate(pair)}
    q, meta = {}, {}
    for t in walls:
        img = tex_src[t]
        h, w = img.shape
        th = SHORT_H if t in stacked else TEX_H
        tw = max(1, round(w * th / h))
        q[t] = quantise(scale_rgb(wad.pal[img], th, tw))
        meta[t] = dict(src_w=w, src_h=h, height=th, width=tw)
        if t in stacked and exposure[t] > h:
            raise SystemExit(f'{t} is stacked but E1M1 can show {exposure[t]} '
                             f'units of it (texture is {h}); un-stack it')

    # 2. per-texture unique columns; stacked pairs share column storage
    def uniq(t):
        cols, idx = [], []
        for c in range(q[t].shape[1]):
            col = tuple(int(v) for v in q[t][:, c])
            if col not in cols:
                cols.append(col)
            idx.append(cols.index(col))
        return cols, idx
    groups = []                        # [(names, [32-byte columns], {name: idx})]
    done = set()
    for t in walls:
        if t in done:
            continue
        if t in stacked:
            pair = STACKED[stacked[t][0]]
            u = {p: uniq(p) for p in pair if p in q}
            n = max(len(u[p][0]) for p in u)
            cols = []
            for k in range(n):
                col = bytearray(TEX_H)
                for half, p in enumerate(pair):
                    if p in u and k < len(u[p][0]):
                        col[half * SHORT_H:(half + 1) * SHORT_H] = bytes(
                            wall_byte(s) for s in u[p][0][k])
                cols.append(bytes(col))
            groups.append((list(u), cols, {p: u[p][1] for p in u}))
            done.update(u)
        else:
            cols, idx = uniq(t)
            groups.append(([t], [bytes(wall_byte(s) for s in c) for c in cols], {t: idx}))
            done.add(t)

    # 3. pack column groups into banks, interleaved 8 per page, sharing pages
    regs = [Region(*r) for r in regions]
    place = {}
    ri = 0
    for names, cols, idx in groups:
        while regs[ri].slots_free() < len(cols):
            ri += 1                       # never split a texture across banks
            if ri == len(regs):
                raise SystemExit('wall textures do not fit the bank regions')
        R = regs[ri]
        base_page = R.slot // 8
        for k, col in enumerate(cols):
            s = R.slot + k
            for row, byte in enumerate(col):
                R.mem[(s // 8) * 256 + row * 8 + s % 8] = byte
        first = R.slot - base_page * 8
        for half, nm in enumerate(names):
            ptr = R.start + base_page * 256
            ix = [first + i for i in idx[nm]]
            assert max(ix) < 256
            rowoff = 128 if (nm in stacked and stacked[nm][1] == 1) else 0
            place[nm] = dict(bank=R.bank, ptr=ptr, rowoff=rowoff, index=ix,
                             unique=len(set(idx[nm])), stored_cols=len(cols))
        R.slot += len(cols)
        R.top = max(R.top, (R.slot + 7) // 8 * 256)

    # 4. flats: one page each, after the walls; animation groups contiguous
    units = [grp for grp in ANIM_FLATS if set(grp) <= set(flat_names)]
    units += [[f] for f in flat_names if not any(f in g for g in units)]
    order, fplace = [], {}
    for unit in units:                # an animation group: consecutive pages, one bank
        need = 256 * len(unit)
        R = next((r for r in regs if r.end - r.start - r.top >= need), None)
        if R is None:
            raise SystemExit('flats do not fit the bank regions')
        for f in unit:
            page = R.top // 256
            sq = quantise(scale_rgb(wad.pal[wad.flat(f)], FLAT_N, FLAT_N))
            R.mem[page * 256:(page + 1) * 256] = bytes(floor_byte(s) for s in sq.ravel())
            R.top += 256
            fplace[f] = dict(bank=R.bank, ptr=R.start + page * 256)
            order.append(f)
        R.slot = R.top // 256 * 8

    # 5. write outputs
    os.makedirs(out, exist_ok=True)
    tex_ids = {t: i for i, t in enumerate(walls)}
    flat_ids = {f: i for i, f in enumerate(order)}
    banks = {}
    for R in regs:
        if R.top:
            fn = f'tex_bank{R.bank}.bin'
            open(os.path.join(out, fn), 'wb').write(bytes(R.mem[:R.top]))
            banks[R.bank] = dict(file=fn, load=R.start, size=R.top)

    def cid(s):
        return ''.join(ch if ch.isalnum() else '_' for ch in s)
    S = ['; GENERATED by master_assets.py -- do not edit.',
         '; HAZEL tables for the textured Master build (docs/master_textured_spec.md).',
         '\t.export tex_dir, flat_bank, flat_page, flip_tab', '',
         '\t.segment "FLIPTAB"', 'flip_tab:']
    S += ['\t.byte ' + ','.join(f'${b:02X}' for b in FLIP[i:i + 16]) for i in range(0, 256, 16)]
    S += ['', '\t.segment "TEXTAB"', 'tex_dir:']
    S += [f'\t.word tex_{cid(t)}' for t in walls]
    S += ['flat_bank:', '\t.byte ' + ','.join(str(fplace[f]['bank']) for f in order),
          'flat_page:', '\t.byte ' + ','.join(f'>${fplace[f]["ptr"]:04X}' for f in order)]
    for t in walls:
        p = place[t]
        S += [f'tex_{cid(t)}:\t\t; {t} {meta[t]["src_w"]}x{meta[t]["src_h"]} -> '
              f'{meta[t]["width"]}x{meta[t]["height"]}, {p["unique"]} unique columns',
              f'\t.word ${p["ptr"]:04X}', f'\t.byte {p["bank"]}, {meta[t]["width"]}, {p["rowoff"]}']
        ix = p['index']
        S += ['\t.byte ' + ','.join(str(v) for v in ix[i:i + 16]) for i in range(0, len(ix), 16)]
    open(os.path.join(out, 'textab.s'), 'w').write('\n'.join(S) + '\n')

    I = ['; GENERATED by master_assets.py -- do not edit.',
         f'TEX_COUNT = {len(walls)}', f'FLAT_COUNT = {len(order)}',
         f'TEX_HEIGHT = {TEX_H}']
    I += [f'TEX_{cid(t)} = {i}' for t, i in tex_ids.items()]
    I += [f'FLAT_{cid(f)} = {i}' for f, i in flat_ids.items()]
    for b, v in banks.items():
        I += [f'TEXBANK{b}_LOAD = ${v["load"]:04X}', f'TEXBANK{b}_SIZE = ${v["size"]:04X}']
    I += [f'PAL_LOGICAL{i}_PHYS = {p}' for i, p in enumerate(PHYSICAL)]
    open(os.path.join(out, 'textab.inc'), 'w').write('\n'.join(I) + '\n')

    man = dict(palette=PALETTE, physical=PHYSICAL, shades=SHADES, gain=GAIN,
               chroma=CHROMA, tex_height=TEX_H, banks=banks,
               textures=[dict(name=t, id=tex_ids[t], **meta[t],
                              **{k: v for k, v in place[t].items()},
                              max_exposed=exposure[t]) for t in walls],
               flats=[dict(name=f, id=flat_ids[f], **fplace[f]) for f in order],
               anim_flats=ANIM_FLATS, stacked=STACKED)
    json.dump(man, open(os.path.join(out, 'assets.json'), 'w'), indent=1)

    # report
    idx_bytes = sum(len(place[t]['index']) for t in walls)
    hz = 256 + 2 * len(walls) + 5 * len(walls) + idx_bytes + 2 * len(order)
    wall_bytes = sum(len(g[1]) for g in groups) * TEX_H
    rep = [f'E1M1 assets: {len(walls)} wall textures, {len(order)} flats',
           f'wall columns: {sum(len(g[1]) for g in groups)} stored ({wall_bytes} B)',
           f'flats: {len(order)} x 256 = {256 * len(order)} B',
           f'HAZEL tables: {hz} B (dir {2 * len(walls)}, headers {5 * len(walls)}, '
           f'index {idx_bytes}, flat tables {2 * len(order)}, FLIP 256)']
    for R in regs:
        rep.append(f'bank {R.bank}: ${R.start:04X}-${R.start + R.top - 1:04X} used '
                   f'({R.top} of {R.end - R.start} B)' if R.top else f'bank {R.bank}: unused')
    rep.append('')
    rep.append(f'{"texture":9s} {"source":>8s} {"stored":>7s} {"uniq":>5s} bank  ptr   rowoff exposed')
    for t in walls:
        m, p = meta[t], place[t]
        rep.append(f'{t:9s} {m["src_w"]:>3d}x{m["src_h"]:<4d} {m["width"]:>3d}x{m["height"]:<3d} '
                   f'{p["unique"]:>5d}  {p["bank"]}   ${p["ptr"]:04X}  {p["rowoff"]:>3d}    {exposure[t]}')
    open(os.path.join(out, 'report.txt'), 'w').write('\n'.join(rep) + '\n')
    return man


# ── decoding (the step-2 reference renderer reads textures through these) ──
class Assets:
    """Read textures back from the BUILT bytes: the bank images plus the
    assembled HAZEL tables. mem(bank, addr) is the sideways-RAM view."""
    def __init__(self, out, hazel, hazel_base, labels):
        self.man = json.load(open(os.path.join(out, 'assets.json')))
        self.banks = {int(b): (v['load'], open(os.path.join(out, v['file']), 'rb').read())
                      for b, v in self.man['banks'].items()}
        self.hz, self.hzb, self.lab = hazel, hazel_base, labels

    def mem(self, bank, addr):
        load, data = self.banks[bank]
        return data[addr - load]

    def hzr(self, addr):
        return self.hz[addr - self.hzb]

    def texture_header(self, tid):
        d = self.lab['tex_dir'] + 2 * tid
        h = self.hzr(d) | self.hzr(d + 1) << 8
        ptr = self.hzr(h) | self.hzr(h + 1) << 8
        return ptr, self.hzr(h + 2), self.hzr(h + 3), self.hzr(h + 4), h + 5

    def wall_bytes(self, tid):
        """(rows, width) array of texel BYTES as the drawer reads them."""
        ptr, bank, width, rowoff, ixa = self.texture_header(tid)
        rows = self.man['textures'][tid]['height']
        out = np.zeros((rows, width), int)
        for c in range(width):
            i = self.hzr(ixa + c)
            colbase = ptr + (i >> 3) * 256 + (i & 7)
            for r in range(rows):
                y = rowoff + 8 * r
                assert y + (i & 7) <= 255          # never crosses a page
                out[r, c] = self.mem(bank, colbase + y)
        return out

    def flat_bytes(self, fid):
        bank = self.hzr(self.lab['flat_bank'] + fid)
        page = self.hzr(self.lab['flat_page'] + fid)
        return np.array([self.mem(bank, page * 256 + k) for k in range(256)]).reshape(16, 16)


def assemble_tables(out, hazel_base=0xC000):
    """Assemble textab.s with ca65/ld65 (TEXTAB, then FLIPTAB on a page
    boundary) -> (bytes, labels). Step 1 places it in the real HAZEL map."""
    cfg = os.path.join(out, 'textab_test.cfg')
    open(cfg, 'w').write(
        f'MEMORY {{ HZ: start=${hazel_base:04X}, size=$2000, file=%O, fill=no; }}\n'
        'SEGMENTS { TEXTAB: load=HZ, type=ro; FLIPTAB: load=HZ, type=ro, align=$100; }\n')
    o, b, m = (os.path.join(out, n) for n in ('textab.o', 'textab.bin', 'textab.map'))
    subprocess.run(['ca65', os.path.join(out, 'textab.s'), '-o', o], check=True)
    subprocess.run(['ld65', '-C', cfg, o, '-o', b, '-Ln', m], check=True)
    labels = {}
    for line in open(m):
        p = line.split()
        if len(p) >= 3 and p[0] == 'al':
            labels[p[2].lstrip('.')] = int(p[1], 16)
    return open(b, 'rb').read(), labels


def previews(out, A):
    """walls.png / flats.png drawn FROM THE PACKED BYTES, exactly as the
    drawer would: each texel row = byte, then FLIP[byte] on the next line."""
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
    import pygame
    pygame.init()
    font = pygame.font.Font(None, 16)
    Z = 2                                  # screen pixel -> 2x2 preview pixels; BBC pixels are 2:1
    def put(surf, x, y, byte, wide):
        for k, c in enumerate(mode1_pixels(byte)[2 if not wide else 0:]):
            surf.fill(PALETTE[c], (x + k * 2 * Z, y, 2 * Z, Z))
    texs = A.man['textures']
    rowh = [t['height'] * 2 * Z + 18 for t in texs]
    W = max(t['width'] * 4 * Z for t in texs) + 8
    s = pygame.Surface((W, sum(rowh))); s.fill((40, 40, 40))
    y = 0
    for t, rh in zip(texs, rowh):
        s.blit(font.render(f"{t['name']}  {t['src_w']}x{t['src_h']} -> {t['width']}x{t['height']}",
                           True, (220, 220, 220)), (0, y))
        wb = A.wall_bytes(t['id'])
        for r in range(wb.shape[0]):
            for c in range(wb.shape[1]):
                for line, b in enumerate((wb[r, c], FLIP[wb[r, c]])):
                    put(s, c * 4 * Z, y + 16 + (2 * r + line) * Z, b, False)
        y += rh
    pygame.image.save(s, os.path.join(out, 'walls.png'))
    fl = A.man['flats']
    cols = 6
    cw, ch = 16 * 8 * Z + 10, 16 * 2 * Z + 18
    s = pygame.Surface((cols * cw, -(-len(fl) // cols) * ch)); s.fill((40, 40, 40))
    for k, f in enumerate(fl):
        x0, y0 = (k % cols) * cw, (k // cols) * ch
        s.blit(font.render(f['name'], True, (220, 220, 220)), (x0, y0))
        fb = A.flat_bytes(f['id'])
        for r in range(16):
            for c in range(16):
                for line, b in enumerate((fb[r, c], FLIP[fb[r, c]])):
                    put(s, x0 + c * 8 * Z, y0 + 16 + (2 * r + line) * Z, b, True)
    pygame.image.save(s, os.path.join(out, 'flats.png'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=os.path.join(ROOT, 'build', 'master'))
    ap.add_argument('--no-preview', action='store_true')
    a = ap.parse_args()
    build(a.out)
    hz, lab = assemble_tables(a.out)
    if not a.no_preview:
        previews(a.out, Assets(a.out, hz, 0xC000, lab))
    print(open(os.path.join(a.out, 'report.txt')).read().split('\n\n')[0])
    print(f'outputs in {os.path.relpath(a.out, ROOT)}/')


if __name__ == '__main__':
    main()
