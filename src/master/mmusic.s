; The music player (docs/master_textured_spec.md step 7ac): plays the
; beebtune E1M1 listing (music/e1m1.bas) on the SN76489. master_music.py is
; the spec (Player); test_master_music.py runs this against it, chip byte
; for chip byte.
;
; Resident at MUS_ORG ($0300-$07FF), copied there from bank 5 MUS_STAGE by
; the boot stub once the OS is done with pages 3..7. Three entries:
;   MUS_INIT   silence the chip, rewind the tune, first refill (SEI held)
;   MUS_REFILL copy the tune's tokens (bank 7 MUS_STREAM) into the ring
;              at MUS_RING (HAZEL $D700) while it has room, in play order:
;              the main loop, once a frame (255 tokens: 8.6 s of the tune
;              at its densest)
;   MUS_TICK   the 50 Hz tick, from split_irq @top: decode the ring and
;              start the due notes, step the envelopes, write what changed
; Only the tick moves m_rd, only the refill m_wr (stored after the token).
;
; The chip hangs off System VIA port A, which the driver's keyboard scan
; shares (DDRA $7F, key numbers written to $FE4F): the tick saves DDRA and
; the port, puts the keyboard on auto-scan (latch bit 3 high, off PA7)
; while it writes, and puts all three back.

        .include "abi.inc"
        .include "mmusic_tab.inc"

RING = MUS_RING
OFF = 4                                 ; phases: 0 attack 1 decay 2 sustain
                                        ;  3 release 4 off

        .segment "MUS"
        jmp init
        jmp refill
; ---------------------------------------------------------------------------
; MUS_TICK (MUS_ORG+6): A free, X Y kept
; ---------------------------------------------------------------------------
tick:
        phx
        phy
        lda $FE43
        sta m_ddra
        lda $FE4F
        sta m_ora
        lda #$FF
        sta $FE43                       ; port A all out
        lda #$0B
        sta $FE40                       ; keyboard off PA7 (auto-scan)
; 1. decode the ring: block starts and waits move W; an event plays once
;    due (else it waits in the ring for a later tick)
@ev:    ldx m_rd
        cpx m_wr
        bne :+
        jmp @env                        ; ring empty
:       ldy RING,x
        cpy #$FF
        bne :+
        jmp @blk
:       cpy #MUS_NP
        bcc :+
        jmp @wait
:       lda m_w+1                       ; an event: due = ((W >> 8) + 1) >> 1
        clc
        adc #1
        sta m_t
        lda m_w+2
        adc #0
        sta m_t+1
        lda m_w+3
        adc #0
        lsr a
        ror m_t+1
        ror m_t
        lda m_tick
        cmp m_t
        lda m_tick+1
        sbc m_t+1
        bpl :+
        jmp @env                        ; not due (tick - due < 0)
:       inx
        stx m_rd
        lda mus_da,y                    ; len << 4 | (env - 1) << 2 | voice
        and #3
        tax                             ; X = the channel
        stz ch_lvl,x
        lda mus_da,y
        and #$F0
        bne @snd
        txa
        bne @sil                        ; a tone of length 0: silent
@snd:   lda mus_da,y
        pha
        and #$0C
        lsr a
        lsr a
        inc a
        sta ch_env,x
        stz ch_ph,x                     ; attack
        pla
        lsr a
        lsr a
        lsr a
        lsr a
        phy
        tay
        lda mus_dur,y
        sta ch_dur,x
        ply
        lda mus_db,y
        cpx #0
        beq @nz
        tay                             ; a tone: its period's index
        lda cbase,x
        ora mus_plo,y
        jsr chip
        lda mus_phi,y
        jsr chip
        jmp @ev
@nz:    ora #$E0                        ; noise: the control byte
        jsr chip
        jmp @ev
@sil:   stz ch_env,x                    ; silent: the channel off at level 0
        lda #OFF
        sta ch_ph,x
        jmp @ev
@wait:  inx                             ; a wait: W += D * S%
        stx m_rd
        clc
        lda m_w
        adc mus_wd0-MUS_NP,y
        sta m_w
        lda m_w+1
        adc mus_wd1-MUS_NP,y
        sta m_w+1
        lda m_w+2
        adc mus_wd2-MUS_NP,y
        sta m_w+2
        bcc :+
        inc m_w+3
:       jmp @ev
@blk:   inx                             ; a block starts (PROCnext)
        stx m_rd
        ldy m_j
        cpy #MUS_Q
        bne @nwrap
        ldy #0                          ; the order wrapped: Z -= R%
        ldx #0
        sec
@sub:   lda m_z,x
        sbc mus_r,x
        sta m_z,x
        inx
        txa
        eor #4
        bne @sub
@nwrap: iny
        sty m_j
        ldx #0                          ; W = Z, Z += K% * S%
        clc
@add:   lda m_z,x
        sta m_w,x
        adc mus_ks,x
        sta m_z,x
        inx
        txa
        eor #4
        bne @add
        jmp @ev
; 2. per channel 3..0: two 1 cs envelope steps, then the attenuation
@env:   ldx #3
@ch:    lda ch_ph,x
        cmp #OFF
        beq @att
        jsr step
        jsr step
@att:   lda ch_lvl,x
        lsr a
        lsr a
        lsr a
        eor #$0F                        ; 15 - level / 8
        cmp ch_att,x
        beq @nx
        sta ch_att,x
        ora vbase,x
        jsr chip
@nx:    dex
        bpl @ch
        inc m_tick
        bne :+
        inc m_tick+1
:       lda #$03
        sta $FE40                       ; keyboard: manual scan again
        lda m_ora
        sta $FE4F
        lda m_ddra
        sta $FE43
        ply
        plx
        rts

; chip: A -> the SN76489 (port A all out; X Y kept). Write enable low for
; the chip's 8us (32 of its 4 MHz clocks), then high.
chip:
        sta $FE4F
        stz $FE40
        nop
        nop
        nop
        nop
        nop
        nop
        lda #$08
        sta $FE40
        rts

; step: one 1 cs step of channel X's envelope (Y clobbered)
step:
        lda ch_dur,x
        beq @go                         ; 0: held
        dec ch_dur,x
        bne @go
        lda ch_ph,x
        cmp #3
        bcs @go
        lda #3
        sta ch_ph,x                     ; the duration's end: release
@go:    ldy ch_env,x
        lda ch_ph,x
        beq @atk
        cmp #2
        bcc @dec
        beq @sus
        cmp #OFF
        beq @ret
        lda ch_lvl,x                    ; release: AR < 0 down to 0, then off
        clc
        adc e_ar,y
        bcc @zero
        beq @zero
        sta ch_lvl,x
@ret:   rts
@zero:  stz ch_lvl,x
        lda #OFF
        sta ch_ph,x
        rts
@atk:   lda ch_lvl,x                    ; attack: AA > 0 up to ALA
        clc
        adc e_aa,y
        cmp e_ala,y
        bcc :+
        lda #1
        sta ch_ph,x
        lda e_ala,y
:       sta ch_lvl,x
        rts
@dec:   lda e_ad,y                      ; decay: AD <= 0 down to ALD
        beq @tosus
        clc
        adc ch_lvl,x
        bcc @ald                        ; below 0
        cmp e_ald,y
        bcc @ald
        beq @ald
        sta ch_lvl,x
        rts
@ald:   lda e_ald,y
        sta ch_lvl,x
@tosus: lda #2
        sta ch_ph,x
        rts
@sus:   lda e_as,y                      ; sustain: AS <= 0, no lower than 0
        beq @ret
        clc
        adc ch_lvl,x
        bcs :+
        lda #0
:       sta ch_lvl,x
        rts

; ---------------------------------------------------------------------------
; MUS_REFILL: copy tokens into the ring while it has room, in play order
; (pages BANK_WALK; A X Y clobbered)
; ---------------------------------------------------------------------------
refill:
        lda #BANK_WALK
        sta $FE30
more:   ldx m_wr
        inx
        cpx m_rd
        beq rf_full                     ; wr + 1 = rd: full
        dex
tok:    lda ff_byte                     ; (patched: the stream pointer)
        inc tok+1
        bne :+
        inc tok+2
:       sta RING,x
        inx
        stx m_wr                        ; (after the token: the IRQ reads)
        cmp #$FF
        bne more
        ldy m_jr                        ; a block's end: on to the next
        cpy #MUS_Q                      ;  in the play order
        bne :+
        ldy #0
:       ldx MUS_ORDER,y
        iny
        sty m_jr
        lda MUS_OFFLO,x
        sta tok+1
        lda MUS_OFFHI,x
        sta tok+2
        bra more
rf_full:
        rts

; ---------------------------------------------------------------------------
; MUS_INIT: silence, rewind, first refill (SEI held; the driver's init)
; ---------------------------------------------------------------------------
init:
        lda #$FF
        sta $FE43
        lda #$0B
        sta $FE40
        ldx #3
:       lda vbase,x
        ora #$0F
        jsr chip
        lda #$0F
        sta ch_att,x
        lda #OFF
        sta ch_ph,x
        stz ch_lvl,x
        stz ch_env,x
        stz ch_dur,x
        dex
        bpl :-
        lda #$03
        sta $FE40
        lda #$7F
        sta $FE43
        ldx #m_end - m_rd - 1
:       stz m_rd,x
        dex
        bpl :-
        lda #<ff_byte
        sta tok+1
        lda #>ff_byte
        sta tok+2
        jmp refill

cbase:  .byte $E0, $C0, $A0, $80        ; SOUND 0..3 -> the chip's registers
vbase:  .byte $F0, $D0, $B0, $90        ;  and their attenuations
ff_byte: .byte $FF                      ; a block end: the first refill
                                        ;  starts block order[0]
mus_ks: .dword MUS_KS                   ; K% * S%: a block, in 1/256 cs
mus_r:  .dword MUS_R                    ; R%: the loop's rewind
        MUS_TABLES

m_rd:   .res 1
m_wr:   .res 1
m_tick: .res 2
m_j:    .res 1
m_jr:   .res 1
m_w:    .res 4
m_z:    .res 4
m_end:
m_t:    .res 2
m_ddra: .res 1
m_ora:  .res 1
ch_lvl: .res 4
ch_ph:  .res 4
ch_env: .res 4
ch_dur: .res 4
ch_att: .res 4
