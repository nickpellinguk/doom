; The H4a host pump (tools/tube_link_rig.mjs): one frame's requests out
; through register 3 (the fill server takes them by NMI), then the frame
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
        lda #$17                        ; clear Q I J V
        sta $FEE0
:       bit $FEE0                       ; the server is ready when frame
        bpl :-                          ;  -1's list (one byte) is waiting
        lda #$88                        ; set M: the server's NMI
        sta $FEE0
pump_n:
out:    lda cnt_o
        ora cnt_o+1
        beq in
:       bit $FEE4                       ; register 3: room
        bvc :-
        lda (ptr_o)
        sta $FEE5
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
