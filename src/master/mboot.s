; Master engine disc loader (docs/master_textured_spec.md). !BOOT, *RUN at
; $1900. Both discs: the Master's and (TUBE) the Tube Master's.
;
; THE LOADING SCREEN (section 9): DOOM's title picture (title_art.py, 10K)
; goes up first, in the game's own 10K window (its CRTC set-up: 64 bytes a
; line, 160 lines, centred; the 10K screen size), and stays up the whole
; load, a bar growing along its bottom two lines as each file lands. It
; lives in the shadow screen's first half, $3000-$57FF -- so everything
; that must wait for the last disc access is parked in the other half,
; compressed (MPARK, lzpark.py: HAZEL, ANDY, the gun and the panel, 8.5K at
; shadow PARK_PAGE..$7FFF). The stub is two pages ($0900-$0AFF); the
; bounce page is $0B00 (the OS's soft keys; the engine's span pool later).
;
; Order matters, because two things may not be touched until the last
; disc access is done: HAZEL (the filing system keeps its workspace there)
; and ANDY (the MOS's font and workspace).
;
;   1. MODE 130 (shadow Mode 2), the cursor off, the game's CRTC window;
;      the page-9 stub copied down (its bar routine runs from there); the
;      picture: MTITLE staged at main $3000, copied to shadow $3000 a page
;      at a time through the bounce page $0B00 (the CPU sees main or shadow
;      at $3000, not both).
;   2. TUBE: the fill server, SERVER into the second processor and started
;      there (the MOS serves it until its OSCLI *GOIO 1903 lands on l_cont).
;   3. Banks 4, 7, 5 and 6: staged at $3000, copied into their banks.
;   4. MPARK, staged at $3000 and parked in shadow above the picture.
;   5. From page 9: MMAIN straight to $0F00 (this overwrites the loader),
;      MCBITS to $5800. The last disc access.
;   6. SEI; the stub decompresses the parked streams (lz) into HAZEL
;      ($C000, ACCCON Y), ANDY ($8000, ROMSEL bit 7), the gun (shadow $3000,
;      MGUN0) and the panel (shadow $5800, MPANEL: last, its stream above
;      its home), copies the music player bank 5 $B800 -> $0300, JMP DRV_ORG.
;      No OS call after this point. The game draws over the picture.

        .include "abi.inc"
        .include "mboot_park.inc"       ; (build_master_ssd.py: PARK_PAGE,
                                        ;  PARK_PAGES, S_HAZEL .. S_PANEL,
ROMSEL_COPY = $F4                       ;  BAR_W, BAR_BYTE)
BAR_ADDR  = $3000 + 19 * 512 + 2        ; the bar: lines 154-157 (title_art)
BP        = $88                         ; (2) the bar's next column

.ifdef TUBE
.define STAGE "FFFF3000"                ; (a second processor is on: a bare
.else                                   ;  3000 would be ITS address)
.define STAGE "3000"
.endif

        .segment "CODE"
ldr:
        jmp l_start
l_cont:                                 ; $1903 (TUBE: *GOIO 1903, from the
        ldx #<c_b4                      ;  fill server, started below)
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
        ldx #<c_pk
        ldy #>c_pk
        jsr $FFF7                       ; *LOAD MPARK 3000
        lda #PARK_PAGE                  ; parked above the picture
        ldx #PARK_PAGES
        jsr park
        jsr bar
        jmp s_load                      ; (page 9: MMAIN overwrites this)

l_start:
        ldx #0
@vdu:   lda vdu_init,x                  ; MODE 130, the cursor off
        jsr $FFEE
        inx
        cpx #vdu_end - vdu_init
        bne @vdu
        ldx #0                          ; the game's window
@crtc:  lda crtc_tab,x
        sta $FE00
        lda crtc_tab+1,x
        sta $FE01
        inx
        inx
        cpx #crtc_end - crtc_tab
        bne @crtc
        lda #$0C                        ; screen size 10K (latch B4 = B5 = 1)
        sta $FE40
        lda #$0D
        sta $FE40
        ldx #0                          ; the stub to pages 9-10 now: its
:       lda stub_image,x                ;  bar routine serves the whole load
        sta $0900,x
        lda stub_image+$100,x
        sta $0A00,x
        inx
        bne :-
        lda #<BAR_ADDR
        sta BP
        lda #>BAR_ADDR
        sta BP+1
        ldx #<c_ti
        ldy #>c_ti
        jsr $FFF7                       ; *LOAD MTITLE 3000
        lda #$30                        ; the picture: shadow $3000-$57FF
        ldx #40
        jsr park
.ifndef TUBE
        jmp l_cont
.else
        ; TUBE MASTER (docs/tube_master.md H4): the fill server: SERVER into
        ; the second processor (its own address), started there through
        ; the MOS's Tube host code at &0406 -- claim, then transfer type 4,
        ; execute. The host then serves the second processor (the MOS's
        ; Tube loop) until the server's first act, an OSCLI: *GOIO 1903,
        ; l_cont above, the rest of the boot.
        ldx #<c_sv
        ldy #>c_sv
        jsr $FFF7                       ; *LOAD SERVER
        jsr bar
@claim: lda #$C0 + TUBE_ID
        jsr $0406
        bcc @claim
        lda #4
        ldx #<s_entry
        ldy #>s_entry
        jsr $0406
@hang:  bra @hang                       ; (not reached: the MOS serves the Tube)
TUBE_ID = $3D                           ; (any client id: the claim is ours)
s_entry: .dword SERVER_ENTRY            ; fs_main, in the second processor
.endif

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
        sta $0B00,y
        iny
        bne @pk1
        lda $FE34
        ora #$04                        ; X: CPU on shadow
        sta $FE34
@pk2:   lda $0B00,y                    ; bounce -> shadow page
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
        jmp bar                         ; (a bank loaded)
oldrom: .byte 0
c_ti:   .byte "LOAD MTITLE ", STAGE, 13
c_b4:   .byte "LOAD MBANK4 ", STAGE, 13
c_b7:   .byte "LOAD MBANK7 ", STAGE, 13
c_b5:   .byte "LOAD MBANK5 ", STAGE, 13
c_b6:   .byte "LOAD MBANK6 ", STAGE, 13
c_pk:   .byte "LOAD MPARK ", STAGE, 13
.ifdef TUBE
c_sv:   .byte "LOAD SERVER", 13
.endif
vdu_init:
        .byte 22, 130                   ; MODE 130: Mode 2 in shadow RAM
        .byte 23, 1, 0, 0, 0, 0, 0, 0, 0, 0     ; the cursor off
vdu_end:
crtc_tab:                               ; walk_drv.s's window (and cur_park)
        .byte 1, 64, 2, 90, 6, 20, 7, 28, 8, 0, 10, $20
        .byte 14, $3F, 15, $FF, 12, $30 >> 3, 13, 0
crtc_end:

stub_image:
        .segment "STUB"                 ; runs at $0900 (copied there first)
stub:
s_load:
        ldx #<s_main
        ldy #>s_main
        jsr $FFF7                       ; *LOAD MMAIN (to its own $0F00)
        jsr bar
        ldx #<s_cbits
        ldy #>s_cbits
        jsr $FFF7                       ; *LOAD MCBITS (to its own $5800) -- LAST
        jsr bar
.ifdef TUBE
        sei
        lda #$1D                        ; no Tube interrupts (clear Q J M V)
        sta $FEE0
        lda #$82                        ; but the server's IRQ from register
        sta $FEE0                       ;  1 (set I): it waits to see I
.endif
        sei
        lda $FE34                       ; X (read the parked streams in
        ora #$0C                        ;  shadow) and Y (HAZEL at $C000)
        sta $FE34
        ldx #0
@un:    lda s_src,x                     ; each stream: from shadow, to its
        sta $80                         ;  home (ANDY: ROMSEL bit 7)
        lda s_src+1,x
        sta $81
        lda s_dst,x
        sta $82
        lda s_dst+1,x
        sta $83
        lda s_rom,x
        sta $FE30
        phx
        jsr lz
        plx
        inx
        inx
        cpx #8
        bne @un
        lda $FE34
        and #$F3                        ; X and Y off again
        sta $FE34
        lda #5                          ; the music player (step 7ac): bank 5
        sta $FE30                       ;  MUS_STAGE -> MUS_ORG, pages 3..7,
        lda #>MUS_STAGE                 ;  the OS's until its last call
        sta $81
        lda #>MUS_ORG
        sta $83
        stz $80
        stz $82
        ldy #0
        ldx #5
@cp:    lda ($80),y
        sta ($82),y
        iny
        bne @cp
        inc $81
        inc $83
        dex
        bne @cp
        stz $FE30                       ; ANDY out (the engine pages its own)
        jmp DRV_ORG                     ; -> driver (SEI held; no OS from here)

; lz: the stream at ($80) into ($82) (lzpark.decode): $00-$7F t + 1
; literals, $80-$FE (t & $7F) + 3 bytes from dest - d (d: 2 bytes), $FF end
lz:     lda ($80)
        jsr s_inc
        cmp #$FF
        beq @rts
        cmp #$80
        bcs @m
        tax
        inx
@l:     lda ($80)
        sta ($82)
        jsr s_inc
        jsr d_inc
        dex
        bne @l
        bra lz
@m:     and #$7F
        clc
        adc #3
        tax
        sec                             ; the source: dest - d
        lda $82
        sbc ($80)
        sta $84
        jsr s_inc                       ; (INC keeps C)
        lda $83
        sbc ($80)
        sta $85
        jsr s_inc
@c:     lda ($84)
        sta ($82)
        inc $84
        bne :+
        inc $85
:       jsr d_inc
        dex
        bne @c
        bra lz
@rts:   rts
s_inc:  inc $80
        bne :+
        inc $81
:       rts
d_inc:  inc $82
        bne :+
        inc $83
:       rts

; bar: BAR_W more columns of the loading bar (lines 154-157 of the
; picture, its black last row), BP the next; ACCCON X for the call
bar:    lda $FE34
        ora #$04
        sta $FE34
        ldx #BAR_W
@b:     lda #BAR_BYTE
        ldy #3
:       sta (BP),y
        dey
        bpl :-
        lda BP
        clc
        adc #8
        sta BP
        bcc :+
        inc BP+1
:       dex
        bne @b
        lda $FE34
        and #$FB
        sta $FE34
        rts

; the streams: shadow source, destination, ROMSEL (HAZEL, ANDY, the gun,
; the panel -- the panel last: its stream sits above its home)
s_src:  .word S_HAZEL, S_ANDY, S_GUN, S_PANEL
s_dst:  .word $C000, $8000, MGUN0, MPANEL
s_rom:  .byte 0, 0, $80, 0, 0, 0, 0, 0  ; (indexed by X = 2 * stream)
s_main:  .byte "LOAD MMAIN", 13
s_cbits: .byte "LOAD MCBITS", 13
s_end:
stub_len = s_end - stub
        .assert stub_len <= 512, error, "stub must fit pages 9 and 10"
        .assert l_cont = $1903, error, "the server's *GOIO 1903 lands on l_cont"
        .assert S_PANEL >= MPANEL + $600, error, "the panel's stream sits above its home"
