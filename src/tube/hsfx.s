; ============================================================================
; tube/hsfx.s -- sound effects on the Tube host (docs/tube_master.md H9).
; master_sfx.py is the spec (Sfx); test_sfx.py holds this to it chip byte
; for chip byte. Main RAM (segment SFX, $5005 up): the split IRQ calls
; sfx_tick with ACCCON's X cleared (the drawer may have shadow paged in).
;
;   sfx_start  A = an effect (the main loop; X Y kept): a request the next
;              tick takes, if its priority is at least the playing one's
;   sfx_tick   (the IRQ, right after MUS_TICK; A free, X Y kept) a taken
;              request starts its effect; a playing one writes its frame
;              -- SOUND 3's tone period and attenuation, the noise's
;              control when it changes and its attenuation -- over what
;              the music wrote this tick; at its end both are silenced and
;              the music's ch_att for them spoiled, so it writes its own
;              levels again on its next tick
; The chip is on System VIA port A, shared with the keyboard scan: saved,
; written and restored as MUS_TICK does.
; ============================================================================
        .setcpu "65C02"
        .export sfx_start, sfx_tick, sx_cur, sx_req
        .segment "SFX"

sfx_start:
        phx
        tax
        lda sx_cur
        bmi @ok                         ; nothing playing
        lda sx_pri,x
        cmp sx_cp
        bcc @no                         ; outranked
@ok:    stx sx_req
@no:    plx
        rts

sfx_tick:
        lda sx_req
        bmi @nr
        sta sx_cur                      ; a request: its effect from frame 0
        phx
        tax
        lda sx_pri,x
        sta sx_cp
        lda sx_lo,x
        sta sx_rd+1
        lda sx_hi,x
        sta sx_rd+2
        plx
        lda #$FF
        sta sx_req
        sta sx_nc                       ; (no noise control written yet)
@nr:    lda sx_cur
        bpl :+
        rts
:       phx
        phy
        lda $FE43                       ; the port, as MUS_TICK has it
        sta sx_ddra
        lda $FE4F
        sta sx_ora
        lda #$FF
        sta $FE43
        lda #$0B
        sta $FE40
        ldy #0
        jsr sx_rd                       ; the frame's first byte
        cmp #$FF
        bne @fr
        lda #$9F                        ; the end: both silent, the music's
        jsr sx_chip                     ;  levels spoiled (its next tick
        lda #$FF                        ;  writes them)
        jsr sx_chip
        sta MUS_CH_ATT+3
        sta MUS_CH_ATT+0
        sta sx_cur
        bra @out
@fr:    iny
        jsr sx_rd                       ; N hi (bit 7: no tone)
        bmi @nt
        dey
        jsr sx_rd
        jsr sx_chip                     ; $80 | N lo
        iny
        jsr sx_rd
        jsr sx_chip                     ; N hi
        iny
        jsr sx_rd
        lsr a
        lsr a
        lsr a
        lsr a
        ora #$90
        jsr sx_chip                     ; its attenuation
        bra @nz
@nt:    lda #$9F
        jsr sx_chip
@nz:    ldy #3
        jsr sx_rd                       ; the noise control ($FF: none)
        cmp #$FF
        bne :+
        lda #$FF                        ; no noise: silent
        jsr sx_chip
        bra @adv
:       cmp sx_nc
        beq :+
        sta sx_nc
        ora #$E0
        jsr sx_chip                     ; (only when it changes)
:       ldy #2
        jsr sx_rd
        and #$0F
        ora #$F0
        jsr sx_chip
@adv:   clc                             ; the next frame
        lda sx_rd+1
        adc #4
        sta sx_rd+1
        bcc @out
        inc sx_rd+2
@out:   lda #$03                        ; the port back
        sta $FE40
        lda sx_ora
        sta $FE4F
        lda sx_ddra
        sta $FE43
        ply
        plx
        rts

sx_rd:  lda $FFFF,y                     ; (patched: the frame)
        rts

; sx_chip: A -> the SN76489 (as mmusic.s's chip; X Y kept)
sx_chip:
        sta $FE4F
        stz $FE40
        nop
        nop
        nop
        nop
        nop
        nop
        pha
        lda #$08
        sta $FE40
        pla
        rts

sx_cur:  .byte $FF                      ; the effect playing ($FF none)
sx_req:  .byte $FF                      ; a request ($FF none)
sx_cp:   .byte 0                        ; the playing one's priority
sx_nc:   .byte $FF                      ; the noise control last written
sx_ddra: .byte 0
sx_ora:  .byte 0
        .include "hsfx_tab.s"
