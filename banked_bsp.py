#!/usr/bin/env python3
"""The Master engine rig: the py65 harness for the Master link on the banked
memory model (banked_mem.py: ROMSEL $FE30, ACCCON $FE34, ANDY, HAZEL, shadow
RAM).

build_banked() lays out every image the disc ships: bank 4 (seg headers,
vertex planes, recips, side tables), bank 7 (node / subsector SoA, angle
tables, bbox, collision, anim CFG), bank 5 and bank 6 (textures, flats, the
fill's cold code, the part records), ANDY (per-seg wall tables), main RAM
(engine code, the bank-C clipper content laid linear at CBITS_M) and HAZEL
(the fill). MasterBspRender runs frames on it; plot_h / plot_v /
RASTER_ENTRY are RTS emit stubs whose PCs the rig traps, so last_lines is
the engine's complete emitted line list.
"""
import os
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import pygame; pygame.init()
import e1m1 as dw
import fp
from banked_mem import BankedMemory
from bsp_render_6502 import BspRender6502

import abi
BANK_L0, BANK_C, BANK_L2 = abi.BANK_L0, abi.BANK_C, abi.BANK_L2
from symmap import sym as _rsym

N_FRAMES = 64                           # the driver's sin/cos table: one
ANGLE_STEP = 256 // N_FRAMES            #  entry per 4 angle units


def sincos_table():
    """64 entries x 8 bytes: smag,sneg,sone,cmag,cneg,cone,ab,pad."""
    t = bytearray(N_FRAMES * 8)
    for i in range(N_FRAMES):
        a = (i * ANGLE_STEP) & 0xFF
        sm, sn, so, cm, cn, co = fp.fp_sincos(a)
        e = i * 8
        t[e+0] = sm & 0xFF
        t[e+1] = 1 if sn else 0
        t[e+2] = 1 if so else 0
        t[e+3] = cm & 0xFF
        t[e+4] = 1 if cn else 0
        t[e+5] = 1 if co else 0
        t[e+6] = a
        t[e+7] = 0
    return bytes(t)




def build_banked(flatr):
    """flatr: a constructed BspRender6502 (the WAD-loading base). Returns the
    BankedMemory holding every Master image."""
    # Build the banked engine BEFORE reading its bins: without this, the
    # region loop below loads whatever a PREVIOUS process linked — every
    # consumer ran one build behind its sources (caught 2026-07-10 when a
    # vrcache negative-test alternated PASS/FAIL run-to-run).
    import asmbuild
    VAR = 2                             # the Master link (bank-C content in
                                        # main RAM at CBITS_M)
    asmbuild.build('engine')
    fmem = flatr.sc.mpu.memory
    bm = BankedMemory(list(fmem))
    layout = dw.packed_layout
    off_vwh = layout['off_vwh']
    rom_main = flatr.rom_main

    # --- bank L0: pure level data, verts evicted to L2.
    # [SoA $8000 | seg_hdr $9000 (stride 18, heights INLINED at +12..17;
    # the separate FHCH stream retired 2026-07-11) | TABL0 $BE90].
    # SSMASK -> MAIN $0A80 (rule exception, measured: hub reads it per
    # subsector under whatever bank; main = 0 paging. 237 B.)
    # --- bank A (BANK_SEG=4, two-bank re-cut 2026-08-13): seg headers+DIRs
    # @ $8000, vertex planes @ ROM_VERTS_C, recip @ RECIP_M8/M8H, VYCACHE BSS
    # (must ship ZERO — the key plane doubles as validity), TABL0 @ $BE90 ---
    la = bytearray(16384)
    off_verts = layout['off_verts']; off_hdr = layout['off_seg_hdr']
    n_segs = layout['n_segs']
    from symmap import sym as _vsym
    def bdst(name):
        return _vsym(name) - 0x8000    # dst offsets BY SYMBOL —
                                                 # the .s equates are the
                                                 # single source (2026-07-21)
    # HEADERS ONLY at the bottom (2026-08-17). The side tables and the DIR
    # source used to ride along as one contiguous blob copy; they are placed by
    # symbol now, at the top of the bank, so everything between the header
    # block and the vertex planes is one free run for the main-RAM caches.
    hdr_bytes = layout['off_dirs'] - off_hdr
    la[:hdr_bytes] = bytes(rom_main[off_hdr:off_hdr + hdr_bytes])
    for _nm, _off, _n in (('ROM_LV1X_LO_C', layout['off_lv1'],   512),
                          ('ROM_BPAL_BFH_C', layout['off_bpal'], 256)):
        _d = bdst(_nm)
        la[_d:_d + _n] = bytes(rom_main[_off:_off + _n])
    # (SS_FH/SS_CH left bank A for bank B 2026-08-19 — seeded into lb
    #  below with the rest of the five adjacent SS planes)
    assert hdr_bytes <= bdst('ROM_VERTS_C'), "seg headers reach the vertex planes"
    # DIR planes (3 x LAY_MAX_DIRS) also land at ROM_DIRS_C in BOTH banks:
    # the shared CROSS_MAG_DECIDE reads them from node classify (bank WALK)
    # AND seg backface (bank SEG) — $B700 is free in both windows
    dirs_off = layout['off_dirs']       # (page-slotted headers: NOT n_segs*stride)
    dir_blob = bytes(rom_main[dirs_off:dirs_off + 3 * layout['max_dirs']])
    la[0x3700:0x3700 + len(dir_blob)] = dir_blob
    # driver tables: sincos and the use vectors moved to BANK C 2026-08-17 (see
    # the C image below) so the top of bank A could take the seg side tables;
    # STEPTAB went with them — DELETED, it had no reader in either language
    # after the single-step momentum rework replaced stepping with arithmetic.
    import colmap as _cm
    _ut = _cm.blobs()[abi.USETAB_BASE]        # USETAB (bank A —
    la[abi.USETAB_BASE-0x8000:abi.USETAB_BASE-0x8000 + len(_ut)] = _ut  # seed BEFORE
                                                        # define_bank COPIES)
    vlen = off_hdr - off_verts
    la[bdst('ROM_VERTS_C'):bdst('ROM_VERTS_C') + vlen] = bytes(rom_main[off_verts:off_hdr])
    # static-object (billboard) table -- bank SEG, beside the vertex planes
    # it is read alongside (layout.inc ROM_OBJ_C)
    _oo = layout['off_obj']; _od = bdst('ROM_OBJ_C')
    la[_od:_od + 0x200] = bytes(rom_main[_oo:_oo + 0x200])   # the hole only
                                                             # (K planes below)
    # EXACT recip lengths (256 + 128): a padded 1K copy here would drag
    # flat-image garbage over the VYCACHE key plane at $B300 -> stale serves
    # (from fp, not via a flat image: page 0 nibble-swapped -- the fast path
    #  indexes (vy_l & $F0) | vy_h -- and the far half unswapped)
    from fp import _RECIP_M8
    _m8 = bdst('RECIP_M8'); _m8h = bdst('RECIP_M8H')
    for i in range(256):
        la[_m8 + (((i & 0x0F) << 4) | (i >> 4))] = _RECIP_M8[i]
    for i in range(128, 256):
        la[_m8h + i - 128] = _RECIP_M8[i]
    # RECIP_S: the junior-page shift table, beside the mantissa tables it is
    # read with (2026-08-17 — it was assembled data in main until the census
    # showed every read already ran under bank 4)
    import wad_packed as _wp
    la[bdst('RECIP_S'):bdst('RECIP_S') + 256] = _wp.srecip_table()
    if dw.ANIM_SECTORS:
        import anim_sectors as _an0
        for addr, blob in _an0.gen_6502_tables().items():
            if 0xBA00 <= addr < 0xBB00:           # TABL0 @ $BA00 (bank A, moved 2026-09-02)
                la[addr - 0x8000:addr - 0x8000 + len(blob)] = blob
            # (SSMASK no longer routed here: its blob is keyed at its
            #  bank-B home $B400 and seeded in the L2 section below)
    # LV1 K planes -> bank A $B900/$B980 + DBOUND $B880 (exact-backface
    # 2026-08-26). MUST precede define_bank: it copies the image.
    la[0x3900:0x3980] = bytes(rom_main[layout['off_bktlo']:layout['off_bktlo'] + 128])
    la[0x3980:0x3A00] = bytes(rom_main[layout['off_bkthi']:layout['off_bkthi'] + 128])
    la[0x3880:0x3900] = bytes(rom_main[layout['off_dbound']:layout['off_dbound'] + 128])
    # USE VECTORS -> bank A $96FC (EVICTED from bank C 2026-09-02): read under
    # SEG right before ENG_PMOVE_USE pages SEG for USETAB, so bank A is zero-
    # cost.  Fills the 260 B gap above the seg headers.  Seeded here, before
    # define_bank COPIES la.
    import colmap as _cm_uv
    _uvb = _cm_uv.use_vectors()
    _uvd = bdst('ROM_DRV_USEVEC_C')
    assert _uvd + len(_uvb) <= 0x1800, 'USEVEC runs into VCACHE @ $9800'
    la[_uvd:_uvd + len(_uvb)] = _uvb
    bm.define_bank(BANK_L0, la)                   # BANK_L0 == BANK_SEG (4)
    # post-define content gate (2026-08-28): the dead-write class above is
    # silent — verify the planes actually live in the defined bank.
    for _go, _gn in ((0x3900, 'off_bktlo'), (0x3980, 'off_bkthi'),
                     (0x3880, 'off_dbound')):
        assert bytes(bm._banks[BANK_L0][_go:_go + 128]) == \
               bytes(rom_main[layout[_gn]:layout[_gn] + 128]), \
               f'bank A plane at ${0x8000+_go:04X} ({_gn}) did not survive define_bank'

    # --- bank C = clipper ($8000) + rasteriser ($A900) ---
    c = bytearray(16384)
    CB = abi.CBITS_M                            # = CBANK_ORG (abi.inc)
    clip = open('engine_cbits_m.bin', 'rb').read()
    c[:len(clip)] = clip
    # VRCACHE fat-path planes are BSS at $9700-$A2D3, directly below the raster code @ $A300 (the
    # clipper must stay below $9700 — guarded here). Must be seeded BEFORE
    # define_bank: it COPIES the image into a fresh buffer.
    assert len(clip) <= 0x1800, f'clipper {len(clip)} bytes reaches VEXPL_CONT at $9800'
    # Driver tables, evicted from bank A 2026-08-17 so its bottom 19 pages come
    # free: sincos $9900 (512 B), use vectors $9B00. Both are read ONLY by
    # walk_drv, which pages this bank for them (one ROMSEL write each, and the
    # sincos read happens once per frame).
    import colmap as _cm0
    _sct = sincos_table
    from symmap import sym as _csym
    # sincos: bank C $9900 (walk_drv pages C once/frame for it)
    _scd = _csym('ROM_DRV_SINCOS_C') - CB
    _scb = _sct()
    assert _scd + len(_scb) <= 0x2400, 'sincos runs into the records arenas'
    c[_scd:_scd + len(_scb)] = _scb
    # USE VECTORS seeded into bank A (la) BEFORE define_bank(BANK_L0) -- see
    # the bank-A section above.
    # Billboard art templates -> BANK C (2026-08-29), abutting USEVEC.  They
    # lived with the level data in bank A, which made obj_stamp page BANK_SEG
    # for four art bytes and BANK_C to draw, EVERY template line: 22.4 ROMSEL
    # stores/frame (tools/pagecensus.py).  In bank C the loop needs no paging
    # at all -- the object prologue's PAGE BANK_C covers it, and nothing in
    # src/clip pages.  This home is Python-seeded, so ld65 cannot police it:
    # the asserts below and the matching pair in layout.inc are the guard.
    _art_off = layout['off_obj_art']
    _art_n = layout['art_len']              # all three windows (652 B)
    _art_d = _csym('OBJ_ART') - CB
    assert _art_d == 0x1B00, f'OBJ_ART banked home moved to ${_art_d + CB:04X}'
    assert _art_d >= 0x1B00, 'object art overlaps the driver sincos ($9900-$9AFF)'
    assert _art_d + _art_n <= 0x1E00, 'object art runs into VDESC @ $9E00 (bank-C compaction)'
    # window alignment is LOAD-BEARING: the walker's four abs,X reads only
    # stay carry-free because every window head is 256-aligned
    assert _art_d % 256 == 0, 'OBJ_ART windows must be page-aligned'
    c[_art_d:_art_d + _art_n] = rom_main[_art_off:_art_off + _art_n]

    # (VRCACHE_CODE moved to main $2B00 2026-07-10 — loads via the generic region loop)
    # vertex-span descriptor tables (banked homes: bank C $B200/$B400 —
    # the verticals section runs under C, zero paging on the code path)
    for i, d in enumerate(dw.vspan_desc):
        c[(_csym('VDESC')-CB) + i] = d   # VDESC (moved by C compaction)
    assert len(dw.vspan_expl) <= 0x80, \
        f'{len(dw.vspan_expl)} explicit vspan entries overrun the 128-slot split'
    for i, (lo, hi, cont) in enumerate(dw.vspan_expl):
        _lo, _hi, _ct = dw.vexpl_bytes(i, lo, hi, cont)   # H2 half-baking
        c[(_csym('VEXPL_LO')-CB) + i] = _lo   # VEXPL (C compaction)
        c[(_csym('VEXPL_HI')-CB) + i] = _hi
        c[0x1800 + i] = _ct                # VEXPL_CONT @ $9800 (moved off
        #  the page head 2026-08-22 to give the clipper its ceiling back;
        #  128 slots end $96FF, flush against BOT_RECORDS $9700)
    # MASTER: the kept bank-C run ($8000-$A0FF offsets) is MAIN RAM at
    # CBITS_M..+$20FF; banks 5 and 6 hold the wall textures (bank 6's
    # tail the part records), ANDY the per-seg wall tables and main
    # RAM (mtex_ix) the column index blob -- all from master_walls, as the
    # disc ships them. (Step 1-3 poisoned bank 6 to catch a missed
    # bank-C rebase; texels there now garble the output the same way.)
    assert len(clip) <= 0x1800, f'clipper {len(clip)} bytes reaches VEXPL_CONT'
    for i in range(0x2100):
        bm[CB + i] = c[i]
    import master_walls as _mw
    _ti = _mw.rig_images()
    # the fill's cold code lives in bank 6 above the flats (cfg B6CM)
    import asmbuild as _ab6
    from engine_load import _regions as _rg6
    _ab6.build('engine')
    b6 = bytearray(_ti['b6'])
    (_c6,) = [a for a, fn in _rg6() if fn == 'engine_b6c_m.bin']
    _code6 = open('engine_b6c_m.bin', 'rb').read()
    assert _ti['b6_tex_end'] <= _c6, 'bank 6 texels reach the fill code'
    assert not any(b6[_c6 - 0x8000:_c6 - 0x8000 + len(_code6)]), \
        'bank 6 code overlays seeded data'
    b6[_c6 - 0x8000:_c6 - 0x8000 + len(_code6)] = _code6
    bm.define_bank(BANK_C, bytes(b6))
    bm.define_bank(5, _ti['b5'])
    bm.define_andy(_ti['andy'])
    for i, v in enumerate(_ti['ix']):
        bm[_ti['ix_base'] + i] = v
    # (FHCH moved into bank L0 2026-07-10 — level data out of main, $2400-$33xx freed for code)

    # --- sqr tables: lo pages -> $1C00, HI pages -> $0200 (banked
    # SQRH_BASE, 2026-07-27 — $1E00 is the LCODE island now) ---
    from symmap import sym as _sq       # the Master's: HAZEL (MSQR, master/mfill.s)
    sqr_at = _sq('sqr_quad_m')
    for i in range(0x600):
        bm[sqr_at + i] = fmem[abi.SQR_MIR_LO + i]   # $0200-$07FF quad+mirrors
    # (LV1 K planes + DBOUND moved ABOVE define_bank(BANK_L0) 2026-08-28:
    #  define_bank COPIES, so writes here were DEAD. DBOUND was shipping
    #  as ZEROS in every banked build/disc — the banded backface's bound
    #  read 0 and mis-culled near-band diagonals: the 009C.9A tick bleed,
    #  a front SOLID culled with the room behind drawn through its
    #  columns. See project_rhs_bleed_2.)
    # OBJ_ANYB main-RAM bitmap copy (2026-08-25 grind): hardware fills it
    # via anim_init/obj_anyb_fill; model runs may skip init, so seed it
    _bits = layout['off_obj'] + 7 * layout['n_obj']   # OBJ_BITS = ROM_OBJ_C+7*N_OBJ
    from symmap import sym as _bsym
    _anyb = _bsym('OBJ_ANYB')
    for i in range(layout['obj_bits_len']):
        bm[_anyb + i] = rom_main[_bits + i]

    # --- bank B (BANK_WALK=7): node/ss SoA @ $8000 (SS_PHI rebased onto
    # the bank-A header base), L8/AE/VATOX behind the SoA, bbox @ ROM_BBOX_C,
    # COLIDX, ANIM CFG @ $B300 + SSMASK staging @ $B400 (the corner memo
    # planes and the extent cache BSS that used to sit at $A600-$AFFF went
    # with those two caches, 2026-09-04) ---
    lb = bytearray(16384)
    lb[:off_verts] = bytes(rom_main[:off_verts])         # node/ss SoA pages
    # SS_PG rebase (RESURRECTED 2026-08-29, one line per loader): the
    # plane ships the FINAL header hi byte (page + >ROM_SEG_HDR_C), so
    # the prologue's CLC/ADC died. rom_main keeps the RAW page (the
    # python mirror reads it there). Empty subsectors rebase harmlessly
    # (CNT $FF is the empty test).
    for _i in range(layout['n_ss']):
        lb[layout['off_ss'] + _i] = (rom_main[layout['off_ss'] + _i] + 0x80) & 0xFF
    # SS_FH/SS_CH: planes 3+4 of the five adjacent SS planes ($8900 PG,
    # $8A00 SI, $8B00 FH, $8C00 CH, $8D00 VZ — VZ arrives via the colmap
    # blob router below). SS_CNT (the 2026-08-29 PG/CNT split) rides the
    # same loop to its own bank-B page at $B500 (the free pair below the DIR
    # planes; $A900 REJECTED: reads catching the window mid bank-C raster
    # excursion saw raster state there — vrcache warm mismatches).
    _nss = layout['n_ss']
    for _nm, _off in (('ROM_SS_FH_C', layout['off_ss_fh']),
                      ('ROM_SS_CH_C', layout['off_ss_ch']),
                      ('ROM_SS_CNT_C', layout['off_ss_cnt'])):
        _d = bdst(_nm)
        lb[_d:_d + _nss] = bytes(rom_main[_off:_off + _nss])
    # the angle tables and bbox, from their Python sources (angle_bbox, the
    # packed bbox table) -- dst offsets BY SYMBOL
    import angle_bbox as _A
    for i in range(256):
        lb[bdst('L8_TAB') + i] = _A._L8[i] & 0xFF
        lb[bdst('AE_LO') + i] = _A._ATANEXP[i] & 0xFF
        lb[bdst('AE_HI') + i] = (_A._ATANEXP[i] >> 8) & 0xFF
    for k in range(1025):
        c_ = (_A._vatox_lo[k + 512] + _A._vatox_hi[k + 512]) // 2
        lb[bdst('VATOX') + k] = max(0, min(255, c_))
    lb[bdst('L2_BBOX'):bdst('L2_BBOX') + len(flatr.bbox_table)] = bytes(flatr.bbox_table)
    if dw.ANIM_SECTORS:
        import anim_sectors as _an
        for addr, blob in _an.gen_6502_tables().items():
            if 0xB300 <= addr < 0xB400:          # CFG @ $B300 (bank B)
                lb[addr - 0x8000:addr - 0x8000 + len(blob)] = blob
            elif addr == 0xB400:                 # SSMASK: bank-B HOME (the
                # hub reads it in place under WALK since 2026-08-19; the
                # $1100 main copy and the copy-down are gone)
                assert len(blob) <= 256, f'SSMASK {len(blob)} B overflows its $B400 page'
                lb[0x3400:0x3400 + len(blob)] = blob
    # DIR planes: BANK A ONLY since 2026-08-30.  The bank-B duplicate served
    # cross_products_banded and node_band when entered from WALK context;
    # that whole chain pages SEG for itself now -- which it had to anyway,
    # because ROM_DBOUND_C is bank A and reading it under WALK had silently
    # DISABLED the exact-descent band refine.  $B700-$B87F is free in bank B.
    # (Measured: the backface side reads DIR 25.8x/frame, this side 0.)
    # (PMB stitching DELETED 2026-08-19: bank B carries no code any more —
    #  the pm_frame slices ride the CODE region tail and load with the
    #  rest of main below.)
    # collision map (colmap.py): banked homes SPLIT across banks since
    # the slide arc — USETAB lives in BANK A ($BE00, read under SEG by
    # pmove_use); everything else is bank B. The first cut routed ALL
    # blobs to lb and banked SPACE read TABL0-neighborhood garbage (the
    # 2026-08-14 'again' investigation's real find).
    for _ca, _cb in _cm.blobs().items():
        if not isinstance(_ca, int) or _ca == abi.USETAB_BASE:   # USETAB seeded in
            continue                                    # the LA section
        if _ca < 0x8000:                                # COLPORT etc: MAIN
            for _k, _v in enumerate(_cb):               # (model RAM; discs
                bm[_ca + _k] = _v                       # ship via COLDAT)
        else:
            lb[_ca - 0x8000:_ca - 0x8000 + len(_cb)] = _cb
    # corner-phi memo validity: KDXH plane ships $80-filled — the
    bm.define_bank(BANK_L2, lb)                   # BANK_L2 == BANK_WALK (7)


    # --- banked bsp_render code (_bk variants) into low RAM ---
    # Region list comes FROM THE LD65 CONFIG (engine_load._regions) so a new
    # MEMORY area can never be silently missing here (a hardcoded list once
    # dropped the RCCODE rotation-cache region -> bca_frame jumped into
    # garbage and the disc hung at boot). Skip the clipper bank (loaded into
    # BANK_C above, not main RAM).
    from engine_load import _regions
    # BUILD FIRST (2026-09-02): these files are read RAW, and the four
    # build variants share their names -- without this, the rig loaded
    # whatever variant a previous build left (the C02-driver-on-NMOS-rig
    # wedge).  asmbuild's on-disk marker makes this a no-op when the
    # right variant is already there.
    import asmbuild as _ab
    _ab.build('engine')
    for addr, fn in _regions():
        if fn.startswith('span_clip') or fn == 'bsp_render_hud_bk.bin' \
                or fn in ('engine_cbits_m.bin', 'engine_b6c_m.bin', 'engine_gun_m.bin'):
            continue    # clipper + HUD -> BANK_C (rc/anim/vrcache/sel are main now)
                        # MASTER: CBITS was placed with the C data above
        if os.path.exists(fn):
            d = open(fn, 'rb').read()
            for i, b in enumerate(d):
                bm[addr + i] = b

    # (ROM-pointer block retired 2026-07-10: bases are layout.inc constants)
    bm[0xFF00] = 0x00
    bm.select(BANK_L0)
    return bm


class MasterBspRender(BspRender6502):
    """The Master link on the banked memory model: bank-C content in main RAM
    at CBITS_M, the fill in HAZEL, the screens in shadow RAM, and plot_h /
    plot_v / RASTER_ENTRY RTS emit stubs whose PCs the rig traps -- so
    last_lines is the engine's complete emitted line list."""
    VAR = 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.bm = build_banked(self)
        self.sc.mpu.memory = self.bm     # swap in banked memory
        # No main-RAM framebuffer: $5800-$7FFF is the clipper and its data
        # (the screens are in shadow RAM).
        self.sc.SCREEN_START = None
        self.sc.SCREEN_SIZE = 0
        sc = self.sc
        from symmap import sym as _sym
        _span_init = _sym('span_init')
        def master_init():
            sc._run(_span_init)
            sc.total_cycles = 0
        sc.init = master_init
        # the span rig's entries and plot traps, by symbol
        for _n, _s in (('ENTRY_INIT', 'span_init'),
                       ('ENTRY_MARK_SOLID', 'span_mark_solid'),
                       ('ENTRY_HAS_GAP', 'span_has_gap'),
                       ('ENTRY_INTERP_ST', 'interp_store'),
                       ('ENTRY_DRAW_CLIP', 'draw_clipped_line'),
                       ('ENTRY_DRAW_CLIP_S16', 'draw_clipped_line_s16'),
                       ('ENTRY_FUSED_BEGIN', 'fused_begin'),
                       ('ENTRY_FUSED_ABOVE', 'fused_above_raw'),
                       ('ENTRY_FUSED_BELOW', 'fused_below_raw'),
                       ('ENTRY_FUSED_MERGE', 'fused_merge_range')):
            setattr(sc, _n, _sym(_s))
        sc.PLOT_PCS = frozenset((_sym('plot_h'), _sym('plot_v'),
                                 _sym('RASTER_ENTRY')))
        # The filler writes the shadow buffer through ACCCON X, objects are
        # off (they apply span lines outside a seg's fill window), and the
        # sky map is seeded where the disc builder seeds it.
        import fill_ref
        self.bm.define_shadow()
        _anyb = _sym('OBJ_ANYB')
        for i in range(32):
            self.bm[_anyb + i] = 0
        sk = _sym('mf_skymap')
        for i, b in enumerate(fill_ref.sky_bitmap()):
            self.bm[sk + i] = b
        self.bm[abi.DV_BACKHI] = abi.MSCREEN0 >> 8
        self.bm[0xFE34] = 0x09                # ACCCON at render time: D | Y

    def render_frame(self, px, py, ab, floor_z=0):
        # the screens as the disc leaves them: blank, with the control panel
        # (master_panel) at MPANEL, under both buffers' views, and gun_b0 at
        # MGUN0
        import master_panel as MP
        self.bm.clear_shadow()
        self.bm.shadow_store(abi.MPANEL, MP.panel_bytes())   # the one panel
        self.bm.shadow_store(abi.MGUN0, open('engine_gun_m.bin', 'rb').read())
        self.bm[_rsym('bca_ab')] = ab & 0xFF
        return super().render_frame(px, py, ab, floor_z)

    def framebuffer(self):
        """The 10K back buffer the filler drew (shadow RAM)."""
        lo = abi.MSCREEN0
        return self.bm.shadow_bytes(lo, lo + 10240)
