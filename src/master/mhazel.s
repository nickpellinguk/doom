; HAZEL resident code for the Master engine disc (step 1 of
; docs/master_textured_spec.md). Assembled at $C000 and copied there by
; the boot loader AFTER its last disc access (the filing system keeps its
; workspace in HAZEL).
;
; The driver calls in with ACCCON = D|X|Y: HAZEL paged in, and the CPU's
; $3000-$7FFF view on the SHADOW buffers. So this code reads nothing from
; main RAM above $3000: its arguments arrive in MHZ_ARGS (HAZEL itself),
; and its tables live here too.
;
;   MHZ_PATTERN  draw the test pattern into BOTH buffers: the 10 shades as
;                vertical wall-format bands below an empty HUD row, in the
;                view's character rows only (0..16): rows 17..19 are the
;                control panel the loader left there (master_panel.py)
;   MHZ_HUD      A = back buffer page hi; draw "tttt ff" into its top
;                character row: frame time in 1MHz ticks (hex) and fields

        .include "abi.inc"
        .include "mhazel_tab.inc"      ; generated: nothing but sizes

ptr     = $70                           ; zp scratch (frame-scoped, driver-
cnt     = $72                           ; owned outside the render: the
tmp     = $73                           ; render is done when these run)
glyph   = $74
VIEW_ROWS = 17                          ; the 136-line view; the panel below

        .segment "HAZEL"
        jmp pattern                     ; MHZ_PATTERN = $C000
        jmp hud                         ; MHZ_HUD     = $C003
args:   .byte 0, 0, 0                   ; MHZ_ARGS    = $C006
        .assert args = MHZ_ARGS, error, "MHZ_ARGS moved"

; ----------------------------------------------------------------------
pattern:
        lda #>MSCREEN0
        jsr pat_buf
        lda #>MSCREEN1
pat_buf:                                ; A = buffer page hi
        sta ptr+1
        stz ptr
        ldx #0                          ; clear the view: 2 pages a row
        lda #0
        ldy #0
:       sta (ptr),y
        iny
        bne :-
        inc ptr+1
        inx
        cpx #VIEW_ROWS * 2
        bne :-
        lda ptr+1                       ; back to the buffer start + 512:
        sec                             ; character row 1 (row 0 = HUD)
        sbc #VIEW_ROWS * 2 - 2
        sta ptr+1
        ldx #0                          ; byte column
@col:   lda colbyte,x
        sta tmp                         ; every line (Mode 2: no cross-hatch)
        lda ptr+1
        pha
        lda #VIEW_ROWS - 1
        sta cnt                         ; character rows 1..16
@row:   ldy #0
        lda tmp
@line:  sta (ptr),y
        iny
        cpy #8
        bne @line
        lda ptr+1
        clc
        adc #2
        sta ptr+1
        dec cnt
        bne @row
        pla
        sta ptr+1
        lda ptr
        clc
        adc #8
        sta ptr
        bcc :+
        inc ptr+1
:       inx
        cpx #64
        bne @col
        rts

; ----------------------------------------------------------------------
; HUD: 6 glyph cells in character row 0 (Mode 2: a cell is 2 byte columns,
; 16 bytes) at byte columns 1..8 and 11..14.
hud:
        sta ptr+1
        lda #8                          ; byte column 1 (+8 per column)
        sta ptr
        lda args+1
        jsr hex2                        ; frame time hi
        lda args
        jsr hex2                        ; frame time lo
        lda ptr                         ; skip a cell
        clc
        adc #16
        sta ptr
        lda args+2
        ; fall through: fields
hex2:   pha                             ; two hex digits of A
        lsr a
        lsr a
        lsr a
        lsr a
        jsr digit
        pla
        and #$0F
digit:  asl a                           ; glyph offset = digit * 16
        asl a
        asl a
        asl a
        tax
        ldy #0
:       lda font,x
        sta (ptr),y
        inx
        iny
        cpy #16
        bne :-
        lda ptr
        clc
        adc #16
        sta ptr
        rts

        .include "mhazel_tab.s"         ; generated: colbyte, font
