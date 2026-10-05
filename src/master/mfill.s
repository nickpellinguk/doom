; ============================================================================
; master/mfill.s -- the step-3 span-diff FILLER (MASTER build only).
; docs/master_textured_spec.md; the executable spec is fill_ref.py, which
; this file mirrors bit for bit.
;
; The clip spans ARE the unfilled screen. Every span update a seg makes
; (span_mark_solid for a solid wall; the fused top/bottom walk + merge for
; a portal) removes area from them, and that area is exactly what the seg
; must fill. So:
;   mf_snap  -- at the cascade head (seg_emit hgp_fwd): copy the spans that
;               overlap the seg's columns [lo, hi)
;   mf_fill  -- after the seg's updates: for every EVEN pixel x in [lo, hi)
;               compare the snapshot span at x with the live one and fill
;               the removed bands, split at the seg's own front ceiling (T)
;               and floor (B) lines: y < T ceiling, y > B floor, else wall
;   hz_run   -- one run of one strip into the back buffer, X set only for
;               the duration of the run (HAZEL code reaches shadow RAM
;               through ACCCON X; nothing here reads main RAM above $3000
;               while X is set)
;
; Everything lives in HAZEL ($C800+, ACCCON Y held on by the driver for the
; whole render). Span edges are evaluated exactly as the clipper's Python
; twin evaluates them (endpoint_spans._span_top/_span_bot: floor
; interpolation on the TOP anchor TXLO/TDEN), the seg lines exactly as
; fill_ref's floor interpolation of the s16 projected endpoints.
;
; Arithmetic is the plain shift-add / shift-subtract kind: correctness first,
; the cycle grind is step 7.
; ============================================================================
.if ::MASTER
.setcpu "65C02"                         ; the Master's 65C12

.include "../zp.inc"

ACC_DY  = $09                           ; render-time ACCCON: display shadow, HAZEL in
ACC_DXY = $0D                           ;  ... plus X: CPU on the shadow buffers

; step-3 shades, in the RIGHT-strip ($33) position: wall_byte(shade) in
; master_assets.py, pixel 2 = colour a, pixel 3 = colour b
WB_SKY   = $30                          ; (cyan, cyan)
WB_CEIL  = $10                          ; (black, cyan)
WB_FLOOR = $01                          ; (black, red)
WB_WALL  = $13                          ; (red, white)   solid walls
WB_STEP  = $31                          ; (cyan, white)  upper / lower walls

PTR = RASTER_ZP_X1                      ; zp pair x1/y1 (the rasteriser's JMP
                                        ; vector -- there is no rasteriser on
                                        ; the Master; DCL is done by fill time)

.export mf_snap, mf_fill, mf_skymap

; ----------------------------------------------------------------------------
.segment "MFILLBSS"
mf_lo:   .res 1                         ; clamped [lo, hi) of the seg
mf_hi:   .res 1
mf_x:    .res 1                         ; current even pixel
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
sh_wall: .res 1
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
r_c:     .res 1
r_ys:    .res 1
r_ye:    .res 1
r_part:  .res 1
r_ev:    .res 1
r_od:    .res 1
r_keep:  .res 1

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
   LDA #WB_WALL
   BIT zp_seg_flags
   BVS :+                               ; V set: solid seg
   LDA #WB_STEP
:  STA sh_wall
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
   ; --- first even pixel: (lo + 1) & ~1 ---
   LDA mf_lo
   INC A
   AND #$FE
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

col:
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
   LDX #Y_BIAS                          ; clamp(T, 48, 208)
   LDY #Y_BIAS + 160
   JSR clamp_ln
   STA c_tc
   LDX #ST_B
   JSR st_val
   LDX #Y_BIAS - 1                      ; clamp(B, 47, 207)
   LDY #VIS_YMAX
   JSR clamp_ln
   STA c_bc
   ; --- the bands ---
   LDA c_new
   BNE @two
   LDA c_ot                             ; closed: [ot, ob]
   STA b_y0
   LDA c_ob
   STA b_y1
   JSR band
   JMP next_col
@two:
   LDA c_nt                             ; top band [ot, nt-1]
   BEQ @bot                             ; (nt = 0: nothing above it)
   DEC A
   STA b_y1
   LDA c_ot
   STA b_y0
   JSR band
@bot:
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
   ADC #2
   BCC @step
   RTS
@step:
   STA mf_x
   LDX #ST_T
   JSR st_step
   LDX #ST_B
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
   LDA sh_wall
   JSR run
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
   LDA mf_x
   LSR A
   STA r_c
   ; fall into hz_run

; ============================================================================
; hz_run: strip r_c, screen lines [r_ys, r_ye], shade r_part, back buffer.
; Even lines get the shade, odd lines FLIP of it, merged into the strip's
; half of each byte. X is set only inside the write loop.
; ============================================================================
hz_run:
   LDA r_c
   AND #1
   BNE @odd
   LDA r_part                           ; even strip: the LEFT ($CC) half
   ASL A
   ASL A
   STA r_ev
   LDA #$33
   STA r_keep
   BRA @go
@odd:
   LDA r_part
   STA r_ev
   LDA #$CC
   STA r_keep
@go:
   LDX r_ev
   LDA mf_flip,X
   STA r_od
   ; PTR = buffer + (ys >> 3) * 512 + (c >> 1) * 8,  Y = ys & 7
   LDA r_c
   LSR A                                ; k = byte column 0..63
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
   ASL A                                ; (ys >> 3) * 2 pages
   CLC
   ADC PTR+1
   ADC DV_BACKHI                        ; the buffer being drawn
   STA PTR+1
   LDA r_ys
   AND #7
   TAY
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
   LDA (PTR),Y
   AND r_keep
   BCS @o
   ORA r_ev
   BRA @w
@o:
   ORA r_od
@w:
   STA (PTR),Y
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
