; Master engine disc loader (docs/master_textured_spec.md). !BOOT, *RUN at
; $1900.
;
; Order matters, because two things may not be touched until the last
; disc access is done: HAZEL (the filing system keeps its workspace there)
; and, once loaded, the engine's own main-RAM image.
;
;   1. MODE 129 (shadow Mode 1) + palette, while the OS is whole.
;   2. Banks 4 and 7: staged at $3000, copied into their sideways banks.
;   3. MHAZEL: staged at $3000 and parked in SHADOW RAM (the screen; it
;      shows as noise during the load), bounced a page at a time through
;      $0A00 because the CPU sees either main or shadow at $3000, not both.
;   4. From the page-9 stub: MMAIN straight to $0F00 (on the Master DFS
;      keeps its workspace in HAZEL, so main RAM from $0E00 is free) --
;      this overwrites the loader; then MCBITS to $5800. Last disc access.
;   5. SEI; the parked block goes shadow -> $0A00 -> HAZEL ($C000);
;      JMP DRV_ORG. No OS call after this point.
;
; (The first cut staged MHAZEL at $3000 AFTER the engine image was in
; place, which wrote the HAZEL block over engine code at $3000+.)

        .include "abi.inc"

ROMSEL_COPY = $F4
BOUNCE  = $0A00                         ; one free OS buffer page

        .segment "CODE"
ldr:
        ldx #0
@vdu:   lda vdu_init,x                  ; MODE 129 + palette first
        jsr $FFEE
        inx
        cpx #vdu_end - vdu_init
        bne @vdu
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
        ldx #<c_hz
        ldy #>c_hz
        jsr $FFF7                       ; *LOAD MHAZEL 3000
        sei
        lda #$30                        ; park it in shadow $3000+ (page by page)
        sta $81
        ldx #HAZEL_PAGES
@park:  ldy #0
@pk1:   lda ($80),y                     ; main page -> bounce
        sta BOUNCE,y
        iny
        bne @pk1
        lda $FE34
        ora #$04                        ; X: CPU on shadow
        sta $FE34
@pk2:   lda BOUNCE,y                    ; bounce -> shadow page
        sta ($80),y
        iny
        bne @pk2
        lda $FE34
        and #$FB
        sta $FE34
        inc $81
        dex
        bne @park
        cli
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
c_hz:   .byte "LOAD MHAZEL 3000", 13
vdu_init:
        .byte 22, 129                   ; MODE 129: Mode 1 in shadow RAM
        .byte 19, 1, 1, 0, 0, 0         ; logical 1 -> red
        .byte 19, 2, 6, 0, 0, 0         ; logical 2 -> cyan
        .byte 19, 3, 7, 0, 0, 0         ; logical 3 -> white
vdu_end:

stub_image:
        .segment "STUB"                 ; runs at $0900
stub:
        ldx #<s_main
        ldy #>s_main
        jsr $FFF7                       ; *LOAD MMAIN (to its own $0F00)
        ldx #<s_cbits
        ldy #>s_cbits
        jsr $FFF7                       ; *LOAD MCBITS (to its own $5800) -- LAST
        sei
        lda #$00
        sta $80
        sta $82
        lda #$30
        sta $81                         ; shadow source
        lda #$C0
        sta $83                         ; HAZEL destination
        ldx #HAZEL_PAGES
@hz:    lda $FE34
        ora #$04                        ; X: read the parked page from shadow
        sta $FE34
        ldy #0
@h1:    lda ($80),y
        sta BOUNCE,y
        iny
        bne @h1
        lda $FE34
        and #$FB
        ora #$08                        ; X off, Y on: write HAZEL
        sta $FE34
@h2:    lda BOUNCE,y
        sta ($82),y
        iny
        bne @h2
        lda $FE34
        and #$F7                        ; Y off again (ROM back at $C000)
        sta $FE34
        inc $81
        inc $83
        dex
        bne @hz
        jmp DRV_ORG                     ; -> driver (SEI held; no OS from here)
s_main:  .byte "LOAD MMAIN", 13
s_cbits: .byte "LOAD MCBITS", 13
s_end:
stub_len = s_end - stub
        .assert stub_len <= 256, error, "stub must fit page 9"
