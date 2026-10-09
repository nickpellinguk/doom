; ============================================================================
; master/mfill.s -- the span-diff FILLER and WALL TEXTURER (MASTER build
; only). docs/master_textured_spec.md; the executable specs are fill_ref.py
; (step 3: what each seg fills) and tex_ref.py (step 4: the wall texels),
; which this file mirrors bit for bit.
;
; The clip spans ARE the unfilled screen. Every span update a seg makes
; (span_mark_solid for a solid wall; the fused top/bottom walk + merge for
; a portal) removes area from them, and that area is exactly what the seg
; must fill. So:
;   mf_snap  -- at the cascade head (seg_emit hgp_fwd): copy the spans that
;               overlap the seg's columns [lo, hi)
;   mf_fill  -- after the seg's updates: for every BYTE COLUMN whose first
;               pixel x = 4k lies in [lo, hi), compare the snapshot span at
;               x with the live one and fill
;               the removed bands, split at the seg's own front ceiling (T)
;               and floor (B) lines: y < T ceiling, y > B floor, else wall
;   hz_run   -- one solid-shade run of one byte column, X set
;               only for the duration of the run (HAZEL code reaches shadow
;               RAM through ACCCON X; nothing here reads main RAM above
;               $3000 while X is set)
;   tx_seg / tx_getd / wall_run / trun -- step 4: the wall rows textured
;               (see STEP 4 below)
;
; EVERY WRITE IS A WHOLE BYTE -- a 4x2 fat pixel -- and nothing is ever read
; back: a byte column belongs to one seg, its bands and splits are sampled
; at its first pixel, shades fill both its strips, and a wall byte is
; (left texel << 2) | right texel, the two strips independent textures.
;
; Code lives in HAZEL ($C800+, ACCCON Y held on by the driver for the whole
; render), the wall tables in ANDY, the bank-6 tail and main RAM (segments
; MANDY / MB6T / MTEXIX). Span edges are evaluated exactly as the clipper's
; Python twin evaluates them (endpoint_spans._span_top/_span_bot: floor
; interpolation on the TOP anchor TXLO/TDEN), the seg lines exactly as
; fill_ref's floor interpolation of the s16 projected endpoints.
;
; Arithmetic is the plain shift-add / shift-subtract kind: correctness first,
; the cycle grind is step 7.
; ============================================================================
.setcpu "65C02"                         ; the Master's 65C12

.include "../zp.inc"
.include "../layout.inc"                ; ROM_SEG_HDR_C, LAY_HDR_STRIDE

ACC_DY  = $09                           ; render-time ACCCON: display shadow, HAZEL in
ACC_DXY = $0D                           ;  ... plus X: CPU on the shadow buffers

; step-3 shades, in the RIGHT-strip ($33) position: wall_byte(shade) in
; master_assets.py, pixel 2 = colour a, pixel 3 = colour b
WB_SKY   = $28                          ; Mode 2 left-pixel bytes: cyan
SKY_EV   = $3D                          ; the sky's bytes (master_assets
SKY_OD   = $3E                          ;  SKY_BYTE, FLIP of it): cyan + white
WB_CEIL  = $20                          ;  blue
WB_FLOOR = $02                          ;  red

PTR = RASTER_ZP_X1                      ; zp pair x1/y1 (the rasteriser's JMP
                                        ; vector -- there is no rasteriser on
                                        ; the Master; DCL is done by fill time)
TP  = RASTER_ZP_DX                      ; zp pair: the rasteriser's dx/dy, only
                                        ; raster.s touches it (not linked here)
; (Step 5b's sector light masks, maskEven / maskOdd, were retired in step
; 6a: the Mode 2 demo is for texturing. The level still keys the pending
; plane spans.)
; The wall write loop's zero page (tr_screen, a leaf): bytes no code in the
; MASTER link references (zp.inc notes the reuse), and TP, which is dead
; while the loop runs.
zw_lvl  = zp_plot_i                     ; left strip's v (5.11)
zw_lvh  = zp_dcl_out
zw_dh   = zp_clr_save_x                 ; right strip's v hi - left's (5.3,
                                        ;  step 7d; 0 when the run shares)
zw_tvl  = zw_dh                         ; (the span loops' sv)
zw_ddh  = zp_vs_cch                     ; zw_dh's step per character row (5.3)
zw_rowm = zp_old_cur                    ; row mask (th - 1) * 8
zw_tl   = TP                            ; left texel column pointer (2)
zw_tr   = bca_boxp                      ; right texel column pointer (2)
zw_ev   = zp_save2                      ; the combined texel byte (scratch)
zw_np   = zp_bca_p1_h                   ; blocks of four pairs left
; The span loops' zero page (sp_go2 / sl_go, leaves; tr_screen reloads
; all of the above per run, so they share it): U, V, dU, dV (4.4)
su      = zw_lvl
sv      = zw_tvl
sdu     = zw_tr
sdv     = zw_rowm
sf_nb   = zw_np                         ; sp_go2: blocks of four bytes left
NONE = $FF                              ; no texture (master_walls.NONE)
HZ_LINE = VIEW_LINES / 2                ; the horizon: line 68 of the 136
HZ_PAIR = HZ_LINE / 2                   ; its pair (34); the rows per side
VIEW_PAIRS = VIEW_LINES / 2             ; line pairs in the view (68)
HDR_PER_PAGE = 256 / LAY_HDR_STRIDE     ; page-slotted seg headers

.export mf_snap, mf_xt
.ifndef TUBE
.export mf_fill, mf_skymap, mf_frame
.endif
.ifndef SERVER
.export split_init, gun_draw
.else
; THE FILL SERVER (docs/tube_master.md H3, src/tube/fserve.s): this file
; assembled for the second processor. The screen's code goes (the split,
; the gun, every write loop, the plane spans' sweep and flush); what
; drew now records -- hz_run and the planes mark fserve's cell grid,
; tr_screen emits the run's WALL record.
.import fs_mark, fs_wall, fs_planes, fs_poll
.export pl_rowc, uvat, hz_run, tr_screen, mf_ep, pl_p, pl_d, pl_kb, pl_u, pl_v
.export pc_uc, pc_du, pc_vc, pc_dv, pc_lv, far_tone, pl_kind, pl_df, pl_dc
.export pl_ff, pl_fc, tw_ll, tw_rl, tw_sh, l_v, l_step, t_v, t_tid, t_step
.export zw_dl, zw_ddl, sn_xs, sn_xe, sn_xlo, sn_den, sn_tl, sn_tr, sn_bl, sn_br
.export sn_bxlo, sn_bden, sn_n, tx_slot, mf_x, r_ys, r_ye
.exportzp zw_ddh
.endif
.ifdef TUBE
.export rq_frame, rq_fill, rq_end
.export hi16, flip                      ; (tube/hdraw.s: lsr4 and flip)
.endif

; ----------------------------------------------------------------------------
; Step 4 wall tables: filled by the image builders from master_walls.py
; (Walls.images), found there by these labels.
.segment "MANDY"                        ; ANDY ($8000-$8FFF, ROMSEL bit 7)
man_slot_d:   .res $300                 ; per header slot (644): dressing id,
                                        ;  or man_ndress + merged-list id
man_slot_ll:  .res $300                 ; per slot: L16 = round(16*length) lo
man_slot_lh:  .res $300                 ;  ... hi
man_dr_up:    .res $100                 ; per dressing: upper part id ($FF none)
man_dr_lo:    .res $100                 ;  lower part id
man_dr_mid:   .res $100                 ;  solid-seg part id
man_dr_ub:    .res $100                 ;  u base (world units)
man_pl_first: .res $20                  ; per merged seg: first piece
man_pl_n:     .res $20                  ;  piece count
man_pc_sl:    .res $40                  ; per piece: start (1/16 units) lo
man_pc_sh:    .res $40                  ;  ... hi
man_pc_dr:    .res $40                  ;  dressing id
man_ndress:   .res 1                    ; dressing count
man_ss_ff:    .res $C4                  ; per subsector: floor flat id
man_ss_fc:    .res $C4                  ;  ceiling flat id ($FF: sky)
.assert man_slot_ll - man_slot_d = $300 .and man_slot_lh - man_slot_ll = $300, error, "tx_seg steps the slot planes by $300"
.assert <man_slot_d = 0, error, "slot planes must be page-aligned"

.segment "MB6T"                         ; bank 6 tail, above the texels
mb6_pt_tid:   .res $A0                  ; per part (<= 160): texture id
mb6_pt_k0:    .res $A0                  ;  K = 2048*th*(fc-fh)//src_h, bytes
mb6_pt_k1:    .res $A0                  ;  0-2 (to 8 significant bits, 7v)
mb6_pt_k2:    .res $A0
mb6_pt_v0:    .res $A0                  ;  Vtop (5.11) lo / hi
mb6_pt_v1:    .res $A0
mb6_tp_sl:    .res $20                  ; per texture (32): 8 - shift, index =
                                        ;  hi((u & mask) << sl)
mb6_tp_ml:    .res $20                  ;  u mask = 16*src_w - 1, lo / hi
mb6_tp_mh:    .res $20
mb6_tp_rowm:  .res $20                  ;  row mask (th-1)*8, applied to v hi
mb6_tp_ph:    .res $20                  ;  texel pages hi byte
mb6_tp_bank:  .res $20                  ;  sideways bank (5 or 6)
mb6_tp_ro:    .res $20                  ;  row offset (128: stacked lower half)
mb6_tp_ixl:   .res $20                  ;  column index table address lo / hi
mb6_tp_ixh:   .res $20
mb6_fl_bank:  .res $20                  ; per flat (23): sideways bank
mb6_fl_page:  .res $20                  ;  its 256-byte page (16x16 bytes)
mb6_mcx:      .res 2                    ; map centre x * 1024 (mod 2^16)
mb6_mcy:      .res 2                    ; -map centre y * 1024 (mod 2^16)

.segment "MB6R"                         ; bank 6 $8000 (step 7v; ANDY not paged)
mb6_rc:       .res $E00                 ; RT_z[h] = (2^(8+z) + h/2) / h, h 1-255,
                                        ;  per exponent z: a lo page, a hi page
                                        ;  (0: does not fit, the divide instead)
mb6_pt_m:     .res $A0                  ; per part: K = m << z, m (8 bits)
mb6_pt_rp:    .res $A0                  ;  its z's lo-table page (hi = + 1)

.segment "MTEXIX"                       ; main RAM: read with ACCCON X clear
mtex_ix:      .res $420                 ; column index bytes, every texture

.segment "MPLANE"                       ; HAZEL $C280: the span snapshot, the
sn_xs:   .res 32                        ;  run buffer and the per-frame row cache
sn_xe:   .res 32
sn_xlo:  .res 32
sn_den:  .res 32
sn_tl:   .res 32
sn_tr:   .res 32
sn_bl:   .res 32
sn_br:   .res 32
pe_ct:   .res 64                        ; per byte column, this seg's textured
pe_cb:   .res 64                        ;  ceiling / floor whole-pair interval
pe_ft:   .res 64                        ;  (top, bottom pair; empty: $FF, 0)
pe_fb:   .res 64
sp_start: .res VIEW_PAIRS                       ; MakeSpans: per pair, its span's start
pc_ep:        .res VIEW_PAIRS                   ; per line pair p: the frame epoch and
pc_d:         .res VIEW_PAIRS                   ;  plane height D it was computed for,
pc_uc:        .res VIEW_PAIRS                   ;  U, dU, V, dV (4.4): U, V at byte
pc_du:        .res VIEW_PAIRS                   ;  column 32
pc_vc:        .res VIEW_PAIRS
pc_dv:        .res VIEW_PAIRS
pc_lv:        .res VIEW_PAIRS                   ;  and its level (1: the far tone, 7n)
sn_bxlo:      .res 32                   ; snapshot: the BOTTOM line's own anchor
sn_bden:      .res 32                   ;  (POOL_BXLO / BDEN; 2026-10-08 -- read
                                        ;  as the top's, a bottom-only fused
                                        ;  edge was misplaced)

.segment "MSQR"                         ; HAZEL $D800 (engine_master.cfg HZQ)
sqr_quad_m: .res $600                   ; SQR_MIR_LO on the Master (abi.inc)

.ifndef SERVER
.segment "MUSRING"                      ; HAZEL $D700 (HZR): the music ring,
mus_ring_m: .res $100                   ;  MUS_RING (src/master/mmusic.s)
.assert mus_ring_m = MUS_RING, error, "MUS_RING is HZR's page"
.endif

.segment "MFILLBSS"
zw_dl:   .res 1                         ; step 7t: zw_dh's fraction byte
zw_ddl:  .res 1                         ;  and zw_ddh's
mf_lo:   .res 1                         ; clamped [lo, hi) of the seg
mf_hi:   .res 1
mf_x:    .res 1                         ; current byte column's first pixel
mf_i:    .res 1                         ; snapshot cursor
sn_n:    .res 1                         ; snapshot count
; seg lines: y = y1 + floor(D * (x - sx1) / W)
l_sx1:   .res 2
l_w:     .res 2
lt_y1:   .res 2
lt_dm:   .res 2                         ; |D| top
lt_neg:  .res 1                         ; D < 0
lb_y1:   .res 2
lb_dm:   .res 2
lb_neg:  .res 1
sh_ceil: .res 1
; per column
c_ot:    .res 1
c_ob:    .res 1
c_nt:    .res 1
c_nb:    .res 1
c_new:   .res 1                         ; 0 = column closed by the seg
c_tc:    .res 1                         ; clamp(T, 48, 208)
c_bc:    .res 1                         ; clamp(B, 47, 207)
b_y0:    .res 1
b_y1:    .res 1
; span-edge evaluator
ev_a0:   .res 1
ev_a1:   .res 1
ev_xlo:  .res 1
ev_den:  .res 1
; line evaluator
ln_y1:   .res 2
ln_dm:   .res 2
ln_neg:  .res 1
ln_y:    .res 2
; steppers (see STEPPERS below)
st_f:    .res 84
ST_T  = 0                               ; st_f's steppers (st_init)
ST_B  = 14
ST_OT = 28
ST_OB = 42
ST_NT = 56
ST_NB = 70
mf_oi:   .res 1                         ; snapshot index the OT/OB steppers hold
mf_ns:   .res 1                         ; live slot the NT/NB steppers hold
mf_lc:   .res 1                         ; live-list cursor
si_x:    .res 1
si_y0:   .res 2
si_d:    .res 2
si_neg:  .res 1
si_w:    .res 2
si_k:    .res 2
si_xlo:  .res 1
si_a0:   .res 1
si_a1:   .res 1
; maths
m_p:     .res 4                         ; product / dividend
m_a:     .res 2                         ; multiplicand
m_b:     .res 2                         ; multiplier / divisor
m_r:     .res 2                         ; remainder
; run writer
r_ys:    .res 1
r_ye:    .res 1
r_part:  .res 1
r_ev:    .res 1
hz_x:    .res 1                         ; hz_sky: even line ^ odd line byte
hz_n:    .res 1                         ; hz_run: lines, the first line's
hz_a:    .res 1                         ;  place in its row, the last line
hz_l:    .res 1                         ;  (from the row), rows after the
hz_r:    .res 1                         ;  first (and * 2), scratch
hz_r2:   .res 1
hz_e:    .res 1
; ---- step 4: wall textures (tex_ref.py is the executable spec) ----
mf_xt:   .res 2                         ; near-plane crossing t (0..256), set
                                        ; by reproject_at_crossing (bsp/lo.s)
tx_solid: .res 1                        ; V of zp_seg_flags: solid seg
tx_slot: .res 2                         ; header slot = packed seg index
tx_l16:  .res 2                         ; round(16 * seg length)
pc_n:    .res 1                         ; pieces (1, or a merged seg's list)
pc_sl:   .res 4                         ; piece start (1/16 units), lo / hi
pc_sh:   .res 4
pc_up:   .res 4                         ; piece dressing: up / lo / mid part
pc_lo:   .res 4                         ; ids and u base
pc_mid:  .res 4
pc_ub:   .res 4
cur_up:  .res 1                         ; the piece holding this byte's left d
cur_lo:  .res 1
cur_mid: .res 1
cur_bl:  .res 1                         ; ub * 16 - start (mod 2^16): u = it
cur_bh:  .res 1                         ;  + d
tx_d1:   .res 2                         ; d at the projected ends
tx_d2:   .res 2
tx_wa:   .res 3                         ; 1/depth weights (16 bits after norm)
tx_wb:   .res 3
tx_xl:   .res 1                         ; first / last strip centre
tx_xh:   .res 1
tx_dl:   .res 2                         ; exact d there
tx_dh:   .res 2
tx_ra:   .res 5                         ; raw 1/depth there (normalised to
tx_rb:   .res 5                         ;  A, B <= 255)
tx_n:    .res 4                         ; left strip's numerator / denominator,
tx_den:  .res 2                         ;  stepped by constants
tx_dn:   .res 4
tx_dden: .res 2
tx_d:    .res 2                         ; the left strip's d (x + 1)
c_dok:   .res 1                         ; d computed for this byte
c_t:     .res 2                         ; unclamped T, B at x (s16, biased)
c_b:     .res 2
b_kind:  .res 1                         ; 0 closed column, 1 top band, 2 bottom
at_xc:   .res 1                         ; at(): strip centre
at_a:    .res 4
at_b:    .res 4
at_n:    .res 5                         ; raw weight (33 bits)
at_den:  .res 2
t_tmp:   .res 4
t_i:     .res 1
t_k:     .res 1
t_sx:    .res 1
t_tid:   .res 1
t_step:  .res 2
t_f:     .res 1                         ; step 7s: the step's next 8 bits,
t_fp:    .res 2                         ;  (ys - T) * f >> 8, B - T
t_h:     .res 2
t_v:     .res 2
t_rowm:  .res 1
t_n:     .res 1
t_ev:    .res 1
t_od:    .res 1
c_tr:    .res 2                         ; the right strip's own T, B (x + 2;
c_br:    .res 2                         ;  c_t..c_b and c_tr..c_br are pairs)
q_t:     .res 2                         ; the strip's T (tv_v0) and d (tcol)
q_d:     .res 2
tx_dr:   .res 2                         ; the right strip's d (x + 3)
l_v:     .res 2                         ; the left strip's v and pair step
l_step:  .res 2                         ;  (t_v / t_step: the right strip's)
c_trok:  .res 1
tw_ll:   .res 1                         ; trun -> tr_screen: the two strips'
tw_lh:   .res 1                         ;  texel column addresses
tw_rl:   .res 1
tw_rh:   .res 1
tw_np:   .res 1                         ; whole pairs in the run
sp_tick:  .res 1                        ; split_irq: fields, for the colour cycle
sp_phase: .res 1                        ; the split: $80 after the panel event
tw_slim: .res 2                         ; step 5n: the seg's shared-v limit,
tw_sh:   .res 1                         ;  and this run shares ($80) or not
tw_ly:   .res 1                         ; tr_fetch: the caller's Y                         ; c_tr / c_br made for this byte
t_part:  .res 1                         ; trun: the part, its left strip's
t_sl:    .res 2                         ;  (undoubled) step
ss_x:    .res 3                         ; per band kind (b_kind): the byte,
ss_pt:   .res 3                         ;  part and left step of its last run
ss_sl:   .res 3                         ;  ($FF: none) -- the right strip's
ss_sh:   .res 3                         ;  step extrapolates from them
ss_hl:   .res 3                         ;  and that left step's B - T (step 7e:
ss_hh:   .res 3                         ;  an equal one reuses the step)
pp_used: .res 4                         ; per partial-line slot: one recorded
                                        ;  since its kind's range opened (7k)
t_hl:    .res 1                         ; this run's left B - T
t_hh:    .res 1
tx_x0:   .res 1                         ; the seg's first byte column
tx_ax:   .res 1                         ; a look-ahead exact d: its byte
tx_ad:   .res 2                         ;  and value
tx_kk:   .res 1                         ; tx_dat's step (-1, 0, 1)
tx_lx:   .res 1                         ; the last byte column with an exact d
tx_dp:   .res 2                         ;  (its x, and that d)
; ---- step 5: floors and ceilings (plane_ref.py is the executable spec) ----
mf_ep:   .res 1                         ; frame epoch (pc_ep; 0 never valid)
pl_up:   .res 2                         ; the frame's view terms: Up, Vp (4.12)
pl_vp:   .res 2
pl_sm:   .res 1                         ;  |sin|, unity, negative
pl_s1:   .res 1
pl_sn:   .res 1
pl_cm:   .res 1                         ;  |cos|, unity, negative
pl_c1:   .res 1
pl_cn:   .res 1
pl_df:   .res 1                         ; this seg: eye height over its floor,
pl_dc:   .res 1                         ;  its ceiling over the eye (0: none)
pl_ff:   .res 1                         ;  floor / ceiling flat ($FF: none)
pl_fc:   .res 1
pl_d:    .res 1                         ; this run: D, flat, shade, kind,
pl_fl:   .res 1                         ;  pair range, byte column
pl_sh:   .res 1
pl_kind: .res 1                         ;  0 ceiling, 1 floor
pl_p:    .res 1
pl_p0:   .res 1
pl_p1:   .res 1
pl_kb:   .res 1
pl_u:    .res 1
pl_v:    .res 1
pl_uc:   .res 2                         ; pl_row: U, V at column 32 (4.12)
pl_vc:   .res 2
pl_k0:   .res 1                         ; the seg's byte columns
pl_k1:   .res 1
pl_y:    .res 1
pl_j:    .res 1
pl_lv:   .res 1                         ; this seg's light level (0-4)                         ; pl_row: the row index j
pl_ye0:  .res 1
mk_t1:   .res 1                         ; MakeSpans: previous / this column's
mk_b1:   .res 1                         ;  pair interval, the column
mk_t2:   .res 1
mk_b2:   .res 1
mk_k:    .res 1
mk_ke:   .res 1                         ; step 7r: the sentinel column
sp_n:    .res 1                         ; the span loops' byte count
sf_q:    .res 1                         ; sp_go2: the entry position * 8
mq_b:    .res 1                         ; QMUL / uvat: multiplier, product lo,
mq_l:    .res 1                         ;  scratch; mul8x32's byte index
mq_t:    .res 1
mq_i:    .res 1
d8_by:   .res 1                         ; d8_byte: the dividend byte
pl_e:    .res 4                         ; pl_row: E, and an 8x32 product
pl_q:    .res 5
pl_h:    .res 2
pl_a:    .res 2

; ----------------------------------------------------------------------------
.segment "MFILL"

; x16 tables, page-aligned at the segment's start ($C800): x * 16 =
; lo16[x] + 256 * hi16[x]; hi16 is also x >> 4, the flat texel index's
; column nibble (U >> 12 from U's high byte)
hi16:
.repeat 256, I
   .byte I >> 4
.endrepeat
lo16:
.repeat 256, I
   .byte (I << 4) & $FF
.endrepeat

; flip: a Mode 2 byte with its two pixels swapped -- the floor and ceiling
; cross-hatch (step 6d: a flat texel's pair on a line pair's even line,
; flip[] of it on the odd). Walls use none: both lines the same byte.
flip:
.repeat 256, I
   .byte ((I << 1) & $AA) | ((I >> 1) & $55)
.endrepeat
.assert (hi16 & $FF) = 0 && (lo16 & $FF) = 0 && (flip & $FF) = 0, error, "x16 / flip tables must be page-aligned"

; Sky map: one bit per subsector, set when its ceiling is F_SKY1.
; SEEDED BY THE IMAGE BUILDER (banked_bsp / tools/build_master_ssd.py).
mf_skymap: .res 32

bitmask: .byte 1, 2, 4, 8, 16, 32, 64, 128

; ============================================================================
; BANK 6 CODE (MB6C, $A500-): the cold set-up -- everything that runs with
; bank 6 (BANK_C, the emit cascade's bank) paged. It must never page
; another sideways bank and keep running: the routines that do (trun's
; screen side, pl_cell's flat read, the span loops, mf_frame) stay in
; HAZEL and put bank 6 back before they return. (ANDY is fine: it only
; overlays $8000-$8FFF.)
; ============================================================================
.segment "MB6C"

; ---- mf_range: [lo, hi) = clamp(sx1), clamp(sx2) to 0..255; C=1 if empty
mf_range:
   LDA zp_seg_sx1_h
   BMI @lo0
   BNE @lo255
   LDA zp_seg_sx1_l
   BRA @los
@lo0:
   LDA #0
   BRA @los
@lo255:
   LDA #255
@los:
   STA mf_lo
   LDA zp_seg_sx2_h
   BMI @hi0
   BNE @hi255
   LDA zp_seg_sx2_l
   BRA @his
@hi0:
   LDA #0
   BRA @his
@hi255:
   LDA #255
@his:
   STA mf_hi
   LDA mf_lo
   CMP mf_hi                            ; C=1: lo >= hi, nothing to do
   RTS

; ============================================================================
; mf_snap: copy the live spans that overlap [lo, hi) (the list is x-sorted)
; ============================================================================
mf_snap:
   STZ sn_n
   JSR mf_range
   BCS @rts
   LDX zp_head
   LDY #0
@lp:
   CPX #0
   BEQ @done
   LDA POOL_XSTART,X
   CMP mf_hi
   BCS @done                            ; starts at/after hi: no more overlap
   LDA mf_lo
   CMP POOL_XEND,X
   BCS @next                            ; xend <= lo: before the range
   LDA POOL_XSTART,X
   STA sn_xs,Y
   LDA POOL_XEND,X
   STA sn_xe,Y
   LDA POOL_TXLO,X
   STA sn_xlo,Y
   LDA POOL_TDEN,X
   STA sn_den,Y
   LDA POOL_TL,X
   STA sn_tl,Y
   LDA POOL_TR,X
   STA sn_tr,Y
   LDA POOL_BL,X
   STA sn_bl,Y
   LDA POOL_BR,X
   STA sn_br,Y
   LDA POOL_BXLO,X
   STA sn_bxlo,Y
   LDA POOL_BDEN,X
   STA sn_bden,Y
   INY
@next:
   LDA POOL_NEXT,X
   TAX
   BRA @lp
@done:
   STY sn_n
@rts:
   RTS

.ifndef TUBE                            ; (the fill: on the second processor)
; ============================================================================
; mf_fill: fill what the seg's span updates removed
; ============================================================================
mf_fill:
   JSR mf_range
   BCS @rts0
   LDA sn_n
   BNE @go
@rts0:
   RTS
@go:
   ; --- shades: solid or portal wall; sky or plain ceiling ---
   STZ tx_solid
   BIT zp_seg_flags
   BVC :+                               ; V set: solid seg
   DEC tx_solid
:
   LDA zp_node_ch_l
   AND #7
   TAY
   LDA zp_node_ch_l
   LSR A
   LSR A
   LSR A
   TAX
   LDA mf_skymap,X
   AND bitmask,Y
   BEQ :+
   LDA #WB_SKY
   BRA :++
:  LDA #WB_CEIL
:  STA sh_ceil
   ; --- seg line parameters (VX1 is the left endpoint) ---
   LDA zp_seg_sx1_l
   STA l_sx1
   LDA zp_seg_sx1_h
   STA l_sx1+1
   SEC
   LDA zp_seg_sx2_l
   SBC zp_seg_sx1_l
   STA l_w
   LDA zp_seg_sx2_h
   SBC zp_seg_sx1_h
   STA l_w+1
   ; top: y1 = sy1_top, D = sy2_top - sy1_top
   LDA zp_seg_sy1_top_l
   STA lt_y1
   LDA zp_seg_sy1_top_h
   STA lt_y1+1
   SEC
   LDA zp_seg_sy2_top_l
   SBC zp_seg_sy1_top_l
   STA lt_dm
   LDA zp_seg_sy2_top_h
   SBC zp_seg_sy1_top_h
   STA lt_dm+1
   STZ lt_neg
   BPL :+
   DEC lt_neg
   SEC
   LDA #0
   SBC lt_dm
   STA lt_dm
   LDA #0
   SBC lt_dm+1
   STA lt_dm+1
:
   ; bottom
   LDA zp_seg_sy1_bot_l
   STA lb_y1
   LDA zp_seg_sy1_bot_h
   STA lb_y1+1
   SEC
   LDA zp_seg_sy2_bot_l
   SBC zp_seg_sy1_bot_l
   STA lb_dm
   LDA zp_seg_sy2_bot_h
   SBC zp_seg_sy1_bot_h
   STA lb_dm+1
   STZ lb_neg
   BPL :+
   DEC lb_neg
   SEC
   LDA #0
   SBC lb_dm
   STA lb_dm
   LDA #0
   SBC lb_dm+1
   STA lb_dm+1
:
   ; --- first byte sample: (lo + 3) & ~3 (the byte column's first pixel) ---
   LDA mf_lo
   CLC
   ADC #3
   BCC :+
   RTS                                  ; lo > 252: no byte column starts here
:  AND #$FC
   STA mf_x
   STZ mf_i
   LDA #$FF
   STA mf_oi                            ; no old-edge steppers yet
   STA mf_ns                            ; no new-edge steppers yet
   LDA zp_head
   STA mf_lc                            ; live-list cursor
   ; --- T and B steppers from x0: k = x0 - sx1 ---
   SEC
   LDA mf_x
   SBC l_sx1
   STA si_k
   LDA #0
   SBC l_sx1+1
   STA si_k+1
   LDA l_w
   STA si_w
   LDA l_w+1
   STA si_w+1
   LDA lt_y1
   STA si_y0
   LDA lt_y1+1
   STA si_y0+1
   LDA lt_dm
   STA si_d
   LDA lt_dm+1
   STA si_d+1
   LDA lt_neg
   STA si_neg
   LDX #ST_T
   JSR st_init
   LDA lb_y1
   STA si_y0
   LDA lb_y1+1
   STA si_y0+1
   LDA lb_dm
   STA si_d
   LDA lb_dm+1
   STA si_d+1
   LDA lb_neg
   STA si_neg
   LDX #ST_B
   JSR st_init
   JSR sh_lim
   LDA #$FF
   STA tx_lx                            ; no exact d yet in this seg
   STA tx_ax                            ; no look-ahead d
   STA ss_x                             ; no previous run steps
   STA ss_x+1
   STA ss_x+2
   LDA mf_x
   STA tx_x0
   LDA mf_x
   CMP mf_hi
   BCC :+
   RTS                                  ; no byte column in [lo, hi)
:  JSR tx_seg                           ; the seg's wall texture set-up

col:
.ifdef SERVER
   JSR fs_poll                          ; (the last list, out to the host)
.endif
   STZ c_dok                            ; d not yet computed for this byte
   STZ c_trok                           ; nor the right strip's lines
   LDA mf_x
   CMP mf_hi
   BCC adv
   JMP mf_planes                        ; x >= hi: the seg's planes, done
   ; --- old span at x: advance while xe <= x ---
adv:
   LDY mf_i
   CPY sn_n
   BCC @have
   JMP mf_planes                        ; no snapshot span left
@have:
   LDA mf_x
   CMP sn_xe,Y
   BCC @in
   INC mf_i
   BRA adv
@in:
   CMP sn_xs,Y
   BCS @old
   JMP next_col                         ; x < xs: a gap before it
@old:
   CPY mf_oi
   BEQ @oval
   STY mf_oi                            ; a new old-span: set up OT / OB here
   LDA sn_xlo,Y
   STA si_xlo
   LDA sn_den,Y
   STA si_w
   STZ si_w+1
   LDA sn_tl,Y
   STA si_a0
   LDA sn_tr,Y
   STA si_a1
   LDX #ST_OT
   JSR st_init8
   LDY mf_oi
   LDA sn_bxlo,Y                        ; the bottom line's own anchor
   STA si_xlo
   LDA sn_bden,Y
   STA si_w
   STZ si_w+1
   LDA sn_bl,Y
   STA si_a0
   LDA sn_br,Y
   STA si_a1
   LDX #ST_OB
   JSR st_init8
@oval:
   LDA st_f+ST_OT                       ; (step 7i: y read as it is)
   STA c_ot
   LDA st_f+ST_OB                       ; (step 7i: y read as it is)
   STA c_ob
   ; --- live span at x: the cursor only moves right ---
@lc:
   LDX mf_lc
   BEQ @none
   LDA mf_x
   CMP POOL_XEND,X
   BCC @inx
   LDA POOL_NEXT,X
   STA mf_lc
   BRA @lc
@inx:
   CMP POOL_XSTART,X
   BCC @none                            ; x < xs: no live span covers x
   CPX mf_ns
   BEQ @nval
   STX mf_ns                            ; a new live span: set up NT / NB
   LDA POOL_TXLO,X
   STA si_xlo
   LDA POOL_TDEN,X
   STA si_w
   STZ si_w+1
   LDA POOL_TL,X
   STA si_a0
   LDA POOL_TR,X
   STA si_a1
   LDX #ST_NT
   JSR st_init8
   LDX mf_ns
   LDA POOL_BXLO,X                      ; the bottom line's own anchor
   STA si_xlo
   LDA POOL_BDEN,X
   STA si_w
   STZ si_w+1
   LDA POOL_BL,X
   STA si_a0
   LDA POOL_BR,X
   STA si_a1
   LDX #ST_NB
   JSR st_init8
@nval:
   LDA st_f+ST_NT                       ; (step 7i: y read as it is)
   STA c_nt
   LDA st_f+ST_NB                       ; (step 7i: y read as it is)
   STA c_nb
   LDA #1
   STA c_new
   BRA @lines
@none:
   STZ c_new
@lines:
   ; --- T and B at x, clamped for comparison against the visible band ---
   LDA st_f+ST_T
   STA c_t
   LDX st_f+ST_T+1
   STX c_t+1
   BMI @tlo                             ; clamp(T, 48, VIS_YMAX + 1)
   BNE @thi
   CMP #Y_BIAS
   BCC @tlo
   CMP #VIS_YMAX + 1
   BCC @tc
@thi:
   LDA #VIS_YMAX + 1
   BRA @tc
@tlo:
   LDA #Y_BIAS
@tc:
   STA c_tc
   LDA st_f+ST_B
   STA c_b
   LDX st_f+ST_B+1
   STX c_b+1
   BMI @blo                             ; clamp(B, 47, VIS_YMAX)
   BNE @bhi
   CMP #Y_BIAS - 1
   BCC @blo
   CMP #VIS_YMAX
   BCC @bc
@bhi:
   LDA #VIS_YMAX
   BRA @bc
@blo:
   LDA #Y_BIAS - 1
@bc:
   STA c_bc
   ; --- the bands ---
   LDA c_new
   BNE @two
   STZ b_kind                           ; closed: [ot, ob]
   LDA c_ot
   STA b_y0
   LDA c_ob
   STA b_y1
   JSR band
   JMP next_col
@two:
   LDA #1
   STA b_kind
   LDA c_nt                             ; top band [ot, nt-1]
   BEQ @bot                             ; (nt = 0: nothing above it)
   DEC A
   STA b_y1
   LDA c_ot
   STA b_y0
   JSR band
@bot:
   LDA #2
   STA b_kind
   LDA c_nb                             ; bottom band [nb+1, ob]
   CMP #255
   BEQ next_col
   INC A
   STA b_y0
   LDA c_ob
   STA b_y1
   JSR band
; ST_STEP B: advance stepper B (a constant offset) by four pixels, inline.
; Step 7i: r is held biased, rb = r - W + 2^16, so r + R >= W is the carry
; out of rb + R: no compare. Then rb -= W (C = 1 in, borrow out: C = 0),
; and y takes Qs1 = Qs +/- 1 in one add; else y += Qs (C = 0).
.macro ST_STEP B
.local wrap, done
   CLC
   LDA st_f+B+4
   ADC st_f+B+10
   STA st_f+B+4
   LDA st_f+B+5
   ADC st_f+B+11
   BCS wrap
   STA st_f+B+5
   LDA st_f+B+0                         ; y += Qs (C = 0)
   ADC st_f+B+8
   STA st_f+B+0
   LDA st_f+B+1
   ADC st_f+B+9
   STA st_f+B+1
   BRA done
wrap:
   TAX                                  ; rb -= W (C = 1 in)
   LDA st_f+B+4
   SBC st_f+B+6
   STA st_f+B+4
   TXA
   SBC st_f+B+7
   STA st_f+B+5
   LDA st_f+B+0                         ; y += Qs1 (C = 0: the borrow)
   ADC st_f+B+2
   STA st_f+B+0
   LDA st_f+B+1
   ADC st_f+B+3
   STA st_f+B+1
done:
.endmacro

; ST_STEP8 B: the same for a span edge stepper (OT, OB, NT, NB): W is the
; span's u8 denominator and y is read as a byte, so only the low bytes
; move (rb = r - W + 2^8)
.macro ST_STEP8 B
.local wrap, done
   CLC
   LDA st_f+B+4
   ADC st_f+B+10
   BCS wrap
   STA st_f+B+4
   LDA st_f+B+0                         ; y += Qs (C = 0)
   ADC st_f+B+8
   STA st_f+B+0
   BRA done
wrap:
   SBC st_f+B+6                         ; rb -= W (C = 1 in, 0 out)
   STA st_f+B+4
   LDA st_f+B+0                         ; y += Qs1
   ADC st_f+B+2
   STA st_f+B+0
done:
.endmacro

next_col:
   LDA mf_x
   CLC
   ADC #4                               ; the next byte column
   BCC @step
   JMP mf_planes
@step:
   STA mf_x                             ; every stepper moves 4 pixels: once
   CLC                                  ;  per byte. The texture d stepper:
   LDA tx_n                             ;  n += dn, den += dden
   ADC tx_dn
   STA tx_n
   LDA tx_n+1
   ADC tx_dn+1
   STA tx_n+1
   LDA tx_n+2
   ADC tx_dn+2
   STA tx_n+2
   LDA tx_n+3
   ADC tx_dn+3
   STA tx_n+3
   CLC
   LDA tx_den
   ADC tx_dden
   STA tx_den
   LDA tx_den+1
   ADC tx_dden+1
   STA tx_den+1
   ST_STEP ST_T                         ; (inline: phase 1)
   ST_STEP ST_B
   LDA mf_oi
   BMI nc_no_o
   ST_STEP8 ST_OT
   ST_STEP8 ST_OB
nc_no_o:
   LDA mf_ns
   BMI nc_no_n
   ST_STEP8 ST_NT
   ST_STEP8 ST_NB
nc_no_n:
   JMP col

; ============================================================================
; STEPPERS. Each tracks y(x) = y0 +/- floor(|D| * k / W) for k = x - x0
; exactly as x steps by 4 (one byte column): |D| * k = q * W + r with
; 0 <= r < W, and 4|D| = Q * W + R, so a step is r += R, q += Q, then one
; conditional r -= W, q += 1. A negative slope is y0 - ceil(|D| * k / W),
; the floor of (|D| * k + W - 1) / W (step 7i: so y is read as it is,
; no correction). W = 0 is a constant y0 (a zero-width line). The set-up
; pays one multiply and two divides; each step a few adds.
; Field offsets in st_* (X = stepper base): y 0/1, Qs1 2/3 (y's step on a
; wrap), rb 4/5 (r - W + 2^16), W 6/7, Qs 8/9 (y's step), R 10/11.
; ============================================================================
; (ST_T .. ST_NB: defined with st_f, above)

; st_init8: a span edge -- y0 = si_a0, D = si_a1 - si_a0, W = si_w (den),
; k = x - si_xlo (u8 values; the clipper's floor interpolation)
st_init8:
   STX si_x
   LDA si_a0
   STA si_y0
   STZ si_y0+1
   STZ si_neg
   LDA si_a1
   SEC
   SBC si_a0
   BCS @pos
   DEC si_neg
   LDA si_a0
   SEC
   SBC si_a1
@pos:
   STA si_d
   STZ si_d+1
   SEC
   LDA mf_x
   SBC si_xlo
   STA si_k
   STZ si_k+1
   LDX si_x
   ; fall into st_init

; st_init: si_y0, si_d (|D|), si_neg, si_w, si_k  -> stepper X. A stepper
; holds y itself: y = y0 + q (y0 - q when negative), the biased remainder
; rb = r - W + 2^16 (r + R >= W is then the carry of rb + R), and steps by
; rb += R, y += Qs (+/-Q); on the carry rb -= W, y += Qs1 (Qs +/- 1).
; A constant (W = 0) is y0 with rb = R = 0: its step never moves it.
st_init:
   STX si_x
   LDA si_w
   STA st_f+6,X
   LDA si_w+1
   STA st_f+7,X
   ORA si_w
   BNE @var
   LDA si_y0                            ; W = 0: constant y0
   STA st_f+0,X
   LDA si_y0+1
   STA st_f+1,X
   STZ st_f+2,X
   STZ st_f+3,X
   STZ st_f+4,X
   STZ st_f+5,X
   STZ st_f+8,X
   STZ st_f+9,X
   STZ st_f+10,X
   STZ st_f+11,X
   RTS
@var:
   LDA si_d                             ; q, r = |D| * k / W
   STA m_a
   LDA si_d+1
   STA m_a+1
   LDA si_k
   STA m_b
   LDA si_k+1
   STA m_b+1
   JSR mul16
   LDA si_neg
   BEQ @fl
   SEC                                  ; negative: the ceiling, |D| * k
   LDA si_w                             ;  + W - 1
   SBC #1
   STA m_a
   LDA si_w+1
   SBC #0
   STA m_a+1
   CLC
   LDA m_p
   ADC m_a
   STA m_p
   LDA m_p+1
   ADC m_a+1
   STA m_p+1
   BCC @fl
   INC m_p+2
   BNE @fl
   INC m_p+3
@fl:
   LDA si_w
   STA m_b
   LDA si_w+1
   STA m_b+1
   JSR div32
   LDX si_x
   SEC                                  ; rb = r - W (+ 2^16)
   LDA m_r
   SBC si_w
   STA st_f+4,X
   LDA m_r+1
   SBC si_w+1
   STA st_f+5,X
   LDA si_neg                           ; y = y0 +/- q
   BNE @yneg
   CLC
   LDA si_y0
   ADC m_p
   STA st_f+0,X
   LDA si_y0+1
   ADC m_p+1
   STA st_f+1,X
   BRA @qr
@yneg:
   SEC
   LDA si_y0
   SBC m_p
   STA st_f+0,X
   LDA si_y0+1
   SBC m_p+1
   STA st_f+1,X
@qr:
   LDA si_d                             ; Q, R = 4|D| / W
   ASL A
   STA m_p
   LDA si_d+1
   ROL A
   STA m_p+1
   LDA #0
   ROL A
   STA m_p+2
   STZ m_p+3
   ASL m_p
   ROL m_p+1
   ROL m_p+2
   LDA si_w
   STA m_b
   LDA si_w+1
   STA m_b+1
   JSR div32
   LDX si_x
   LDA m_r
   STA st_f+10,X
   LDA m_r+1
   STA st_f+11,X
   LDA si_neg
   BNE @qneg
   LDA m_p                              ; Qs = Q, Qs1 = Q + 1
   STA st_f+8,X
   CLC
   ADC #1
   STA st_f+2,X
   LDA m_p+1
   STA st_f+9,X
   ADC #0
   STA st_f+3,X
   RTS
@qneg:
   LDA m_p                              ; Qs1 = -Q - 1 = ~Q, Qs = ~Q + 1
   EOR #$FF
   STA st_f+2,X
   CLC
   ADC #1
   STA st_f+8,X
   LDA m_p+1
   EOR #$FF
   STA st_f+3,X
   ADC #0
   STA st_f+9,X
   RTS

; st_peek: ln_y = stepper X's y one step on (4 pixels), its state kept
st_peek:
   CLC                                  ; the carry of rb + R: a wrap
   LDA st_f+4,X
   ADC st_f+10,X
   LDA st_f+5,X
   ADC st_f+11,X
   LDA st_f+0,X
   BCS @w
   ADC st_f+8,X                         ; y + Qs (C = 0)
   STA ln_y
   LDA st_f+1,X
   ADC st_f+9,X
   STA ln_y+1
   RTS
@w:
   CLC                                  ; y + Qs1
   ADC st_f+2,X
   STA ln_y
   LDA st_f+1,X
   ADC st_f+3,X
   STA ln_y+1
   RTS

; tr_lines: the right strip's lines (once per byte): the midpoints of the
; byte's T, B and the next byte's, Tr = T + ((T(x + 4) - T) >> 1)
tr_lines:
   LDA c_trok
   BNE @rts
   INC c_trok
   LDX #ST_T
   JSR st_peek
   SEC
   LDA ln_y
   SBC c_t
   STA m_a
   LDA ln_y+1
   SBC c_t+1
   CMP #$80
   ROR A
   STA m_a+1
   ROR m_a
   CLC
   LDA c_t
   ADC m_a
   STA c_tr
   LDA c_t+1
   ADC m_a+1
   STA c_tr+1
   LDX #ST_B
   JSR st_peek
   SEC
   LDA ln_y
   SBC c_b
   STA m_a
   LDA ln_y+1
   SBC c_b+1
   CMP #$80
   ROR A
   STA m_a+1
   ROR m_a
   CLC
   LDA c_b
   ADC m_a
   STA c_br
   LDA c_b+1
   ADC m_a+1
   STA c_br+1
@rts:
   RTS

; ---- band: [b_y0, b_y1] (biased, inclusive) -> ceiling / wall / floor runs
band:
   LDA b_y0                             ; clamp to the visible [48, 207]
   CMP #Y_BIAS
   BCS :+
   LDA #Y_BIAS
   STA b_y0
:  LDA b_y1
   CMP #VIS_YMAX + 1
   BCC :+
   LDA #VIS_YMAX
   STA b_y1
:  LDA b_y1
   CMP b_y0
   BCS :+
   RTS                                  ; empty
:
   ; ceiling [y0, min(y1, tc-1)]
   LDA c_tc
   DEC A
   CMP b_y1
   BCC :+
   LDA b_y1
:  STA r_ye                             ; (biased, converted in run)
   LDA b_y0
   STA r_ys
   JSR ceil_run
   ; wall [max(y0, tc), min(y1, bc)]
   LDA b_y0
   CMP c_tc
   BCS :+
   LDA c_tc
:  STA r_ys
   LDA c_bc
   CMP b_y1
   BCC :+
   LDA b_y1
:  STA r_ye
   JSR wall_run
   ; floor [max(y0, tc, bc+1), y1]
   LDA c_bc
   INC A
   CMP c_tc
   BCS :+
   LDA c_tc
:  CMP b_y0
   BCS :+
   LDA b_y0
:  STA r_ys
   LDA b_y1
   STA r_ye
   JMP floor_run

; ---- run: A = shade byte (right-strip form); [r_ys, r_ye] biased --------
run:
   STA r_part
   LDA r_ye
   CMP r_ys
   BCS :+
   RTS                                  ; empty
:  SEC
   LDA r_ys
   SBC #Y_BIAS
   STA r_ys
   SEC
   LDA r_ye
   SBC #Y_BIAS
   STA r_ye
   ; fall into hz_run

.endif
.macro HZ_DRIVE S, SIZE
   ; Y = a (the first line's place in its row), hz_n = n lines: rows
   ; r = (a + n - 1) >> 3 after the first. The LAST row first: a temporary
   ; RTS on the body after its last line (body 8 is the row tail); then
   ; the rest in one entry, the row tail looping r times.
   STY hz_a
   TYA
   CLC
   ADC hz_n
   SBC #0                               ; (C = 0: - 1) last line L
   STA hz_l
   LSR A
   LSR A
   LSR A
   STA hz_r                             ; r
   ASL A
   STA hz_r2
   CLC
   ADC PTR+1                            ; the last row
   STA PTR+1
   LDA hz_l
   AND #7
   INC A                                ; e = (L & 7) + 1, 1..8
 .if SIZE = 4
   ASL A
   ASL A
 .else
   STA hz_e                             ; * 7 = * 8 - 1
   ASL A
   ASL A
   ASL A
   SEC
   SBC hz_e
 .endif
   TAY
   LDA .ident(.concat(.string(S), "_b0")),Y
   PHA
   LDA #$60                             ; RTS over body e
   STA .ident(.concat(.string(S), "_b0")),Y
   PHY
   LDX #0                               ; enter at line 0, or at a if the
   LDA hz_r                             ;  run is in one row
   BNE :+
   LDA hz_a
   ASL A
   TAX
:  JSR .ident(.concat(.string(S), "_call"))
   PLY
   PLA
   STA .ident(.concat(.string(S), "_b0")),Y                      ; restored
   LDA hz_r
   BEQ :+
   SEC                                  ; the first row: r rows back
   LDA PTR+1
   SBC hz_r2
   STA PTR+1
   LDA hz_a
   ASL A
   TAX
   JSR .ident(.concat(.string(S), "_call"))                      ; rows 0 .. r - 1, from line a
:  JMP hz_done
.ident(.concat(.string(S), "_call")):
 .if SIZE = 4
   LDA r_ev
 .else                                  ; (h2: the entry line's byte, r_ev
   TXA                                  ;  on an even line, r_ev ^ hz_x on
   AND #2                               ;  an odd)
   BEQ :+
   LDA hz_x
:  EOR r_ev
 .endif
   JMP (.ident(.concat(.string(S), "_tab")),X)
.endmacro

.ifndef SERVER                          ; (the screen: not on the fill server)
.segment "MFILL"                        ; (HAZEL: paged for the whole run)
; ============================================================================
; THE PANEL RASTER SPLIT (step 6c). The 3D view is Mode 2; the control
; panel, lines 136..159, is Mode 1 art (master_panel.py). The User VIA's T1
; runs free, phase-locked to vsync, alternating two periods that add up to
; the 312-line field (19968us): at the panel's first line the handler
; switches the video ULA to Mode 1 and the panel's palette, and in the
; blank around vsync back to Mode 2. The panel's logical colours are chosen
; (master_panel.split_palette) so a switch rewrites only palette entries
; 1..6: entries 8..15, which the Mode 2 view never uses, hold the panel's
; red and cyan for good. The handler is reached through the MOS IRQ entry
; ($E59E: STA $FC / ... / JMP (IRQ1V)) -- page 2 is free on the Master
; since the quarter-square quad moved to HAZEL (SQR_MIR_LO_M) -- and lives
; here in HAZEL, which is paged for the whole run, so it runs whatever
; ACCCON X or ROMSEL hold when it lands.
; ============================================================================
SPLIT_VP = 14161                        ; T1 latch: vsync -> the panel switch
SPLIT_PV = 19964 - SPLIT_VP             ;  and back (each period is latch + 2)
ULA_MODE1 = $D8                         ; video ULA control: Mode 1, Mode 2
ULA_MODE2 = $F4

; split_init: the driver's last init step (SEI held, HAZEL in)
split_init:
   LDX #8                               ; palette 8..15: the panel's red
:  TXA                                  ;  (8, 9, 12, 13) and cyan (10, 11,
   ASL A                                ;  14, 15), for good
   ASL A
   ASL A
   ASL A
   STA sp_phase
   TXA
   AND #2
   BEQ :+
   LDA #6 ^ 7                           ; cyan
   BRA :++
:  LDA #1 ^ 7                           ; red
:  ORA sp_phase
   STA $FE21
   INX
   CPX #16
   BNE :---
   LDA #$7F                             ; every other IRQ source off: both
   STA $FE4E                            ;  VIAs (the engine polls its flags)
   STA $FE6E
   LDA #3
   STA $FE08                            ;  and the ACIA (master reset)
   LDA #<split_irq
   STA $0204                            ; IRQ1V (the MOS's own vector: fixed,
   LDA #>split_irq                      ;  like the hardware it serves)
   STA $0205
   LDA $FE6B                            ; User VIA T1: continuous, PB7 off
   AND #$3F
   ORA #$40
   STA $FE6B
   STZ sp_phase                         ; the next event: the panel
   LDA #2
   STA $FE4D                            ; lock to the next vsync edge
:  LDA $FE4D
   AND #2
   BEQ :-
   LDA #<SPLIT_VP
   STA $FE64
   LDA #>SPLIT_VP
   STA $FE65                            ; T1 starts: vsync -> panel
   LDA #<SPLIT_PV
   STA $FE66
   LDA #>SPLIT_PV
   STA $FE67                            ; then panel -> vsync
   LDA #$C0
   STA $FE6E                            ; User VIA T1 IRQ on
   CLI
   RTS

; cyc_tab: the colour cycle (step 6e), 8 phases x logical 8..15, palette
; register values; master_assets.cycle_colour is the spec (test_master_tex
; compares). 8-11 the nukage wave (green green yellow green, entry k at
; phase p showing step (k + p) & 3), 12 the lamp halo (red, off 1 phase in
; 4), 13 the lamp glint (white red red red), 14 / 15 two blinks (yellow /
; red, each off 1 phase in 4, out of step).
cyc_tab:
.repeat 8, P
.repeat 4, K                            ; 8-11: green green yellow green
   .byte ((8 + K) << 4) | ((2 * (((K + P) & 3) = 0) + 2 * (((K + P) & 3) = 1) + 3 * (((K + P) & 3) = 2) + 2 * (((K + P) & 3) = 3)) ^ 7)
.endrepeat
   .byte (12 << 4) | ((1 * ((P & 3) <> 3)) ^ 7)         ; halo: red, off 1 in 4
   .byte (13 << 4) | ((1 + 6 * ((P & 3) = 0)) ^ 7)      ; glint: white, red
   .byte (14 << 4) | ((3 * ((P & 3) <> 0)) ^ 7)         ; blink: yellow
   .byte (15 << 4) | ((1 * ((P & 3) <> 2)) ^ 7)         ; blink: red
.endrepeat

; split_irq: IRQ1V (A is in $FC; X, Y untouched)
split_irq:
   LDA $FE6D
   AND #$40
   BNE @t1
   LDA $FC                              ; not T1
   RTI
@t1:
   LDA sp_phase
   EOR #$80
   STA sp_phase
   BMI @panel
   JMP @top
@panel:
   LDA #(1 << 4) | 7                    ; the panel's first line: black (1,
   STA $FE21                            ;  4, 5) FIRST, in line 135's blank,
   LDA #(4 << 4) | 7                    ;  THEN Mode 1 (2026-10-09: Mode 1
   STA $FE21                            ;  first showed line 136's zero
   LDA #(5 << 4) | 7                    ;  bytes through the view's red in
   STA $FE21                            ;  entry 1 until its write landed,
   LDA #ULA_MODE1                       ;  a flickering red sliver; in Mode
   STA $FE20                            ;  2 they are entry 0, black). Line
                                        ;  136 is all black (entries 0 1 4
                                        ;  5) either way, so the rest can
                                        ;  land during it: white (2, 3, 6),
                                        ;  red (8 9 12 13) and cyan (10 11
                                        ;  14 15), which the view cycles
   LDA #(2 << 4) | 0
   STA $FE21
   LDA #(3 << 4) | 0
   STA $FE21
   LDA #(6 << 4) | 0
   STA $FE21
.repeat 8, K
   LDA #((8 + K) << 4) | ((((K & 2) / 2) * 5 + 1) ^ 7)
   STA $FE21
.endrepeat
   LDA #<SPLIT_VP                       ; the period after the next event
   STA $FE66
   LDA #>SPLIT_VP
   STA $FE67
   BRA @ack
@top:
   LDA #ULA_MODE2                       ; vsync: Mode 2, entries 1..6 back
   STA $FE20                            ;  to their colours
.repeat 6, K
   LDA #((K + 1) << 4) | ((K + 1) ^ 7)
   STA $FE21
.endrepeat
   PHX                                  ; entries 8..15: the colour cycle's
   INC sp_tick                          ;  phase, one every 16 fields
   LDA sp_tick
   LSR A
   AND #$38                             ; phase * 8
   TAX
.repeat 8, K
   LDA cyc_tab + K,X
   STA $FE21
.endrepeat
   PLX
   LDA #<SPLIT_PV
   STA $FE66
   LDA #>SPLIT_PV
   STA $FE67
   JSR MUS_TICK                         ; the music's 50 Hz tick (step 7ac,
                                        ;  main $0300: X Y kept)
@ack:
   LDA #$40
   STA $FE6D                            ; T1's flag down
   LDA $FC
   RTI

.ifndef TUBE                            ; (the fill: on the second processor)
.segment "MB6C"
; ============================================================================
; hz_run: byte column mf_x >> 2, screen lines [r_ys, r_ye], shade r_part
; (a left-pixel byte): the WHOLE byte (both pixels the shade) on every
; line. Nothing is read back. X is set only inside the write loop.
; ============================================================================
hz_run:
   LDA r_part
   CMP #WB_SKY
   BNE :+
   JMP hz_sky                           ; the sky: cross-hatched (HAZEL)
:  LSR A
   ORA r_part                           ; shade | (shade >> 1): both pixels
   STA r_ev
   JSR ln_ptr                           ; PTR, Y for line r_ys
   SEC
   LDA r_ye
   SBC r_ys
   INC A
   STA hz_n                             ; line count
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow (no main reads >= $3000)
   HZ_DRIVE h1, 4                       ; A = the byte, every line

; hz_done: (HZ_DRIVE's exit)
hz_done:
   LDA #ACC_DY
   STA $FE34                            ; back to main RAM
   RTS

.segment "MFILL"                        ; (HAZEL: bank 6 is full)
; The unrolled character rows: body k writes line k of the row at PTR;
; after line 7 the row tail moves PTR on a row and loops hz_r times.
; HZ_DRIVE lays a temporary RTS on the body after the last row's last
; line (body 8 being the tail).
h1_b0:
.repeat 8, K
   LDY #K
   STA (PTR),Y
.endrepeat
h1_t:                                  ; body 8: the next character row
   INC PTR+1
   INC PTR+1
   DEC hz_r
   BNE h1_b0
   RTS
h1_tab:
.repeat 8, K
   .word h1_b0 + 4 * K
.endrepeat

; hz_sky: hz_run for the sky (step 6d): cyan + white, the pixels swapped
; on odd lines (SKY_BYTE, then flip[] of it), the h2 bodies EORing the
; byte between lines
hz_sky:
   LDA #SKY_EV
   STA r_ev
   LDA #SKY_EV ^ SKY_OD
   STA hz_x
   JSR ln_ptr                           ; PTR, Y for line r_ys
   SEC
   LDA r_ye
   SBC r_ys
   INC A
   STA hz_n                             ; line count
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow (no main reads >= $3000)
   HZ_DRIVE h2, 7                       ; A = the line's byte, EOR hz_x on

h2_b0:
.repeat 8, K
   LDY #K
   STA (PTR),Y
   EOR hz_x                             ; (absolute: 7-byte bodies)
.endrepeat
h2_t:                                  ; body 8: the next character row
   INC PTR+1
   INC PTR+1
   DEC hz_r
   BNE h2_b0
   RTS
h2_tab:
.repeat 8, K
   .word h2_b0 + 7 * K
.endrepeat
.assert h2_t - h2_b0 = 56, error, "h2 bodies must be 7 bytes (hz_x absolute)"

.endif
.segment "MB6C"

; ============================================================================
; gun_draw (step 6f): the gun overlay into the back buffer, after the frame
; and before the flip (the driver calls it, bank 6 paged). master_gun.py is
; the spec, and compiles it into gun_b0 / gun_b1 (mgun_tab.s): straight-line
; stores to the buffer's absolute addresses, opaque bytes simply written,
; edge bytes (screen AND mask) OR data -- the art's transparent pixels keep
; the view.
; ============================================================================
gun_draw:
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow
   LDA DV_BACKHI
   CMP #>MSCREEN1
   BEQ :+
   JSR gun_b0
   BRA :++
:  JSR gun_b1
:  LDA #ACC_DY
   STA $FE34
   RTS

.include "mgun_tab.s"
.else
.segment "MB6C"
; hz_run (SERVER): the run's lines [r_ys, r_ye] (unbiased) of byte column
; mf_x >> 2 are its shade r_part: fserve's cell grid
hz_run:
   LDA r_part
   JMP fs_mark
.endif

.ifndef TUBE                            ; (the fill: on the second processor)
; ============================================================================
; STEP 4: WALL TEXTURES (tex_ref.py is the executable spec; master_walls.py
; generates every table read here -- ANDY, the bank-6 tail, mtex_ix).
;
;   tx_seg   once per seg: the header slot -> its dressing (or merged-seg
;            pieces) and length; d (1/16 world units along the seg) at the
;            projected ends (a near-clipped end from the crossing t); the
;            1/depth weights from the endpoint reciprocals; exact d and the
;            raw weight at the first and last strip centres; then the
;            per-strip numerator / denominator and their constant steps
;   tx_getd  per byte, on demand: d = n / den for both strips (x + 1, x + 3),
;            and the piece holding the left one
;   wall_run the wall rows of a band: the band's part, textured, or the
;            ceiling shade when the part is NONE
;   trun     one textured run: column = ((u & mask) * R) >> 16 -> stored
;            column -> texel column pointer; v (5.11) = Vtop + (y_even - T)
;            * step, step = K / (B - T), stepped per line pair
; ============================================================================

; ld_dress: X = dressing id -> piece slot Y (ANDY paged)
ld_dress:
   LDA man_dr_up,X
   STA pc_up,Y
   LDA man_dr_lo,X
   STA pc_lo,Y
   LDA man_dr_mid,X
   STA pc_mid,Y
   LDA man_dr_ub,X
   STA pc_ub,Y
   RTS

; set_cur: piece X -> cur_*
set_cur:
   LDA pc_up,X
   STA cur_up
   LDA pc_lo,X
   STA cur_lo
   LDA pc_mid,X
   STA cur_mid
   LDY pc_ub,X                          ; u base = ub * 16 - start
   SEC
   LDA lo16,Y
   SBC pc_sl,X
   STA cur_bl
   LDA hi16,Y
   SBC pc_sh,X
   STA cur_bh
   RTS

; l16t: m_p = L16 * t + 128 (the caller takes bytes 1-2: >> 8)
l16t:
   LDA tx_l16
   STA m_a
   LDA tx_l16+1
   STA m_a+1
   LDA mf_xt
   STA m_b
   LDA mf_xt+1
   STA m_b+1
   JSR mul16
   CLC
   LDA m_p
   ADC #128
   STA m_p
   BCC :+
   INC m_p+1
   BNE :+
   INC m_p+2
:  RTS

; at: A = strip centre xc -> at_n (5 bytes) = raw wa*(sx2-xc) + wb*(xc-sx1),
; m_p (16) = exact d = (d1*a + d2*b) / (a + b) with a, b shifted together
; until both < $8000 (d1 when a + b = 0)
at:
   STA at_xc
   SEC
   LDA zp_seg_sx2_l
   SBC at_xc
   STA m_b
   LDA zp_seg_sx2_h
   SBC #0
   STA m_b+1
   LDA tx_wa
   STA m_a
   LDA tx_wa+1
   STA m_a+1
   JSR mul16
   LDX #3
:  LDA m_p,X
   STA at_a,X
   DEX
   BPL :-
   SEC
   LDA at_xc
   SBC zp_seg_sx1_l
   STA m_b
   LDA #0
   SBC zp_seg_sx1_h
   STA m_b+1
   LDA tx_wb
   STA m_a
   LDA tx_wb+1
   STA m_a+1
   JSR mul16
   LDX #3
:  LDA m_p,X
   STA at_b,X
   DEX
   BPL :-
   CLC                                  ; raw = a + b (33 bits)
   LDX #0
:  LDA at_a,X
   ADC at_b,X
   STA at_n,X
   INX
   TXA
   EOR #4
   BNE :-
   LDA #0
   ROL A
   STA at_n+4
@nm:
   LDA at_a+3
   ORA at_b+3
   BNE @by
   LDA at_a+2
   ORA at_b+2
   BMI @by
   BNE @sh
   LDA at_a+1
   ORA at_b+1
   BPL @ok                              ; both < $8000
   BRA @sh
@by:                                    ; step 7i: >= 2^23, at least 9
   LDX #0                               ;  shifts due: a whole byte first
:  LDA at_a+1,X                         ;  (the same as 8 single shifts)
   STA at_a,X
   LDA at_b+1,X
   STA at_b,X
   INX
   CPX #3
   BNE :-
   STZ at_a+3
   STZ at_b+3
   BRA @nm
@sh:
   LSR at_a+3
   ROR at_a+2
   ROR at_a+1
   ROR at_a
   LSR at_b+3
   ROR at_b+2
   ROR at_b+1
   ROR at_b
   BRA @nm
@ok:
   CLC
   LDA at_a
   ADC at_b
   STA at_den
   LDA at_a+1
   ADC at_b+1
   STA at_den+1
   ORA at_den
   BNE @dv
   LDA tx_d1
   STA m_p
   LDA tx_d1+1
   STA m_p+1
   RTS
@dv:
   ; step 7k: (d1 a + d2 b) / (a + b) = d1 + (d2 - d1) b / (a + b), so
   ; floor = d1 + floor((d2 - d1) b / den), and for d2 < d1
   ; d1 - ceil((d1 - d2) b / den), the ceiling as (P + den - 1) / den:
   ; one multiply, the same integer
   SEC
   LDA tx_d2
   SBC tx_d1
   STA m_a
   LDA tx_d2+1
   SBC tx_d1+1
   STA m_a+1
   BCS @up                              ; d2 >= d1
   SEC                                  ; |d2 - d1|
   LDA #0
   SBC m_a
   STA m_a
   LDA #0
   SBC m_a+1
   STA m_a+1
@up:
   PHP                                  ; (C: the sign)
   LDA at_b
   STA m_b
   LDA at_b+1
   STA m_b+1
   JSR mul16                            ; P = |d2 - d1| * b
   LDA at_den
   STA m_b
   LDA at_den+1
   STA m_b+1
   PLP
   BCS @fl
   SEC                                  ; the ceiling: P + den - 1
   LDA m_b
   SBC #1
   STA t_tmp
   LDA m_b+1
   SBC #0
   STA t_tmp+1
   CLC
   LDA m_p
   ADC t_tmp
   STA m_p
   LDA m_p+1
   ADC t_tmp+1
   STA m_p+1
   BCC @cl
   INC m_p+2
   BNE @cl
   INC m_p+3
@cl:
   JSR divq16
   SEC                                  ; d = d1 - q
   LDA tx_d1
   SBC m_p
   STA m_p
   LDA tx_d1+1
   SBC m_p+1
   STA m_p+1
   RTS
@fl:
   JSR divq16
   CLC                                  ; d = d1 + q
   LDA tx_d1
   ADC m_p
   STA m_p
   LDA tx_d1+1
   ADC m_p+1
   STA m_p+1
   RTS

; ---- tx_getd: this byte's d for both strips (once), and the piece -------
tx_getd:
   LDA c_dok
   BEQ :+
   RTS
:  INC c_dok
   ; the left strip's d: on the seg's odd bytes (x + 5 <= xh) the midpoint
   ; of the exact d either side (the previous byte's, and a look-ahead the
   ; next byte reuses); on its even bytes exact
   LDA mf_x
   SEC
   SBC tx_x0
   AND #4
   BEQ @even
   LDA mf_x
   CLC
   ADC #5
   BCS @even
   CMP tx_xh
   BEQ :+
   BCS @even
:  LDA mf_x                             ; d(x - 3): the previous byte's, or
   SEC                                  ;  exact
   SBC #4
   CMP tx_lx
   BNE @da
   LDA tx_dp
   STA tx_d
   LDA tx_dp+1
   STA tx_d+1
   BRA @db
@da:
   LDA #$FF
   JSR tx_dat
   LDA m_p
   STA tx_d
   LDA m_p+1
   STA tx_d+1
@db:
   LDA #1                               ; d(x + 5), kept for the next byte
   JSR tx_dat
   LDA m_p
   STA tx_ad
   LDA m_p+1
   STA tx_ad+1
   LDA mf_x
   CLC
   ADC #4
   STA tx_ax
   CLC                                  ; d = (d(x - 3) + d(x + 5)) >> 1
   LDA tx_d
   ADC tx_ad
   STA tx_d
   LDA tx_d+1
   ADC tx_ad+1
   ROR A
   STA tx_d+1
   ROR tx_d
   BRA @piece
@even:
   LDA tx_ax
   CMP mf_x
   BNE @ex
   LDA tx_ad                            ; the look-ahead made last byte
   STA tx_d
   LDA tx_ad+1
   STA tx_d+1
   BRA @piece
@ex:
   LDA #0
   JSR tx_dat
   LDA m_p
   STA tx_d
   LDA m_p+1
   STA tx_d+1
@piece:
   ; the right strip, at x + 3: the left strip's d past the map's end;
   ; else from the previous byte's exact d, dr = d + ((d - d_prev) >> 1);
   ; else exact (a second division)
   LDA tx_d
   STA tx_dr
   LDA tx_d+1
   STA tx_dr+1
   LDA mf_x
   CLC
   ADC #3
   BCS @rec_j                           ; (x + 3 > 255 > xh)
   CMP tx_xh
   BCC :+
   BEQ :+
@rec_j:
   JMP @rec
:  LDA mf_x
   SEC
   SBC #4
   CMP tx_lx
   BNE @exact
   SEC                                  ; m_a = (d - d_prev) >> 1 (signed)
   LDA tx_d
   SBC tx_dp
   STA m_a
   LDA tx_d+1
   SBC tx_dp+1
   CMP #$80
   ROR A
   STA m_a+1
   ROR m_a
   CLC
   LDA tx_d
   ADC m_a
   STA tx_dr
   LDA tx_d+1
   ADC m_a+1
   STA tx_dr+1
   BRA @rec
@exact:
   LDA tx_dn+3                          ; n + dn / 2, den + dden / 2: two
   CMP #$80                             ;  pixels on (the steps are 4)
   ROR A
   STA t_tmp+3
   LDA tx_dn+2
   ROR A
   STA t_tmp+2
   LDA tx_dn+1
   ROR A
   STA t_tmp+1
   LDA tx_dn
   ROR A
   STA t_tmp
   CLC
   LDX #0
   LDY #4
:  LDA tx_n,X
   ADC t_tmp,X
   STA m_p,X
   INX
   DEY
   BNE :-
   LDA tx_dden+1
   CMP #$80
   ROR A
   STA m_b+1
   LDA tx_dden
   ROR A
   CLC
   ADC tx_den
   STA m_b
   LDA m_b+1
   ADC tx_den+1
   STA m_b+1
   ORA m_b
   BEQ @rec                             ; (den 0: the left d, as dL)
   JSR divq16
   LDA m_p
   STA tx_dr
   LDA m_p+1
   STA tx_dr+1
@rec:
   LDA mf_x                             ; this byte's exact d, for the next
   STA tx_lx
   LDA tx_d
   STA tx_dp
   LDA tx_d+1
   STA tx_dp+1
@pc:
   LDA pc_n
   CMP #2
   BCC @rts                             ; one piece: cur_* already set
   STZ t_i                              ; the last piece with start <= d
   LDX #0
@pl:
   INX
   CPX pc_n
   BCS @take
   LDA tx_d
   CMP pc_sl,X
   LDA tx_d+1
   SBC pc_sh,X
   BCC @pl
   STX t_i
   BRA @pl
@take:
   LDX t_i
   JMP set_cur
@rts:
   RTS

; tx_dat: A = k (-1, 0, 1) -> m_p = the exact d at the left strip centre
; + 4k pixels: (n + k dn) / (den + k dden); dL when that den is 0
tx_dat:
   STA tx_kk
   LDX #3
:  LDA tx_n,X
   STA m_p,X
   DEX
   BPL :-
   LDA tx_den
   STA m_b
   LDA tx_den+1
   STA m_b+1
   LDA tx_kk
   BEQ @div
   BMI @back
   CLC                                  ; k = 1
   LDX #0
   LDY #4
:  LDA m_p,X
   ADC tx_dn,X
   STA m_p,X
   INX
   DEY
   BNE :-
   CLC
   LDA m_b
   ADC tx_dden
   STA m_b
   LDA m_b+1
   ADC tx_dden+1
   STA m_b+1
   BRA @div
@back:
   SEC                                  ; k = -1
   LDX #0
   LDY #4
:  LDA m_p,X
   SBC tx_dn,X
   STA m_p,X
   INX
   DEY
   BNE :-
   SEC
   LDA m_b
   SBC tx_dden
   STA m_b
   LDA m_b+1
   SBC tx_dden+1
   STA m_b+1
@div:
   LDA m_b
   ORA m_b+1
   BNE :+
   LDA tx_dl                            ; den 0: dL
   STA m_p
   LDA tx_dl+1
   STA m_p+1
   RTS
:  JMP divq16

; ---- wall_run: the wall rows [r_ys, r_ye] (biased) of the current band ----
wall_run:
   LDA r_ye
   CMP r_ys
   BCS :+
   RTS                                  ; empty
:  JSR tx_getd
   LDA cur_mid
   BIT tx_solid
   BMI @part                            ; solid: the whole band is `mid`
   LDX b_kind
   LDA #NONE
   DEX
   BMI @part                            ; closed portal column: none
   LDA cur_up
   DEX
   BMI @part                            ; top band: upper
   LDA cur_lo                           ; bottom band: lower
@part:
   CMP #NONE
   BNE trun
   LDA sh_ceil                          ; no texture: the ceiling shade
   JMP run

; ---- trun: A = part id; textured run over [r_ys, r_ye] (biased) ---------
; One part, one bank, the byte's extents; two independent strips: each has
; its own column (u at its centre) and its own v (its own T and B lines).
; Every line is written WHOLE: (left texel << 2) | right texel, FLIP of it
; on odd lines -- a 4x2 fat pixel, nothing read back.
trun:
   STA t_part                           ; (step 7i: the part's K and Vtop
   TAX                                  ;  are read from its record by
   LDA mb6_pt_tid,X                     ;  tv_div / tv_v0, not copied)
   STA t_tid
   ; the left strip: its v from T, B at x; its column from d at x + 1
   LDA c_t
   STA q_t
   LDA c_t+1
   STA q_t+1
   SEC                                  ; B - T: the step's divisor
   LDA c_b
   SBC c_t
   STA t_hl
   STA m_b
   LDA c_b+1
   SBC c_t+1
   STA t_hh
   STA m_b+1
   ; step 7e: the band's last run in this seg had the same part and B - T:
   ; the same step, no division (K is the part's; exact)
   LDY b_kind
   LDA mf_x
   LDX ss_x,Y
   STA ss_x,Y                           ; (this byte's run, from here on)
   CPX #$FF
   BEQ @sdiv                            ; (none yet in this seg)
   LDA t_part
   CMP ss_pt,Y
   BNE @sdiv
   LDA t_hl
   CMP ss_hl,Y
   BNE @sdiv
   LDA t_hh
   CMP ss_hh,Y
   BNE @sdiv
   LDA ss_sl,Y
   STA t_step
   STA t_sl
   LDA ss_sh,Y
   STA t_step+1
   STA t_sl+1
   BRA @sgot
@sdiv:
   JSR tv_divm
   LDY b_kind                           ; the band's step cache: this run
   LDA t_part
   STA ss_pt,Y
   LDA t_hl
   STA ss_hl,Y
   LDA t_hh
   STA ss_hh,Y
   LDA t_step                           ; (the left step, for the next byte)
   STA t_sl
   STA ss_sl,Y
   LDA t_step+1
   STA t_sl+1
   STA ss_sh,Y
@sgot:
   JSR tv_v0
   LDA t_v
   STA l_v
   LDA t_v+1
   STA l_v+1
   LDA t_step
   STA l_step
   LDA t_step+1
   STA l_step+1
   LDA tx_d
   STA q_d
   LDA tx_d+1
   STA q_d+1
   LDY #0                               ; -> tw_ll, tw_lh
   JSR tcol
   ; step 5n: a left step <= the seg's limit shares the left v (t_v,
   ; t_step are still the left strip's): none of the right v is made
   STZ tw_sh
   STZ zw_ddh                           ; (shared: no delta step)
   STZ zw_ddl
   LDA tw_slim
   CMP t_sl
   LDA tw_slim+1
   SBC t_sl+1
   BCC @own
   DEC tw_sh                            ; ($FF: shared; the right strip's
   BRA @rr                              ;  v is the left's, as t_v / t_step)
@own:
   ; the right strip: its own v from its own T, B (the midpoints with the
   ; next byte's), its own exact step; its column from d at x + 3
   JSR tr_lines
   LDA c_tr
   STA q_t
   LDA c_tr+1
   STA q_t+1
   ; its step exact (step 7g: no longer extrapolated from the left
   ; steps), reusing the left step when the heights match (7e)
   SEC                                  ; step 7e: Br - Tr = B - T: the left
   LDA c_br                             ;  step (exact)
   SBC c_tr
   STA m_b
   TAX
   LDA c_br+1
   SBC c_tr+1
   STA m_b+1
   CPX t_hl
   BNE @rdiv
   CMP t_hh
   BNE @rdiv
   LDA t_sl
   STA t_step
   LDA t_sl+1
   STA t_step+1
   BRA @rv
@rdiv:
   JSR tv_divm                          ; K over Br - Tr (7v)
@rv:
   JSR tv_v0
   JSR tr_ddh
@rr:
   LDA tx_dr
   STA q_d
   LDA tx_dr+1
   STA q_d+1
   LDY #tw_rl - tw_ll                   ; -> tw_rl, tw_rh
   JSR tcol
.ifndef SERVER
   LDX t_tid
   LDA mb6_tp_rowm,X
   STA zw_rowm
   CMP rm_cur
   BEQ :+
   JSR rm_patch                         ; a new mask: into the pair bodies
:
.endif
   JMP tr_screen

.ifndef SERVER                          ; (the screen: not on the fill server)
.segment "MFILL"
; trun's screen side (HAZEL: it pages the texture's bank). X = t_tid.
tr_screen:
   SEC
   LDA r_ys
   SBC #Y_BIAS
   STA r_ys
   SEC
   LDA r_ye
   SBC #Y_BIAS
   STA r_ye
   SEC
   SBC r_ys
   INC A
   STA t_n                              ; line count
   JSR ln_ptr                           ; PTR, Y for line r_ys
   LDA tw_ll                            ; the texel column pointers
   STA zw_tl
   LDA tw_lh
   STA zw_tl+1
   LDA tw_rl
   STA zw_tr
   LDA tw_rh
   STA zw_tr+1
   LDA l_v                              ; both strips' v into zero page
   STA zw_lvl
   LDA l_v+1
   STA zw_lvh
   SEC                                  ; the right strip: a 5.3 delta
   LDA t_v+1                            ;  from the left's v (t_v = l_v
   SBC l_v+1                            ;  when the run shares: 0)
   STA zw_dh
   LDA #$80                             ; (its fraction: rounds once, 7t)
   STA zw_dl
   LDA mb6_tp_bank,X                    ; (X = t_tid still)
   STA $FE30                            ; the texture's bank
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow (no main reads >= $3000)
   ; an odd first line: its pair's odd byte, alone; then the next pair's v
   TYA
   LSR A
   BCC @even
   JSR tr_fetch
   STA (PTR),Y
   DEC t_n
   BEQ @done
   JSR tr_vstep
   INY                                  ; (odd -> even: the row's end only
   CPY #8                               ;  at line 7)
   BNE @even
   LDY #0
   INC PTR+1
   INC PTR+1
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ;  steps
   ADC zw_ddh
   STA zw_dh
@even:
   ; whole pairs from even line Y: the unrolled bodies, entered so the
   ; first pass does (pairs & 3) of them (or 4) and the rest run whole
   ; blocks of four, one count per block
   LDA t_n
   LSR A
   STA tw_np
   ROR t_n                              ; (bit 7: an even last line remains)
   LDA tw_np
   BEQ @tail
   CLC
   ADC #3
   LSR A
   LSR A
   STA zw_np                            ; blocks = ceil(pairs / 4)
   LDA tw_np
   AND #3
   ASL A
   STA zw_ev
   TYA
   ASL A
   ASL A
   ORA zw_ev
   TAX                                  ; Y * 4 + (pairs & 3) * 2
   BIT tw_sh
   BMI :+
   LDA zw_lvh                           ; the left strip's row pushed and
   AND zw_rowm                          ;  the right strip's in Y, as a step
   PHA                                  ;  leaves them
   LDA zw_lvh
   CLC
   ADC zw_dh
   AND zw_rowm
   TAY
   JMP (tr_ent,X)
:  LDA zw_lvh                           ; (step 5n: one v, its row)
   AND zw_rowm
   TAY
   JMP (ts_ent,X)
@tail:
   BIT t_n
   BPL @done
   JSR tr_fetch                         ; the last line, even, alone
   STA (PTR),Y
@done:
   LDA #ACC_DY
   STA $FE34                            ; back to main RAM
   LDA #BANK_C
   STA $FE30                            ; and the cascade's bank
   RTS

; tb_end: the blocks are done; Y = the last pair's odd line (PTR already on
; the next character row if that was line 7). An even last line, if any,
; is the next line, with the next pair's v.
tb_end:
   BIT t_n
   BPL @done
   INY
   CPY #8
   BNE :+
   LDY #0
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ;  delta steps
   ADC zw_ddh
   STA zw_dh
:  JSR tr_vstep
   JSR tr_fetch
   STA (PTR),Y
@done:
   LDA #ACC_DY
   STA $FE34
   LDA #BANK_C
   STA $FE30
   RTS
.else
.segment "MB6C"
tr_screen:                              ; (SERVER: the run's WALL record)
   JMP fs_wall
.endif

.segment "MFILL"
; sh_lim: step 5n, the seg's shared-v limit (tex_ref.shared_limit):
; tw_slim = (384 w - 1) // max(|DT|, |DB|), $FFFF if that overflows or w
; or the rise is 0 -- a run whose left step is <= it shares one v
sh_lim:
   LDA lt_dm                            ; m_b = the larger rise
   CMP lb_dm
   LDA lt_dm+1
   SBC lb_dm+1
   LDX #lt_dm - lt_dm
   BCS :+
   LDX #lb_dm - lt_dm
:  LDA lt_dm,X
   STA m_b
   LDA lt_dm+1,X
   STA m_b+1
   ORA m_b
   BEQ @all
   LDA l_w+1
   BMI @all
   ORA l_w
   BEQ @all
   STZ m_p                              ; m_p = 768 w = (3 w) << 8 ...
   LDA l_w
   ASL A
   STA m_p+1
   LDA l_w+1
   ROL A
   STA m_p+2
   LDA #0
   ROL A
   STA m_p+3
   CLC
   LDA m_p+1
   ADC l_w
   STA m_p+1
   LDA m_p+2
   ADC l_w+1
   STA m_p+2
   LDA m_p+3
   ADC #0
   LSR A                                ; ... >> 1 = 384 w
   STA m_p+3
   ROR m_p+2
   ROR m_p+1
   ROR m_p
   LDA m_p                              ; - 1 (384 w > 0: no borrow out)
   BNE :+++
   LDA m_p+1
   BNE :++
   LDA m_p+2
   BNE :+
   DEC m_p+3
:  DEC m_p+2
:  DEC m_p+1
:  DEC m_p
   JSR div32
   LDA m_p+2
   ORA m_p+3
   BNE @all
   LDA m_p
   STA tw_slim
   LDA m_p+1
   STA tw_slim+1
   RTS
@all:
   LDA #$FF
   STA tw_slim
   STA tw_slim+1
   RTS

.ifndef SERVER                          ; (the screen: not on the fill server)
; tr_fetch: A = the current pair's combined texel byte (Y kept)
tr_fetch:
   STY tw_ly
   LDA zw_lvh                           ; row = (v >> 11) & (th - 1), * 8
   AND zw_rowm
   TAY
   LDA (zw_tl),Y
   STA zw_ev
   LDA zw_lvh                           ; the right row: v hi + delta
   CLC
   ADC zw_dh
   AND zw_rowm
   TAY
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA zw_ev
   LDY tw_ly
   RTS

; tr_vstep: the left strip's v on one pair (the right's is a delta)
tr_vstep:
   CLC
   LDA zw_lvl
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RTS

; THE PAIR BODIES: four versions tv_0..tv_3, each four pairs unrolled (a
; whole character row: version s's pairs are on lines 2s, 2s+2, ... mod 8,
; PTR moving on a row after line 6's pair). Each body is
;   [both v stepped]  te_sj:  [both texels read through zw_tl / zw_tr,
;   combined; even line lit; odd line FLIP lit]
; so entering at te_sj skips the step (the first pair's v is current) and
; the last pair's step never runs. One count per block of four. The row
; mask is an immediate (RM_AND: rm_patch rewrites every one when the
; texture's mask changes). Each step pushes the left strip's row and ends
; with the right strip's in Y (tr_screen does the same before jumping in):
; the pair reads the right texel, PLY, and ORs in the left one. Step 7d:
; only the left v is stepped; the right row is its high byte plus zw_dh,
; a 5.3 delta exact at the run's first line and stepped by zw_ddh in the
; step of each line-0 pair (a new character row; inline at the two other
; crossings: an odd first line 7, tb_end's last line) -- tex_ref.
; No CLC before the left step: texels only use the left pixel's bits
; ($AA), so the previous pair's LSR A left carry clear, and nothing after
; it (stores, INC / DEC, branches) touches it; the bodies are entered
; only past their first step (te_sj), and their starts only from the loop.
; The line-0 pair stores with STA (PTR), then LDY #1.
rm_n .set 0
.macro RM_AND
   .ident(.sprintf("rm_%d", rm_n)) = * + 1
   AND #$FF                             ; (patched: the row mask)
   rm_n .set rm_n + 1
.endmacro
tr_ent:                                 ; X = Y * 4 + (pairs & 3) * 2
   .word te_00, te_13, te_22, te_31   ; Y = 0: r = 0..3
   .word te_10, te_23, te_32, te_01   ; Y = 2: r = 0..3
   .word te_20, te_33, te_02, te_11   ; Y = 4: r = 0..3
   .word te_30, te_03, te_12, te_21   ; Y = 6: r = 0..3

tv_0:
   ; (pairs on lines 0, 2, 4, 6)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ; a new character row: the delta
   ADC zw_ddh
   STA zw_dh
   CLC
   ADC zw_lvh                           ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_00:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_01:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_02:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_03:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   DEC zw_np
   BEQ :+
   JMP tv_0
:  JMP tb_end
tv_1:
   ; (pairs on lines 2, 4, 6, 0)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_10:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_11:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_12:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ; a new character row: the delta
   ADC zw_ddh
   STA zw_dh
   CLC
   ADC zw_lvh                           ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_13:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP tv_1
:  JMP tb_end
tv_2:
   ; (pairs on lines 4, 6, 0, 2)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_20:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_21:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ; a new character row: the delta
   ADC zw_ddh
   STA zw_dh
   CLC
   ADC zw_lvh                           ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_22:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_23:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP tv_2
:  JMP tb_end
tv_3:
   ; (pairs on lines 6, 0, 2, 4)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_30:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_dl
   CLC
   ADC zw_ddl
   STA zw_dl
   LDA zw_dh                            ; a new character row: the delta
   ADC zw_ddh
   STA zw_dh
   CLC
   ADC zw_lvh                           ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_31:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_32:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND
   PHA                                  ; the left row, for the fetch
   LDA zw_lvh
   CLC
   ADC zw_dh                            ; the right row: + the 5.3 delta
   RM_AND
   TAY
te_33:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   PLY                                  ; the left row
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP tv_3
:  JMP tb_end

; Step 5n: the shared-v bodies (sv_0..sv_3, entries ts_sj), as tv_* but
; with one v: the left strip's v steps alone and its row (Y, made by the
; step) reads both texel columns. The exit copies it to the right strip's for tb_end.
ts_ent:                                 ; X = Y * 4 + (pairs & 3) * 2
   .word ts_00, ts_13, ts_22, ts_31   ; Y = 0: r = 0..3
   .word ts_10, ts_23, ts_32, ts_01   ; Y = 2: r = 0..3
   .word ts_20, ts_33, ts_02, ts_11   ; Y = 4: r = 0..3
   .word ts_30, ts_03, ts_12, ts_21   ; Y = 6: r = 0..3

sv_0:
   ; (pairs on lines 0, 2, 4, 6)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_00:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_01:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_02:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_03:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   DEC zw_np
   BEQ :+
   JMP sv_0
:  JMP ts_end
sv_1:
   ; (pairs on lines 2, 4, 6, 0)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_10:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_11:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_12:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_13:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP sv_1
:  JMP ts_end
sv_2:
   ; (pairs on lines 4, 6, 0, 2)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_20:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_21:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_22:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_23:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP sv_2
:  JMP ts_end
sv_3:
   ; (pairs on lines 6, 0, 2, 4)
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_30:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #6
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   INC PTR+1                            ; the next character row
   INC PTR+1
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_31:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   STA (PTR)                            ; line 0: no index
   LDY #1
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_32:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #2
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   LDA zw_lvl                           ; (C clear: LSR A of a texel)
   ADC l_step
   STA zw_lvl
   LDA zw_lvh
   ADC l_step+1
   STA zw_lvh
   RM_AND                               ; the row, ready
   TAY
ts_33:
   LDA (zw_tr),Y
   LSR A                                ; the right pixel: one shift
   ORA (zw_tl),Y
   LDY #4
   STA (PTR),Y                          ; both lines: the same byte
   INY
   STA (PTR),Y
   DEC zw_np
   BEQ :+
   JMP sv_3
:  JMP ts_end

; rm_patch: A = the row mask, into every RM_AND of the pair bodies (bank 6,
; called by trun only when the mask changes: rm_cur holds the patched one)
.segment "MB6C"
rm_patch:
   STA rm_cur
.repeat rm_n, i
   STA .ident(.sprintf("rm_%d", i))
.endrepeat
   RTS
rm_cur:  .byte $FF                      ; the bodies' assembled mask
.segment "MFILL"

ts_end:
   JMP tb_end                           ; (zw_dh = 0: one v for both)
.endif

.segment "MB6C"

; tr_ddh: step 7d, the right strip's delta step per character row (8
; lines, 5.3): zw_ddh = (t_step - l_step + 32) >> 6 of the PAIR steps
tr_ddh:                                 ; (step 7t: 16 bits, 4 pair steps'
   SEC                                  ;  difference -- 8 lines' worth --
   LDA t_step                           ;  5.3 plus 8 fraction bits; 7d
   SBC l_step                           ;  rounded it to 5.3, drifting up to
   STA zw_ddl                           ;  a texel over a full-height run)
   LDA t_step+1
   SBC l_step+1
   STA zw_ddh
   ASL zw_ddl
   ROL zw_ddh
   ASL zw_ddl
   ROL zw_ddh
   RTS

; QMUL A0, B0: A (hi), mq_l (lo) = A0 * B0, one quarter-square 8 x 8
; inline (step 7k: mf_mul8 without the call or the staging)
.macro QMUL A0, B0
.local pos, big, done
   LDA A0
   SEC
   SBC B0
   BCS pos
   EOR #$FF
   ADC #1                               ; (C = 0 from the SBC)
pos:
   TAY                                  ; Y = |a - b|
   LDA A0
   CLC
   ADC B0
   TAX                                  ; X = (a + b) & $FF
   BCS big
   SEC
   LDA SQR_LO,X
   SBC SQR_LO,Y
   STA mq_l
   LDA SQR_HI,X
   SBC SQR_HI,Y
   BRA done
big:                                    ; a + b >= 256 (C = 1)
   LDA SQR2_LO,X
   SBC SQR_LO,Y
   STA mq_l
   LDA SQR2_HI,X
   SBC SQR_HI,Y
done:
.endmacro

; ---- tvstep: the run's v. In: m_b (B - T, tv_divm), q_t (s16 T), the
; part t_part's K and Vtop (its record), r_ys (biased). Out: t_step = the PAIR step (2 * K / (B - T), 0 if
; B <= T), t_v = Vtop + ((ys & ~1) - T) * step (mod 2^16) ----------------
; (tv_divm's divide and zero exits, ahead of it: the inline 8x8s put
;  them out of branch range below)
tvd_rs:
   STY m_b                              ; m_b = h again (B - T < 256)
   STZ m_b+1
tvd_w:
   LDA mb6_pt_k0,X
   STA m_p
   LDA mb6_pt_k1,X
   STA m_p+1
   LDA mb6_pt_k2,X
   STA m_p+2
   STZ m_p+3
   LDA m_b+1
   BMI tvd_z
   ORA m_b
   BEQ tvd_z
   JSR div32
   LDA m_p
   STA t_step
   LDA m_p+1
   STA t_step+1
   RTS
tvd_z:
   STZ t_step
   STZ t_step+1
   RTS
tv_divm:                                ; (m_b = B - T, set by the caller)
   LDX t_part                           ; K: the part's (step 7i)
   LDA m_b+1                            ; step 7v: 0 < B - T <= 255: the step is
   BNE tvd_w                               ;  (m * RT_z[h] + 128) >> 8, two 8x8s
   LDY m_b                              ;  (RT 0: does not fit, the divide)
   BEQ tvd_z
   LDA mb6_pt_rp,X                      ; the part's z tables
   STA tvd_tl+2
   INC A
   STA tvd_th+2
   LDA mb6_pt_m,X
   STA m_a
tvd_tl:
   LDA mb6_rc,Y                         ; (patched: RT lo page)
   STA m_b
tvd_th:
   LDA mb6_rc,Y                         ; (patched: RT hi page)
   STA m_b+1
   ORA m_b
   BEQ tvd_rs                              ; (Y = h)
   QMUL m_a, m_b                        ; m * RT lo: hi in A, lo in mq_l
   STA t_step
   LDA mq_l
   CMP #$80                             ; C = the rounding bit (the hi byte
   LDA t_step                           ;  is <= $FE: no carry out)
   ADC #0
   STA t_step
   QMUL m_a, m_b+1                      ; m * RT hi (X, Y used)
   STA t_step+1
   CLC
   LDA mq_l
   ADC t_step
   STA t_step
   BCC :+
   INC t_step+1
:  RTS

; tv_v0: t_v from t_step (the line step), then t_step doubled (the pair's)
tv_v0:
   LDA r_ys                             ; m_a = (ys & ~1) - T (s16)
   AND #$FE
   SEC
   SBC q_t
   STA m_a
   LDA #0
   SBC q_t+1
   STA m_a+1
   ORA m_a
   BEQ @top                             ; the run starts at T: v = Vtop
   LDA m_a
   AND m_a+1
   CMP #$FF
   BNE @mul
   LDX t_part                           ; one line above T: v = Vtop - step
   SEC
   LDA mb6_pt_v0,X
   SBC t_step
   STA t_v
   LDA mb6_pt_v1,X
   SBC t_step+1
   STA t_v+1
   JMP @dbl
@top:
   LDX t_part
   LDA mb6_pt_v0,X
   STA t_v
   LDA mb6_pt_v1,X
   STA t_v+1
   JMP @dbl
@mul:
   LDA m_a+1                            ; step 7s: ys - T >= 512 (and B > T):
   CMP #2                               ;  + (ys - T) * f >> 8, f = the step's
   BCC @plain                           ;  next 8 bits, (r << 8) / (B - T) --
   BMI @plain
   LDA m_b+1                            ;  (ys - T) * step alone multiplies
   BMI @plain                           ;  the step's truncation into rows of
   ORA m_b                              ;  error on a near wall
   BEQ @plain
   JSR tv_fine                          ; (ys - T) * step, 8 bits finer
   BRA @add
@plain:
   LDA t_step                           ; m_a * step
   STA m_b
   LDA t_step+1
   STA m_b+1
   JSR mul16
@add:
   LDX t_part
   CLC
   LDA m_p
   ADC mb6_pt_v0,X
   STA t_v
   LDA m_p+1
   ADC mb6_pt_v1,X
   STA t_v+1
@dbl:
   ASL t_step                           ; step is per LINE: a pair moves 2 steps
   ROL t_step+1
   RTS

; tv_fine (step 7s): m_p = (ys - T) * step + ((ys - T) * f >> 8), m_a =
; ys - T, m_b = B - T; f = (r << 8) / (B - T), r = K mod (B - T)
tv_fine:
   LDA m_a                              ; r = K - step * (B - T), mod 2^16
   STA t_fp                             ;  (the step may come from a cache:
   LDA m_a+1                            ;  no remainder to hand); m_b = B - T
   STA t_fp+1                           ;  (the caller's)
   LDA m_b
   STA t_h
   LDA m_b+1
   STA t_h+1
   LDA t_step
   STA m_a
   LDA t_step+1
   STA m_a+1
   JSR mul16
   LDX t_part
   SEC
   LDA mb6_pt_k0,X
   SBC m_p
   STA m_r
   LDA mb6_pt_k1,X
   SBC m_p+1
   STA m_r+1
   LDA t_fp                             ; ys - T back
   STA m_a
   LDA t_fp+1
   STA m_a+1
   LDA t_h
   STA m_b
   LDA t_h+1
   STA m_b+1
   LDX #8                               ; f: 8 restoring steps (r < B - T)
tf_fl:
   ASL m_r
   ROL m_r+1
   BCS tf_fs                              ; (a 17th bit: certainly >= B - T)
   LDA m_r
   CMP m_b
   LDA m_r+1
   SBC m_b+1
   BCC tf_f0
tf_fs:
   LDA m_r
   SEC
   SBC m_b
   STA m_r
   LDA m_r+1
   SBC m_b+1
   STA m_r+1
   SEC
tf_f0:
   ROL t_f
   DEX
   BNE tf_fl
   LDA t_f                              ; (ys - T) * f
   STA m_b
   STZ m_b+1
   JSR mul16
   LDA m_p+1                            ; >> 8
   STA t_fp
   LDA m_p+2
   STA t_fp+1
   LDA t_step                           ; + (ys - T) * step
   STA m_b
   LDA t_step+1
   STA m_b+1
   JSR mul16
   CLC
   LDA m_p
   ADC t_fp
   STA m_p
   LDA m_p+1
   ADC t_fp+1
   STA m_p+1
   RTS

; ---- tcol: q_d (d) -> t_cl / t_ch, the texel column: u = ub * 16 - start
; + d; index = (u & mask) >> shift, as hi((u & mask) << (8 - shift)) ----
tcol:                                   ; (Y = 0: -> tw_ll / tw_lh; 2: ->
   PHY                                  ;  tw_rl / tw_rh, step 7i)
   LDX t_tid
   CLC                                  ; u = (ub * 16 - start) + d
   LDA cur_bl
   ADC q_d
   AND mb6_tp_ml,X
   STA m_a
   LDA cur_bh
   ADC q_d+1
   AND mb6_tp_mh,X
   LDY mb6_tp_sl,X
:  ASL m_a
   ROL A
   DEY
   BNE :-
   CLC                                  ; stored column index (main RAM:
   ADC mb6_tp_ixl,X                     ;  read with X clear)
   STA TP
   LDA mb6_tp_ixh,X
   ADC #0
   STA TP+1
   LDA (TP)
   PLY
.ifdef SERVER
   STA tw_ll,Y                          ; (SERVER: the column number)
   RTS
.endif
   PHA
   AND #7                               ; texel column = page + idx>>3,
   CLC                                  ;  byte idx&7, + row offset
   ADC mb6_tp_ro,X
   STA tw_ll,Y
   PLA
   LSR A
   LSR A
   LSR A
   CLC
   ADC mb6_tp_ph,X
   STA tw_lh,Y
   RTS

.ifndef SERVER                          ; (the screen: not on the fill server)
; ln_ptr: r_ys (unbiased line) -> PTR = the back buffer's byte column
; mf_x >> 2 at that line's character row, Y = r_ys & 7 (X preserved)
ln_ptr:
   LDA mf_x
   LSR A
   LSR A                                ; byte column k 0..63
ln_ptrk:                                ; (A = byte column k)
   PHA
   ASL A
   ASL A
   ASL A
   STA PTR                              ; (k * 8) & $FF
   PLA
   LSR A
   LSR A
   LSR A
   LSR A
   LSR A                                ; k >> 5 = (k * 8) >> 8
   STA PTR+1
   LDA r_ys
   LSR A
   LSR A
   LSR A
   ASL A                                ; (line >> 3) * 2 pages
   CLC
   ADC PTR+1
   ADC DV_BACKHI                        ; the buffer being drawn
   STA PTR+1
   LDA r_ys
   AND #7
   TAY
   RTS
.endif

; ============================================================================
; STEP 5: FLOORS AND CEILINGS (plane_ref.py is the executable spec). ONE
; texel read per 4x2 fat pixel: a plane byte is the 16x16 flat's byte at
; the byte column's centre and the line PAIR's centre, written whole (FLIP
; on the pair's odd line). Along a line pair a plane is affine, so per
; pair the row maths (4.12) gives U, V at byte column 32 and dU, dV, each
; rounded to 4.4 (pl_row, cached per frame in pc_*), and along a
; HORIZONTAL SPAN U += dU, V += dV per byte column, in 8 bits (see PLANES
; AS HORIZONTAL SPANS below).
; ============================================================================

.segment "MFILL"                        ; (HAZEL: entered with bank 7 paged)
; mf_frame: render_frame entry (bsp/walk.s, MASTER) -- new epoch and
; the view terms (from the view zero page the harness / driver
; staged; copied, so later scratch use of it cannot matter)
mf_frame:
   INC mf_ep
   BNE @ep
   LDX #VIEW_PAIRS - 1                  ; wrapped: no stale epoch may match
:  STZ pc_ep,X
   CPX #HZ_PAIR
   BCS :+
   STZ zr_ep,X
:  DEX
   BPL :--
   INC mf_ep
@ep:
   STZ pd_open                          ; no plane spans pending
   STZ pd_open+1
   LDA zp_br_smag
   STA pl_sm
   LDA zp_br_sone
   STA pl_s1
   LDA zp_br_sneg
   STA pl_sn
   LDA zp_br_cmag
   STA pl_cm
   LDA zp_br_cone
   STA pl_c1
   LDA zp_br_cneg
   STA pl_cn
   ; Up = mcx + px88 * 32, Vp = mcy - py88 * 32  (mod 2^16)
   LDA zp_br_px
   STA pl_a
   LDA zp_br_px_h
   STA pl_a+1
   LDX #5
:  ASL pl_a
   ROL pl_a+1
   DEX
   BNE :-
   LDA #BANK_C                          ; (the map centre terms: bank 6 tail)
   STA $FE30
   CLC
   LDA mb6_mcx
   ADC pl_a
   STA pl_up
   LDA mb6_mcx+1
   ADC pl_a+1
   STA pl_up+1
   LDA zp_br_py
   STA pl_a
   LDA zp_br_py_h
   STA pl_a+1
   LDX #5
:  ASL pl_a
   ROL pl_a+1
   DEX
   BNE :-
   SEC
   LDA mb6_mcy
   SBC pl_a
   STA pl_vp
   LDA mb6_mcy+1
   SBC pl_a+1
   STA pl_vp+1
   LDA #BANK_WALK                       ; render_frame's bank
   STA $FE30
   RTS

.segment "MB6C"
; pl_seg: per seg (tx_seg, pl_ff / pl_fc read from ANDY) -- the light masks,
; D = vz - fh / ch - vz (0 if the eye is not on the plane's side)
pl_seg:
   LDA pl_ff                            ; light: level = top 3 bits
   LSR A
   LSR A
   LSR A
   LSR A
   LSR A
   STA pl_lv
   LDA pl_ff
   AND #$1F
   STA pl_ff
   SEC
   LDA #0
   SBC zp_seg_bot_dlt                   ; vz - fh
   BVS :+
   BPL :++
:  LDA #0
:  STA pl_df
   LDA zp_seg_top_dlt                   ; ch - vz
   BPL :+
   LDA #0
:  STA pl_dc
.ifdef SERVER
   JMP fs_planes                        ; (the seg's planes: grid codes)
.else
   RTS
.endif

; the five light levels' masks (master_walls.LIGHT_MASKS): FF.FF, AA.FF,
; AA.AA, 0A.AA, 0A.0A -- progressively darker


; ceil_run / floor_run: [r_ys, r_ye] (biased) of the band's ceiling / floor
ceil_run:
   LDA pl_dc
   BEQ @shade
   LDA pl_fc
   CMP #$FF
   BEQ @shade
   STA pl_fl
   LDA pl_dc
   STA pl_d
   LDA sh_ceil
   STA pl_sh
   STZ pl_kind
   JMP prun
@shade:
   LDA sh_ceil
   JMP run

floor_run:
   LDA pl_df
   BEQ @shade
   STA pl_d
   LDA pl_ff
   STA pl_fl
   LDA #WB_FLOOR
   STA pl_sh
   LDA #1
   STA pl_kind
   JMP prun
@shade:
   LDA #WB_FLOOR
   JMP run

; ============================================================================
; PLANES AS HORIZONTAL SPANS. During the seg's column pass a textured plane
; run is only RECORDED: per byte column, the whole line PAIRS it covers
; (pe_*: one interval per kind per column). After the seg's last column
; (mf_planes) a MakeSpans sweep (DOOM's R_MakeSpans, on pairs) turns them
; into horizontal pair spans, drawn by sp_draw: per byte U += dU, V += dV
; (4.4), one texel read, two whole-byte writes (even line lit, odd line
; FLIP lit). Lines that are not a whole pair at a run's ends, a run on the
; wrong side of the horizon (its shade) and the rare second run of a kind
; in one column are drawn on the spot, a byte at a time.
; ============================================================================

; prun: a textured plane run [r_ys, r_ye] (biased), kind pl_kind
prun:
   LDA r_ye
   CMP r_ys
   BCS :+
   RTS                                  ; empty
:  SEC
   LDA r_ys
   SBC #Y_BIAS
   STA r_ys
   SEC
   LDA r_ye
   SBC #Y_BIAS
   STA r_ye
   LDA mf_x
   LSR A
   LSR A
   STA pl_kb
   ; the wrong side of the horizon keeps the shade (floor: lines < HZ_LINE;
   ; ceiling: lines >= HZ_LINE)
   LDA pl_kind
   BEQ @ceil
   LDA r_ys                             ; floor
   CMP #HZ_LINE
   BCS @parts
   LDA r_ye
   CMP #HZ_LINE
   BCC :+
   LDA #HZ_LINE - 1
:  JSR pl_shade                         ; lines r_ys .. min(ye, HZ_LINE - 1)
   LDA #HZ_LINE
   STA r_ys
   BRA @chk
@ceil:
   LDA r_ye
   CMP #HZ_LINE
   BCC @parts
   LDA r_ys
   PHA
   CMP #HZ_LINE
   BCS :+
   LDA #HZ_LINE
   STA r_ys
:  LDA r_ye
   JSR pl_shade                         ; lines max(ys, HZ_LINE) .. ye
   PLA
   STA r_ys
   LDA #HZ_LINE - 1
   STA r_ye
@chk:
   LDA r_ye
   CMP r_ys
   BCS @parts
   RTS
.ifndef SERVER                          ; (the screen: not on the fill server)
@parts:
   ; an odd first line / even last line: a single line on the spot
   LDA r_ys
   LSR A
   BCC :+
   LDA r_ys
   JSR pl_part
   INC r_ys
   LDA r_ye
   CMP r_ys
   BCS :+
   RTS
:  LDA r_ye
   LSR A
   BCS :+
   LDA r_ye
   JSR pl_part
   LDA r_ye
   BEQ @rts
   DEC r_ye
   CMP r_ys                             ; (old ye vs ys: ye-1 < ys?)
   BEQ @rts
   BCC @rts
:  ; whole pairs ys >> 1 .. ye >> 1: record them, or draw them now if this
   ; column already has a run of this kind
   LDA r_ys
   LSR A
   STA pl_p0
   LDA r_ye
   LSR A
   STA pl_p1
   LDX pl_kb
   LDA pl_kind
   BEQ @rc
   LDA pe_ft,X
   CMP #$FF
   BNE @now
   LDA pl_p0
   STA pe_ft,X
   LDA pl_p1
   STA pe_fb,X
@rts:
   RTS
@rc:
   LDA pe_ct,X
   CMP #$FF
   BNE @now
   LDA pl_p0
   STA pe_ct,X
   LDA pl_p1
   STA pe_cb,X
   RTS
@now:
   LDA pl_p0
   STA pl_p
:  JSR pl_pair
   LDA pl_p
   CMP pl_p1
   INC pl_p
   BCC :-
   RTS
.else
@parts:                                 ; (SERVER: lines r_ys .. r_ye are the
   LDA #$80                             ;  plane fs_planes named for this
   JMP fs_mark                          ;  kind: A = $80 says so)
.endif

; pl_shade: lines r_ys .. A (unbiased) in the plane's solid shade (r_ys,
; r_ye kept)
pl_shade:
   LDX r_ye
   STX pl_ye0
   STA r_ye
   LDA pl_sh
   STA r_part
   JSR hz_run
   LDA pl_ye0
   STA r_ye
   RTS

.ifndef SERVER                          ; (the screen: not on the fill server)
; pl_cell: A = the texel byte at pair pl_p, byte column pl_kb (X clear)
pl_cell:
   JSR pl_rowc
   JSR pl_lvl                           ; step 7n: the far tone?
   BCS pl_cfar
   JSR uvat
   LDX pl_u                             ; texel (V >> 4) * 16 + (U >> 4)
   LDA pl_v
   AND #$F0
   ORA hi16,X
   TAY
   LDX pl_fl
   LDA mb6_fl_page,X                    ; (bank 6 tail, before paging)
   STA pc_rd+2
   LDA mb6_fl_bank,X
   JMP pc_go

; pl_cfar: pl_cell in the far tone (step 7n): the flat's one byte
pl_cfar:
   LDX pl_fl
   LDA far_tone,X
   RTS

; pl_lvl: C = 1 if line pair pl_p (its row made: pl_rowc) draws its
; flat's far tone (step 7n; pl_row: D >= FAR_DM[k >> 1])
pl_lvl:
   LDX pl_p
   LDA pc_lv,X
   LSR A
   RTS

.segment "MFILL"
pc_go:                                  ; (HAZEL: bank 6 goes out)
   STA $FE30                            ; the flat's bank
pc_rd:
   LDA $FF00,Y                          ; (patched: the flat's page)
   LDX #BANK_C
   STX $FE30
   RTS
.endif

.segment "MB6C"

; pl_rowc: make sure pc_*[pl_p] holds this frame's row for pl_d
pl_rowc:
   LDX pl_p
   LDA pc_ep,X
   CMP mf_ep
   BNE :+
   LDA pc_d,X
   CMP pl_d
   BEQ @rts
:  JMP pl_row
@rts:
   RTS

.ifndef SERVER                          ; (the screen: not on the fill server)
; pl_line: A = one line (unbiased) of column pl_kb: its pair's texel, lit
pl_line:
   STA pl_y
   LSR A
   STA pl_p
   JSR pl_cell
   STA t_ev
   LDA pl_y
   JSR pl_wr1
   RTS

; pl_pair: pair pl_p of column pl_kb, both lines (one texel read)
pl_pair:
   JSR pl_cell
   STA t_ev
   LDA pl_p
   ASL A
   JSR pl_wr1
   LDA pl_p
   SEC
   ROL A
   ; fall into pl_wr1

; pl_wr1: A = line; t_ev = the texel byte: write it at column pl_kb (flip[]
; of it on an odd line)
pl_wr1:
   STA pl_y
   LDX r_ys                             ; (ln_ptrk reads r_ys: lend it)
   PHX
   STA r_ys
   LDA pl_kb
   JSR ln_ptrk
   PLX
   STX r_ys
   LDA #ACC_DXY
   STA $FE34
   LDA pl_y
   LSR A                                ; C = odd
   LDA t_ev
   BCC :+
   TAX
   LDA flip,X
:  STA (PTR),Y
   LDA #ACC_DY
   STA $FE34
   RTS
.endif

; mf_planes: the seg's end. Its plane spans stay pending (pd_*): a later
; seg on the same plane widens them; another plane, or the frame's end
; (mf_flush), draws them.
mf_planes:
   RTS

.ifndef SERVER                          ; (the screen: not on the fill server)
.segment "MFILL"
; ---- sp_go2 / sl_go: the span loops (HAZEL: they page the flat's bank).
; sp_setup has set U, V, PTR, Y, the patched steps and page; A = the
; flat's bank. sp_go2 writes a line PAIR per byte (even line the texel,
; odd line flip[] of it); sl_go ONE line (sl_draw patches sl_fb to take
; the flip on an odd line).
; sp_go2 is Duff's device: four bytes unrolled (sf_lp), entered at one of
; four points (sf_eq, q = -n & 3) so the span's last byte ends a block.
; PTR points at the block's first column (+ the line within the character
; row), so each body writes at fixed offsets (LDY #8c, INY; column 0:
; STA (PTR), LDY #1) and only the block end moves PTR on, by 32. Each body is [U, V stepped, Y = U] sf_eq:
; [texel read, both lines written]: an entry skips its step, so the last
; byte's never runs. One count per block.
sp_go2:
   STA $FE30                            ; the flat's bank
   LDA #ACC_DXY
   STA $FE34
   LDA sp_n                             ; blocks = ceil(n / 4)
   CLC
   ADC #3
   LSR A
   LSR A
   STA sf_nb
   LDA #0                               ; q = -n & 3: the last byte ends a
   SEC                                  ;  block
   SBC sp_n
   AND #3
   ASL A
   TAX                                  ; X = q * 2
   ASL A
   ASL A
   STA sf_q                             ; q * 8
   TYA                                  ; PTR (lo 0) + Y - 8q: the block's
   SEC                                  ;  column 0 (q columns before the
   SBC sf_q                             ;  span's first)
   STA PTR
   LDA PTR+1
   SBC #0
   STA PTR+1
   LDY su                               ; (Y = U, A = V at an entry, as a
   LDA sv                               ;  step leaves them)
   JMP (sf_ent,X)
sf_lp:
sf_e0:
   AND #$F0                             ; texel (V >> 4) * 16 + (U >> 4)
   ORA hi16,Y                           ; (Y = U: the step leaves it)
   TAX
sf_r0:
   LDA $FF00,X                          ; (patched: the flat's page)
   STA (PTR)                            ; even line: whole byte (column 0)
   LDY #1
   TAX
   LDA flip,X
   STA (PTR),Y                          ; odd line: FLIP
   CLC
   LDA su
   ADC sdu
   STA su
   TAY                                  ; U, for the next texel
   CLC
   LDA sv
   ADC sdv
   STA sv
sf_e1:
   AND #$F0                             ; texel (V >> 4) * 16 + (U >> 4)
   ORA hi16,Y                           ; (Y = U: the step leaves it)
   TAX
sf_r1:
   LDA $FF00,X                          ; (patched: the flat's page)
   LDY #8
   STA (PTR),Y                          ; even line: whole byte
   INY
   TAX
   LDA flip,X
   STA (PTR),Y                          ; odd line: FLIP
   CLC
   LDA su
   ADC sdu
   STA su
   TAY                                  ; U, for the next texel
   CLC
   LDA sv
   ADC sdv
   STA sv
sf_e2:
   AND #$F0                             ; texel (V >> 4) * 16 + (U >> 4)
   ORA hi16,Y                           ; (Y = U: the step leaves it)
   TAX
sf_r2:
   LDA $FF00,X                          ; (patched: the flat's page)
   LDY #16
   STA (PTR),Y                          ; even line: whole byte
   INY
   TAX
   LDA flip,X
   STA (PTR),Y                          ; odd line: FLIP
   CLC
   LDA su
   ADC sdu
   STA su
   TAY                                  ; U, for the next texel
   CLC
   LDA sv
   ADC sdv
   STA sv
sf_e3:
   AND #$F0                             ; texel (V >> 4) * 16 + (U >> 4)
   ORA hi16,Y                           ; (Y = U: the step leaves it)
   TAX
sf_r3:
   LDA $FF00,X                          ; (patched: the flat's page)
   LDY #24
   STA (PTR),Y                          ; even line: whole byte
   INY
   TAX
   LDA flip,X
   STA (PTR),Y                          ; odd line: FLIP
   DEC sf_nb
   BEQ sf_end
   CLC                                  ; the next four columns
   LDA PTR
   ADC #32
   STA PTR
   BCC :+
   INC PTR+1
:  CLC
   LDA su
   ADC sdu
   STA su
   TAY                                  ; U, for the next texel
   CLC
   LDA sv
   ADC sdv
   STA sv
   JMP sf_lp
sf_end:
   LDA #ACC_DY
   STA $FE34
   LDA #BANK_C
   STA $FE30
   RTS
sf_ent:
   .word sf_e0, sf_e1, sf_e2, sf_e3

sl_go:
   STA $FE30                            ; the flat's bank
   LDA #ACC_DXY
   STA $FE34
sl_lp:
   LDX su                               ; texel (V >> 4) * 16 + (U >> 4)
   LDA sv
   AND #$F0
   ORA hi16,X
   TAX
sl_rd:
   LDA $FF00,X                          ; (patched: the flat's page)
sl_fb:
   BRA sl_wr                            ; (patched: 0 on an odd line, FLIP)
   TAX
   LDA flip,X
sl_wr:
   STA (PTR),Y
   TYA
   CLC
   ADC #8                               ; the next byte column
   TAY
   BCC :+
   INC PTR+1
:  CLC
   LDA su
   ADC sdu
   STA su
   CLC
   LDA sv
   ADC sdv
   STA sv
   DEC sp_n
   BNE sl_lp
   LDA #ACC_DY
   STA $FE34
   LDA #BANK_C
   STA $FE30
   RTS
.endif

.segment "MB6C"
; uvat: pl_u, pl_v = U, V (4.4) at byte column pl_kb of pair pl_p's row:
; Uc + (kb - 32) * dU (mod 2^8), one 8 x 8 multiply each
uvat:                                   ; (step 7p: the two products share
   LDA pl_kb                            ;  their multiplier a = kb - 32: its
   SEC                                  ;  quarter-square offsets patched once,
   SBC #32                              ;  as mul8x32; only the low bytes)
   STA mq_b
   STA uv_p1+1                          ; f(e + a) at SQR_LO + a (SQR2_LO
   STA uv_p2+1                          ;  above it for e + a >= 256)
   SEC
   LDA #0
   SBC mq_b
   STA uv_m1+1                          ; f(|e - a|) at SQR_LO - a (the mirror
   STA uv_m2+1                          ;  page below for e < a)
   LDA #>SQR_LO
   SBC #0
   STA uv_m1+2
   STA uv_m2+2
   LDX pl_p
   LDY pc_du,X                          ; u = Uc + a * dU (mod 256)
   SEC
uv_p1:
   LDA SQR_LO,Y                         ; (patched lo: a)
uv_m1:
   SBC SQR_LO,Y                         ; (patched: SQR_LO - a)
   CLC
   ADC pc_uc,X
   STA pl_u
   LDY pc_dv,X                          ; v = Vc + a * dV (mod 256)
   SEC
uv_p2:
   LDA SQR_LO,Y                         ; (patched lo: a)
uv_m2:
   SBC SQR_LO,Y                         ; (patched: SQR_LO - a)
   CLC
   ADC pc_vc,X
   STA pl_v
   RTS

; pl_prod: pl_q (5) = pl_e * A, or pl_e << 8 when X (unity) is non-zero
pl_prod:
   CPX #0
   BEQ mul8x32
   STZ pl_q
   LDX #3
:  LDA pl_e,X
   STA pl_q+1,X
   DEX
   BPL :-
   RTS

; mul8x32: pl_q (5) = pl_e (4) * A (8): four quarter-square 8x8s
mul8x32:
   STA mq_b                             ; quarter squares, the tables' offsets
   STA m8_p1+1                          ;  patched once: f(e + A) at SQR_LO+A,
   STA m8_p2+1                          ;  f(|e - A|) at SQR_LO-A (the mirror
   SEC                                  ;  page below serves e < A), each byte
   LDA #0                               ;  of pl_e then four indexed reads
   SBC mq_b
   STA m8_m1+1
   STA m8_m2+1
   LDA #>SQR_LO
   SBC #0                               ; (the page below unless A = 0)
   STA m8_m1+2
   CLC
   ADC #>(SQR_HI - SQR_LO)
   STA m8_m2+2
; m8_run: pl_q = pl_e * the multiplier already patched (step 7q: pl_row's
; second product reuses its first's D)
m8_run:
   STZ pl_q
   STZ pl_q+1
   STZ pl_q+2
   STZ pl_q+3
   STZ pl_q+4
   LDX #0
m8_lp:
   LDY pl_e,X
   BEQ m8_nx                              ; zero byte: adds nothing
   SEC
m8_p1:
   LDA SQR_LO,Y                         ; (patched lo: A)
m8_m1:
   SBC SQR_LO,Y                         ; (patched: SQR_LO - A)
   STA mq_l
m8_p2:
   LDA SQR_HI,Y                         ; (patched lo: A)
m8_m2:
   SBC SQR_HI,Y                         ; (patched: SQR_HI - A)
   TAY                                  ; Y = hi
   CLC                                  ; pl_q+i+1 is still clear: no
   LDA mq_l                             ;  carry out of it
   ADC pl_q,X
   STA pl_q,X
   TYA
   ADC pl_q+1,X
   STA pl_q+1,X
m8_nx:
   INX
   CPX #4
   BNE m8_lp
   RTS


; ============================================================================
; Cold set-up code in MAIN RAM (the HAZEL code area is full): routines that
; only ever run with ACCCON X clear -- never inside a screen-write loop --
; may live below $8000. MFILLV: the tail of the object dispatch page
; (VPTABM; objects are off). Step 7i: tx_seg moved from MFILLM (the
; clipper's slack, full) to bank 6, which now starts at $9000 -- above
; ANDY's $8000-$8FFF, so it runs with ANDY paged.
; ============================================================================
.segment "MB6C"
; ---- tx_seg ----------------------------------------------------------------
tx_seg:
.ifndef SERVER                          ; (SERVER: tx_slot is the request's)
   ; slot = (hdr hi - >ROM_SEG_HDR_C) * HDR_PER_PAGE + hdr lo / LAY_HDR_STRIDE
   STZ tx_slot
   STZ tx_slot+1
   SEC
   LDA zp_seg_hdr_p+1
   SBC #>ROM_SEG_HDR_C
   TAX
   BEQ @lo
@pg:
   CLC
   LDA tx_slot
   ADC #HDR_PER_PAGE
   STA tx_slot
   BCC :+
   INC tx_slot+1
:  DEX
   BNE @pg
@lo:
   LDA zp_seg_hdr_p
@l9:
   CMP #LAY_HDR_STRIDE
   BCC @slot
   SBC #LAY_HDR_STRIDE                  ; (C = 1)
   INC tx_slot
   BNE @l9
   INC tx_slot+1
   BRA @l9
@slot:
.endif
   ; ---- ANDY: the slot's dressing and length ----
   LDA #BANK_C | $80                    ; ANDY over $8000-$8FFF
   STA $FE30
   CLC
   LDA #<man_slot_d
   ADC tx_slot
   STA TP
   LDA #>man_slot_d
   ADC tx_slot+1
   STA TP+1
   LDA (TP)
   STA t_k                              ; slot byte
   CLC
   LDA TP+1
   ADC #>(man_slot_ll - man_slot_d)
   STA TP+1
   LDA (TP)
   STA tx_l16
   CLC
   LDA TP+1
   ADC #>(man_slot_lh - man_slot_ll)
   STA TP+1
   LDA (TP)
   STA tx_l16+1
   LDA t_k
   CMP man_ndress
   BCS @merged
   TAX                                  ; one piece
   LDY #0
   JSR ld_dress
   STZ pc_sl
   STZ pc_sh
   LDA #1
   STA pc_n
   BRA @andy_out
@merged:
   SBC man_ndress                       ; (C = 1) merged-list id
   TAX
   LDA man_pl_n,X
   STA pc_n
   LDA man_pl_first,X
   STA t_k
   LDY #0
@pl:
   LDX t_k
   LDA man_pc_sl,X
   STA pc_sl,Y
   LDA man_pc_sh,X
   STA pc_sh,Y
   LDA man_pc_dr,X
   TAX
   JSR ld_dress
   INC t_k
   INY
   CPY pc_n
   BCC @pl
@andy_out:
   LDX zp_node_ch_l                     ; step 5: the subsector's flats
   LDA man_ss_ff,X                      ; (top 3 bits: the light level)
   STA pl_ff
   LDA man_ss_fc,X
   STA pl_fc
   LDA #BANK_C                          ; back to the cascade's bank
   STA $FE30
   JSR pl_seg
   LDX #0
   JSR set_cur                          ; piece 0 until a strip says otherwise
   ; ---- d at the projected ends: 0 and L16, or from the crossing t ----
   STZ tx_d1
   STZ tx_d1+1
   LDA tx_l16
   STA tx_d2
   LDA tx_l16+1
   STA tx_d2+1
   LDA zp_seg_v1_clipped
   BEQ @c2
   JSR l16t                             ; d1 = (L16 * t + 128) >> 8
   LDA m_p+1
   STA tx_d1
   LDA m_p+2
   STA tx_d1+1
   BRA @w
@c2:
   LDA zp_seg_v2_clipped
   BEQ @w
   JSR l16t                             ; d2 = L16 - ((L16 * t + 128) >> 8)
   SEC
   LDA tx_l16
   SBC m_p+1
   STA tx_d2
   LDA tx_l16+1
   SBC m_p+2
   STA tx_d2+1
@w:
   ; ---- weights: (256 + M8) << (Smax - S), both to 16 bits ----
   LDA zp_seg_v1_r_s
   CMP zp_seg_v2_r_s
   BCS :+
   LDA zp_seg_v2_r_s
:  STA t_sx
   LDA zp_seg_v1_r_m8
   STA tx_wa
   LDA #1
   STA tx_wa+1
   STZ tx_wa+2
   SEC
   LDA t_sx
   SBC zp_seg_v1_r_s
   TAX
   BEQ @wb
:  ASL tx_wa
   ROL tx_wa+1
   ROL tx_wa+2
   DEX
   BNE :-
@wb:
   LDA zp_seg_v2_r_m8
   STA tx_wb
   LDA #1
   STA tx_wb+1
   STZ tx_wb+2
   SEC
   LDA t_sx
   SBC zp_seg_v2_r_s
   TAX
   BEQ @wn
:  ASL tx_wb
   ROL tx_wb+1
   ROL tx_wb+2
   DEX
   BNE :-
@wn:
   LDA tx_wa+2
   ORA tx_wb+2
   BEQ @ends
   LSR tx_wa+2
   ROR tx_wa+1
   ROR tx_wa
   LSR tx_wb+2
   ROR tx_wb+1
   ROR tx_wb
   BRA @wn
@ends:
   ; ---- first / last strip centres ----
   LDA mf_x
   INC A
   STA tx_xl
   LDA mf_hi
   DEC A
   AND #$FE                             ; last even pixel < hi
   INC A
   STA tx_xh
   LDA tx_xl
   JSR at                               ; -> m_p: exact d, at_n: raw weight
   LDA m_p
   STA tx_dl
   LDA m_p+1
   STA tx_dl+1
   LDX #4
:  LDA at_n,X
   STA tx_ra,X
   DEX
   BPL :-
   LDA tx_xh
   JSR at
   LDA m_p
   STA tx_dh
   LDA m_p+1
   STA tx_dh+1
   LDX #4
:  LDA at_n,X
   STA tx_rb,X
   DEX
   BPL :-
   ; A, B: the raw weights shifted together until both fit a byte. Step
   ; 7i: while either is >= 2^16 at least 9 more shifts are due, so a
   ; whole byte goes at once (the same result as 8 single shifts)
@ab:
   LDA tx_ra+2
   ORA tx_ra+3
   ORA tx_ra+4
   ORA tx_rb+2
   ORA tx_rb+3
   ORA tx_rb+4
   BEQ @abb
   LDX #0
:  LDA tx_ra+1,X
   STA tx_ra,X
   LDA tx_rb+1,X
   STA tx_rb,X
   INX
   CPX #4
   BNE :-
   STZ tx_ra+4
   STZ tx_rb+4
   BRA @ab
@abb:                                   ; < 2^16: bit by bit (two bytes)
   LDA tx_ra+1
   ORA tx_rb+1
   BEQ @abok
   LSR tx_ra+1
   ROR tx_ra
   LSR tx_rb+1
   ROR tx_rb
   BRA @abb
@abok:
   ; den0 = A * (xh - xl); n0 = dL * den0
   LDA tx_ra
   STA m_a
   STZ m_a+1
   SEC
   LDA tx_xh
   SBC tx_xl
   STA m_b
   STZ m_b+1
   JSR mul16
   LDA m_p
   STA tx_den
   STA m_b
   LDA m_p+1
   STA tx_den+1
   STA m_b+1
   LDA tx_dl
   STA m_a
   LDA tx_dl+1
   STA m_a+1
   JSR mul16
   LDX #3
:  LDA m_p,X
   STA tx_n,X
   DEX
   BPL :-
   ; dden = 4 * (B - A)  (s16: per 4-pixel byte)
   SEC
   LDA tx_rb
   SBC tx_ra
   STA tx_dden
   LDA #0
   SBC #0
   STA tx_dden+1
   ASL tx_dden                          ; (x 2: per strip pair; x 2 again:
   ROL tx_dden+1                        ;  one step per 4-pixel byte)
   ASL tx_dden
   ROL tx_dden+1
   ; dn = 4 * (dH * B - dL * A)  (s32: per 4-pixel byte)
   LDA tx_dh
   STA m_a
   LDA tx_dh+1
   STA m_a+1
   LDA tx_rb
   STA m_b
   STZ m_b+1
   JSR mul16
   LDX #3
:  LDA m_p,X
   STA t_tmp,X
   DEX
   BPL :-
   LDA tx_dl
   STA m_a
   LDA tx_dl+1
   STA m_a+1
   LDA tx_ra
   STA m_b
   STZ m_b+1
   JSR mul16
   SEC
   LDX #0
:  LDA t_tmp,X
   SBC m_p,X
   STA tx_dn,X
   INX
   TXA                                  ; (keeps C)
   EOR #4
   BNE :-
   ASL tx_dn
   ROL tx_dn+1
   ROL tx_dn+2
   ROL tx_dn+3
   ASL tx_dn                            ; (per 4-pixel byte)
   ROL tx_dn+1
   ROL tx_dn+2
   ROL tx_dn+3
.ifndef SERVER
   JMP pe_init                          ; the plane extents, empty
.else
   RTS
.endif


.segment "MFILLV"

; 2^20 // k for k = 2j + 1, j = 0..HZ_PAIR - 1 (the depth of a pair k lines off the
; horizon, 1024 * depth / D), three byte planes
pl_zk0:
.repeat HZ_PAIR, J
   .byte <((1 << 20) / (2 * J + 1))
.endrepeat
pl_zk1:
.repeat HZ_PAIR, J
   .byte >((1 << 20) / (2 * J + 1))
.endrepeat
pl_zk2:
.repeat HZ_PAIR, J
   .byte ^((1 << 20) / (2 * J + 1))
.endrepeat


.segment "MFMAIN"                       ; main RAM (ACCCON X clear only)
pp_all:  .res 256                       ; partial lines per column, 4 slots of
                                        ;  64 (kind * 2 + odd; $FF none)
pd_open: .res 2                         ; per kind (0 ceiling, 1 floor): the
pd_d:    .res 2                         ;  pending spans' plane (D, flat,
pd_fl:   .res 2                         ;  light level) and byte columns
pd_lv:   .res 2
pd_k0:   .res 2
pd_k1:   .res 2
sk0:     .res 1                         ; pe_init: the seg's byte columns
sk1:     .res 1
ck0:     .res 1                         ; pe_clr: the columns to clear
ck1:     .res 1
pk_kind: .res 1                         ; pe_kind: the kind and its plane
pk_d:    .res 1
pk_fl:   .res 1
fl_kind: .res 1                         ; pd_flush / pp_slot state
pp_s:    .res 1
pp_base: .res 1
pp_k:    .res 1
pp_k1:   .res 1
zr_ep:   .res HZ_PAIR                        ; per row j: the epoch ZC / ZS are for
zc0:     .res HZ_PAIR                        ; ZC[j] = Zk * |c| (4 bytes)
zc1:     .res HZ_PAIR
zc2:     .res HZ_PAIR
zc3:     .res HZ_PAIR
zs0:     .res HZ_PAIR                        ; ZS[j] = Zk * |s|
zs1:     .res HZ_PAIR
zs2:     .res HZ_PAIR
zs3:     .res HZ_PAIR

.segment "MFILLC"                       ; main RAM code: runs with ACCCON X
                                        ;  clear (row maths, span sweeps)
; pl_row: the row maths for pair pl_p and D pl_d, into pc_*[pl_p]
;   k = |2p + 1 - HZ_LINE| (j = k >> 1 indexes pl_zk = 2^20 // k)
;   Pc = D * ZC[j], Ps = D * ZS[j], ZC = Zk * |c|, ZS = Zk * |s| (unity:
;   Zk << 8), made once per frame per j (zr_*): the same integers as
;   (D * Zk) * |c|, one multiply per term
;   A = +-(Pc >> 8), hV = +-(Pc >> 14), Bs = +-(Ps >> 8), hU = +-(Ps >> 14)
;   at byte column 32: U = Up + A + hU, V = Vp - Bs + hV; dU = 2 hU,
;   dV = 2 hV; all four rounded to 4.4 (the high byte, + low >= $80)
pl_row:
   LDA pl_p
   SEC
   SBC #HZ_PAIR
   BCS :+                               ; floor: j = p - HZ_PAIR
   EOR #$FF                             ; ceiling: j = HZ_PAIR - 1 - p
:  TAX                                  ; X = j
   LDY pl_p
   LDA mf_ep                            ; this frame's row for D
   STA pc_ep,Y
   LDA pl_d
   STA pc_d,Y
   CMP far_dm,X                         ; step 7n: far iff D >= FAR_DM[j]
   LDA #0
   ROL A
   STA pc_lv,Y
.ifndef SERVER                          ; (SERVER: the list joins neighbouring
   BEQ :+                               ;  far cells on their row maths too)
   RTS                                  ; a far row: its U, V are never read
:
.endif
   STX pl_j
   LDA zr_ep,X
   CMP mf_ep
   BEQ :+
   JSR pl_zrow                          ; this frame's ZC[j], ZS[j]
   LDX pl_j
:  LDA zc0,X                            ; cos: Pc = D * ZC[j]
   STA pl_e
   LDA zc1,X
   STA pl_e+1
   LDA zc2,X
   STA pl_e+2
   LDA zc3,X
   STA pl_e+3
   LDA pl_d
   JSR mul8x32
   JSR pl_hq                            ; hV = Pc >> 14 (A = Pc >> 8: pl_q+1)
   LDX pl_p
   LDA pl_cn                            ; the cos sign folded into the sums
   BNE @cn
   JSR pl_dh                            ; dV = round(2 hV)
   STA pc_dv,X
   CLC                                  ; Uc = Up + A, Vc = Vp + hV
   LDA pl_up
   ADC pl_q+1
   STA pl_uc
   LDA pl_up+1
   ADC pl_q+2
   STA pl_uc+1
   CLC
   LDA pl_vp
   ADC pl_h
   STA pl_vc
   LDA pl_vp+1
   ADC pl_h+1
   STA pl_vc+1
   BRA @sin
@cn:
   JSR pl_dhn                           ; dV = round(-2 hV)
   STA pc_dv,X
   SEC                                  ; Uc = Up - A, Vc = Vp - hV
   LDA pl_up
   SBC pl_q+1
   STA pl_uc
   LDA pl_up+1
   SBC pl_q+2
   STA pl_uc+1
   SEC
   LDA pl_vp
   SBC pl_h
   STA pl_vc
   LDA pl_vp+1
   SBC pl_h+1
   STA pl_vc+1
@sin:                                   ; sin: Ps = D * ZS[j]
   LDX pl_j
   LDA zs0,X
   STA pl_e
   LDA zs1,X
   STA pl_e+1
   LDA zs2,X
   STA pl_e+2
   LDA zs3,X
   STA pl_e+3
   JSR m8_run                           ; (D still patched: step 7q)
   JSR pl_hq                            ; hU = Ps >> 14 (Bs = Ps >> 8: pl_q+1)
   LDX pl_p
   LDA pl_sn
   BNE @sn
   JSR pl_dh                            ; dU = round(2 hU)
   STA pc_du,X
   CLC                                  ; Uc += hU, Vc -= Bs
   LDA pl_uc
   ADC pl_h
   STA pl_uc
   LDA pl_uc+1
   ADC pl_h+1
   STA pl_uc+1
   SEC
   LDA pl_vc
   SBC pl_q+1
   STA pl_vc
   LDA pl_vc+1
   SBC pl_q+2
   BRA @rnd
@sn:
   JSR pl_dhn                           ; dU = round(-2 hU)
   STA pc_du,X
   SEC                                  ; Uc -= hU, Vc += Bs
   LDA pl_uc
   SBC pl_h
   STA pl_uc
   LDA pl_uc+1
   SBC pl_h+1
   STA pl_uc+1
   CLC
   LDA pl_vc
   ADC pl_q+1
   STA pl_vc
   LDA pl_vc+1
   ADC pl_q+2
@rnd:                                   ; A = Vc hi: both rounded to 4.4
   LDY pl_vc                            ;  (the high byte, + low >= $80)
   CPY #$80
   ADC #0
   STA pc_vc,X
   LDA pl_uc
   CMP #$80
   LDA pl_uc+1
   ADC #0
   STA pc_uc,X
   RTS

.segment "MARITH"                       ; (main RAM: room; ACCCON X clear)
; pl_hq: pl_h = (pl_q >> 14) mod 2^16 (the product's bits 14-29)
pl_hq:
   LDA pl_q+2
   STA pl_h
   LDA pl_q+3
   STA pl_h+1
   LDA pl_q+1
   ASL A
   ROL pl_h
   ROL pl_h+1
   ASL A
   ROL pl_h
   ROL pl_h+1
   RTS

; pl_dh: A = 2 pl_h (mod 2^16) rounded to 4.4; pl_dhn: the same of -2 pl_h
pl_dh:
   LDA pl_h
   ASL A
   TAY
   LDA pl_h+1
   ROL A
   CPY #$80
   ADC #0
   RTS
pl_dhn:
   LDA pl_h
   ASL A
   STA pl_a                             ; (scratch: 2 h)
   LDA pl_h+1
   ROL A
   STA pl_a+1
   SEC
   LDA #0
   SBC pl_a
   TAY
   LDA #0
   SBC pl_a+1
   CPY #$80
   ADC #0
   RTS
.segment "MFILLC"

.ifndef SERVER                          ; (the screen: not on the fill server)
.segment "MB6C"                         ; (bank 6: the sweep and set-up)
; mk_spans: R_MakeSpans over columns pl_k0 .. pl_k1 (+ an empty sentinel):
; (t1, b1) the previous column's pair interval, (t2, b2) this one's;
; pairs leaving close a span ending at the previous column, pairs
; arriving open one here (sp_start). Empty = ($FF, 0).
mk_spans:
   LDA #$FF
   STA mk_t1
   STZ mk_b1
   LDX pl_k1                            ; the sentinel column k1 + 1
   INX
   STX mk_ke
   LDA pl_kind                          ; step 7r: the kind's interval reads
   BEQ mks_c                               ;  patched once, not tested per column
   LDA #<pe_ft
   LDX #>pe_ft
   LDY #<pe_fb
   BRA mks_pt
mks_c:
   LDA #<pe_ct
   LDX #>pe_ct
   LDY #<pe_cb
mks_pt:
   STA mk_rt+1
   STA mk_rt2+1
   STX mk_rt+2
   STX mk_rt2+2
   STY mk_rb+1
   STY mk_rb2+1
   STX mk_rb+2                          ; (the four arrays share a page:
   STX mk_rb2+2                         ;  asserted below)
   LDX pl_k0
   CPX mk_ke
   BCS mks_sent                            ; (an empty range: just the sentinel)
   BRA mks_rd
mks_scan:                                  ; the same interval as the last column:
   INX                                  ;  nothing closes or opens
   CPX mk_ke
   BEQ mks_sent
mks_rd:
mk_rb:
   LDA pe_fb,X                          ; (patched: the kind's bottoms)
   CMP mk_b1
   BNE mks_chg
mk_rt:
   LDA pe_ft,X                          ; (patched: the kind's tops)
   CMP mk_t1
   BEQ mks_scan
mks_chg:
   STX mk_k
mk_rt2:
   LDA pe_ft,X                          ; (patched: tops) this column's
   STA mk_t2                            ;  interval
mk_rb2:
   LDA pe_fb,X                          ; (patched: bottoms)
   STA mk_b2
   JSR mk_step
   LDX mk_k
   BRA mks_scan
mks_sent:                                  ; past the last: the empty sentinel
   STX mk_k                             ;  closes everything
   LDA #$FF
   STA mk_t2
   STZ mk_b2
   ; fall into mk_step

.assert >pe_fb = >pe_ft && >pe_cb = >pe_ct, error, "pe_*t, pe_*b must share a page"

; mk_step: column mk_k's interval (t2, b2) after (t1, b1): close the pairs
; leaving, open the pairs arriving; then (t1, b1) = (t2, b2)
mk_step:
@l1:                                    ; while t1 < t2 and t1 <= b1: close t1
   LDA mk_t1
   CMP mk_t2
   BCS @l2
   LDA mk_b1
   CMP mk_t1
   BCC @l2
   LDA mk_t1
   JSR mk_close
   INC mk_t1
   BRA @l1
@l2:                                    ; while b1 > b2 and b1 >= t1: close b1
   LDA mk_b2
   CMP mk_b1
   BCS @l3
   LDA mk_b1
   CMP mk_t1
   BCC @l3
   JSR mk_close
   DEC mk_b1
   BRA @l2
@l3:                                    ; while t2 < t1 and t2 <= b2: open t2
   LDA mk_t2
   PHA
@l3l:
   LDA mk_t2
   CMP mk_t1
   BCS @l4
   LDA mk_b2
   CMP mk_t2
   BCC @l4
   LDX mk_t2
   LDA mk_k
   STA sp_start,X
   INC mk_t2
   BRA @l3l
@l4:                                    ; while b2 > b1 and b2 >= t2: open b2
   LDA mk_b2
   PHA
@l4l:
   LDA mk_b1
   CMP mk_b2
   BCS @nx
   LDA mk_b2
   CMP mk_t2
   BCC @nx
   LDX mk_b2
   LDA mk_k
   STA sp_start,X
   DEC mk_b2
   BRA @l4l
@nx:
   PLA
   STA mk_b1
   PLA
   STA mk_t1
   RTS

; mk_close: A = pair: draw its span sp_start[A] .. mk_k - 1
mk_close:
   STA pl_p
   TAX
   LDA sp_start,X
   STA pl_kb
   SEC
   LDA mk_k
   SBC pl_kb
   STA sp_n                             ; bytes
   LDA pl_p
   ASL A
   STA pl_y                             ; its even line
   JSR sp_setup
   BCS :+
   JMP sp_go2
:  JMP sm_go2                           ; (step 7n: the far tone)

; sp_setup: pair / line pl_p, line pl_y, byte columns from pl_kb, flat
; pl_fl: U, V at the first column, the steps patched into both loops,
; PTR / Y for line pl_y, the flat's page patched and its bank paged
sp_setup:
   JSR pl_rowc
   LDX pl_p                             ; step 7n: a far row needs no U, V
   LDA pc_lv,X
   STA sm_lv
   BNE @scr
   JSR uvat                             ; U, V at the span's first column
   LDA pl_u
   STA su
   LDA pl_v
   STA sv
   LDX pl_p                             ; and dU, dV for the loops
   LDA pc_du,X
   STA sdu
   LDA pc_dv,X
   STA sdv
@scr:
   ; screen: PTR lo 0, Y = (kb * 8 + line & 7) & $FF, PTR hi = the page:
   ; (line >> 3) * 2 + (kb >> 5) + the back buffer's (step 7r: kb >> 5 is
   ; the carry of kb >= 32)
   LDA pl_y
   LSR A
   LSR A
   AND #$FE                             ; (line >> 3) * 2
   LDX pl_kb
   CPX #32                              ; C = the row's second page
   ADC DV_BACKHI
   STA PTR+1
   STZ PTR
   TXA
   ASL A
   ASL A
   ASL A
   STA pl_a
   LDA pl_y
   AND #7
   ORA pl_a
   TAY
   LDA sm_lv                            ; step 7n: the far tone
   BNE sm_setup
   LDX pl_fl
   LDA mb6_fl_page,X
   STA sl_rd+2
   CMP sf_r0+2                          ; sp_go2's 4 reads: only on a change
   BEQ :+                               ;  of page
   JSR sf_page
:  LDX pl_fl
   LDA mb6_fl_bank,X                    ; A = its bank: the loop pages it
   CLC                                  ; (C = 0: sp_go2 / sl_go)
   RTS

; sm_setup: a far row (step 7n): sm_b = the flat's tone; C = 1
sm_setup:
   LDX pl_fl
   LDA far_tone,X
   STA sm_b
   SEC
   RTS

; sf_page: A = the flat's page, patched into sp_go2's 4 texel reads
sf_page:
   STA sf_r0+2
   STA sf_r1+2
   STA sf_r2+2
   STA sf_r3+2
   RTS

; pl_part: A = a partial line (unbiased: an odd first or even last line of
; a run) of column pl_kb: recorded for a line span at the flush (slot
; kind * 2 + odd, one line per column), or, if the slot is taken, drawn
; on the spot
pl_part:
   STA pl_y
   LSR A                                ; C = odd
   LDA pl_kind
   ROL A                                ; slot = kind * 2 + odd
   TAY
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ORA pl_kb
   TAX
   LDA pp_all,X
   CMP #$FF
   BNE @now
   LDA pl_y
   STA pp_all,X
   LDA #1                               ; (the slot is in use: step 7k)
   STA pp_used,Y
   RTS
@now:
   LDA pl_y
   JMP pl_line

.segment "MFILLC"                       ; (main RAM: the pending planes)
; pe_init: per seg (end of tx_seg): for each plane kind it textures, keep
; the pending spans if they are the same plane (D, flat, light), widened
; to this seg's byte columns; else draw them (pd_flush) and start anew
pe_init:
   LDA mf_x
   LSR A
   LSR A
   STA sk0
   LDA mf_hi
   DEC A
   LSR A
   LSR A
   STA sk1
   LDA pl_dc                            ; ceiling: textured?
   BEQ @fl
   LDA pl_fc
   CMP #$FF
   BEQ @fl
   STA pk_fl
   LDA pl_dc
   STA pk_d
   LDX #0
   JSR pe_kind
@fl:
   LDA pl_df                            ; floor
   BEQ @rts
   STA pk_d
   LDA pl_ff
   STA pk_fl
   LDX #1
   JMP pe_kind
@rts:
   RTS

; pe_kind: X = kind; the plane (pk_d, pk_fl, pl_lv) over columns sk0..sk1
pe_kind:
   STX pk_kind
   LDA pd_open,X
   BEQ @new
   LDA pd_d,X
   CMP pk_d
   BNE @fl
   LDA pd_fl,X
   CMP pk_fl
   BNE @fl
   LDA pd_lv,X
   CMP pl_lv
   BNE @fl
   LDA sk0                              ; the same plane: widen, clearing
   CMP pd_k0,X                          ;  only the new columns
   BCS @rt
   STA ck0
   LDA pd_k0,X
   DEC A
   STA ck1
   JSR pe_clr
   LDX pk_kind
   LDA sk0
   STA pd_k0,X
@rt:
   LDA pd_k1,X
   CMP sk1
   BCS @rts
   INC A
   STA ck0
   LDA sk1
   STA ck1
   JSR pe_clr
   LDX pk_kind
   LDA sk1
   STA pd_k1,X
@rts:
   RTS
@fl:
   JSR pd_flush                         ; another plane: draw the pending
   LDX pk_kind
@new:
   LDA pk_d
   STA pd_d,X
   LDA pk_fl
   STA pd_fl,X
   LDA pl_lv
   STA pd_lv,X
   LDA sk0
   STA pd_k0,X
   STA ck0
   LDA sk1
   STA pd_k1,X
   STA ck1
   LDA #1
   STA pd_open,X
   TXA                                  ; step 7k: its two partial-line
   ASL A                                ;  slots hold nothing yet
   TAX
   STZ pp_used,X
   STZ pp_used+1,X
   ; fall into pe_clr

; pe_clr: columns ck0..ck1 of kind pk_kind: no pair interval ($FF, 0),
; no partial lines ($FF)
pe_clr:
   LDA pk_kind
   LSR A
   ROR A                                ; kind << 7: its two slots' base
   STA pp_base
   LDX ck0
   LDA pk_kind
   BNE @f
@c:                                     ; ceiling
   LDA #$FF
   STA pe_ct,X
   STZ pe_cb,X
   TXA
   ORA pp_base
   TAY
   LDA #$FF
   STA pp_all,Y
   STA pp_all+64,Y
   CPX ck1
   INX
   BCC @c
   RTS
@f:                                     ; floor
   LDA #$FF
   STA pe_ft,X
   STZ pe_fb,X
   TXA
   ORA pp_base
   TAY
   LDA #$FF
   STA pp_all,Y
   STA pp_all+64,Y
   CPX ck1
   INX
   BCC @f
   RTS

; pd_flush: X = kind: draw its pending spans -- MakeSpans over the pairs,
; then the partial lines' line spans
pd_flush:
   STX fl_kind
   LDA pd_d,X
   STA pl_d
   LDA pd_fl,X
   STA pl_fl
   STX pl_kind
   LDA pd_k0,X
   STA pl_k0
   LDA pd_k1,X
   STA pl_k1
   JSR mk_spans
   LDA fl_kind
   ASL A
   STA pp_s                             ; slot kind * 2: even lines,
   JSR pp_slot
   INC pp_s                             ;  then kind * 2 + 1: odd lines
   JSR pp_slot
   LDX fl_kind
   STZ pd_open,X
   RTS

; pp_slot: slot pp_s over the pending columns: each run of one line drawn
; as a line span (sl_draw)
pp_slot:
   LDX pp_s                             ; step 7k: nothing recorded in this
   LDA pp_used,X                        ;  slot since the range opened: no
   BNE :+                               ;  scan
   RTS
:  LDA pp_s
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA pp_base
   LDX fl_kind                          ; step 7r: X = base | k, the scan's
   LDA pd_k1,X                          ;  last column pp_k1 = base | k1
   ORA pp_base
   STA pp_k1
   LDA pd_k0,X
   ORA pp_base
   TAX
@lp:
   LDA pp_all,X
   CMP #$FF
   BNE @run
   CPX pp_k1
   BEQ @rts
   INX
   BRA @lp
@run:
   STA pl_y                             ; a run of line pl_y from column k
   TXA
   AND #63
   STA pl_kb
@ext:
   CPX pp_k1
   BEQ @last                            ; it reaches the last column
   INX
   LDA pp_all,X
   CMP pl_y
   BEQ @ext
   STX pp_k                             ; X: the first column past it
   TXA
   AND #63
   SEC
   SBC pl_kb
   STA sp_n
   JSR sl_draw
   LDX pp_k
   BRA @lp
@last:
   TXA
   AND #63
   SEC
   SBC pl_kb
   INC A                                ; (through the last column)
   STA sp_n
   JMP sl_draw
@rts:
   RTS

; sl_draw: line pl_y, columns pl_kb .. + sp_n - 1, flat pl_fl
sl_draw:
   LDA pl_y
   LSR A
   STA pl_p
   LDA #sl_wr - sl_fb - 2               ; even line: skip the FLIP
   BCC :+
   LDA #0                               ; odd: through it
:  STA sl_fb+1
   JSR sp_setup
   BCS :+
   JMP sl_go
:  JMP sml_go                           ; (step 7n: the far tone)

; mf_flush: the frame's end (render_frame, MASTER): draw every pending
; plane
.export mf_flush
mf_flush:
   LDA #BANK_C                          ; (the bank-6 tail: flat pages)
   STA $FE30
   LDA pd_open
   BEQ :+
   LDX #0
   JSR pd_flush
:  LDA pd_open+1
   BEQ :+
   LDX #1
   JSR pd_flush
:  LDA #BANK_WALK
   STA $FE30
   RTS
.endif
.segment "MFILLC"

; pl_zrow: X = j -> ZC[j] = Zk * |c|, ZS[j] = Zk * |s| (unity: Zk << 8;
; < 2^29), stamped with this frame's epoch
pl_zrow:
   LDA pl_zk0,X
   STA pl_e
   LDA pl_zk1,X
   STA pl_e+1
   LDA pl_zk2,X
   STA pl_e+2
   STZ pl_e+3
   LDA pl_cm
   LDX pl_c1
   JSR pl_prod
   LDX pl_j
   LDA pl_q
   STA zc0,X
   LDA pl_q+1
   STA zc1,X
   LDA pl_q+2
   STA zc2,X
   LDA pl_q+3
   STA zc3,X
   LDA pl_sm
   LDX pl_s1
   JSR pl_prod
   LDX pl_j
   LDA pl_q
   STA zs0,X
   LDA pl_q+1
   STA zs1,X
   LDA pl_q+2
   STA zs2,X
   LDA pl_q+3
   STA zs3,X
   LDA mf_ep
   STA zr_ep,X
   RTS

.segment "MARITH"                       ; main $7E20: the arithmetic (only ever
                                        ;  called with ACCCON X clear)

; ---- dq_core: the 16-step divide (step 7f: unrolled, in bank 6 -- every
; caller has it paged: div32 already reaches d8_byte there). In: remainder
; seed m_r (lo), A (hi) < m_b; dividend lo word m_p. Out: quotient m_p
; (16), remainder m_r. X, Y used. The dividend, the remainder's lo byte
; and the divisor work in zero page the wall / span loops own (dead in the
; set-up code, the only callers): 5-cycle shifts, no counter, no patching.
dq_d0 = zw_lvl                          ; dividend lo word, quotient in
dq_d1 = zw_lvh
dq_r0 = zw_dh                           ; remainder lo (A: hi)
dq_b0 = zw_ddh                          ; divisor
dq_b1 = zw_rowm
.segment "MB6C"


; ---- mul16: m_p (32) = m_a * m_b (16 x 16), four quarter-square 8x8s
; (zero high bytes skipped); m_a, m_b kept, Y kept. Step 7k: from MARITH
; to bank 6 (every caller is bank-6 code), the 8x8s inline.
mul16:
   PHY
   QMUL m_a, m_b                        ; a0 * b0
   STA m_p+1
   LDA mq_l
   STA m_p
   STZ m_p+2
   STZ m_p+3
   LDA m_a+1
   BNE :+
   JMP m16_a1z
:
   QMUL m_a+1, m_b                      ; a1 * b0, at byte 1
   TAY
   CLC
   LDA mq_l
   ADC m_p+1
   STA m_p+1
   TYA
   ADC m_p+2                            ; (was 0: no carry out)
   STA m_p+2
m16_a1z:
   LDA m_b+1
   BNE :+
   JMP m16_done
:
   QMUL m_a, m_b+1                      ; a0 * b1, at byte 1
   TAY
   CLC
   LDA mq_l
   ADC m_p+1
   STA m_p+1
   TYA
   ADC m_p+2
   STA m_p+2
   BCC :+
   INC m_p+3
:  LDA m_a+1
   BEQ m16_done
   QMUL m_a+1, m_b+1                    ; a1 * b1, at byte 2
   TAY
   CLC
   LDA mq_l
   ADC m_p+2
   STA m_p+2
   TYA
   ADC m_p+3
   STA m_p+3
m16_done:
   PLY
   RTS

dq_core:
   LDX m_p
   STX dq_d0
   LDX m_p+1
   STX dq_d1
   LDX m_r
   STX dq_r0
   LDX m_b
   STX dq_b0
   LDX m_b+1
   STX dq_b1
.repeat 16
.scope
   ASL dq_d0
   ROL dq_d1
   ROL dq_r0
   ROL A
   BCS sub                              ; past 16 bits: certainly >= m_b
   CMP dq_b1
   BCC nx
   BNE sub
   LDY dq_r0
   CPY dq_b0
   BCC nx
sub:                                    ; (C = 1 on every way in)
   TAY
   LDA dq_r0
   SBC dq_b0
   STA dq_r0
   TYA
   SBC dq_b1
   INC dq_d0                            ; quotient bit (ASL freed it)
nx:
.endscope
.endrepeat
   STA m_r+1
   LDA dq_r0
   STA m_r
   LDA dq_d0
   STA m_p
   LDA dq_d1
   STA m_p+1
   RTS
.segment "MARITH"

; ---- divq16: m_p (32) / m_b (16) -> m_p (16), when the quotient < 2^16
; (m_p hi word < m_b): 16 steps, the remainder seeded with the hi word ----
divq16:
   PHY
   LDA m_p+2
   STA m_r
   LDA m_p+3
   JSR dq_core
   PLY
   RTS

.segment "MB6C"
.segment "MARITH"
; ---- mul16 -----------------------------------------------------------------
; ---- div32: m_p (32) / m_b (16) -> quotient m_p (32), remainder m_r.
; Exact. Every E1M1 call has a quotient < 2^16, 95% an 8-bit divisor:
;   8-bit divisor, quotient < 2^16: two byte steps (d8_byte), skipped
;     outright while the remainder is 0 and the byte < m_b
;   16-bit divisor, quotient < 2^16: the 16-step dq_core
;   otherwise the plain 32-step loop
dv_slj:
   JMP dv_slow
div32:
   PHY
   LDA m_b+1
   BNE dv_w16
   LDA m_p+3
   BNE dv_slj
   LDA m_p+2
   CMP m_b
   BCS dv_slj
   LDY m_b
   CPY #129                             ; step 7h: a divisor <= 128 takes the
   BCS :+                               ;  unrolled byte steps in bank 6
   JMP dv8f
:  STY d8_c+1
   STY d8_s+1
   STZ m_p+2                            ; (A = remainder seed; flags kept)
   CMP #0
   BNE dv_b1
   LDA m_p+1
   CMP m_b
   BCS dv_r0
   STZ m_p+1                            ; q byte 0, remainder = the byte
   BRA dv_b0
dv_r0:
   LDA #0
dv_b1:
   LDX m_p+1
   STX d8_by
   JSR d8_byte
   LDX d8_by
   STX m_p+1
dv_b0:
   CMP #0
   BNE dv_l0
   LDA m_p
   CMP m_b
   BCS dv_z0
   STZ m_p
   BRA dv_end
dv_z0:
   LDA #0
dv_l0:
   LDX m_p
   STX d8_by
   JSR d8_byte
   LDX d8_by
   STX m_p
dv_end:
   STA m_r
   STZ m_r+1
   PLY
   RTS
dv_w16:
   LDA m_p+2                            ; quotient < 2^16 iff hi word < m_b
   CMP m_b
   LDA m_p+3
   SBC m_b+1
   BCC :+
   JMP dv_slow
:  LDA m_p+2
   STA m_r
   LDA m_p+3
   STZ m_p+2
   STZ m_p+3
   JSR dq_core
   PLY
   RTS
dv_slow:
   STZ m_r
   STZ m_r+1
   LDX #32
@lp:
   ASL m_p
   ROL m_p+1
   ROL m_p+2
   ROL m_p+3
   ROL m_r
   ROL m_r+1
   BCS @sub                             ; remainder overflowed 16 bits
   LDA m_r
   CMP m_b
   LDA m_r+1
   SBC m_b+1
   BCC @nx
@sub:
   LDA m_r
   SBC m_b
   STA m_r
   LDA m_r+1
   SBC m_b+1
   STA m_r+1
   INC m_p                              ; quotient bit (the bit ASL freed)
@nx:
   DEX
   BNE @lp
   PLY
   RTS

.segment "MB6C"
; d8_byte: (A:d8_by) / d (patched, 8-bit), A < d -> d8_by = quotient,
; A = remainder. X used.
; ---- dv8f (step 7h): div32's 8-bit path for a divisor <= 128 (A = the
; remainder seed m_p+2 < m_b, m_p+3 = 0, PHY done). The remainder stays
; below 128, so each bit is ROL A / CMP / SBC with no overflow test, the
; quotient bit shifted in by ROL from the carry; unrolled, in the zero page
; the wall / span loops own (dq_*: set-up code is the only caller).
dv8f:
   LDX m_b
   STX dq_b0
   STZ m_p+2
   CMP #0                               ; a zero remainder and a byte < m_b:
   BNE @b1                              ;  quotient byte 0, no steps
   LDX m_p+1
   CPX m_b
   BCS @b1
   STZ m_p+1
   TXA
   BRA @b0
@b1:
   LDX m_p+1
   STX dq_d0
   JSR d8_fast
   LDX dq_d0
   STX m_p+1
@b0:
   CMP #0
   BNE @l0
   LDX m_p
   CPX m_b
   BCS @l0
   STZ m_p
   TXA
   BRA @end
@l0:
   LDX m_p
   STX dq_d0
   JSR d8_fast
   LDX dq_d0
   STX m_p
@end:
   STA m_r
   STZ m_r+1
   PLY
   RTS

.segment "MARITH"                      ; (step 7v: out of bank 6, full)
; d8_fast: A (remainder < dq_b0 <= 128) : dq_d0 / dq_b0 -> quotient dq_d0,
; remainder A. ASL brings the first dividend bit out; each ROL dq_d0 then
; shifts the quotient bit (the carry: 1 after SBC, 0 after BCC) in and the
; next dividend bit out.
d8_fast:
   ASL dq_d0
.repeat 8
.scope
   ROL A
   CMP dq_b0
   BCC nx
   SBC dq_b0
nx:
   ROL dq_d0
.endscope
.endrepeat
   RTS

.segment "MB6C"
d8_byte:
   LDX #8
d8_lp:
   ASL d8_by
   ROL A
   BCS d8_s                             ; past 8 bits: certainly >= d
d8_c:
   CMP #0                               ; (patched: d)
   BCC d8_n
d8_s:                                   ; (C = 1 on both ways in)
   SBC #0                               ; (patched: d)
   INC d8_by
d8_n:
   DEX
   BNE d8_lp
   RTS

; ============================================================================
; STEP 7n: THE FLATS' FAR TONE. A line pair far enough out (pl_row:
; D >= FAR_DM[k >> 1]) draws its flat as one byte, far_tone[f] (both
; generated into mfar_tab.s by master_assets.py): no U, V, no texel reads,
; just the byte on the even line and FLIP of it on the odd.
.ifndef SERVER                          ; (the screen: not on the fill server)
.segment "MB6C"
sm_lv:   .byte 0                        ; sp_setup: this row's level
sm_b:    .byte 0                        ; the far tone

; The far loops' zero page: the tone and FLIP of it (su, sv: a far row
; has no U, V)
sm_zb   = su
sm_zf   = sv

; sm_go2: sp_n bytes from PTR + Y, a line pair each (sp_go2's contract).
; Duff's device as sp_go2: four bodies (LDA zp : STA (PTR),Y : INY :
; LDA zp : STA (PTR),Y at fixed offsets), entered so the span's last byte
; ends a block; only the block end moves PTR on, by 32.
sm_go2:
   LDA #ACC_DXY
   STA $FE34
   LDX sm_b
   STX sm_zb
   LDA flip,X
   STA sm_zf
   LDA sp_n                             ; blocks = ceil(n / 4)
   CLC
   ADC #3
   LSR A
   LSR A
   STA sf_nb
   LDA #0                               ; q = -n & 3: the last byte ends a
   SEC                                  ;  block
   SBC sp_n
   AND #3
   ASL A
   TAX                                  ; X = q * 2
   ASL A
   ASL A
   STA sf_q                             ; q * 8
   TYA                                  ; PTR (lo 0) + Y - 8q: the block's
   SEC                                  ;  column 0
   SBC sf_q
   STA PTR
   LDA PTR+1
   SBC #0
   STA PTR+1
   JMP (sm_ent,X)
sm_lp:
sm_e0:
   LDA sm_zb
   STA (PTR)                            ; even line (column 0)
   LDY #1
   LDA sm_zf
   STA (PTR),Y                          ; odd line: FLIP
sm_e1:
   LDA sm_zb
   LDY #8
   STA (PTR),Y
   INY
   LDA sm_zf
   STA (PTR),Y
sm_e2:
   LDA sm_zb
   LDY #16
   STA (PTR),Y
   INY
   LDA sm_zf
   STA (PTR),Y
sm_e3:
   LDA sm_zb
   LDY #24
   STA (PTR),Y
   INY
   LDA sm_zf
   STA (PTR),Y
   DEC sf_nb
   BEQ sm_end
   CLC                                  ; the next four columns
   LDA PTR
   ADC #32
   STA PTR
   BCC sm_lp
   INC PTR+1
   BRA sm_lp
sm_end:
   LDA #ACC_DY
   STA $FE34
   RTS
sm_ent:
   .word sm_e0, sm_e1, sm_e2, sm_e3

; sml_go: sp_n bytes of line pl_y from PTR + Y (sl_go's contract)
sml_go:
   LDA #ACC_DXY
   STA $FE34
   LDA pl_y
   LSR A                                ; C = odd: FLIP
   LDA sm_b
   BCC :+
   TAX
   LDA flip,X
:  STA sm_zb
   LDX sp_n
sml_lp:
   LDA sm_zb
   STA (PTR),Y
   TYA
   CLC
   ADC #8                               ; the next byte column
   TAY
   BCC :+
   INC PTR+1
:  DEX
   BNE sml_lp
   LDA #ACC_DY
   STA $FE34
   RTS
.endif

.include "mfar_tab.s"

.endif
.ifdef TUBE
; ============================================================================
; HOST-LED TUBE MASTER (docs/tube_master.md H2): the fill REQUEST in place
; of the fill. tube_req.py is the spec (encode); test_tube_hreq.py holds
; this to it byte for byte. Bytes go to the second processor through the
; Tube's register 3 (host -> parasite), polled.
;   rq_frame  (walk.s, in place of mf_frame) the view: px88, py88 (24-bit),
;             vz, ab, the view trig
;   rq_fill   (seg_emit, in place of mf_fill, mf_snap's snapshot taken as
;             before) a seg the fill could draw -- [lo, hi) not empty and
;             a span over it: $01, slot | solid << 10 | c1 << 11 | c2 <<
;             12, subsector, ch - vz, fh - vz, lo, hi, the six line ends, the reciprocal
;             terms, t when exactly one end is clipped, then the snapshot
;             and the live spans over [lo, hi), the pool's own fields
;   rq_end    $00: the frame's last request
; ============================================================================
.macro PUTB                             ; A -> register 3 (A kept)
:  BIT $FEE4
   BVC :-                               ; (status bit 6: room)
   STA $FEE5
.endmacro

.segment "MFILL"                        ; HAZEL: walk.s calls it under WALK,
rq_frame:                               ;  the sender is in bank 6
   LDA #BANK_C
   STA $FE30
   JSR rq_view
   LDA #BANK_WALK
   STA $FE30
   RTS
rq_end:                                 ; (walk.s: the frame's end, in place
   LDA #0                               ;  of mf_flush, which leaves WALK paged)
   PUTB
   LDA #BANK_WALK
   STA $FE30
   RTS

.segment "MB6C"
rq_view:
   LDA zp_br_px
   PUTB
   LDA zp_br_px_h
   PUTB
   LDA zp_br_px_x
   PUTB
   LDA zp_br_py
   PUTB
   LDA zp_br_py_h
   PUTB
   LDA zp_br_py_x
   PUTB
   LDA zp_br_vz
   PUTB
   LDA bca_ab
   PUTB
   LDA zp_br_smag                       ; the view trig: |sin|, |cos|, then
   PUTB                                 ;  sneg | sone << 1 | cneg << 2 |
   LDA zp_br_cmag                       ;  cone << 3 (each 0 / 1)
   PUTB
   LDA zp_br_cone
   ASL A
   ORA zp_br_cneg
   ASL A
   ORA zp_br_sone
   ASL A
   ORA zp_br_sneg
   PUTB
   RTS

; (bank 6, beside mf_snap / mf_fill)
rq_fill:
   JSR mf_range
   BCS @rts
   LDA sn_n
   BNE @go
@rts:
   RTS
@go:
   LDA #$01
   PUTB
   ; slot = (hdr hi - >ROM_SEG_HDR_C) * HDR_PER_PAGE + hdr lo / LAY_HDR_STRIDE
   STZ tx_slot
   STZ tx_slot+1
   SEC
   LDA zp_seg_hdr_p+1
   SBC #>ROM_SEG_HDR_C
   TAX
   BEQ @lo
@pg:
   CLC
   LDA tx_slot
   ADC #HDR_PER_PAGE
   STA tx_slot
   BCC :+
   INC tx_slot+1
:  DEX
   BNE @pg
@lo:
   LDA zp_seg_hdr_p
@l9:
   CMP #LAY_HDR_STRIDE
   BCC @slot
   SBC #LAY_HDR_STRIDE                  ; (C = 1)
   INC tx_slot
   BNE @l9
   INC tx_slot+1
   BRA @l9
@slot:
   LDA tx_slot
   PUTB
   LDA tx_slot+1
   BIT zp_seg_flags
   BVC :+
   ORA #$04                             ; solid
:  LDX zp_seg_v1_clipped
   BEQ :+
   ORA #$08                             ; c1
:  LDX zp_seg_v2_clipped
   BEQ :+
   ORA #$10                             ; c2
:  PUTB
   LDA zp_node_ch_l                     ; the subsector
   PUTB
   LDA zp_seg_top_dlt                   ; ch - vz, fh - vz
   PUTB
   LDA zp_seg_bot_dlt
   PUTB
   LDA mf_lo
   PUTB
   LDA mf_hi
   PUTB
   LDX #0                               ; sx1 sx2 ft1 ft2 fb1 fb2 (s16)
@ln:
   LDY rq_lof,X
   LDA VX1,Y
   CPX #2                               ; the y ends less Y_BIAS
   BCC @raw
   SBC #48                              ; (C = 1; the borrow kept through PUTB)
   PUTB
   LDA VX1+1,Y
   SBC #0
   BRA @put
@raw:
   PUTB
   LDA VX1+1,Y
@put:
   PUTB
   INX
   CPX #6
   BNE @ln
   LDA zp_seg_v1_r_m8
   PUTB
   LDA zp_seg_v1_r_s
   PUTB
   LDA zp_seg_v2_r_m8
   PUTB
   LDA zp_seg_v2_r_s
   PUTB
   LDA zp_seg_v1_clipped                ; t when exactly one end is clipped
   BEQ @c1n
   LDA zp_seg_v2_clipped
   BNE @spans
   BRA @xt
@c1n:
   LDA zp_seg_v2_clipped
   BEQ @spans
@xt:
   LDA mf_xt
   PUTB
   LDA mf_xt+1
   PUTB
@spans:
   LDA sn_n                             ; before: the snapshot
   PUTB
   LDX #0
@sn:
   LDA sn_xs,X
   PUTB
   LDA sn_xe,X
   PUTB
   LDA sn_xlo,X
   PUTB
   LDA sn_den,X
   PUTB
   LDA sn_tl,X
   PUTB
   LDA sn_bl,X
   PUTB
   LDA sn_tr,X
   PUTB
   LDA sn_br,X
   PUTB
   LDA sn_bxlo,X
   PUTB
   LDA sn_bden,X
   PUTB
   INX
   CPX sn_n
   BNE @sn
   LDY #0                               ; after: the live spans over [lo, hi)
   LDX zp_head                          ;  (counted, then sent)
@cn:
   CPX #0
   BEQ @cd
   LDA POOL_XSTART,X
   CMP mf_hi
   BCS @cd
   LDA mf_lo
   CMP POOL_XEND,X
   BCS :+
   INY
:  LDA POOL_NEXT,X
   TAX
   BRA @cn
@cd:
   TYA
   PUTB
   LDX zp_head
   BRA @lv
@out:
   RTS
@lv:
   CPX #0
   BEQ @out
   LDA POOL_XSTART,X
   CMP mf_hi
   BCS @out
   LDA mf_lo
   CMP POOL_XEND,X
   BCS @nx
   LDA POOL_XSTART,X
   PUTB
   LDA POOL_XEND,X
   PUTB
   LDA POOL_TXLO,X
   PUTB
   LDA POOL_TDEN,X
   PUTB
   LDA POOL_TL,X
   PUTB
   LDA POOL_BL,X
   PUTB
   LDA POOL_TR,X
   PUTB
   LDA POOL_BR,X
   PUTB
   LDA POOL_BXLO,X
   PUTB
   LDA POOL_BDEN,X
   PUTB
@nx:
   LDA POOL_NEXT,X
   TAX
   JMP @lv

; the six line ends' offsets in the VX1 / VX2 blocks: sx1 sx2 ft1 ft2 fb1 fb2
rq_lof:
   .byte zp_seg_sx1_l - VX1, zp_seg_sx2_l - VX1, zp_seg_sy1_top_l - VX1
   .byte zp_seg_sy2_top_l - VX1, zp_seg_sy1_bot_l - VX1, zp_seg_sy2_bot_l - VX1
.endif
