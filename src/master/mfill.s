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
NONE = $FF                              ; no texture (master_walls.NONE)
HDR_PER_PAGE = 256 / LAY_HDR_STRIDE     ; page-slotted seg headers

.export mf_snap, mf_fill, mf_skymap, mf_xt

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
.assert man_slot_ll - man_slot_d = $300 .and man_slot_lh - man_slot_ll = $300, error, "tx_seg steps the slot planes by $300"
.assert <man_slot_d = 0, error, "slot planes must be page-aligned"

.segment "MB6T"                         ; bank 6 tail, above the texels
mb6_pt_tid:   .res $A0                  ; per part (<= 160): texture id
mb6_pt_k0:    .res $A0                  ;  K = 2048*th*(fc-fh)//src_h, bytes
mb6_pt_k1:    .res $A0                  ;  0-2 (v step per line = K // h)
mb6_pt_k2:    .res $A0
mb6_pt_v0:    .res $A0                  ;  Vtop (5.11) lo / hi
mb6_pt_v1:    .res $A0
mb6_tp_rl:    .res $20                  ; per texture (32): R = 4096*tw//src_w,
mb6_tp_rh:    .res $20                  ;  lo / hi (column = (u&mask)*R >> 16)
mb6_tp_ml:    .res $20                  ;  u mask = 16*src_w - 1, lo / hi
mb6_tp_mh:    .res $20
mb6_tp_rowm:  .res $20                  ;  row mask (th-1)*8, applied to v hi
mb6_tp_ph:    .res $20                  ;  texel pages hi byte
mb6_tp_bank:  .res $20                  ;  sideways bank (5 or 6)
mb6_tp_ro:    .res $20                  ;  row offset (128: stacked lower half)
mb6_tp_ixl:   .res $20                  ;  column index table address lo / hi
mb6_tp_ixh:   .res $20

.segment "MTEXIX"                       ; main RAM: read with ACCCON X clear
mtex_ix:      .res $600                 ; column index bytes, every texture

.segment "MFILLBSS"
mf_lo:   .res 1                         ; clamped [lo, hi) of the seg
mf_hi:   .res 1
mf_x:    .res 1                         ; current byte column's first pixel
mf_i:    .res 1                         ; snapshot cursor
sn_n:    .res 1                         ; snapshot count
sn_xs:   .res 32
sn_xe:   .res 32
sn_xlo:  .res 32
sn_den:  .res 32
sn_tl:   .res 32
sn_tr:   .res 32
sn_bl:   .res 32
sn_br:   .res 32
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
t_kk:    .res 3                         ; the part's K
l_v:     .res 2                         ; the left strip's v and pair step
l_step:  .res 2                         ;  (t_v / t_step: the right strip's)

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
   LDX #ST_T
   JSR st_dup_r
   LDX #ST_B
   JSR st_dup_r
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
   RTS                                  ; x >= hi: done
   ; --- old span at x: advance while xe <= x ---
adv:
   LDY mf_i
   CPY sn_n
   BCC @have
   RTS                                  ; no snapshot span left
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
   RTS
@step:
   STA mf_x
   LDX #1                               ; every stepper moves 2 pixels a
@two:                                   ;  step: twice per byte
   PHX
   CLC                                  ; the texture d stepper: n += dn,
   LDA tx_n                             ; den += dden
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
   PLX
   DEX
   BPL @two
   JMP col

; ============================================================================
; STEPPERS. Each tracks y(x) = y0 +/- floor(|D| * k / W) for k = x - x0
; exactly as x steps by 2: |D| * k = q * W + r with 0 <= r < W, and
; 2|D| = Q * W + R, so a step is r += R, q += Q, then one conditional
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
   LDA si_d                             ; Q, R = 2|D| / W
   ASL A
   STA m_p
   LDA si_d+1
   ROL A
   STA m_p+1
   LDA #0
   ROL A
   STA m_p+2
   STZ m_p+3
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

; st_dup_r: stepper X+84 := stepper X moved on 2 pixels (the right strip's
; T or B line from the same set-up)
st_dup_r:
   PHX
   LDY #14
:  LDA st_f,X
   STA st_f+84,X
   INX
   DEY
   BNE :-
   PLA
   CLC
   ADC #84
   TAX
   ; fall into st_step

; st_step: advance stepper X by two pixels
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
   LDA sh_ceil
   JSR run
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
   LDA #WB_FLOOR
   ; fall into run

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
   LDA #BANK_C                          ; back to the cascade's bank
   STA $FE30
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
   ; dden = 2 * (B - A)  (s16)
   SEC
   LDA tx_rb
   SBC tx_ra
   STA tx_dden
   LDA #0
   SBC #0
   STA tx_dden+1
   ASL tx_dden
   ROL tx_dden+1
   ; dn = 2 * (dH * B - dL * A)  (s32)
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
   RTS

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
   ; the right strip, at x + 3 -- or the left strip's d past the map's end
   LDA tx_d
   STA tx_dr
   LDA tx_d+1
   STA tx_dr+1
   LDA mf_x
   CLC
   ADC #3
   BCS @pc                              ; (x + 3 > 255 > xh)
   CMP tx_xh
   BEQ :+
   BCS @pc
:  CLC
   LDX #0
   LDY #4
:  LDA tx_n,X                           ; n + dn, den + dden (2 pixels on)
   ADC tx_dn,X
   STA m_p,X
   INX
   DEY
   BNE :-
   CLC
   LDA tx_den
   ADC tx_dden
   STA m_b
   LDA tx_den+1
   ADC tx_dden+1
   STA m_b+1
   ORA m_b
   BEQ @pc                              ; (den 0: the left d, as dL)
   JSR divq16
   LDA m_p
   STA tx_dr
   LDA m_p+1
   STA tx_dr+1
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
   ; the left strip: T, B at x, d at x + 1
   LDX #3
:  LDA c_t,X                            ; c_t, c_b (4 bytes) -> q_t, q_b
   STA q_t,X
   DEX
   BPL :-
   LDA tx_d
   STA q_d
   LDA tx_d+1
   STA q_d+1
   JSR tstrip
   LDA t_v
   STA l_v
   LDA t_v+1
   STA l_v+1
   LDA t_step
   STA l_step
   LDA t_step+1
   STA l_step+1
   LDA t_cl
   STA trl_rd+1
   LDA t_ch
   STA trl_rd+2
   ; the right strip: T, B at x + 2, d at x + 3
   LDX #3
:  LDA c_tr,X                           ; c_tr, c_br -> q_t, q_b
   STA q_t,X
   DEX
   BPL :-
   LDA tx_dr
   STA q_d
   LDA tx_dr+1
   STA q_d+1
   JSR tstrip
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
   STA t_ev
   TAX
   LDA mf_flip,X
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

; ---- tstrip: one strip of the run. In: q_t, q_b (s16 T, B), q_d (d),
; t_kk (K), t_vt (Vtop), t_tid, r_ys (biased). Out: t_step = the PAIR step
; (2 * K / (B - T), 0 if B <= T), t_v = Vtop + ((ys & ~1) - T) * step
; (mod 2^16), t_cl/t_ch = the texel column for u = ub*16 - start + d ----
tstrip:
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
   LDA r_ys
   AND #$FE
   SEC
   SBC q_t
   STA m_a
   LDA #0
   SBC q_t+1
   STA m_a+1
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
   ASL t_step                           ; step is per LINE: a pair moves 2 steps
   ROL t_step+1
   ; u = ub * 16 - start + d, & mask; column = (u * R) >> 16
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
   STA m_a+1
   LDA mb6_tp_rl,X
   STA m_b
   LDA mb6_tp_rh,X
   STA m_b+1
   JSR mul16                            ; column = m_p+2
   LDX t_tid
   CLC                                  ; stored column index (main RAM:
   LDA mb6_tp_ixl,X                     ;  read with X clear)
   ADC m_p+2
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

; ---- divq16: m_p (32) / m_b (16) -> m_p (16), when the quotient < 2^16
; (m_p hi word < m_b): 16 steps, the remainder seeded with the hi word ----
divq16:
   LDA m_p+2
   STA m_r
   LDA m_p+3
   STA m_r+1
   LDX #16
@lp:
   ASL m_p
   ROL m_p+1
   ROL m_r
   ROL m_r+1
   BCS @sub
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
   INC m_p
@nx:
   DEX
   BNE @lp
   RTS

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

; ---- mul16: m_p (32) = m_a (16) * m_b (16), unsigned shift-add ----------
mul16:
   STZ m_p
   STZ m_p+1
   STZ m_p+2
   STZ m_p+3
   LDX #16
@lp:
   ASL m_p
   ROL m_p+1
   ROL m_p+2
   ROL m_p+3
   ASL m_b
   ROL m_b+1
   BCC @nx
   CLC
   LDA m_p
   ADC m_a
   STA m_p
   LDA m_p+1
   ADC m_a+1
   STA m_p+1
   BCC @nx
   INC m_p+2
   BNE @nx
   INC m_p+3
@nx:
   DEX
   BNE @lp
   RTS

; ---- div32: m_p (32) / m_b (16) -> quotient in m_p (low 16 used), m_r ----
div32:
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
   RTS

.endif                                  ; ::MASTER
