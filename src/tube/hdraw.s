; The Tube Master host drawer (docs/tube_master.md step 3): draws one
; frame's display list, read from the Tube's register 1, into the screen
; buffer. tube_dl.py is the spec (draw(), encode()); test_tube_host.py
; holds this to it byte for byte.
;
; Records (tube_dl.encode): WALL groups $80 | n-1, tid, k0, then n byte
; columns; SPAN groups $40 | n-1, y (or $88 + y/2: pairs), then n spans;
; FILL $01, k, y0, y1, byte; $00 ends the frame.
;
; Textures and flats are the Master's own images (master_assets): a
; stored wall column at ptr + (i >> 3) * 256 + (i & 7) in its bank, its
; texel rows 8 bytes apart from rowoff; a flat one page, byte (v & $F0) |
; (u >> 4). Texel reads are self-modified LDA abs,X (X the row); the
; screen is written STA (scr),Y. What is constant for a group, a column
; or a span (row mask and offset, columns, steps, flat page, the span's
; end) is patched into the loops' operands. hdraw_tab.inc (tube_host.py)
; supplies SCREEN, the texture and flat tables, lsr4 and flip.

; MASTER (the Tube Master host, docs/tube_master.md H4): assembled into the
; engine link with TUBE. The code and its tables sit in HAZEL (MFILL); the
; tables are .res, seeded by symbol (tube_host.master_tables); lsr4 and
; flip are the fill's own hi16 and flip pages. The screen is the back
; buffer (DV_BACKHI) in shadow RAM, written with ACCCON X set for the
; whole list; zero page is the engine's vertex block, seg scratch that is
; dead between frames (the split IRQ and the music touch none of it).

.ifdef MASTER
        .setcpu "65C02"
        .include "../zp.inc"
        .import hi16, flip
lsr4    = hi16
SKY_BYTE = $3D                          ; master_assets.SKY_BYTE (cyan + white)
PAIR_Y  = $88                           ; tube_dl.PAIR_Y
ACC_DY  = $09                           ; (master/mfill.s) render-time ACCCON,
ACC_DXY = $0D                           ;  and with X: the CPU on the shadow
ZB      = VX1                           ; 26 bytes of scratch, then 2 more
.export hd_frame
.export tx_bank, tx_ph, tx_ro, tx_rm, tx_ixl, tx_ixh, tx_ix, fl_bank, fl_page
.else
        .include "hdraw_tab.inc"
ZB      = $70
.endif

scr     = ZB + $00                      ; screen pointer (page-aligned row)
v       = ZB + $02                           ; a wall's v (5.11); a span's v (4.4)
vst     = ZB + $04                           ; a wall's step a line pair (2 x the
                                        ;  list's: (y & ~1) moves by 2)
dha     = ZB + $06                           ; right row delta: hi = dh (5.3)
ddh     = ZB + $08                           ;  and its step a character row
nl      = ZB + $0A                           ; lines in the run
ln      = ZB + $0B                           ; first line (wall, fill), line (span)
cnt     = ZB + $0C                           ; entries left in the group
kk      = ZB + $0D                           ; byte column
np      = ZB + $0E                           ; line pairs left
tail    = ZB + $0F                           ; a last line alone
tph     = ZB + $10                           ; texture ptr hi
ixp     = ZB + $11                           ; 2: the texture's column index
own     = ZB + $13                           ; bit 7: the right strip's own row
cur     = ZB + $14                           ; a fill's byte
tmp     = ZB + $15
su      = ZB + $16                           ; a span's u
k1      = ZB + $17                           ; a span's last byte column
pair    = ZB + $18                           ; b7 pair span, b6 odd line
slo     = ZB + $19                           ; a span line's place in its cell
.ifdef MASTER                           ; (MASTER: zp_tmp0, past VX1's 26)
spg     = zp_tmp0
odb     = zp_tmp0 + 1
.else
spg     = ZB + $1A                           ; its screen row's page
odb     = ZB + $1B                      ; a fill's odd-line byte
.endif

; one byte off the Tube (register 1, polled)
.macro GETB
.local w
w:      bit $FEE0
        bpl w
        lda $FEE1
.endmacro

.ifdef MASTER
        .segment "MFILL"
SCRHI   = DV_BACKHI                     ; the back buffer's first page
hd_frame:
        lda #ACC_DXY                    ; the shadow buffer, for the list
        sta $FE34
        jsr next
        lda #ACC_DY
        sta $FE34
        rts
.else
        .segment "CODE"
hd_frame:
.endif
next:   GETB
        beq done
        bmi wall
        cmp #$40
        bcc :+
        jmp span
:       jmp fill
done:   rts

; ---------------------------------------------------------------------------
; WALL: a texture over consecutive byte columns
; ---------------------------------------------------------------------------
wall:   and #$3F
        sta cnt
        GETB                            ; tid: bank, page, column index,
        tax                             ;  row mask and offset into the
        lda tx_bank,x                   ;  loops for the whole group
        sta $FE30
        lda tx_ph,x
        sta tph
        lda tx_ixl,x
        sta ixp
        lda tx_ixh,x
        sta ixp+1
        lda tx_rm,x
        sta wa1+1
        sta wa2+1
        sta wa3+1
        sta wa4+1
        sta wa5+1
        sta wa6+1
        lda tx_ro,x
        sta wo1+1
        sta wo2+1
        sta wo3+1
        sta wo4+1
        sta wo5+1
        sta wo6+1
        GETB                            ; k0
        sta kk
        asl a                           ; the group's screen columns: scr lo
        asl a                           ;  = (kk & 31) * 8 and wch = the
        asl a                           ;  page of kk's half (wend steps
        sta scr                         ;  both a column on)
        lda kk
        lsr a
        lsr a
        lsr a
        lsr a
        lsr a
        clc
.ifdef MASTER
        adc SCRHI
.else
        adc #>SCREEN
.endif
        sta wch
wcol:   GETB                            ; y0
        sta ln
        GETB                            ; y1
        sec
        sbc ln
        sta nl
        inc nl                          ; lines
        GETB                            ; cl | own
        sta own
        and #$7F
        jsr colad
        sta wl1+1
        sta wl2+1
        sta wl3+1
        sta wl4+1
        stx wl1+2
        stx wl2+2
        stx wl3+2
        stx wl4+2
        GETB                            ; cr
        jsr colad
        sta wr1+1
        sta wr2+1
        sta wr3+1
        sta wr4+1
        stx wr1+2
        stx wr2+2
        stx wr3+2
        stx wr4+2
        GETB
        sta v
        GETB
        sta v+1
        GETB
        asl a                           ; the step, doubled, into the pair
        sta vst                         ;  loops
        sta ws1l+1
        sta wo1l+1
        GETB
        rol a
        sta vst+1
        sta ws1h+1
        sta wo1h+1
        bit own
        bpl :+
        GETB                            ; dh0
        sta dha+1
        lda #$80
        sta dha
        GETB
        sta ddh
        GETB
        sta ddh+1
:       lda ln                          ; scr, Y for (ln, kk): scr lo and
        lsr a                           ;  wch kept by the group, so only
        lsr a                           ;  the character row's page
        and #$FE
        clc
        adc wch
        sta scr+1
        lda ln
        and #7
        tay
        lda ln
        lsr a
        bcc wl_even
        jsr wbyte                       ; an odd first line: alone
        sta (scr),y
        jsr vstep
        jsr wnext
        dec nl
        bne wl_even
        jmp wend
wl_even:
        lda nl
        and #1
        sta tail
        lda nl
        lsr a
        sta np
        beq wl_last
        bit own
        bmi wo_pair
; shared: the right strip reads the left's row
ws_pair:
        lda v+1
wa1:    and #$FF                        ; (patched: row mask)
wo1:    ora #$FF                        ; (patched: row offset)
        tax
wr1:    lda $FFFF,x                     ; (patched: the right column)
        lsr a                           ; (texels are left pixels: C = 0)
wl1:    ora $FFFF,x                     ; (patched: the left column)
        sta (scr),y
        iny
        sta (scr),y
        iny
        lda v
ws1l:   adc #$FF                        ; (patched: 2 x step; C clear)
        sta v
        lda v+1
ws1h:   adc #$FF
        sta v+1
        cpy #8
        beq ws_row
ws_nx:  dec np
        bne ws_pair
        bra wl_last
ws_row: jsr nextrow
        bra ws_nx
; own: the right strip's row is hi(v) + dh
wo_pair:
        lda v+1
        clc
        adc dha+1
wa2:    and #$FF
wo2:    ora #$FF
        tax
wr2:    lda $FFFF,x
        lsr a                           ; (C = 0)
        sta tmp
        lda v+1
wa3:    and #$FF
wo3:    ora #$FF
        tax
wl2:    lda $FFFF,x
        ora tmp
        sta (scr),y
        iny
        sta (scr),y
        iny
        lda v
wo1l:   adc #$FF
        sta v
        lda v+1
wo1h:   adc #$FF
        sta v+1
        cpy #8
        beq wo_row
wo_nx:  dec np
        bne wo_pair
        bra wl_last
wo_row: jsr nextrow
        jsr dhstep
        bra wo_nx
wl_last:
        lda tail                        ; a last line alone
        beq wend
        jsr wbyte
        sta (scr),y
wend:   inc kk
        lda scr                         ; the next byte column: + 8, and past
        clc                             ;  column 31 the next page
        adc #8
        sta scr
        bcc :+
        inc wch
:       dec cnt
        bmi :+
        jmp wcol
:       jmp next

; wbyte: the line's byte from v (and dh), in A
wbyte:  bit own
        bmi :+
        lda v+1
wa4:    and #$FF
wo4:    ora #$FF
        tax
wr3:    lda $FFFF,x
        lsr a
wl3:    ora $FFFF,x
        rts
:       lda v+1
        clc
        adc dha+1
wa5:    and #$FF
wo5:    ora #$FF
        tax
wr4:    lda $FFFF,x
        lsr a
        sta tmp
        lda v+1
wa6:    and #$FF
wo6:    ora #$FF
        tax
wl4:    lda $FFFF,x
        ora tmp
        rts

; vstep: v += vst
vstep:  clc
        lda v
        adc vst
        sta v
        lda v+1
        adc vst+1
        sta v+1
        rts

; wnext: on a line (the next character row: dh too)
wnext:  iny
        cpy #8
        bne :+
        jsr nextrow
        bit own
        bpl :+
        jsr dhstep
:       rts

; dhstep: dh += ddh (a character row on)
dhstep: clc
        lda dha
        adc ddh
        sta dha
        lda dha+1
        adc ddh+1
        sta dha+1
        rts

; colad: the stored column for logical column A: A lo, X hi
colad:  tay
        lda (ixp),y
        pha
        lsr a
        lsr a
        lsr a
        clc
        adc tph
        tax
        pla
        and #7
        rts

; cellptr: scr = SCREEN + (ln >> 3) * 512 + (kk >> 5) * 256, Y = (kk & 31)
; * 8 + (ln & 7)
cellptr:
        lda kk
        asl a
        asl a
        asl a
        sta scr
        lda kk
        lsr a
        lsr a
        lsr a
        lsr a
        lsr a
        sta tmp
        lda ln
        lsr a
        lsr a
        and #$FE
        clc
        adc tmp
.ifdef MASTER
        adc SCRHI
.else
        adc #>SCREEN
.endif
        sta scr+1
        lda ln
        and #7
        tay
        rts

; nextrow: scr += 512, Y = 0
nextrow:
        ldy #0
        lda scr+1
        clc
        adc #2
        sta scr+1
        rts

; ---------------------------------------------------------------------------
; FILL: one byte column of one byte (the sky's FLIPped on odd lines)
; ---------------------------------------------------------------------------
fill:   GETB
        sta kk
        GETB
        sta ln
        GETB
        sec
        sbc ln
        sta nl
        inc nl
        GETB
        sta cur
        sta odb
        cmp #SKY_BYTE
        bne :+
        tax
        lda flip,x
        sta odb
:       jsr cellptr
        lda ln
        lsr a
        bcc fl_lp
        lda odb                         ; an odd first line
        bra fl_put
fl_lp:  lda cur
        sta (scr),y
        dec nl
        beq fl_end
        iny
        lda odb
fl_put: sta (scr),y
        dec nl
        beq fl_end
        iny
        cpy #8
        bne fl_lp
        jsr nextrow
        bra fl_lp
fl_end: jmp next

; ---------------------------------------------------------------------------
; SPAN: one line (or line pair) of floor or ceiling
; ---------------------------------------------------------------------------
span:   and #$3F
        sta cnt
        GETB                            ; y, or PAIR_Y + y/2
        ldx #0
        cmp #PAIR_Y
        bcc :+
        sbc #PAIR_Y
        asl a
        ldx #$80
:       sta ln
        lsr a                           ; C = an odd line
        txa
        bcc :+
        ora #$40
:       sta pair                        ; b7 pair, b6 odd line
        lda ln
        and #7
        sta slo
        lda ln
        lsr a
        lsr a
        and #$FE
        clc
.ifdef MASTER
        adc SCRHI
.else
        adc #>SCREEN
.endif
        sta spg
        stz scr
sp_each:
        GETB                            ; k0
        sta kk
        GETB
        sta k1
        GETB                            ; flat, or $FF: far
        cmp #$FF
        beq sp_far
        tax
        lda fl_bank,x
        sta $FE30
        lda fl_page,x
        sta fe_tx+2
        sta fo_tx+2
        sta fp_tx+2
        GETB
        sta su
        GETB
        sta v
        GETB
        sta fe_du+1
        sta fo_du+1
        sta fp_du+1
        GETB
        sta fe_dv+1
        sta fo_dv+1
        sta fp_dv+1
        lda #<flat_run
        ldx #>flat_run
        bra sp_go
sp_far: GETB                            ; the far tone: even, odd bytes
        sta cur
        tax
        lda flip,x
        sta odb
        lda #<far_run
        ldx #>far_run
sp_go:  sta sp_jmp+1
        stx sp_jmp+2
        lda kk                          ; byte columns 0-31, then 32-63:
        cmp #32                         ;  one screen page each
        bcs sp_hi
        lda k1
        cmp #32
        bcc :+
        lda #31
:       jsr sp_run
        lda k1
        cmp #32
        bcc sp_done
        lda #32
        sta kk
sp_hi:  lda k1
        jsr sp_run
sp_done:
        dec cnt
        bmi :+
        jmp sp_each
:       jmp next

; sp_run: byte columns kk..A within one page: scr, Y, the end, then the
; flat or far loop
sp_run: and #31
        asl a
        asl a
        asl a
        clc
        adc slo
        adc #8                          ; Y past the last byte (mod 256)
        sta fe_end+1
        sta fo_end+1
        sta fp_end+1
        sta re_end+1
        sta ro_end+1
        sta rp_end+1
        lda kk
        lsr a
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc spg
        sta scr+1
        lda kk
        and #31
        asl a
        asl a
        asl a
        adc slo                         ; (C clear)
        tay
sp_jmp: jmp $FFFF                       ; (patched: flat_run / far_run)

flat_run:
        bit pair
        bmi fp_lp
        bvs fo_lp
fe_lp:  lda v                           ; an even line
        and #$F0
        ldx su
        ora lsr4,x
        tax
fe_tx:  lda $FF00,x                     ; (patched: the flat's page)
        sta (scr),y
        lda su
        clc
fe_du:  adc #$FF                        ; (patched: du)
        sta su
        lda v
        clc
fe_dv:  adc #$FF                        ; (patched: dv)
        sta v
        tya
        clc
        adc #8
        tay
fe_end: cpy #$FF                        ; (patched: the end)
        bne fe_lp
        rts
fo_lp:  lda v                           ; an odd line: FLIPped
        and #$F0
        ldx su
        ora lsr4,x
        tax
fo_tx:  lda $FF00,x
        tax
        lda flip,x
        sta (scr),y
        lda su
        clc
fo_du:  adc #$FF
        sta su
        lda v
        clc
fo_dv:  adc #$FF
        sta v
        tya
        clc
        adc #8
        tay
fo_end: cpy #$FF
        bne fo_lp
        rts
fp_lp:  lda v                           ; a line pair
        and #$F0
        ldx su
        ora lsr4,x
        tax
fp_tx:  lda $FF00,x
        sta (scr),y
        tax
        lda flip,x
        iny
        sta (scr),y
        lda su
        clc
fp_du:  adc #$FF
        sta su
        lda v
        clc
fp_dv:  adc #$FF
        sta v
        tya
        clc
        adc #7
        tay
fp_end: cpy #$FF
        bne fp_lp
        rts

far_run:
        bit pair
        bmi rp_lp
        bvs ro_go
        lda cur
re_lp:  sta (scr),y                     ; even line
        tax
        tya
        clc
        adc #8
        tay
        txa
re_end: cpy #$FF
        bne re_lp
        rts
ro_go:  lda odb
ro_lp:  sta (scr),y                     ; odd line
        tax
        tya
        clc
        adc #8
        tay
        txa
ro_end: cpy #$FF
        bne ro_lp
        rts
rp_lp:  lda cur                         ; line pair
        sta (scr),y
        iny
        lda odb
        sta (scr),y
        tya
        clc
        adc #7
        tay
rp_end: cpy #$FF
        bne rp_lp
        rts

.ifdef MASTER
; the tables, seeded by symbol (tube_host.master_tables)
tx_bank: .res 32                        ; per texture: bank, page, row
tx_ph:   .res 32                        ;  offset and mask, column index
tx_ro:   .res 32                        ;  address
tx_rm:   .res 32
tx_ixl:  .res 32
tx_ixh:  .res 32
fl_bank: .res 32                        ; per flat: bank, page
fl_page: .res 32
tx_ix:   .res 896                       ; the column indexes, every texture
.else
        TABLES
.endif
; a WALL group's columns' page (SCREEN + kk >> 5)
wch:     .res 1
