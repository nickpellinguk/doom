; The H4a host pump (tools/tube_link_rig.mjs): one frame's requests out
; through register 1 (the fill server takes them by IRQ), then the frame
; before's display list in through register 1, polled. Counts and
; pointers in zero page, set by the rig: cnt_o / ptr_o, cnt_i / ptr_i.
.setcpu "65C02"
cnt_o = $70
ptr_o = $72
cnt_i = $74
ptr_i = $76
.segment "CODE"
pump:                                   ; (the first frame: the flags)
        sei
:       bit $FEE2                       ; the server's OSCLI (R2: $02, the
        bpl :-                          ;  command, CR -- the boot stub's
        lda $FEE3                       ;  *GOIO): taken and dropped
        cmp #13
        bne :-
        lda #$1D                        ; clear Q J M V
        sta $FEE0
        lda #$82                        ; set I: the server's IRQ
        sta $FEE0
pump_n:
out:    lda cnt_o
        ora cnt_o+1
        beq in
:       bit $FEE0                       ; register 1: room
        bvc :-
        lda (ptr_o)
        sta $FEE1
        inc ptr_o
        bne :+
        inc ptr_o+1
:       lda cnt_o
        bne :+
        dec cnt_o+1
:       dec cnt_o
        bra out
in:     lda cnt_i
        ora cnt_i+1
        beq done
:       bit $FEE0                       ; register 1: data
        bpl :-
        lda $FEE1
        sta (ptr_i)
        inc ptr_i
        bne :+
        inc ptr_i+1
:       lda cnt_i
        bne :+
        dec cnt_i+1
:       dec cnt_i
        bra in
done:   bra done
.export pump, pump_n, done
