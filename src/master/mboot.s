; Master engine disc loader (docs/master_textured_spec.md). !BOOT, *RUN at
; $1900.
;
; Order matters, because two things may not be touched until the last
; disc access is done: HAZEL (the filing system keeps its workspace there)
; and, once loaded, the engine's own main-RAM image.
;
;   1. MODE 130 (shadow Mode 2, step 6a; its default palette is the eight
;      solid colours), while the OS is whole.
;   2. Banks 4, 7, 5 and 6: staged at $3000, copied into their sideways
;      banks (5 and 6 are the wall textures; 6's tail the part records).
;   3. MHAZEL, then MANDY: staged at $3000 and parked in SHADOW RAM (the
;      screen; it shows as noise during the load) one after the other,
;      bounced a page at a time through $0A00 because the CPU sees either
;      main or shadow at $3000, not both. Then MPANEL, the control panel
;      (master_panel.py), parked where it lives in buffer 1: its character
;      rows 17..19, shadow $7A00-$7FFF, clear of the parked blocks.
;   4. From the page-9 stub: MMAIN straight to $0F00 (on the Master DFS
;      keeps its workspace in HAZEL, so main RAM from $0E00 is free) --
;      this overwrites the loader; then MCBITS to $5800. Last disc access.
;   5. SEI; the parked HAZEL block goes shadow -> $0A00 -> HAZEL ($C000),
;      the parked ANDY block shadow -> ANDY ($8000, ROMSEL bit 7: the MOS
;      keeps its font and workspace there, so it waits for the last OS
;      call too); the panel shadow $7A00 -> $5200 (buffer 0's rows
;      17..19, which the parked ANDY block covered); JMP DRV_ORG. No OS
;      call after this point.
;
; (The first cut staged MHAZEL at $3000 AFTER the engine image was in
; place, which wrote the HAZEL block over engine code at $3000+.)

        .include "abi.inc"

ROMSEL_COPY = $F4
ANDY_PAGES  = 16                        ; MANDY: 4K
PANEL_PAGES = 6                         ; MPANEL: character rows 17..19
PANEL0      = MSCREEN0 + 17 * 512       ; ... of buffer 0 (shadow $5200)
PANEL1      = MSCREEN1 + 17 * 512       ; ... of buffer 1 (shadow $7A00)
; the bounce page is $0A00, one free OS buffer page (written inline)

        .segment "CODE"
ldr:
        ldx #0
@vdu:   lda vdu_init,x                  ; MODE 130 first
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
        ldx #<c_b5
        ldy #>c_b5
        jsr $FFF7                       ; *LOAD MBANK5 3000 (wall textures)
        lda #5
        jsr copy
        ldx #<c_b6
        ldy #>c_b6
        jsr $FFF7                       ; *LOAD MBANK6 3000 (textures + parts)
        lda #6
        jsr copy
        ldx #<c_hz
        ldy #>c_hz
        jsr $FFF7                       ; *LOAD MHAZEL 3000
        lda #$30                        ; park it in shadow $3000+
        ldx #HAZEL_PAGES
        jsr park
        ldx #<c_an
        ldy #>c_an
        jsr $FFF7                       ; *LOAD MANDY 3000
        lda #$30 + HAZEL_PAGES          ; park it in shadow above HAZEL's
        ldx #ANDY_PAGES
        jsr park
        ldx #<c_pn
        ldy #>c_pn
        jsr $FFF7                       ; *LOAD MPANEL 3000
        lda #>PANEL1                    ; park it in buffer 1's panel rows
        ldx #PANEL_PAGES
        jsr park
        ldx #stub_len
:       lda stub_image-1,x
        sta $0900-1,x
        dex
        bne :-
        jmp $0900

park:                                   ; main $3000 (X pages) -> shadow page A
        sta $83
        lda #$30
        sta $81
        lda #0
        sta $80
        sta $82
        sei
@park:  ldy #0
@pk1:   lda ($80),y                     ; main page -> bounce
        sta $0A00,y
        iny
        bne @pk1
        lda $FE34
        ora #$04                        ; X: CPU on shadow
        sta $FE34
@pk2:   lda $0A00,y                    ; bounce -> shadow page
        sta ($82),y
        iny
        bne @pk2
        lda $FE34
        and #$FB
        sta $FE34
        inc $81
        inc $83
        dex
        bne @park
        cli
        rts

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
c_b5:   .byte "LOAD MBANK5 3000", 13
c_b6:   .byte "LOAD MBANK6 3000", 13
c_hz:   .byte "LOAD MHAZEL 3000", 13
c_an:   .byte "LOAD MANDY 3000", 13
c_pn:   .byte "LOAD MPANEL 3000", 13
vdu_init:
        .byte 22, 130                   ; MODE 130: Mode 2 in shadow RAM
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
        sta $0A00,y
        iny
        bne @h1
        lda $FE34
        and #$FB
        ora #$08                        ; X off, Y on: write HAZEL
        sta $FE34
@h2:    lda $0A00,y
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
        lda #$30 + HAZEL_PAGES          ; ANDY: shadow -> $8000, direct (X puts
        sta $81                         ; shadow at $3000-$7FFF; ROMSEL bit 7
        lda #$80                        ; puts ANDY at $8000-$8FFF)
        sta $83
        sta $FE30
        lda $FE34
        ora #$04
        sta $FE34
        ldx #ANDY_PAGES
@an:    lda ($80),y                     ; (Y = 0 from the HAZEL loop)
        sta ($82),y
        iny
        bne @an
        inc $81
        inc $83
        dex
        bne @an
        lda #>PANEL1                    ; the panel: buffer 1's rows -> buffer
        sta $81                         ;  0's (X still on: shadow to shadow)
        lda #>PANEL0
        sta $83
        ldx #PANEL_PAGES
@pn:    lda ($80),y                     ; (Y = 0 again)
        sta ($82),y
        iny
        bne @pn
        inc $81
        inc $83
        dex
        bne @pn
        lda $FE34
        and #$FB
        sta $FE34
        stz $FE30                       ; ANDY out (the engine pages its own)
        jmp DRV_ORG                     ; -> driver (SEI held; no OS from here)
s_main:  .byte "LOAD MMAIN", 13
s_cbits: .byte "LOAD MCBITS", 13
s_end:
stub_len = s_end - stub
        .assert stub_len <= 256, error, "stub must fit page 9"
