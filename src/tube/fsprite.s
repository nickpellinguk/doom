; ============================================================================
; tube/fsprite.s -- the fill server's SPRITES (docs/tube_master.md H6),
; included by fserve.s. sprite_ref.records is the spec; the list's
; SPRITE groups are tube_dl.encode's.
;
;   fs_obj      a $02 request (the host's staged object): its spans into
;               the pool, then its byte columns (k, y0, y1, art column,
;               v0) into fo_buf and a row in the object table
;   fs_sprites  (the frame's end, after the FILLs) the objects farthest
;               first (equal depths in request order), their columns as
;               SPRITE groups: $20 | n-1, kind, k0, step, then 5 bytes a
;               column; a group runs while kind and step hold and k
;               steps by one, at most 32
;
; Per object (biased lines, s16):
;   H    yb - yt, grown by fo_grow (256ths) base anchored; H <= 0: none
;   dvl  (h << 8) // (H + 1)                   the texel step a line, 8.8
;   u    (w << 7) + (4k - cx) * dvl            the art column, 8.8, drawn
;                                              while 0 <= u < w << 8
;   The columns are found without a multiply a column: q = (w << 7) //
;   dvl bounds them (u >= 0 iff 4k - cx >= -q), the first is
;   k = ceil((cx - q) / 4) (at least 0), and u steps 4 * dvl a column.
;   lines [yt, yb) inside the span over x = 4k and the view; the row
;   counts from yt's pair: v0 = ((y0 & ~1) - (yt & ~1)) * dvl.
; ============================================================================
FO_MAX  = 32                            ; objects a frame (more: dropped)
FO_COL  = 6                             ; a column's bytes in fo_buf
FO_BUFSZ = $0600                        ; 256 columns (35 seen)
VIEW_Y1 = Y_BIAS + VIEW_LINES - 1       ; the view's last line (biased)

.segment "FSCODE"
; ---- fs_obj ----------------------------------------------------------------
fs_obj:
   LDA #9                               ; kind, depth, cx, yt, yb
   JSR fs_need
   GETY
   STA fo_kind
   GETY
   STA fo_vy
   GETY
   STA fo_vy+1
   GETY
   STA fo_cx
   GETY
   STA fo_cx+1
   CLC                                  ; yt, yb: + Y_BIAS (the spans' lines)
   GETY
   ADC #Y_BIAS
   STA fo_yt
   GETY
   ADC #0
   STA fo_yt+1
   CLC
   GETY
   ADC #Y_BIAS
   STA fo_yb
   GETY
   ADC #0
   STA fo_yb+1
   JSR fs_used
   JSR fs_pool                          ; its spans: the pool, every live one
   JSR fs_poll
   LDA fo_n                             ; the table full: dropped
   CMP #FO_MAX
   BCC :+
   DROP
   RTS
:
; --- H = yb - yt, grown; nothing if H <= 0 ---
   SEC
   LDA fo_yb
   SBC fo_yt
   STA fm_a
   STA fo_h
   LDA fo_yb+1
   SBC fo_yt+1
   STA fm_a+1
   STA fo_h+1
   BMI @none
   ORA fm_a
   BNE :+
@none:
   RTS
:  LDX fo_kind
   LDA fo_grow,X
   STA fm_b
   STZ fm_b+1
   JSR fs_mul16                         ; H * g
   CLC
   LDA fm_r                             ; (+ 128) >> 8
   ADC #$80
   LDA fm_r+1
   ADC fo_h
   STA fo_h
   LDA fm_r+2
   ADC fo_h+1
   STA fo_h+1
   SEC                                  ; yt = yb - H
   LDA fo_yb
   SBC fo_h
   STA fo_yt
   LDA fo_yb+1
   SBC fo_h+1
   STA fo_yt+1
; --- dvl = (h << 8) // (H + 1) ---
   LDX fo_kind
   STZ m_p
   LDA fo_sh,X
   STA m_p+1
   STZ m_p+2
   STZ m_p+3
   CLC
   LDA fo_h
   ADC #1
   STA m_b
   LDA fo_h+1
   ADC #0
   STA m_b+1
   JSR div32
   LDA m_p
   STA fo_dv
   LDA m_p+1
   STA fo_dv+1
   LDX fo_kind                          ; w << 7, w << 8
   LDA fo_sw,X
   STA fo_w8+1
   STZ fo_w8
   LSR A
   STA fo_w7+1
   LDA #0
   ROR A
   STA fo_w7
   LDA fo_dv                            ; du = 4 dvl
   ASL A
   STA fo_du
   LDA fo_dv+1
   ROL A
   STA fo_du+1
   ASL fo_du
   ROL fo_du+1
   LDA fo_dv
   ORA fo_dv+1
   BNE @q
   STZ fo_k                             ; dvl = 0 (H > 12288): every column
   LDA fo_w7                            ;  at the art's centre
   STA fo_u
   LDA fo_w7+1
   STA fo_u+1
   JMP @cols
; --- q = (w << 7) // dvl; k = max(0, ceil((cx - q) / 4)) ---
@q:
   LDA fo_w7
   STA m_p
   LDA fo_w7+1
   STA m_p+1
   STZ m_p+2
   STZ m_p+3
   LDA fo_dv
   STA m_b
   LDA fo_dv+1
   STA m_b+1
   JSR div32
   LDA m_p                              ; q (<= 3968)
   STA fo_q
   LDA m_p+1
   STA fo_q+1
   SEC                                  ; cx - q + 3, >> 2 (arithmetic)
   LDA fo_cx
   SBC fo_q
   STA fo_t
   LDA fo_cx+1
   SBC fo_q+1
   STA fo_t+1
   CLC
   LDA fo_t
   ADC #3
   STA fo_t
   LDA fo_t+1
   ADC #0
   CMP #$80
   ROR A
   ROR fo_t
   CMP #$80
   ROR A
   ROR fo_t
   STA fo_t+1
   TAX                                  ; (N: the sign)
   BPL :+
   STZ fo_t                             ; before column 0: from 0
   STZ fo_t+1
:  LDA fo_t+1
   BNE @nothing                         ; k >= 64: none
   LDA fo_t
   CMP #64
   BCS @nothing
   STA fo_k
; --- d = 4k - cx; d > q: none; u = (w << 7) + d * dvl ---
   STZ fo_t+1
   ASL A
   ROL fo_t+1
   ASL A
   ROL fo_t+1
   SEC
   SBC fo_cx
   STA fo_t
   LDA fo_t+1
   SBC fo_cx+1
   STA fo_t+1
   SEC                                  ; q - d < 0: none
   LDA fo_q
   SBC fo_t
   LDA fo_q+1
   SBC fo_t+1
   BVC :+
   EOR #$80
:  BPL :+
@nothing:
   RTS
:  LDA fo_t+1                           ; |d| * dvl (<= w << 7)
   STA fo_neg
   BPL :+
   SEC
   LDA #0
   SBC fo_t
   STA fo_t
   LDA #0
   SBC fo_t+1
   STA fo_t+1
:  LDA fo_t
   STA fm_a
   LDA fo_t+1
   STA fm_a+1
   LDA fo_dv
   STA fm_b
   LDA fo_dv+1
   STA fm_b+1
   JSR fs_mul16
   LDA fo_neg
   BMI :+
   CLC
   LDA fo_w7
   ADC fm_r
   STA fo_u
   LDA fo_w7+1
   ADC fm_r+1
   STA fo_u+1
   BRA @cols
:  SEC
   LDA fo_w7
   SBC fm_r
   STA fo_u
   LDA fo_w7+1
   SBC fm_r+1
   STA fo_u+1
; --- the columns ---
@cols:
   LDA fo_bp                            ; the object's first column
   STA fo_p
   LDA fo_bp+1
   STA fo_p+1
   STZ fo_c
   LDA zp_head
   STA fo_sx                            ; the span cursor (moves right)
   LDA #$FF
   STA fo_lr+1                          ; no row cached
@col:
   LDA fo_u+1                           ; u >= w << 8: past the art
   CMP fo_w8+1
   BCC :+
   BNE @done
   LDA fo_u
   CMP fo_w8
   BCS @done
:  LDA fo_k
   ASL A
   ASL A
   STA fo_x                             ; x = 4k
@sp:
   LDX fo_sx                            ; the span over x
   BEQ @next
   CMP POOL_XEND,X
   BCC :+
   LDA POOL_NEXT,X
   STA fo_sx
   LDA fo_x
   BRA @sp
:  CMP POOL_XSTART,X
   BCC @next                            ; a gap: no span over x
   JSR fo_lines
@next:
   CLC
   LDA fo_u
   ADC fo_du
   STA fo_u
   LDA fo_u+1
   ADC fo_du+1
   STA fo_u+1
   BCS @done                            ; (past 64K: past the art)
   INC fo_k
   LDA fo_k
   CMP #64
   BCC @col
@done:
   LDA fo_c                             ; a row in the table if it has any
   BEQ @rts
   LDX fo_n
   LDA fo_vy
   STA fo_tvl,X
   LDA fo_vy+1
   STA fo_tvh,X
   LDA fo_kind
   STA fo_tk,X
   LDA fo_dv
   STA fo_tsl,X
   LDA fo_dv+1
   STA fo_tsh,X
   LDA fo_p
   STA fo_tpl,X
   LDA fo_p+1
   STA fo_tph,X
   LDA fo_c
   STA fo_tc,X
   INC fo_n
@rts:
   RTS

; fo_lines: column fo_k's lines inside span X: a column in fo_buf if any
fo_lines:
   LDA POOL_TXLO,X                      ; top
   STA fe_x0
   LDA POOL_TDEN,X
   STA fe_den
   LDA POOL_TL,X
   STA fe_y0
   LDA POOL_TR,X
   STA fe_y1
   JSR fo_edge
   LDA fe_r                             ; y0 = max(yt, top, Y_BIAS)
   STA fo_y0
   LDA fe_r+1
   STA fo_y0+1
   SEC                                  ; top < yt: y0 = yt
   LDA fo_y0
   SBC fo_yt
   LDA fo_y0+1
   SBC fo_yt+1
   BVC :+
   EOR #$80
:  BPL :+
   LDA fo_yt
   STA fo_y0
   LDA fo_yt+1
   STA fo_y0+1
:
   LDX fo_sx                            ; bottom (its own anchor)
   LDA POOL_BXLO,X
   STA fe_x0
   LDA POOL_BDEN,X
   STA fe_den
   LDA POOL_BL,X
   STA fe_y0
   LDA POOL_BR,X
   STA fe_y1
   JSR fo_edge
   SEC                                  ; y1 = min(yb - 1, bot, VIEW_Y1)
   LDA fo_yb
   SBC #1
   STA fo_y1
   LDA fo_yb+1
   SBC #0
   STA fo_y1+1
   SEC                                  ; bot < y1: y1 = bot
   LDA fe_r
   SBC fo_y1
   LDA fe_r+1
   SBC fo_y1+1
   BVC :+
   EOR #$80
:  BPL :+
   LDA fe_r
   STA fo_y1
   LDA fe_r+1
   STA fo_y1+1
:  LDA fo_y1+1                          ; past the view's last line: it
   BMI @y0                              ;  (a negative y1 stays: y0 >= 48)
   BNE :+
   LDA fo_y1
   CMP #VIEW_Y1 + 1
   BCC @y0
:  LDA #VIEW_Y1
   STA fo_y1
   STZ fo_y1+1
@y0:
   LDA fo_y0+1                          ; y0 < Y_BIAS: Y_BIAS
   BMI :+
   BNE @cmp
   LDA fo_y0
   CMP #Y_BIAS
   BCS @cmp
:  LDA #Y_BIAS
   STA fo_y0
   STZ fo_y0+1
@cmp:
   SEC                                  ; y0 > y1: nothing
   LDA fo_y1
   SBC fo_y0
   LDA fo_y1+1
   SBC fo_y0+1
   BVC :+
   EOR #$80
:  BPL :+
   RTS
:  LDA fo_bp+1                          ; the buffer full: dropped
   CMP #>(fo_buf + FO_BUFSZ - FO_COL)
   BCC :+
   DROP
   RTS
; v0 = ((y0 & ~1) - (yt & ~1)) * dvl (one multiply a different y0)
:  LDA fo_y0
   AND #$FE
   STA fo_t
   LDA fo_yt
   AND #$FE
   STA fo_t+1
   SEC
   LDA fo_t
   SBC fo_t+1
   STA fm_a
   LDA fo_y0+1
   SBC fo_yt+1
   STA fm_a+1
   CMP fo_lr+1
   BNE :+
   LDA fm_a
   CMP fo_lr
   BEQ @have
:  LDA fm_a
   STA fo_lr
   LDA fm_a+1
   STA fo_lr+1
   LDA fo_dv
   STA fm_b
   LDA fo_dv+1
   STA fm_b+1
   JSR fs_mul16
   LDA fm_r
   STA fo_lv
   LDA fm_r+1
   STA fo_lv+1
@have:
   LDA fo_bp
   STA PTR
   LDA fo_bp+1
   STA PTR+1
   LDY #0
   LDA fo_k
   STA (PTR),Y
   INY
   LDA fo_y0                            ; the lines, less Y_BIAS
   SEC
   SBC #Y_BIAS
   STA (PTR),Y
   INY
   LDA fo_y1
   SEC
   SBC #Y_BIAS
   STA (PTR),Y
   INY
   LDA fo_u+1                           ; the art column
   STA (PTR),Y
   INY
   LDA fo_lv
   STA (PTR),Y
   INY
   LDA fo_lv+1
   STA (PTR),Y
   CLC
   LDA fo_bp
   ADC #FO_COL
   STA fo_bp
   BCC :+
   INC fo_bp+1
:  INC fo_c
   RTS

; fo_edge: fe_r (s16) = y at fo_x on the line from (fe_x0, fe_y0) to
; ((fe_x0 + fe_den) & $FF, fe_y1): endpoint_spans._interp on the decoded
; span, y0 + floor((y1 - y0)(x - x0) / (x1 - x0)) -- with d = x1 - x0 made
; positive (off's sign flips with it), P = |off| |dy| and the product's
; sign s: y0 + P // d, or y0 - (P + d - 1) // d (the floor of a negative)
fo_edge:
   LDA fe_x0
   CLC
   ADC fe_den
   SEC
   SBC fe_x0                            ; d = x1 - x0 (8 bits, then signed)
   BNE :+
   LDA fe_y0                            ; x1 = x0: y0
   STA fe_r
   STZ fe_r+1
   RTS
:  STA fe_d
   SEC                                  ; off = x - x0 (s16)
   LDA fo_x
   SBC fe_x0
   STA fe_o
   LDA #0
   SBC #0
   STA fe_o+1
   LDA fe_x0                            ; x1 < x0 (d < 0): negate both
   CLC
   ADC fe_den
   CMP fe_x0
   BCS :+
   SEC
   LDA #0
   SBC fe_d
   STA fe_d
   SEC
   LDA #0
   SBC fe_o
   STA fe_o
   LDA #0
   SBC fe_o+1
   STA fe_o+1
:  LDA fe_o+1                           ; |off|, the sign in fe_on
   STA fe_on
   BPL :+
   SEC
   LDA #0
   SBC fe_o
   STA fe_o
:  SEC                                  ; |dy|, its sign into fe_on
   LDA fe_y1
   SBC fe_y0
   BCS :+
   EOR #$FF
   ADC #1                               ; (C = 0 from the SBC)
   PHA
   LDA fe_on
   EOR #$80
   STA fe_on
   PLA
:  STA fm_b                             ; P = |off| |dy|
   STZ fm_b+1
   LDA fe_o
   STA fm_a
   STZ fm_a+1
   JSR fs_mul16
   LDA fm_r
   STA m_p
   LDA fm_r+1
   STA m_p+1
   LDA fe_on                            ; negative: P + d - 1
   BPL :+
   LDA fe_d
   DEC A
   CLC
   ADC m_p
   STA m_p
   BCC :+
   INC m_p+1
:  STZ m_p+2
   STZ m_p+3
   LDA fe_d
   STA m_b
   STZ m_b+1
   JSR div32
   LDA fe_on                            ; y = y0 + q, or y0 - q
   BMI :+
   CLC
   LDA fe_y0
   ADC m_p
   STA fe_r
   LDA #0
   ADC m_p+1
   STA fe_r+1
   RTS
:  SEC
   LDA fe_y0
   SBC m_p
   STA fe_r
   LDA #0
   SBC m_p+1
   STA fe_r+1
   RTS

; fs_mul16: fm_r (32) = fm_a * fm_b (16 x 16, unsigned; fm_a destroyed)
fs_mul16:
   STZ fm_r+2
   STZ fm_r+3
   LDX #16
@b:
   LSR fm_a+1
   ROR fm_a
   BCC :+
   CLC
   LDA fm_r+2
   ADC fm_b
   STA fm_r+2
   LDA fm_r+3
   ADC fm_b+1
   STA fm_r+3
:  ROR fm_r+3
   ROR fm_r+2
   ROR fm_r+1
   ROR fm_r
   DEX
   BNE @b
   RTS

; ---- fs_sprites: the objects farthest first, as SPRITE groups ------------
fs_sprites:
   STZ fs_gn                            ; no group open
@pick:
   JSR fs_poll
   LDX #$FF                             ; the farthest left (the first of
   LDY #0                               ;  equals: request order)
@scan:
   CPY fo_n
   BCS @got
   LDA fo_tc,Y                          ; (taken: count 0)
   BEQ @sn
   CPX #$FF
   BEQ @take
   LDA fo_tvl,X                         ; depth[Y] > depth[X]?
   CMP fo_tvl,Y
   LDA fo_tvh,X
   SBC fo_tvh,Y
   BCS @sn
@take:
   TYA
   TAX
@sn:
   INY
   BRA @scan
@got:
   CPX #$FF
   BNE :+
   JMP fs_sclose                        ; none left
:  LDA fo_tpl,X
   STA PTR
   LDA fo_tph,X
   STA PTR+1
   LDA fo_tc,X
   STA fo_c
   STZ fo_tc,X                          ; taken
   LDA fo_tk,X
   STA fo_kind
   LDA fo_tsl,X
   STA fo_dv
   LDA fo_tsh,X
   STA fo_dv+1
@col:
   LDY #0
   LDA (PTR),Y                          ; k
   STA fo_k
   LDA fs_gn                            ; joins the open group: kind and
   BEQ @open                            ;  step hold, k steps by one, < 32
   CMP #32
   BCS @open
   LDA fo_kind
   CMP fo_gk
   BNE @open
   LDA fo_dv
   CMP fo_gs
   BNE @open
   LDA fo_dv+1
   CMP fo_gs+1
   BNE @open
   LDA fo_gl
   INC A
   CMP fo_k
   BEQ @put
@open:
   JSR fs_sclose
   LDA fs_op+1                          ; the list full: dropped
   CMP fs_lim
   BCC :+
   DROP
   BRA @cn
:  LDA fs_op
   STA fs_gh
   LDA fs_op+1
   STA fs_gh+1
   LDA #$20
   PUTO
   LDA fo_kind
   STA fo_gk
   PUTO
   LDA fo_k
   PUTO
   LDA fo_dv
   STA fo_gs
   PUTO
   LDA fo_dv+1
   STA fo_gs+1
   PUTO
   BRA @ent                             ; (room checked: header and entry)
@put:
   LDA fs_op+1                          ; the list full: dropped
   CMP fs_lim
   BCC @ent
   DROP
   BRA @cn
@ent:
   INC fs_gn
   LDA fo_k
   STA fo_gl
   LDY #1                               ; y0, y1, col, v0
@cp:
   LDA (PTR),Y
   PUTO
   INY
   CPY #FO_COL
   BNE @cp
@cn:
   CLC
   LDA PTR
   ADC #FO_COL
   STA PTR
   BCC :+
   INC PTR+1
:  DEC fo_c
   BEQ :+
   JMP @col
:  JMP @pick

; fs_sclose: the open SPRITE group's header, $20 | n - 1
fs_sclose:
   LDA fs_gn
   BEQ :+
   DEC A
   ORA #$20
   LDX fs_gh
   STX fs_cp
   LDX fs_gh+1
   STX fs_cp+1
   STA (fs_cp)
   STZ fs_gn
:  RTS

fo_grow: .byte 0, 0, 38, 38, 38, 38, 38  ; sprite_ref.GROW: pickups +15%
.include "fsprite_tab.s"

.segment "FSSPR"
fo_n:    .res 1                         ; objects in the table
fo_tvl:  .res FO_MAX                    ; the table: depth, kind, step,
fo_tvh:  .res FO_MAX                    ;  first column, columns (0: taken)
fo_tk:   .res FO_MAX
fo_tsl:  .res FO_MAX
fo_tsh:  .res FO_MAX
fo_tpl:  .res FO_MAX
fo_tph:  .res FO_MAX
fo_tc:   .res FO_MAX
fo_bp:   .res 2                         ; fo_buf's next column
fo_p:    .res 2                         ; this object's first
fo_c:    .res 1                         ;  and its columns
fo_kind: .res 1
fo_vy:   .res 2
fo_cx:   .res 2
fo_yt:   .res 2
fo_yb:   .res 2
fo_h:    .res 2
fo_dv:   .res 2                         ; dvl
fo_du:   .res 2                         ; 4 dvl
fo_w7:   .res 2
fo_w8:   .res 2
fo_q:    .res 2
fo_t:    .res 2
fo_neg:  .res 1
fo_u:    .res 2
fo_k:    .res 1
fo_x:    .res 1
fo_sx:   .res 1                         ; the span cursor
fo_y0:   .res 2
fo_y1:   .res 2
fo_lr:   .res 2                         ; the last v0's row offset, v0
fo_lv:   .res 2
fo_gk:   .res 1                         ; the open group: kind, step, last k
fo_gs:   .res 2
fo_gl:   .res 1
fe_x0:   .res 1                         ; fo_edge
fe_den:  .res 1
fe_y0:   .res 1
fe_y1:   .res 1
fe_d:    .res 2
fe_o:    .res 2
fe_on:   .res 1
fe_r:    .res 2
fm_a:    .res 2                         ; fs_mul16
fm_b:    .res 2
fm_r:    .res 4
.segment "FSBUF"
fo_buf:  .res FO_BUFSZ                  ; the frame's sprite columns
