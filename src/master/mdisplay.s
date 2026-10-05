; Master display bring-up (step 1 of docs/master_textured_spec.md).
;
; Proves the display half of the Master memory map on its own:
;   - shadow MODE 1, re-cut by the CRTC to a centred 256x160 window
;   - palette black / magenta / cyan / white
;   - two 10K buffers in SHADOW RAM, &3000 and &5800, flipped at vsync
;     through R12/R13 while D (display shadow) stays set
;   - drawing code and its tables live in HAZEL (&C000) and reach the
;     buffers by setting ACCCON X for the duration of the draw: the E bit
;     does nothing while HAZEL is paged in (measured in jsbeeb; matches
;     the hardware rule that Master opcode access needs Y = 0)
;   - every texel row is drawn as a byte then FLIP[byte] on the line below
;
; Buffer A (&3000): wall format, 10 vertical bands of the 10 shades, two
; strips per byte ((TEX1 << 2) OR TEX2). Buffer B (&5800): floor format,
; 10 horizontal bands. The main loop flips A/B every vsync and counts
; frames at FRAMES so a harness can see it running. Uses the OS (MODE,
; VDU 19, OSBYTE 19) for set-up and vsync only; drawing runs with SEI.

        .include "mdisplay_tab.inc"     ; generated: table sizes

OSWRCH  = $FFEE
OSBYTE  = $FFF4
CRTC_A  = $FE00
CRTC_D  = $FE01
ACCCON  = $FE34
ACC_D   = $01                           ; display shadow
ACC_X   = $04                           ; CPU accesses shadow at &3000-&7FFF
ACC_Y   = $08                           ; HAZEL at &C000-&DFFF

BUF_A   = $3000
BUF_B   = $5800

ptr     = $70                           ; zp: screen pointer
cnt     = $72
tmp     = $73
FRAMES  = $74                           ; 16-bit frame counter (harness reads it)
SHOWING = $76                           ; 0 = A displayed, 1 = B
dst     = $78                           ; zp: HAZEL copy destination

        .import __HAZEL_LOAD__, __HAZEL_RUN__, __HAZEL_SIZE__

        .segment "MAIN"
start:
        ldx #0                          ; MODE 129 (shadow 1), palette, cursor off
:       lda vdu_init,x
        jsr OSWRCH
        inx
        cpx #vdu_init_end - vdu_init
        bne :-

        sei
        ldx #crtc_tab_end - crtc_tab - 2
:       lda crtc_tab,x                  ; (register, value) pairs
        sta CRTC_A
        lda crtc_tab+1,x
        sta CRTC_D
        dex
        dex
        bpl :-

        lda ACCCON                      ; page HAZEL in and copy its image up
        sta acc_os
        ora #ACC_Y
        sta ACCCON
        lda #<__HAZEL_LOAD__
        sta ptr
        lda #>__HAZEL_LOAD__
        sta ptr+1
        lda #<__HAZEL_RUN__
        sta dst
        lda #>__HAZEL_RUN__
        sta dst+1
        ldx #>(__HAZEL_SIZE__ + 255)
        ldy #0
copy:   lda (ptr),y
        sta (dst),y
        iny
        bne copy
        inc ptr+1
        inc dst+1
        dex
        bne copy

        lda acc_os                      ; draw both buffers: HAZEL + shadow writes
        ora #ACC_Y | ACC_X | ACC_D
        sta ACCCON
        jsr draw_walls
        jsr draw_floors
        lda acc_os
        sta ACCCON
        cli

        stz FRAMES
        stz FRAMES+1
        stz SHOWING
loop:   lda #19                         ; wait for vsync
        jsr OSBYTE
        lda SHOWING
        eor #1
        sta SHOWING
        tax
        sei
        lda #12
        sta CRTC_A
        lda r12_tab,x
        sta CRTC_D
        lda #13
        sta CRTC_A
        stz CRTC_D
        cli
        inc FRAMES
        bne loop
        inc FRAMES+1
        bra loop

vdu_init:
        .byte 22, 129                   ; MODE 129: Mode 1 in shadow (D set)
        .byte 19, 1, 5, 0, 0, 0         ; logical 1 -> magenta
        .byte 19, 2, 6, 0, 0, 0         ; logical 2 -> cyan
        .byte 19, 3, 7, 0, 0, 0         ; logical 3 -> white
vdu_init_end:

crtc_tab:                               ; 256x160 centred; start = BUF_A
        .byte 1, 64                     ; R1 displayed chars (64 x 4 px)
        .byte 2, 90                     ; R2 hsync: Mode 1's 98 less 8
        .byte 6, 20                     ; R6 displayed rows (20 x 8 lines)
        .byte 7, 28                     ; R7 vsync: as the Model B game
        .byte 8, 0                      ; R8 non-interlaced (field = 312 lines)
        .byte 10, $20                   ; R10 cursor off
        .byte 12, >(BUF_A >> 3)
        .byte 13, <(BUF_A >> 3)
crtc_tab_end:

r12_tab: .byte >(BUF_A >> 3), >(BUF_B >> 3)
acc_os: .byte 0

; ----------------------------------------------------------------------
        .segment "HAZEL"
; Screen address of line y, byte column x: base + (y>>3)*512 + x*8 + (y&7).

; Buffer A: 64 byte columns, each a fixed combined wall byte (COLBYTE[x]).
; Even lines get the byte, odd lines FLIP[byte].
draw_walls:
        lda #<BUF_A
        sta ptr
        lda #>BUF_A
        sta ptr+1
        ldx #0                          ; byte column
@col:   lda colbyte,x
        sta tmp                         ; even-line byte
        phx
        tax
        lda flip,x
        plx
        sta @odd+1                      ; odd-line byte (SMC)
        lda ptr+1
        pha
        lda #20
        sta cnt                         ; character rows
@row:   ldy #0
@line:  lda tmp
        sta (ptr),y
        iny
@odd:   lda #0
        sta (ptr),y
        iny
        cpy #8
        bne @line
        lda ptr+1                       ; next character row: +512
        clc
        adc #2
        sta ptr+1
        dec cnt
        bne @row
        pla
        sta ptr+1
        lda ptr                         ; next byte column: +8
        clc
        adc #8
        sta ptr
        bcc :+
        inc ptr+1
:       inx
        cpx #64
        bne @col
        rts

; Buffer B: 80 texel rows, each a fixed floor byte (ROWBYTE[r]) across all
; 64 byte columns; line 2r gets the byte, line 2r+1 FLIP[byte].
draw_floors:
        ldx #0                          ; texel row
@trow:  txa                             ; line y = 2r: char row y>>3, line y&7
        asl a
        sta tmp
        lsr a
        lsr a
        lsr a                           ; y >> 3
        asl a                           ; * 2 pages (512 B per char row)
        clc
        adc #>BUF_B
        sta ptr+1
        lda tmp
        and #7
        sta ptr
        lda rowbyte,x
        jsr @fill                       ; line 2r
        inc ptr                         ; line 2r+1 (same char row: 2r is even)
        phx
        lda rowbyte,x
        tax
        lda flip,x
        plx
        jsr @fill
        inx
        cpx #80
        bne @trow
        rts
@fill:  ldy #0                          ; 64 bytes, stride 8
        sta tmp
        lda ptr
        pha
:       lda tmp
        sta (ptr),y
        tya
        clc
        adc #8
        tay
        bne :-                          ; Y wraps after 32 bytes: next page
        inc ptr+1
:       lda tmp
        sta (ptr),y
        tya
        clc
        adc #8
        tay
        bne :-
        dec ptr+1
        pla
        sta ptr
        rts

        .include "mdisplay_tab.s"       ; generated: flip, colbyte, rowbyte
