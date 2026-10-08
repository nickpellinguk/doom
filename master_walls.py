#!/usr/bin/env python3
"""Per-seg wall texture data for the BBC Master port (step 4,
docs/master_textured_spec.md).

ONE generator for both consumers: tex_ref.py (the bit-exact Python model)
reads its tables from here, and the image builders seed the 6502's copies
from the same bytes, so the model and the machine cannot disagree about
what a seg is dressed in.

  texture params  per texture id: tw, th (32, or 16 for the stacked short
                  ones), mask = 16*src_w - 1 (src_w is a power of two for
                  every E1M1 texture, so u mod the texture period is an
                  AND); a POWER-OF-TWO column index of n = the next power
                  of two >= tw entries, so the index is (u & mask) >> shift,
                  shift = log2(16*src_w // n), and logical column
                  i * tw // n (no multiply: 25 of the 32 textures have
                  n = tw = src_w / 4, shift 6)
  PARTS           one textured wall part: (texture id, K, Vtop)
                    K    = 2048*th*(fc - fh) // src_h  (0 if fc <= fh),
                           rounded to 8 significant bits m << z (step 7v);
                           the 5.11 step per line is wall_step(part, h),
                           h = B - T
                    Vtop = 2048*th*(ztop - fc + yoff) // src_h  (s16 mod
                           2^16), ztop from DOOM's pegging rules
  DRESSINGS       what one seg (or one piece of a merged seg) wears:
                  (up part, lo part, mid part, ubase) -- a portal draws its
                  top band with `up` and its bottom band with `lo`, a solid
                  seg its whole band with `mid` (mid texture, else lower,
                  else upper: textured_ref's rule for walled two-sided
                  lines); ubase = seg offset + sidedef x offset (world
                  units). NONE ($FF) = no texture: that band's wall rows
                  show the ceiling shade (a sky-to-sky upper shows sky)
  MERGED segs     the engine merges colinear neighbour segs with the same
                  sectors; all 20 merges in E1M1 join different linedefs,
                  so a merged seg is a list of PIECES (start in 1/16 world
                  units from the engine seg's v1, dressing id) and a strip
                  picks the last piece whose start <= d
  per SLOT        (the engine's page-slotted seg header slot = its packed
                  seg index): dressing id, or N_DRESS + merged-list id, and
                  L16 = round(16 * seg length)

Sector heights are the static WAD heights: movers (step 6) will recompute
the K / Vtop of the parts their sectors touch.
"""
import math

DONTPEGTOP, DONTPEGBOTTOM = 0x08, 0x10
NONE = 0xFF
# Sector light: five mask levels, (maskEven, maskOdd) ANDed into every byte
# a seg writes (even lines / odd lines, the odd line after its FLIP); sky
# is never masked. Level = min(4, (255 - light) >> 5): E1M1's 255/224 -> 0,
# 208/192 -> 1, 176/160 -> 2, 144/128 -> 3.
LIGHT_MASKS = [(0xFF, 0xFF), (0xAA, 0xFF), (0xAA, 0xAA), (0x0A, 0xAA), (0x0A, 0x0A)]


def light_level(light):
    return min(4, (255 - light) >> 5)
SKY = 'F_SKY1'


def _name(b):
    return b.rstrip(b'\0').decode().upper()


# ---- step 7v: the run's step from a reciprocal table ----------------------
# K is kept to 8 significant bits, K = m << z (128 <= m <= 255; 0 for an
# untextured height). For h = B - T <= 255 the step is
#     step = (m * RT_z[h] + 128) >> 8,  RT_z[h] = (2^(8+z) + h // 2) // h
# one 16-bit table per z in the level (bank 6 $8000: 2 pages each), and
# K // h where RT_z[h] would not fit 16 bits (stored 0) or h > 255.
RT_ZMAX = 7                         # tables that fit below mb6_pt_m


def step_mz(K):
    """K -> (m, z): K rounded to 8 significant bits, ~ m << z."""
    if K <= 0:
        return 0, 0
    z = max(K.bit_length() - 8, 0)
    m = (K + ((1 << (z - 1)) if z else 0)) >> z
    if m > 255:
        m, z = m >> 1, z + 1
    return m, z


def recip_t(z, h):
    """RT_z[h] (1 <= h <= 255), 0 where it would not fit 16 bits."""
    t = ((1 << (8 + z)) + h // 2) // h
    return t if t < 0x10000 else 0


def wall_step(p, h):
    """The run's line step (5.11) for part p over h = B - T lines."""
    if h <= 0:
        return 0
    if h <= 255:
        t = recip_t(p['z'], h)
        if t:
            return ((p['m'] * t + 128) >> 8) & 0xFFFF
    return (p['K'] // h) & 0xFFFF


class Walls:
    def __init__(self, dw, tex, man):
        """dw: e1m1; tex: name -> (texel array, src_w, src_h) as
        textured_ref loads it; man: master_assets manifest (assets.json)."""
        self.dw = dw
        self.tex = tex
        self.tid = {t['name']: t['id'] for t in man['textures']}
        self.tparams = []
        for t in man['textures']:
            tb, sw, sh = tex[t['name']]
            th, tw = tb.shape
            assert sw & (sw - 1) == 0, f"{t['name']}: source width {sw} not a power of two"
            n = 1 << (tw - 1).bit_length()
            assert 16 * sw % n == 0, f"{t['name']}: index {n} does not divide 16*{sw}"
            shift = (16 * sw // n).bit_length() - 1
            assert 3 <= shift <= 8 and n <= 256
            self.tparams.append(dict(name=t['name'], tw=tw, th=th, sw=sw, sh=sh,
                                     mask=16 * sw - 1, n=n, shift=shift))
        self.parts, self._part_ix = [], {}
        self.dress, self._dress_ix = [], {}
        self.pieces = []                       # merged segs: [(start16, dressing)]
        n = len(dw.fp_segs_vwh)
        self.slot_dress = [0] * n
        self.slot_len = [0] * n
        self.info = [self._seg(si) for si in range(n)]
        # slot byte: a dressing id, or len(dress) + merged-list id
        nd = len(self.dress)
        self.slot_dress = [v if v >= 0 else nd + (-1 - v) for v in self.slot_dress]
        assert nd + len(self.pieces) <= 256, 'slot byte overflow'

    # ---- builders ---------------------------------------------------------
    def _part(self, name, ztop, fc, fh, yoff):
        if name == '-' or name not in self.tid:
            return NONE
        p = self.tparams[self.tid[name]]
        K = (2048 * p['th'] * (fc - fh)) // p['sh'] if fc > fh else 0
        m, z = step_mz(K)
        K = m << z                      # step 7v: 8 significant bits
        vtop = ((2048 * p['th'] * (ztop - fc + yoff)) // p['sh']) & 0xFFFF
        key = (self.tid[name], K, vtop)
        if key not in self._part_ix:
            self._part_ix[key] = len(self.parts)
            self.parts.append(dict(tid=key[0], K=K, vtop=vtop, m=m, z=z))
        return self._part_ix[key]

    def _dressing(self, sd, flags, ubase, front, back):
        fh, fc = front[0], front[1]
        up, lo, mid = _name(sd[2]), _name(sd[3]), _name(sd[4])
        th = lambda nm: self.tex[nm][2] if nm in self.tex else 0
        # solid: mid texture, else lower, else upper
        m = mid if mid != '-' else (lo if lo != '-' else up)
        ztop = fh + th(m) if flags & DONTPEGBOTTOM else fc   # (the middle
        p_mid = self._part(m, ztop, fc, fh, sd[1])           #  rule, fallbacks too)
        p_up = p_lo = NONE
        if back is not None:
            sky_sky = _name(front[3]) == SKY and _name(back[3]) == SKY
            if not sky_sky:
                ztop = fc if flags & DONTPEGTOP else back[1] + th(up)
                p_up = self._part(up, ztop, fc, fh, sd[1])
            ztop = fc if flags & DONTPEGBOTTOM else back[0]
            p_lo = self._part(lo, ztop, fc, fh, sd[1])
        assert 0 <= ubase < 256, ubase
        key = (p_up, p_lo, p_mid, ubase)
        if key not in self._dress_ix:
            self._dress_ix[key] = len(self.dress)
            self.dress.append(key)
        return self._dress_ix[key]

    def _seg(self, si):
        dw = self.dw
        seg, front, back = dw.fp_segs_vwh[si][:3]
        front = dw.sectors[front]
        back = dw.sectors[back] if back is not None and back >= 0 else None
        V1, V2 = dw.vertexes[seg[0]], dw.vertexes[seg[1]]
        dx, dy = V2[0] - V1[0], V2[1] - V1[1]
        L2 = dx * dx + dy * dy
        along = lambda v: (v[0] - V1[0]) * dx + (v[1] - V1[1]) * dy
        raw = []
        for s in dw.segs:
            if s[2] != seg[2]:
                continue
            a, b = dw.vertexes[s[0]], dw.vertexes[s[1]]
            if any((p[0] - V1[0]) * dy != (p[1] - V1[1]) * dx for p in (a, b)):
                continue
            if 0 <= along(a) < L2 and 0 < along(b) <= L2:
                raw.append((along(a), s))
        raw.sort()
        assert raw and raw[0][0] == 0, f'seg {si}: no raw seg at v1'
        pcs = []
        for at, s in raw:
            ld = dw.linedefs[s[3]]
            sd = dw.sidedefs[ld[5] if s[4] == 0 else ld[6]]
            start = int(round(16 * at / math.sqrt(L2)))
            pcs.append((start, self._dressing(sd, ld[2], s[5] + sd[0], front, back)))
        self.slot_len[si] = int(round(16 * math.sqrt(L2)))
        if len(pcs) == 1:
            self.slot_dress[si] = pcs[0][1]
        else:
            self.slot_dress[si] = -1 - len(self.pieces)    # fixed up below
            self.pieces.append(pcs)
        return dict(front=front, back=back, sky=_name(front[3]) == SKY,
                    level=light_level(front[4]))

    # ---- lookups (the model's view of the tables) -------------------------
    def dressing_at(self, si, d):
        """(piece start, dressing) holding distance d (1/16 units) along
        seg si: the last piece whose start <= d (the first if none)."""
        v = self.slot_dress[si]
        if v < len(self.dress):
            return 0, self.dress[v]
        cur = None
        for start, dr in self.pieces[v - len(self.dress)]:
            if cur is None or d >= start:
                cur = (start, dr)
        return cur[0], self.dress[cur[1]]

    def images(self, man):
        """The 6502's copies: {'andy': 4K at man_slot_d's page (ANDY),
        'b6t': bytes at mb6_pt_tid (bank 6 tail), 'ix': bytes at mtex_ix},
        laid out by the MASTER link's labels (src/master/mfill.s)."""
        from symmap import sym
        L = lambda n: sym(n)
        andy_base, b6t_base = L('man_slot_d'), L('mb6_pt_tid')
        assert andy_base == 0x8000
        andy = bytearray(0x1000)
        put = lambda n, i, v: andy.__setitem__(L(n) - andy_base + i, v)
        n = len(self.slot_dress)
        assert n <= 0x300
        for i in range(n):
            put('man_slot_d', i, self.slot_dress[i])
            put('man_slot_ll', i, self.slot_len[i] & 0xFF)
            put('man_slot_lh', i, self.slot_len[i] >> 8)
        assert len(self.dress) <= 0x100
        for i, (up, lo, mid, ub) in enumerate(self.dress):
            put('man_dr_up', i, up)
            put('man_dr_lo', i, lo)
            put('man_dr_mid', i, mid)
            put('man_dr_ub', i, ub)
        k = 0
        assert len(self.pieces) <= 0x20
        for m, pcs in enumerate(self.pieces):
            put('man_pl_first', m, k)
            put('man_pl_n', m, len(pcs))
            assert len(pcs) <= 4, 'tx_seg holds at most 4 pieces'
            for start, dr in pcs:
                put('man_pc_sl', k, start & 0xFF)
                put('man_pc_sh', k, start >> 8)
                put('man_pc_dr', k, dr)
                k += 1
        assert k <= 0x40
        put('man_ndress', 0, len(self.dress))
        # step 5: per subsector, its sector's floor / ceiling flat id
        # ($FF: sky, drawn as the solid shade); the floor byte's top 3 bits
        # carry the sector's light level
        fid = {f['name']: f['id'] for f in man['flats']}
        dw = self.dw
        assert len(dw.fp_ssectors) <= 0xC4
        for ss, (cnt, first) in enumerate(dw.fp_ssectors):
            if not cnt:
                continue
            sec = dw.sectors[dw.fp_segs_vwh[first][1]]
            fpic, cpic = _name(sec[2]), _name(sec[3])
            assert fid[fpic] < 0x20
            put('man_ss_ff', ss, fid[fpic] | light_level(sec[4]) << 5)   # + light
            put('man_ss_fc', ss, 0xFF if cpic == SKY else fid[cpic])
        b6t = bytearray(0x700)
        bput = lambda n, i, v: b6t.__setitem__(L(n) - b6t_base + i, v)
        assert len(self.parts) <= 0xA0
        for i, p in enumerate(self.parts):
            bput('mb6_pt_tid', i, p['tid'])
            assert p['K'] < 1 << 24
            for j in range(3):
                bput(f'mb6_pt_k{j}', i, (p['K'] >> (8 * j)) & 0xFF)
            bput('mb6_pt_v0', i, p['vtop'] & 0xFF)
            bput('mb6_pt_v1', i, p['vtop'] >> 8)
        # step 7v: the step tables and per-part m / table page (bank 6 $8000)
        b6r_base = L('mb6_rc')
        assert b6r_base == 0x8000
        b6r = bytearray(0x1000)
        zs = sorted(set(p['z'] for p in self.parts if p['m']))
        assert len(zs) <= RT_ZMAX, 'too many step-table exponents'
        for k, z in enumerate(zs):
            for h in range(1, 256):
                t = recip_t(z, h)
                b6r[k * 0x200 + h] = t & 0xFF
                b6r[k * 0x200 + 0x100 + h] = t >> 8
        for i, p in enumerate(self.parts):
            k = zs.index(p['z']) if p['m'] else 0
            b6r[L('mb6_pt_m') - b6r_base + i] = p['m']
            b6r[L('mb6_pt_rp') - b6r_base + i] = (b6r_base >> 8) + 2 * k
        ix = bytearray()
        ix_base = L('mtex_ix')
        assert len(self.tparams) <= 0x20
        for t, tp in zip(man['textures'], self.tparams):
            i = t['id']
            assert t['ptr'] & 0xFF == 0 and t['rowoff'] in (0, 128)
            assert tp['th'] == (16 if t['rowoff'] or t['height'] == 16 else 32)
            bput('mb6_tp_sl', i, 8 - tp['shift'])     # index = hi((u & mask) << sl)
            bput('mb6_tp_ml', i, tp['mask'] & 0xFF)
            bput('mb6_tp_mh', i, tp['mask'] >> 8)
            bput('mb6_tp_rowm', i, (tp['th'] - 1) * 8)
            bput('mb6_tp_ph', i, t['ptr'] >> 8)
            bput('mb6_tp_bank', i, t['bank'])
            bput('mb6_tp_ro', i, t['rowoff'])
            a = ix_base + len(ix)
            bput('mb6_tp_ixl', i, a & 0xFF)
            bput('mb6_tp_ixh', i, a >> 8)
            assert len(t['index']) == tp['tw']
            ix += bytes(t['index'][j * tp['tw'] // tp['n']] for j in range(tp['n']))
        assert len(ix) <= 0x420, 'column index blob overruns mtex_ix'
        # step 5: flat bank / page by flat id, and the map centre's 4.12 terms
        assert len(man['flats']) <= 0x20
        for f in man['flats']:
            assert f['ptr'] & 0xFF == 0
            bput('mb6_fl_bank', f['id'], f['bank'])
            bput('mb6_fl_page', f['id'], f['ptr'] >> 8)
        cx = (dw.MAP_CENTER_X * 1024) & 0xFFFF
        cy = (-dw.MAP_CENTER_Y * 1024) & 0xFFFF
        bput('mb6_mcx', 0, cx & 0xFF)
        bput('mb6_mcx', 1, cx >> 8)
        bput('mb6_mcy', 0, cy & 0xFF)
        bput('mb6_mcy', 1, cy >> 8)
        assert L('mb6_mcy') + 2 - b6t_base <= len(b6t)
        return {'andy': bytes(andy), 'b6t': bytes(b6t), 'b6t_base': b6t_base,
                'b6r': bytes(b6r),
                'ix': bytes(ix), 'ix_base': ix_base}

    def report(self):
        return (f'{len(self.parts)} parts, {len(self.dress)} dressings, '
                f'{len(self.pieces)} merged segs '
                f'({sum(len(p) for p in self.pieces)} pieces), '
                f'{len(self.slot_dress)} slots')


_RIG = None


def rig_images():
    """Everything the MASTER build's wall texturer reads, as the disc ships
    it: bank 5, bank 6 (texels + the mb6 tail), ANDY, and the mtex_ix blob
    for main RAM. Cached per process (the asset build and the walk of the
    map are slow)."""
    global _RIG
    if _RIG is None:
        import textured_ref as T
        R = T.TexturedRef()
        W = Walls(R.dw, R.tex, R.A.man)
        im = W.images(R.A.man)
        b5 = bytearray(16384)
        b6 = bytearray(16384)
        for bank, buf in ((5, b5), (6, b6)):
            load, data = R.A.banks[bank]
            buf[load - 0x8000:load - 0x8000 + len(data)] = data
        t6 = im['b6t_base'] - 0x8000
        assert len(R.A.banks[6][1]) <= t6, 'bank 6 texels reach the mb6 tail'
        b6[t6:t6 + len(im['b6t'])] = im['b6t']
        assert len(R.A.banks[6][1]) == 0, 'bank 6 $8000-$8FFF holds the step tables'
        b6[0:len(im['b6r'])] = im['b6r']
        _RIG = dict(b5=bytes(b5), b6=bytes(b6), andy=im['andy'], ix=im['ix'],
                    ix_base=im['ix_base'],
                    b6_tex_end=0x8000 + len(R.A.banks[6][1]))
    return _RIG


if __name__ == '__main__':
    import os
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    import textured_ref as T
    R = T.TexturedRef()
    W = Walls(R.dw, R.tex, R.A.man)
    print(W.report())
    print('max K', max(p['K'] for p in W.parts), 'max L16', max(W.slot_len))
    im = W.images(R.A.man)
    print({k: len(v) for k, v in im.items()})
