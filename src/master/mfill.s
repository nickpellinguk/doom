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
.if ::MASTER
.setcpu "65C02"                         ; the Master's 65C12

.include "../zp.inc"
.include "../layout.inc"                ; ROM_SEG_HDR_C, LAY_HDR_STRIDE

ACC_DY  = $09                           ; render-time ACCCON: display shadow, HAZEL in
ACC_DXY = $0D                           ;  ... plus X: CPU on the shadow buffers

; step-3 shades, in the RIGHT-strip ($33) position: wall_byte(shade) in
; master_assets.py, pixel 2 = colour a, pixel 3 = colour b
WB_SKY   = $30                          ; (cyan, cyan)
WB_CEIL  = $10                          ; (black, cyan)
WB_FLOOR = $01                          ; (black, red)

PTR = RASTER_ZP_X1                      ; zp pair x1/y1 (the rasteriser's JMP
                                        ; vector -- there is no rasteriser on
                                        ; the Master; DCL is done by fill time)
TP  = RASTER_ZP_DX                      ; zp pair: the rasteriser's dx/dy, only
                                        ; raster.s touches it (not linked here)
; Sector light (step 5b): every byte a seg writes is ANDed with these --
; maskEven on even lines, maskOdd on odd lines (after the FLIP) -- set per
; seg from its front sector's level (light_masks); sky is never masked.
; The rasteriser's cnt pair (raster.s only, not linked here).
maskEven = RASTER_ZP_CNT
maskOdd  = RASTER_ZP_CNT+1
NONE = $FF                              ; no texture (master_walls.NONE)
HDR_PER_PAGE = 256 / LAY_HDR_STRIDE     ; page-slotted seg headers

.export mf_snap, mf_fill, mf_skymap, mf_xt, mf_frame, mf_tick

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
mb6_pt_k1:    .res $A0                  ;  0-2 (v step per line = K // h)
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
sp_start: .res 80                       ; MakeSpans: per pair, its span's start
pc_ep:        .res 80                   ; per line pair p: the frame epoch and
pc_d:         .res 80                   ;  plane height D it was computed for,
pc_u0l:       .res 80                   ;  U0 / dU / V0 / dV (4.12, mod 2^16)
pc_u0h:       .res 80
pc_dul:       .res 80
pc_duh:       .res 80
pc_v0l:       .res 80
pc_v0h:       .res 80
pc_dvl:       .res 80
pc_dvh:       .res 80

.segment "MFILLBSS"
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
st_f:    .res 112
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
r_od:    .res 1
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
cur_ub:  .res 1
cur_sl:  .res 1
cur_sh:  .res 1
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
t_vt:    .res 2
t_step:  .res 2
t_v:     .res 2
t_rowm:  .res 1
t_n:     .res 1
t_ev:    .res 1
t_od:    .res 1
t_cl:    .res 1                         ; texel column base lo / hi
t_ch:    .res 1
c_tr:    .res 2                         ; the right strip's own T, B (x + 2;
c_br:    .res 2                         ;  c_t..c_b and c_tr..c_br are pairs)
q_t:     .res 2                         ; tstrip's T, B (same layout) and d
q_b:     .res 2
q_d:     .res 2
tx_dr:   .res 2                         ; the right strip's d (x + 3)
l_v:     .res 2                         ; the left strip's v and pair step
l_step:  .res 2                         ;  (t_v / t_step: the right strip's)
tx_lx:   .res 1                         ; the last byte column with an exact d
tx_dp:   .res 2                         ;  (its x, and that d)
t_kk:    .res 3                         ; the part's K
; ---- step 5: floors and ceilings (plane_ref.py is the executable spec) ----
mf_ep:   .res 1                         ; frame epoch (pc_ep; 0 never valid)
mf_tick: .res 1                         ; frames (NUKAGE frame = tick/8 mod 3)
mf_nuk:  .res 1
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
pl_u:    .res 2
pl_v:    .res 2
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
sp_u:    .res 2                         ; the span loop's U, V (4.12)
sp_v:    .res 2
sp_t:    .res 1
sp_n:    .res 1
mq_b:    .res 1                         ; mf_mul8: multiplier, product lo,
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

; Sky map: one bit per subsector, set when its ceiling is F_SKY1.
; SEEDED BY THE IMAGE BUILDER (banked_bsp / tools/build_master_ssd.py).
mf_skymap: .res 32

bitmask: .byte 1, 2, 4, 8, 16, 32, 64, 128

; FLIP: swap the two pixels of every pair (0<->1, 2<->3): the cross-hatch
; second line. Same table as master_assets.FLIP.
mf_flip:
.repeat 256, I
   .byte (((I & $AA) >> 1) | ((I & $55) << 1)) & $FF
.endrepeat

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
   INY
@next:
   LDA POOL_NEXT,X
   TAX
   BRA @lp
@done:
   STY sn_n
@rts:
   RTS

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
   ; the right strip's own T and B: the same lines from x0 + 2
   CLC
   LDA si_k
   ADC #2
   STA si_k
   BCC :+
   INC si_k+1
:  LDX #ST_BR
   JSR st_init
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
   LDX #ST_TR
   JSR st_init
   LDA #$FF
   STA tx_lx                            ; no exact d yet in this seg
   LDA mf_x
   CMP mf_hi
   BCC :+
   RTS                                  ; no byte column in [lo, hi)
:  JSR tx_seg                           ; the seg's wall texture set-up

col:
   STZ c_dok                            ; d not yet computed for this byte
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
   LDA sn_bl,Y
   STA si_a0
   LDA sn_br,Y
   STA si_a1
   LDX #ST_OB
   JSR st_init8
@oval:
   LDX #ST_OT
   JSR st_val
   LDA ln_y
   STA c_ot
   LDX #ST_OB
   JSR st_val
   LDA ln_y
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
   LDA POOL_BL,X
   STA si_a0
   LDA POOL_BR,X
   STA si_a1
   LDX #ST_NB
   JSR st_init8
@nval:
   LDX #ST_NT
   JSR st_val
   LDA ln_y
   STA c_nt
   LDX #ST_NB
   JSR st_val
   LDA ln_y
   STA c_nb
   LDA #1
   STA c_new
   BRA @lines
@none:
   STZ c_new
@lines:
   ; --- T and B at x, clamped for comparison against the visible band ---
   LDX #ST_T
   JSR st_val
   LDA ln_y
   STA c_t
   LDA ln_y+1
   STA c_t+1
   LDX #Y_BIAS                          ; clamp(T, 48, 208)
   LDY #Y_BIAS + 160
   JSR clamp_ln
   STA c_tc
   LDX #ST_B
   JSR st_val
   LDA ln_y
   STA c_b
   LDA ln_y+1
   STA c_b+1
   LDX #Y_BIAS - 1                      ; clamp(B, 47, 207)
   LDY #VIS_YMAX
   JSR clamp_ln
   STA c_bc
   LDX #ST_TR                           ; the right strip's own T, B (x + 2):
   JSR st_val                           ;  its v only -- every extent is the
   LDA ln_y                             ;  byte's
   STA c_tr
   LDA ln_y+1
   STA c_tr+1
   LDX #ST_BR
   JSR st_val
   LDA ln_y
   STA c_br
   LDA ln_y+1
   STA c_br+1
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
   LDX #ST_T
   JSR st_step
   LDX #ST_B
   JSR st_step
   LDX #ST_TR
   JSR st_step
   LDX #ST_BR
   JSR st_step
   LDA mf_oi
   BMI @no_o
   LDX #ST_OT
   JSR st_step
   LDX #ST_OB
   JSR st_step
@no_o:
   LDA mf_ns
   BMI @no_n
   LDX #ST_NT
   JSR st_step
   LDX #ST_NB
   JSR st_step
@no_n:
   JMP col

; ============================================================================
; STEPPERS. Each tracks y(x) = y0 +/- floor(|D| * k / W) for k = x - x0
; exactly as x steps by 4 (one byte column): |D| * k = q * W + r with
; 0 <= r < W, and 4|D| = Q * W + R, so a step is r += R, q += Q, then one
; conditional
; r -= W, q += 1. A negative slope reads y0 - (q + (r != 0)) (floor of a
; negative quotient). W = 0 is a constant y0 (a zero-width line). The
; set-up pays one multiply and two divides; each step a few adds.
; Field offsets in st_* (X = stepper base): y0 0/1, q 2/3, r 4/5, W 6/7,
; Q 8/9, R 10/11, neg 12, const 13.
; ============================================================================
ST_T  = 0
ST_B  = 14
ST_OT = 28
ST_OB = 42
ST_NT = 56
ST_NB = 70
ST_TR = 84                              ; the right strip's T / B (x + 2)
ST_BR = 98

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

; st_init: si_y0, si_d (|D|), si_neg, si_w, si_k  -> stepper X
st_init:
   STX si_x
   LDA si_y0
   STA st_f+0,X
   LDA si_y0+1
   STA st_f+1,X
   LDA si_neg
   STA st_f+12,X
   LDA si_w
   STA st_f+6,X
   LDA si_w+1
   STA st_f+7,X
   ORA si_w
   BNE @var
   LDA #1
   STA st_f+13,X                        ; W = 0: constant y0
   RTS
@var:
   STZ st_f+13,X
   LDA si_d                             ; q, r = |D| * k / W
   STA m_a
   LDA si_d+1
   STA m_a+1
   LDA si_k
   STA m_b
   LDA si_k+1
   STA m_b+1
   JSR mul16
   LDA si_w
   STA m_b
   LDA si_w+1
   STA m_b+1
   JSR div32
   LDX si_x
   LDA m_p
   STA st_f+2,X
   LDA m_p+1
   STA st_f+3,X
   LDA m_r
   STA st_f+4,X
   LDA m_r+1
   STA st_f+5,X
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
   LDA m_p
   STA st_f+8,X
   LDA m_p+1
   STA st_f+9,X
   LDA m_r
   STA st_f+10,X
   LDA m_r+1
   STA st_f+11,X
   RTS

; st_step: advance stepper X by four pixels (one byte column)
st_step:
   LDA st_f+13,X
   BNE @rts
   CLC
   LDA st_f+4,X
   ADC st_f+10,X
   STA st_f+4,X
   LDA st_f+5,X
   ADC st_f+11,X
   STA st_f+5,X
   CLC
   LDA st_f+2,X
   ADC st_f+8,X
   STA st_f+2,X
   LDA st_f+3,X
   ADC st_f+9,X
   STA st_f+3,X
   LDA st_f+4,X                         ; r >= W ?
   CMP st_f+6,X
   LDA st_f+5,X
   SBC st_f+7,X
   BCC @rts
   LDA st_f+4,X
   SBC st_f+6,X                         ; (C = 1 from the compare)
   STA st_f+4,X
   LDA st_f+5,X
   SBC st_f+7,X
   STA st_f+5,X
   INC st_f+2,X
   BNE @rts
   INC st_f+3,X
@rts:
   RTS

; st_val: ln_y = stepper X's current y (s16)
st_val:
   LDA st_f+13,X
   BEQ @var
   LDA st_f+0,X
   STA ln_y
   LDA st_f+1,X
   STA ln_y+1
   RTS
@var:
   LDA st_f+2,X
   STA m_p
   LDA st_f+3,X
   STA m_p+1
   LDA st_f+12,X
   BEQ @pos
   LDA st_f+4,X                         ; negative: y0 - (q + (r != 0))
   ORA st_f+5,X
   BEQ @sub
   INC m_p
   BNE @sub
   INC m_p+1
@sub:
   SEC
   LDA st_f+0,X
   SBC m_p
   STA ln_y
   LDA st_f+1,X
   SBC m_p+1
   STA ln_y+1
   RTS
@pos:
   CLC
   LDA st_f+0,X
   ADC m_p
   STA ln_y
   LDA st_f+1,X
   ADC m_p+1
   STA ln_y+1
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

; ============================================================================
; hz_run: byte column mf_x >> 2, screen lines [r_ys, r_ye], shade r_part:
; the WHOLE byte (both strips the shade -- a 4x2 fat pixel) on even lines,
; FLIP of it on odd lines. Nothing is read back. X is set only inside the
; write loop.
; ============================================================================
hz_run:
   LDA r_part
   ASL A
   ASL A
   ORA r_part                           ; (shade << 2) | shade
   STA r_ev
   TAX
   LDA mf_flip,X
   STA r_od
   LDA r_part
   CMP #WB_SKY
   BEQ :+                               ; sky: never darkened
   LDA r_ev
   AND maskEven
   STA r_ev
   LDA r_od
   AND maskOdd
   STA r_od
:
   JSR ln_ptr                           ; PTR, Y for line r_ys
   SEC
   LDA r_ye
   SBC r_ys
   TAX
   INX                                  ; line count
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow (no main reads >= $3000)
@lp:
   TYA
   LSR A                                ; C = line parity
   LDA r_ev
   BCC :+
   LDA r_od
:  STA (PTR),Y
   DEX
   BEQ @done
   INY
   CPY #8
   BNE @lp
   LDY #0
   INC PTR+1
   INC PTR+1
   BRA @lp
@done:
   LDA #ACC_DY
   STA $FE34                            ; back to main RAM
   RTS

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
   LDA pc_ub,X
   STA cur_ub
   LDA pc_sl,X
   STA cur_sl
   LDA pc_sh,X
   STA cur_sh
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
   ORA at_a+2
   ORA at_b+3
   ORA at_b+2
   BNE @sh
   LDA at_a+1
   ORA at_b+1
   BPL @ok                              ; both < $8000
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
   LDA tx_d1                            ; num = d1 * a + d2 * b
   STA m_a
   LDA tx_d1+1
   STA m_a+1
   LDA at_a
   STA m_b
   LDA at_a+1
   STA m_b+1
   JSR mul16
   LDX #3
:  LDA m_p,X
   STA t_tmp,X
   DEX
   BPL :-
   LDA tx_d2
   STA m_a
   LDA tx_d2+1
   STA m_a+1
   LDA at_b
   STA m_b
   LDA at_b+1
   STA m_b+1
   JSR mul16
   CLC
   LDX #0
:  LDA m_p,X
   ADC t_tmp,X
   STA m_p,X
   INX
   TXA
   EOR #4
   BNE :-
   LDA at_den
   STA m_b
   LDA at_den+1
   STA m_b+1
   JMP divq16

; ---- tx_getd: this byte's d for both strips (once), and the piece -------
tx_getd:
   LDA c_dok
   BEQ :+
   RTS
:  INC c_dok
   LDA tx_den
   ORA tx_den+1
   BNE @div
   LDA tx_dl
   STA tx_d
   LDA tx_dl+1
   STA tx_d+1
   BRA @piece
@div:
   LDX #3
:  LDA tx_n,X
   STA m_p,X
   DEX
   BPL :-
   LDA tx_den
   STA m_b
   LDA tx_den+1
   STA m_b+1
   JSR divq16
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
   TAX
   LDA mb6_pt_tid,X
   STA t_tid
   LDA mb6_pt_v0,X
   STA t_vt
   LDA mb6_pt_v1,X
   STA t_vt+1
   LDA mb6_pt_k0,X
   STA t_kk
   LDA mb6_pt_k1,X
   STA t_kk+1
   LDA mb6_pt_k2,X
   STA t_kk+2
   ; the left strip: its v from T, B at x; its column from d at x + 1
   LDX #3
:  LDA c_t,X                            ; c_t, c_b (4 bytes) -> q_t, q_b
   STA q_t,X
   DEX
   BPL :-
   JSR tvstep
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
   JSR tcol
   LDA t_cl
   STA trl_rd+1
   LDA t_ch
   STA trl_rd+2
   ; the right strip: its own v from T, B at x + 2; its column from d at x + 3
   LDX #3
:  LDA c_tr,X                           ; c_tr, c_br -> q_t, q_b
   STA q_t,X
   DEX
   BPL :-
   JSR tvstep
   LDA tx_dr
   STA q_d
   LDA tx_dr+1
   STA q_d+1
   JSR tcol
   LDA t_cl
   STA trr_rd+1
   LDA t_ch
   STA trr_rd+2
   LDX t_tid
   LDA mb6_tp_rowm,X
   STA t_rowm
   ; ---- the screen side: whole bytes ----
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
   LDA mb6_tp_bank,X                    ; (X = t_tid still)
   STA $FE30                            ; the texture's bank
   LDA #ACC_DXY
   STA $FE34                            ; -> shadow (no main reads >= $3000)
pb_tex:
   LDA l_v+1                            ; row = (v >> 11) & (th - 1), * 8
   AND t_rowm
   TAX
trl_rd:
   LDA $FFFF,X                          ; (patched: left texel column)
   ASL A
   ASL A
   STA t_ev
   LDA t_v+1
   AND t_rowm
   TAX
trr_rd:
   LDA $FFFF,X                          ; (patched: right texel column)
   ORA t_ev
   TAX
   AND maskEven                         ; lit
   STA t_ev
   LDA mf_flip,X
   AND maskOdd
   STA t_od
@line:
   TYA
   LSR A                                ; C = line parity
   LDA t_ev
   BCC :+
   LDA t_od
:  STA (PTR),Y                          ; the whole byte: no read
   DEC t_n
   BEQ @done
   INY
   CPY #8
   BNE :+
   LDY #0
   INC PTR+1
   INC PTR+1
:  TYA
   LSR A
   BCS @line                            ; odd line: same texel pair
   CLC                                  ; even line: both strips' next pair
   LDA l_v
   ADC l_step
   STA l_v
   LDA l_v+1
   ADC l_step+1
   STA l_v+1
   CLC
   LDA t_v
   ADC t_step
   STA t_v
   LDA t_v+1
   ADC t_step+1
   STA t_v+1
   BRA pb_tex
@done:
   LDA #ACC_DY
   STA $FE34                            ; back to main RAM
   LDA #BANK_C
   STA $FE30                            ; and the cascade's bank
   RTS

; ---- tvstep: the run's v. In: q_t, q_b (s16 T, B), t_kk (K), t_vt (Vtop),
; r_ys (biased). Out: t_step = the PAIR step (2 * K / (B - T), 0 if
; B <= T), t_v = Vtop + ((ys & ~1) - T) * step (mod 2^16) ----------------
tvstep:
   LDA t_kk
   STA m_p
   LDA t_kk+1
   STA m_p+1
   LDA t_kk+2
   STA m_p+2
   STZ m_p+3
   SEC
   LDA q_b
   SBC q_t
   STA m_b
   LDA q_b+1
   SBC q_t+1
   STA m_b+1
   BMI @z
   ORA m_b
   BEQ @z
   JSR div32
   LDA m_p
   STA t_step
   LDA m_p+1
   STA t_step+1
   BRA @v0
@z:
   STZ t_step
   STZ t_step+1
@v0:
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
   SEC                                  ; one line above T: v = Vtop - step
   LDA t_vt
   SBC t_step
   STA t_v
   LDA t_vt+1
   SBC t_step+1
   STA t_v+1
   BRA @dbl
@top:
   LDA t_vt
   STA t_v
   LDA t_vt+1
   STA t_v+1
   BRA @dbl
@mul:
   LDA t_step
   STA m_b
   LDA t_step+1
   STA m_b+1
   JSR mul16
   CLC
   LDA m_p
   ADC t_vt
   STA t_v
   LDA m_p+1
   ADC t_vt+1
   STA t_v+1
@dbl:
   ASL t_step                           ; step is per LINE: a pair moves 2 steps
   ROL t_step+1
   RTS

; ---- tcol: q_d (d) -> t_cl / t_ch, the texel column: u = ub * 16 - start
; + d; index = (u & mask) >> shift, as hi((u & mask) << (8 - shift)) ----
tcol:
   LDA cur_ub
   STA m_a
   STZ m_a+1
   LDX #4
:  ASL m_a
   ROL m_a+1
   DEX
   BNE :-
   SEC
   LDA m_a
   SBC cur_sl
   STA m_a
   LDA m_a+1
   SBC cur_sh
   STA m_a+1
   CLC
   LDA m_a
   ADC q_d
   STA m_a
   LDA m_a+1
   ADC q_d+1
   STA m_a+1
   LDX t_tid
   LDA m_a
   AND mb6_tp_ml,X
   STA m_a
   LDA m_a+1
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
   PHA
   AND #7                               ; texel column = page + idx>>3,
   CLC                                  ;  byte idx&7, + row offset
   ADC mb6_tp_ro,X
   STA t_cl
   PLA
   LSR A
   LSR A
   LSR A
   CLC
   ADC mb6_tp_ph,X
   STA t_ch
   RTS

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

; ============================================================================
; STEP 5: FLOORS AND CEILINGS (plane_ref.py is the executable spec). ONE
; texel read per 4x2 fat pixel: a plane byte is the 16x16 flat's byte at
; the byte column's centre and the line PAIR's centre, written whole (FLIP
; on the pair's odd line). 4.12 fixed point: along a line pair a plane is
; affine, so per pair the row maths gives U0, dU, V0, dV (pl_row, cached
; per frame in pc_*), and along a HORIZONTAL SPAN U += dU, V += dV per byte
; column (see PLANES AS HORIZONTAL SPANS below).
; ============================================================================

; mf_frame: render_frame entry (bsp/walk.s, MASTER) -- new epoch, NUKAGE
; frame, and the view terms (from the view zero page the harness / driver
; staged; copied, so later scratch use of it cannot matter)
mf_frame:
   INC mf_ep
   BNE @ep
   LDX #79                              ; wrapped: no stale epoch may match
:  STZ pc_ep,X
   CPX #40
   BCS :+
   STZ zr_ep,X
:  DEX
   BPL :--
   INC mf_ep
@ep:
   STZ pd_open                          ; no plane spans pending
   STZ pd_open+1
   INC mf_tick                          ; NUKAGE frame = (tick >> 3) mod 3
   LDA mf_tick
   LSR A
   LSR A
   LSR A
:  CMP #3
   BCC :+
   SBC #3
   BRA :-
:  STA mf_nuk
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

; pl_seg: per seg (tx_seg, pl_ff / pl_fc read from ANDY) -- the NUKAGE frame,
; D = vz - fh / ch - vz (0 if the eye is not on the plane's side)
pl_seg:
   LDA pl_ff                            ; light: level = top 3 bits
   LSR A
   LSR A
   LSR A
   LSR A
   LSR A
   TAX
   STX pl_lv
   LDA light_even,X
   STA maskEven
   LDA light_odd,X
   STA maskOdd
   LDA pl_ff
   AND #$1F
   JSR nuk
   STA pl_ff
   LDA pl_fc
   JSR nuk
   STA pl_fc
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
   RTS

; the five light levels' masks (master_walls.LIGHT_MASKS): FF.FF, AA.FF,
; AA.AA, 0A.AA, 0A.0A -- progressively darker
light_even: .byte $FF, $AA, $AA, $0A, $0A
light_odd:  .byte $FF, $FF, $AA, $AA, $0A

nuk:                                    ; A = flat id -> NUKAGE-animated id
   CMP #3
   BCS @rts                             ; not a NUKAGE frame ($FF too)
   CLC
   ADC mf_nuk
   CMP #3
   BCC @rts
   SBC #3
@rts:
   RTS

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
; (4.12), one texel read, two whole-byte writes (even line lit, odd line
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
   ; the wrong side of the horizon keeps the shade (floor: lines < 80;
   ; ceiling: lines >= 80)
   LDA pl_kind
   BEQ @ceil
   LDA r_ys                             ; floor
   CMP #80
   BCS @parts
   LDA r_ye
   CMP #80
   BCC :+
   LDA #79
:  JSR pl_shade                         ; lines r_ys .. min(ye, 79)
   LDA #80
   STA r_ys
   BRA @chk
@ceil:
   LDA r_ye
   CMP #80
   BCC @parts
   LDA r_ys
   PHA
   CMP #80
   BCS :+
   LDA #80
   STA r_ys
:  LDA r_ye
   JSR pl_shade                         ; lines max(ys, 80) .. ye
   PLA
   STA r_ys
   LDA #79
   STA r_ye
@chk:
   LDA r_ye
   CMP r_ys
   BCS @parts
   RTS
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

; pl_cell: A = the texel byte at pair pl_p, byte column pl_kb (X clear)
pl_cell:
   JSR pl_rowc
   LDX pl_p
   LDA pc_dul,X                         ; U = U0 + kb * dU
   STA m_a
   LDA pc_duh,X
   STA m_a+1
   JSR kbmul
   LDX pl_p
   CLC
   LDA m_p
   ADC pc_u0l,X
   STA pl_u
   LDA m_p+1
   ADC pc_u0h,X
   STA pl_u+1
   LDA pc_dvl,X                         ; V = V0 + kb * dV
   STA m_a
   LDA pc_dvh,X
   STA m_a+1
   JSR kbmul
   LDX pl_p
   CLC
   LDA m_p
   ADC pc_v0l,X
   STA pl_v
   LDA m_p+1
   ADC pc_v0h,X
   STA pl_v+1
   LDA pl_u+1                           ; texel (V >> 12) * 16 + (U >> 12)
   LSR A
   LSR A
   LSR A
   LSR A
   STA pl_a
   LDA pl_v+1
   AND #$F0
   ORA pl_a
   TAY
   LDX pl_fl
   LDA mb6_fl_page,X                    ; (bank 6 tail, before paging)
   STA pc_rd+2
   LDA mb6_fl_bank,X
   STA $FE30
pc_rd:
   LDA $FF00,Y                          ; (patched: the flat's page)
   LDX #BANK_C
   STX $FE30
   RTS

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

; pl_wr1: A = line; t_ev = the texel byte: write it (even: & maskEven;
; odd: FLIP & maskOdd) at column pl_kb
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
   LSR A
   LDA t_ev
   BCS :+
   AND maskEven
   BRA :++
:  TAX
   LDA mf_flip,X
   AND maskOdd
:  STA (PTR),Y
   LDA #ACC_DY
   STA $FE34
   RTS

; mf_planes: the seg's end. Its plane spans stay pending (pd_*): a later
; seg on the same plane widens them; another plane, or the frame's end
; (mf_flush), draws them.
mf_planes:
   RTS

; ---- sp_go2 / sl_go: the span loops (HAZEL: they run with ACCCON X
; set). sp_setup (main RAM) has set U, V, PTR, Y, the patched steps and
; page, and paged the flat's bank. sp_go2 writes a line PAIR per byte
; (even line lit, odd line FLIP lit); sl_go ONE line (sl_draw patches the
; FLIP and the mask for its parity).
sp_go2:
   LDA #ACC_DXY
   STA $FE34
sp_lp:
   LDA sp_u+1                           ; texel (V >> 12) * 16 + (U >> 12)
   LSR A
   LSR A
   LSR A
   LSR A
   STA sp_t
   LDA sp_v+1
   AND #$F0
   ORA sp_t
   TAX
sp_rd:
   LDA $FF00,X                          ; (patched: the flat's page)
   TAX
   AND maskEven
   STA (PTR),Y                          ; even line: whole byte
   INY
   LDA mf_flip,X
   AND maskOdd
   STA (PTR),Y                          ; odd line: FLIP
   TYA
   CLC
   ADC #7                               ; the next byte column
   TAY
   BCC :+
   INC PTR+1
:  CLC
   LDA sp_u
sp_adul:
   ADC #0                               ; (patched dU)
   STA sp_u
   LDA sp_u+1
sp_aduh:
   ADC #0
   STA sp_u+1
   CLC
   LDA sp_v
sp_advl:
   ADC #0                               ; (patched dV)
   STA sp_v
   LDA sp_v+1
sp_advh:
   ADC #0
   STA sp_v+1
   DEC sp_n
   BNE sp_lp
   LDA #ACC_DY
   STA $FE34
   LDA #BANK_C
   STA $FE30
   RTS

sl_go:
   LDA #ACC_DXY
   STA $FE34
sl_lp:
   LDA sp_u+1                           ; texel (V >> 12) * 16 + (U >> 12)
   LSR A
   LSR A
   LSR A
   LSR A
   STA sp_t
   LDA sp_v+1
   AND #$F0
   ORA sp_t
   TAX
sl_rd:
   LDA $FF00,X                          ; (patched: the flat's page)
   TAX
sl_fl:
   LDA mf_flip,X                        ; (patched: odd line FLIP, even TXA)
sl_mk:
   .byte $25, maskOdd                   ; AND zp (patched: the line's mask)
   STA (PTR),Y
   TYA
   CLC
   ADC #8                               ; the next byte column
   TAY
   BCC :+
   INC PTR+1
:  CLC
   LDA sp_u
sl_adul:
   ADC #0                               ; (patched dU)
   STA sp_u
   LDA sp_u+1
sl_aduh:
   ADC #0
   STA sp_u+1
   CLC
   LDA sp_v
sl_advl:
   ADC #0                               ; (patched dV)
   STA sp_v
   LDA sp_v+1
sl_advh:
   ADC #0
   STA sp_v+1
   DEC sp_n
   BNE sl_lp
   LDA #ACC_DY
   STA $FE34
   LDA #BANK_C
   STA $FE30
   RTS

; kbmul: m_p (16) = m_a * pl_kb (mod 2^16; kb < 64: six shift-add steps)
kbmul:                                  ; m_p = m_a * pl_kb (mod 2^16)
   LDA pl_kb
   STA mq_b
   LDA m_a+1
   JSR mf_mul8
   LDA mq_l
   STA m_p+1
   LDA m_a
   JSR mf_mul8
   CLC
   ADC m_p+1
   STA m_p+1
   LDA mq_l
   STA m_p
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
   STA mq_b
   STZ pl_q
   STZ pl_q+1
   STZ pl_q+2
   STZ pl_q+3
   STZ pl_q+4
   STZ mq_i
@lp:
   LDX mq_i
   LDA pl_e,X
   BEQ @nx                              ; zero byte: adds nothing
   JSR mf_mul8
   TAY                                  ; Y = hi
   LDX mq_i
   CLC                                  ; pl_q+i+1 is still clear: no
   LDA mq_l                             ;  carry out of it
   ADC pl_q,X
   STA pl_q,X
   TYA
   ADC pl_q+1,X
   STA pl_q+1,X
@nx:
   INC mq_i
   LDA mq_i
   CMP #4
   BNE @lp
   RTS

; neg_ah: A non-zero -> pl_a, pl_h negated (mod 2^16)
neg_ah:
   CMP #0
   BEQ @rts
   SEC
   LDA #0
   SBC pl_a
   STA pl_a
   LDA #0
   SBC pl_a+1
   STA pl_a+1
   SEC
   LDA #0
   SBC pl_h
   STA pl_h
   LDA #0
   SBC pl_h+1
   STA pl_h+1
@rts:
   RTS

; ============================================================================
; Cold set-up code in MAIN RAM (the HAZEL code area is full): routines that
; only ever run with ACCCON X clear -- never inside a screen-write loop --
; may live below $8000. MFILLM: the slack after the clipper code (CBITS);
; MFILLV: the tail of the object dispatch page (VPTABM; objects are off).
; ============================================================================
.segment "MFILLM"
; ---- tx_seg ----------------------------------------------------------------
tx_seg:
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
   ; A, B: the raw weights shifted together until both fit a byte
@ab:
   LDA tx_ra+1
   ORA tx_ra+2
   ORA tx_ra+3
   ORA tx_ra+4
   ORA tx_rb+1
   ORA tx_rb+2
   ORA tx_rb+3
   ORA tx_rb+4
   BEQ @abok
   LSR tx_ra+4
   ROR tx_ra+3
   ROR tx_ra+2
   ROR tx_ra+1
   ROR tx_ra
   LSR tx_rb+4
   ROR tx_rb+3
   ROR tx_rb+2
   ROR tx_rb+1
   ROR tx_rb
   BRA @ab
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
   JMP pe_init                          ; the plane extents, empty


.segment "MFILLV"
; q_a_h: pl_a = (pl_q >> 8) mod 2^16, pl_h = (pl_q >> 14) mod 2^16
q_a_h:
   LDA pl_q+1
   STA pl_a
   LDA pl_q+2
   STA pl_a+1
   LDA pl_q+2                           ; (q >> 16) << 2 | q bits 14-15
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

; h63: pl_h = 63 * pl_h = (pl_h << 6) - pl_h (mod 2^16); X preserved
h63:
   LDA pl_h
   STA pl_q
   LDA pl_h+1
   STA pl_q+1
   LDY #6
:  ASL pl_q
   ROL pl_q+1
   DEY
   BNE :-
   SEC
   LDA pl_q
   SBC pl_h
   STA pl_h
   LDA pl_q+1
   SBC pl_h+1
   STA pl_h+1
   RTS

; 2^20 // k for k = 2j + 1, j = 0..39 (the depth of a pair k lines off the
; horizon, 1024 * depth / D), three byte planes
pl_zk0:
.repeat 40, J
   .byte <((1 << 20) / (2 * J + 1))
.endrepeat
pl_zk1:
.repeat 40, J
   .byte >((1 << 20) / (2 * J + 1))
.endrepeat
pl_zk2:
.repeat 40, J
   .byte ^((1 << 20) / (2 * J + 1))
.endrepeat


.segment "MFILL"
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
zr_ep:   .res 40                        ; per row j: the epoch ZC / ZS are for
zc0:     .res 40                        ; ZC[j] = Zk * |c| (4 bytes)
zc1:     .res 40
zc2:     .res 40
zc3:     .res 40
zs0:     .res 40                        ; ZS[j] = Zk * |s|
zs1:     .res 40
zs2:     .res 40
zs3:     .res 40

.segment "MFILLC"                       ; main RAM code: runs with ACCCON X
                                        ;  clear (row maths, span sweeps)
; pl_row: the row maths for pair pl_p and D pl_d, into pc_*[pl_p]
;   k = |2p + 1 - 80| (j = k >> 1 indexes pl_zk = 2^20 // k)
;   Pc = D * ZC[j], Ps = D * ZS[j], ZC = Zk * |c|, ZS = Zk * |s| (unity:
;   Zk << 8), made once per frame per j (zr_*): the same integers as
;   (D * Zk) * |c|, one multiply per term
;   A = +-(Pc >> 8), hV = +-(Pc >> 14), Bs = +-(Ps >> 8), hU = +-(Ps >> 14)
;   U0 = Up + A - 63 hU, dU = 2 hU, V0 = Vp - Bs - 63 hV, dV = 2 hV
pl_row:
   LDA pl_p
   SEC
   SBC #40
   BCS :+                               ; floor: j = p - 40
   EOR #$FF                             ; ceiling: j = 39 - p
:  TAX
   STX pl_j
   LDA zr_ep,X
   CMP mf_ep
   BEQ :+
   JSR pl_zrow                          ; this frame's ZC[j], ZS[j]
   LDX pl_j
:  LDA zc0,X                            ; cos: Pc = D * ZC[j] -> A, hV
   STA pl_e
   LDA zc1,X
   STA pl_e+1
   LDA zc2,X
   STA pl_e+2
   LDA zc3,X
   STA pl_e+3
   LDA pl_d
   JSR mul8x32
   JSR q_a_h                            ; pl_a = Pc >> 8, pl_h = Pc >> 14
   LDA pl_cn
   JSR neg_ah
   LDX pl_p
   ; V0 = Vp - Bs - 63 hV (Bs below), dV = 2 hV; A, hV now
   LDA pl_h
   ASL A
   STA pc_dvl,X
   LDA pl_h+1
   ROL A
   STA pc_dvh,X
   JSR h63                              ; pl_h = 63 * hV
   CLC                                  ; U0 starts as Up + A
   LDA pl_up
   ADC pl_a
   STA pc_u0l,X
   LDA pl_up+1
   ADC pl_a+1
   STA pc_u0h,X
   SEC                                  ; V0 starts as Vp - 63 hV
   LDA pl_vp
   SBC pl_h
   STA pc_v0l,X
   LDA pl_vp+1
   SBC pl_h+1
   STA pc_v0h,X
   ; sin: Ps = D * ZS[j] -> Bs, hU
   LDX pl_j
   LDA zs0,X
   STA pl_e
   LDA zs1,X
   STA pl_e+1
   LDA zs2,X
   STA pl_e+2
   LDA zs3,X
   STA pl_e+3
   LDA pl_d
   JSR mul8x32
   JSR q_a_h                            ; pl_a = Ps >> 8, pl_h = Ps >> 14
   LDA pl_sn
   JSR neg_ah
   LDX pl_p
   LDA pl_h
   ASL A
   STA pc_dul,X
   LDA pl_h+1
   ROL A
   STA pc_duh,X
   JSR h63                              ; pl_h = 63 * hU
   SEC                                  ; U0 -= 63 hU
   LDA pc_u0l,X
   SBC pl_h
   STA pc_u0l,X
   LDA pc_u0h,X
   SBC pl_h+1
   STA pc_u0h,X
   SEC                                  ; V0 -= Bs
   LDA pc_v0l,X
   SBC pl_a
   STA pc_v0l,X
   LDA pc_v0h,X
   SBC pl_a+1
   STA pc_v0h,X
   LDA mf_ep
   STA pc_ep,X
   LDA pl_d
   STA pc_d,X
   RTS

.segment "MFILL"                        ; (HAZEL: the sweep and set-up)
; mk_spans: R_MakeSpans over columns pl_k0 .. pl_k1 (+ an empty sentinel):
; (t1, b1) the previous column's pair interval, (t2, b2) this one's;
; pairs leaving close a span ending at the previous column, pairs
; arriving open one here (sp_start). Empty = ($FF, 0).
mk_spans:
   LDA #$FF
   STA mk_t1
   STZ mk_b1
   LDA pl_k0
   STA mk_k
@col:
   LDA mk_k
   CMP pl_k1
   BEQ :+
   BCS @sent                            ; past the last: the empty sentinel
:  TAX
   LDA pl_kind
   BEQ :+
   LDA pe_ft,X
   STA mk_t2
   LDA pe_fb,X
   STA mk_b2
   BRA @go
:  LDA pe_ct,X
   STA mk_t2
   LDA pe_cb,X
   STA mk_b2
   BRA @go
@sent:
   LDA #$FF
   STA mk_t2
   STZ mk_b2
@go:
   LDA mk_t2                            ; keep this column's interval for
   PHA                                  ;  the next step
   LDA mk_b2
   PHA
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
   CMP mk_t1
   BCS @l4
   LDA mk_b2
   CMP mk_t2
   BCC @l4
   LDX mk_t2
   LDA mk_k
   STA sp_start,X
   INC mk_t2
   BRA @l3
@l4:                                    ; while b2 > b1 and b2 >= t2: open b2
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
   BRA @l4
@nx:
   PLA
   STA mk_b1
   PLA
   STA mk_t1
   LDA mk_k
   INC mk_k
   CMP pl_k1
   BEQ :+                               ; k <= k1: the next column (k1 + 1
   BCS @rts                             ;  is the sentinel: all closed)
:  JMP @col
@rts:
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
   JMP sp_go2

; sp_setup: pair / line pl_p, line pl_y, byte columns from pl_kb, flat
; pl_fl: U, V at the first column, the steps patched into both loops,
; PTR / Y for line pl_y, the flat's page patched and its bank paged
sp_setup:
   JSR pl_rowc
   LDX pl_p
   LDA pc_dul,X                         ; U, V at the span's first column
   STA m_a
   STA sp_adul+1                        ; and dU, dV into the loops
   STA sl_adul+1
   LDA pc_duh,X
   STA m_a+1
   STA sp_aduh+1
   STA sl_aduh+1
   JSR kbmul
   LDX pl_p
   CLC
   LDA m_p
   ADC pc_u0l,X
   STA sp_u
   LDA m_p+1
   ADC pc_u0h,X
   STA sp_u+1
   LDA pc_dvl,X
   STA m_a
   STA sp_advl+1
   STA sl_advl+1
   LDA pc_dvh,X
   STA m_a+1
   STA sp_advh+1
   STA sl_advh+1
   JSR kbmul
   LDX pl_p
   CLC
   LDA m_p
   ADC pc_v0l,X
   STA sp_v
   LDA m_p+1
   ADC pc_v0h,X
   STA sp_v+1
   ; screen: PTR lo 0, Y = (kb * 8 + line & 7) & $FF, PTR hi = the page
   LDA pl_kb
   ASL A
   ASL A
   ASL A
   STA pl_a
   LDA pl_y
   AND #7
   ORA pl_a
   TAY
   STZ PTR
   LDA pl_kb
   LSR A
   LSR A
   LSR A
   LSR A
   LSR A
   STA PTR+1
   LDA pl_y
   LSR A
   LSR A
   LSR A                                ; (line >> 3) * 2
   ASL A
   CLC
   ADC PTR+1
   ADC DV_BACKHI
   STA PTR+1
   LDX pl_fl
   LDA mb6_fl_page,X
   STA sp_rd+2
   STA sl_rd+2
   LDA mb6_fl_bank,X
   STA $FE30
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
   ; fall into pe_clr

; pe_clr: columns ck0..ck1 of kind pk_kind: no pair interval ($FF, 0),
; no partial lines ($FF)
pe_clr:
   LDA pk_kind
   LSR A
   ROR A                                ; kind << 7: its two slots' base
   STA pp_base
   LDX ck0
@lp:
   LDA pk_kind
   BNE @f
   LDA #$FF
   STA pe_ct,X
   STZ pe_cb,X
   BRA @pp
@f:
   LDA #$FF
   STA pe_ft,X
   STZ pe_fb,X
@pp:
   TXA
   ORA pp_base
   TAY
   LDA #$FF
   STA pp_all,Y
   STA pp_all+64,Y
   CPX ck1
   INX
   BCC @lp
   RTS

; pd_flush: X = kind: draw its pending spans -- MakeSpans over the pairs,
; then the partial lines' line spans -- in the plane's own light; the
; current seg's masks are kept
pd_flush:
   STX fl_kind
   LDA maskEven
   PHA
   LDA maskOdd
   PHA
   LDA pd_lv,X
   TAY
   LDA light_even,Y
   STA maskEven
   LDA light_odd,Y
   STA maskOdd
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
   PLA
   STA maskOdd
   PLA
   STA maskEven
   RTS

; pp_slot: slot pp_s over the pending columns: each run of one line drawn
; as a line span (sl_draw)
pp_slot:
   LDA pp_s
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA pp_base
   LDX fl_kind
   LDA pd_k0,X
   STA pp_k
   LDA pd_k1,X
   STA pp_k1
@lp:
   LDA pp_k
   CMP pp_k1
   BEQ :+
   BCS @rts
:  ORA pp_base
   TAX
   LDA pp_all,X
   CMP #$FF
   BNE @run
   INC pp_k
   BRA @lp
@run:
   STA pl_y                             ; a run of line pl_y from column k
   LDA pp_k
   STA pl_kb
@ext:
   INC pp_k
   LDA pp_k
   CMP pp_k1
   BEQ :+
   BCS @end
:  ORA pp_base
   TAX
   LDA pp_all,X
   CMP pl_y
   BEQ @ext
@end:
   SEC
   LDA pp_k
   SBC pl_kb
   STA sp_n
   JSR sl_draw
   BRA @lp
@rts:
   RTS

; sl_draw: line pl_y, columns pl_kb .. + sp_n - 1, flat pl_fl
sl_draw:
   LDA pl_y
   LSR A
   STA pl_p
   BCC @even
   LDA #$BD                             ; odd: LDA mf_flip,X; AND maskOdd
   STA sl_fl
   LDA #<mf_flip
   STA sl_fl+1
   LDA #>mf_flip
   STA sl_fl+2
   LDA #<maskOdd
   STA sl_mk+1
   BRA @go
@even:
   LDA #$8A                             ; even: TXA; NOP; NOP; AND maskEven
   STA sl_fl
   LDA #$EA
   STA sl_fl+1
   STA sl_fl+2
   LDA #<maskEven
   STA sl_mk+1
@go:
   JSR sp_setup
   JMP sl_go

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
; ---- mf_mul8: A * mq_b -> A (hi), mq_l (lo). Quarter squares:
; a*b = f(a+b) - f(|a-b|), f(n) = n*n >> 2, from the boot-built SQR_*
; tables (main RAM $0200-$07FF: readable whatever ACCCON X). X, Y used.
mf_mul8:
   STA mq_t
   SEC
   SBC mq_b
   BCS :+
   EOR #$FF
   ADC #1                               ; (C = 0 from the SBC)
:  TAY                                  ; Y = |a - b|
   LDA mq_t
   CLC
   ADC mq_b
   TAX                                  ; X = (a + b) & $FF
   BCS @big
   SEC
   LDA SQR_LO,X
   SBC SQR_LO,Y
   STA mq_l
   LDA SQR_HI,X
   SBC SQR_HI,Y
   RTS
@big:                                   ; a + b >= 256 (C = 1)
   LDA SQR2_LO,X
   SBC SQR_LO,Y
   STA mq_l
   LDA SQR2_HI,X
   SBC SQR_HI,Y
   RTS

; ---- dq_set / dq_core: the 16-step divide, divisor m_b patched into the
; immediates. In: remainder seed m_r (lo), A (hi) < m_b; dividend lo word
; m_p. Out: quotient m_p (16), remainder m_r. X, Y used. ----------------
dq_set:
   LDA m_b
   STA dq_cl+1
   STA dq_sl+1
   LDA m_b+1
   STA dq_ch+1
   STA dq_sh+1
   RTS
dq_core:
   LDX #16
dq_lp:
   ASL m_p
   ROL m_p+1
   ROL m_r
   ROL A
   BCS dq_sub                           ; past 16 bits: certainly >= m_b
dq_ch:
   CMP #0                               ; (patched: m_b hi)
   BCC dq_nx
   BNE dq_sub
   LDY m_r
dq_cl:
   CPY #0                               ; (patched: m_b lo)
   BCC dq_nx
dq_sub:                                 ; (C = 1 on every way in)
   TAY
   LDA m_r
dq_sl:
   SBC #0                               ; (patched: m_b lo)
   STA m_r
   TYA
dq_sh:
   SBC #0                               ; (patched: m_b hi)
   INC m_p                              ; quotient bit (ASL freed it)
dq_nx:
   DEX
   BNE dq_lp
   STA m_r+1
   RTS

; ---- divq16: m_p (32) / m_b (16) -> m_p (16), when the quotient < 2^16
; (m_p hi word < m_b): 16 steps, the remainder seeded with the hi word ----
divq16:
   PHY
   JSR dq_set
   LDA m_p+2
   STA m_r
   LDA m_p+3
   JSR dq_core
   PLY
   RTS

.segment "MFILL"
; ---- clamp_ln: A = clamp(ln_y (s16), X, Y) as u8 ------------------------
clamp_ln:
   STX m_r                              ; lo
   STY m_r+1                            ; hi
   LDA ln_y+1
   BMI @lo                              ; negative -> lo
   BNE @hi                              ; >= 256 -> hi
   LDA ln_y
   CMP m_r
   BCC @lo
   CMP m_r+1
   BCS @hi
   RTS
@lo:
   LDA m_r
   RTS
@hi:
   LDA m_r+1
   RTS

.segment "MARITH"
; ---- mul16 -----------------------------------------------------------------
mul16:                                  ; m_p (32) = m_a * m_b (16 x 16),
   PHY                                  ;  four quarter-square 8x8s; m_a,
   LDA m_b                              ;  m_b kept
   STA mq_b
   LDA m_a
   JSR mf_mul8                            ; a0 * b0
   STA m_p+1
   LDA mq_l
   STA m_p
   STZ m_p+2
   STZ m_p+3
   LDA m_a+1
   BEQ @a1z
   JSR mf_mul8                            ; a1 * b0, at byte 1
   TAY
   CLC
   LDA mq_l
   ADC m_p+1
   STA m_p+1
   TYA
   ADC m_p+2                            ; (was 0: no carry out)
   STA m_p+2
@a1z:
   LDA m_b+1
   BEQ @done
   STA mq_b
   LDA m_a
   JSR mf_mul8                            ; a0 * b1, at byte 1
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
   BEQ @done
   JSR mf_mul8                            ; a1 * b1, at byte 2
   TAY
   CLC
   LDA mq_l
   ADC m_p+2
   STA m_p+2
   TYA
   ADC m_p+3
   STA m_p+3
@done:
   PLY
   RTS

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
   STY d8_c+1
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
   BCS dv_slj
   JSR dq_set
   LDA m_p+2
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

.segment "MFILL"
; d8_byte: (A:d8_by) / d (patched, 8-bit), A < d -> d8_by = quotient,
; A = remainder. X used.
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

.endif                                  ; ::MASTER
