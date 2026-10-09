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
; (u >> 4). Texels are read with self-modified LDA abs,X (X the row), the
; screen written STA (scr),Y. hdraw_tab.inc (tube_host.py) supplies
; SCREEN, the texture and flat tables, lsr4 and flip.

        .include "hdraw_tab.inc"

scr     = $70                           ; screen pointer (a cell, or a line)
v       = $72                           ; v (5.11), a span's v (4.4)
vst     = $74                           ; v's step a line pair (2 x the list's)
dha     = $76                           ; right row delta: hi = dh (5.3)
ddh     = $78                           ;  and its step a character row
nl      = $7A                           ; lines left in the run
ln      = $7B                           ; the line being drawn
cnt     = $7C                           ; entries left in the group
kk      = $7D                           ; byte column
rowm    = $7E                           ; texture row mask ((th - 1) * 8)
rowo    = $7F                           ;  and row offset (0 / 128)
tph     = $80                           ; texture ptr hi
ixp     = $81                           ; 2: the texture's column index
own     = $83                           ; bit 7: the right strip's own row
cur     = $84                           ; the pair's byte
tmp     = $85
su      = $86                           ; span u, du, dv
du      = $87
dv      = $88
k1      = $89                           ; a span's last byte column
pair    = $8A                           ; bit 7: a pair span; bit 0: line odd

; one byte off the Tube (register 1, polled)
.macro GETB
.local w
w:      bit $FEE0
        bpl w
        lda $FEE1
.endmacro

        .segment "CODE"
hd_frame:
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
        GETB                            ; tid
        tax
        lda tx_bank,x
        sta $FE30
        lda tx_ph,x
        sta tph
        lda tx_ro,x
        sta rowo
        lda tx_rm,x
        sta rowm
        lda tx_ixl,x
        sta ixp
        lda tx_ixh,x
        sta ixp+1
        GETB                            ; k0
        sta kk
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
        tay
        lda (ixp),y                     ; the stored column
        pha
        and #7
        sta wl_l0+1
        sta wl_l1+1
        pla
        lsr a
        lsr a
        lsr a
        clc
        adc tph
        sta wl_l0+2
        sta wl_l1+2
        GETB                            ; cr
        tay
        lda (ixp),y
        pha
        and #7
        sta wl_r0+1
        sta wl_r1+1
        pla
        lsr a
        lsr a
        lsr a
        clc
        adc tph
        sta wl_r0+2
        sta wl_r1+2
        GETB
        sta v
        GETB
        sta v+1
        GETB
        sta vst
        GETB
        sta vst+1
        asl vst                         ; a line pair moves v two steps
        rol vst+1                       ;  ((y & ~1) moves by 2)
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
:       jsr cellptr                     ; scr, Y for (ln, kk)
; the run, line by line: a line pair's byte is made on its first line
wl_calc:
        bit own
        bmi wl_own
        lda v+1
        and rowm
        ora rowo
        tax
wl_r0:  lda $FFFF,x                     ; right texel (patched)
        lsr a
wl_l0:  ora $FFFF,x                     ; left texel (patched)
        bra wl_put
wl_own: lda v+1                         ; the right row: hi(v) + dh
        clc
        adc dha+1
        and rowm
        ora rowo
        tax
wl_r1:  lda $FFFF,x
        lsr a
        sta tmp
        lda v+1
        and rowm
        ora rowo
        tax
wl_l1:  lda $FFFF,x
        ora tmp
wl_put: sta (scr),y
        sta cur
        dec nl
        beq wl_end
        lda ln                          ; a pair done: v on a step
        inc ln
        lsr a
        bcc wl_line
        clc
        lda v
        adc vst
        sta v
        lda v+1
        adc vst+1
        sta v+1
wl_line:
        iny
        cpy #8
        bne wl_go
        jsr nextrow                     ; the next character row
        bit own
        bpl wl_go
        clc                             ; dh: ddh more a character row
        lda dha
        adc ddh
        sta dha
        lda dha+1
        adc ddh+1
        sta dha+1
wl_go:  lda ln
        lsr a
        bcc wl_calc                     ; an even line: a new pair
        lda cur
        bra wl_put
wl_end: inc kk
        dec cnt
        bmi :+
        jmp wcol
:       jmp next

; cellptr: scr = SCREEN + (ln >> 3) * 512 + kk * 8, Y = ln & 7
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
        adc #>SCREEN
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
        tax
        lda #0
        sta dha                         ; odd-line byte (cellptr uses tmp)
        cpx #SKY_BYTE
        bne :+
        lda flip,x
        sta dha
:       jsr cellptr
fl_lp:  lda ln
        lsr a
        lda cur
        bcc :+
        ldx dha                         ; odd line: the sky's FLIP
        beq :+
        txa
:       sta (scr),y
        dec nl
        beq fl_end
        inc ln
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
        cmp #PAIR_Y
        bcc :+
        sbc #PAIR_Y
        asl a
        ldx #$80
        bra :++
:       ldx #0
:       stx pair
        sta ln
        lsr a                           ; C = an odd line
        lda pair
        bcc :+
        ora #$40
:       sta pair                        ; b7 pair, b6 odd line
        lda ln                          ; scr = the line's start
        lsr a
        lsr a
        and #$FE
        clc
        adc #>SCREEN
        sta scr+1
        lda ln
        and #7
        sta sp_lo                       ; (scr stays page-aligned)
        stz scr
sp_each:
        GETB                            ; k0
        sta kk
        GETB
        sta k1
        GETB                            ; flat, or $FF
        cmp #$FF
        bne sp_flat
        GETB                            ; far: one byte
        sta cur
        tax
        lda flip,x
        sta tmp
        jsr sp_ptr
sf_lp:  lda cur
        bit pair
        bmi sf_pr
        bvc :+                          ; an odd line: FLIPped
        lda tmp
:       sta (scr),y
        bra sf_nx
sf_pr:  sta (scr),y
        iny
        lda tmp
        sta (scr),y
        dey
sf_nx:  jsr sp_step
        bcc sf_lp
        jmp sp_done
sp_flat:
        tax
        lda fl_bank,x
        sta $FE30
        lda fl_page,x
        sta sp_tx+2
        GETB
        sta su
        GETB
        sta v
        GETB
        sta du
        GETB
        sta dv
        jsr sp_ptr
sp_lp:  lda v                           ; the texel: (v & $F0) | (u >> 4)
        and #$F0
        ldx su
        ora lsr4,x
        tax
sp_tx:  lda $FF00,x                     ; (patched: the flat's page)
        bit pair
        bmi sp_pr
        bvc :+                          ; an odd line: FLIPped
        tax
        lda flip,x
:       sta (scr),y
        bra sp_nx
sp_pr:  sta (scr),y
        tax
        lda flip,x
        iny
        sta (scr),y
        dey
sp_nx:  clc
        lda su
        adc du
        sta su
        clc
        lda v
        adc dv
        sta v
        jsr sp_step
        bcc sp_lp
sp_done:
        dec cnt
        bmi :+
        jmp sp_each
:       jmp next

; sp_ptr: the span's first byte: page scr+1 + kk >> 5 (the line's page
; kept in sp_sv), Y = kk * 8 + the line in its cell
sp_ptr: lda kk
        lsr a
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc scr+1
        sta sp_pg
        lda kk
        asl a
        asl a
        asl a
        clc
        adc sp_lo                       ; (+ the line in its cell, < 8)
        tay
        lda scr+1
        pha
        lda sp_pg
        sta scr+1
        pla
        sta sp_sv
        rts
; sp_step: on a byte column; C set past k1 (scr+1 restored)
sp_step:
        lda kk
        cmp k1
        bcs sp_last
        inc kk
        tya
        clc
        adc #8
        tay
        bcc :+
        inc scr+1
:       clc
        rts
sp_last:
        lda sp_sv
        sta scr+1
        sec
        rts

sp_pg:  .res 1
sp_lo:  .res 1
sp_sv:  .res 1

        TABLES
