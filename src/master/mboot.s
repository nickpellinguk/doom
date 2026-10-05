; Master engine disc loader (step 1 of docs/master_textured_spec.md).
; !BOOT, *RUN at $1900. Same shape as the Model B loader (src/boot/
; modelb_boot.s): bank images are staged at $3000 and copied into their
; sideways banks; the engine's MAIN image is staged at $3000 and copied
; down to $0F00 by a stub in page 9 (it overwrites this loader).
;
; The Master differences:
;   - bank C's content is NOT a bank: MCBITS loads straight to $5800 in
;     main RAM (free: the screens are in shadow RAM);
;   - only banks 4 and 7 are loaded (5 and 6 are for textures);
;   - the screen is MODE 129 (shadow Mode 1) with the black/magenta/cyan/
;     white palette; the driver then cuts it to 256x160;
;   - the HAZEL block is copied to $C000 LAST, after the final disc
;     access, because the filing system keeps its workspace in HAZEL.

        .include "abi.inc"

ROMSEL_COPY = $F4

        .segment "CODE"
ldr:
        ldx #<c_b4
        ldy #>c_b4
        jsr $FFF7                       ; *LOAD MBANK4 3000 (bank A)
        lda #4
        jsr copy
        ldx #<c_b7
        ldy #>c_b7
        jsr $FFF7                       ; *LOAD MBANK7 3000 (bank B)
        lda #7
        jsr copy
        ldx #stub_len
:       lda stub_image-1,x
        sta $0900-1,x
        dex
        bne :-
        jmp $0900

copy:                                   ; A = bank: $3000-$6FFF -> $8000
        ldx ROMSEL_COPY
        stx oldrom
        sei
        sta $FE30
        sta ROMSEL_COPY
        lda #$00
        sta $80
        sta $82
        lda #$30
        sta $81
        lda #$80
        sta $83
        ldx #$40
@pg:    ldy #0
@by:    lda ($80),y
        sta ($82),y
        iny
        bne @by
        inc $81
        inc $83
        dex
        bne @pg
        lda oldrom
        sta $FE30
        sta ROMSEL_COPY
        cli
        rts
oldrom: .byte 0
c_b4:   .byte "LOAD MBANK4 3000", 13
c_b7:   .byte "LOAD MBANK7 3000", 13

stub_image:
        .segment "STUB"                 ; runs at $0900
stub:
        ldx #<s_main
        ldy #>s_main
        jsr $FFF7                       ; *LOAD MMAIN 3000 (staged)
        lda #$00
        sta $80
        sta $82
        lda #$30
        sta $81
        lda #>LOW_BASE
        sta $83
        ldy #0
@cp:    lda ($80),y
        sta ($82),y
        iny
        bne @cp
        inc $81
        inc $83
        lda $83
        cmp #>CBITS_M                   ; MAIN image ends where CBITS starts
        bne @cp
        ldx #<s_cbits
        ldy #>s_cbits
        jsr $FFF7                       ; *LOAD MCBITS (to its own $5800)
        ldx #<s_hazel
        ldy #>s_hazel
        jsr $FFF7                       ; *LOAD MHAZEL 3000 -- LAST disc access
        ldx #0
@vdu:   lda vdu_init,x                  ; MODE 129 + palette (last OS output)
        jsr $FFEE
        inx
        cpx #vdu_end - vdu_init
        bne @vdu
        sei
        lda $FE34
        pha
        ora #$08                        ; Y: HAZEL in at $C000
        sta $FE34
        lda #$00
        sta $80
        sta $82
        lda #$30
        sta $81
        lda #$C0
        sta $83
        ldx #HAZEL_PAGES
@hz:    ldy #0
@hzb:   lda ($80),y
        sta ($82),y
        iny
        bne @hzb
        inc $81
        inc $83
        dex
        bne @hz
        pla
        sta $FE34                      ; HAZEL out again (the driver pages it)
        jmp DRV_ORG                     ; -> driver (its SEI kills the OS)
s_main:  .byte "LOAD MMAIN 3000", 13
s_cbits: .byte "LOAD MCBITS", 13
s_hazel: .byte "LOAD MHAZEL 3000", 13
vdu_init:
        .byte 22, 129                   ; MODE 129: Mode 1 in shadow RAM
        .byte 19, 1, 5, 0, 0, 0         ; logical 1 -> magenta
        .byte 19, 2, 6, 0, 0, 0         ; logical 2 -> cyan
        .byte 19, 3, 7, 0, 0, 0         ; logical 3 -> white
vdu_end:
s_end:
stub_len = s_end - stub
        .assert stub_len <= 256, error, "stub must fit page 9"
