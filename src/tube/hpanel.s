; ============================================================================
; tube/hpanel.s -- the panel's big numbers on the Tube host (docs/
; tube_master.md H7), included by hdraw.s in bank 6 (MB6C). master_panel.py
; is the spec (panel_with, glyph_bytes); test_panel_nums.py holds this to
; it.
;
; pn_update (bank 6 paged, once a frame): each field (ammo, health,
; armour) whose value (pn_val) is not the one on show (pn_shown) is drawn
; -- three glyphs, leading zeros blank, 999 at most. A glyph is pre-built
; with the stone's cross-hatch and the panel's black top line, so drawing
; it is two straight 24-byte copies into the ONE shared panel (MPANEL,
; shadow: ACCCON X), character rows 17 and 18 at the field's byte column.
; ============================================================================
pn_src  = ixp                           ; (hdraw's zero page: dead between
pn_dst  = scr                           ;  frames)

pn_update:
   LDX #PN_FIELDS - 1
@f:
   TXA
   ASL A
   TAY                                  ; Y = the field's word
   LDA pn_val,Y
   CMP pn_shown,Y
   BNE @draw
   LDA pn_val+1,Y
   CMP pn_shown+1,Y
   BEQ @nx
@draw:
   LDA pn_val,Y                         ; on show from now
   STA pn_shown,Y
   STA pn_t
   LDA pn_val+1,Y
   STA pn_shown+1,Y
   STA pn_t+1
   STX pn_f
   JSR pn_field
   LDX pn_f
@nx:
   DEX
   BPL @f
   RTS

; pn_field: field pn_f showing pn_t (u16)
pn_field:
   LDA pn_t+1                           ; 999 at most
   CMP #>1000
   BCC @lo
   BNE @cap
   LDA pn_t
   CMP #<1000
   BCC @lo
@cap:
   LDA #<999
   STA pn_t
   LDA #>999
   STA pn_t+1
@lo:
   LDX #0                               ; hundreds: subtract 100s
@h:
   LDA pn_t
   SEC
   SBC #100
   TAY
   LDA pn_t+1
   SBC #0
   BCC @hd
   STA pn_t+1
   STY pn_t
   INX
   BRA @h
@hd:
   STX pn_d                             ; (pn_t < 100 now: one byte)
   LDX #0
   LDA pn_t
@t:
   CMP #10
   BCC @td
   SBC #10                              ; (C = 1)
   INX
   BRA @t
@td:
   STA pn_d+2                           ; units
   STX pn_d+1                           ; tens
   LDA pn_d                             ; leading zeros blank
   BNE @go
   LDA #10
   STA pn_d
   LDA pn_d+1
   BNE @go
   LDA #10
   STA pn_d+1
@go:
   LDX pn_f
   LDA pn_k,X
   STA pn_c
   LDA #ACC_DXY                         ; the panel is shadow RAM
   STA $FE34
   LDX #0
@g:
   STX pn_i
   LDA pn_d,X
   JSR pn_glyph1
   LDA pn_c                             ; the next cell: 3 bytes on
   CLC
   ADC #3
   STA pn_c
   LDX pn_i
   INX
   CPX #3
   BNE @g
   LDA #ACC_DY
   STA $FE34
   RTS

; pn_glyph1: glyph A into the cell at byte column pn_c
pn_glyph1:
   TAX
   LDA pn_gl,X
   STA pn_src
   LDA pn_gh,X
   STA pn_src+1
   LDA pn_c                             ; MPANEL + c * 8
   STZ pn_dst+1
   ASL A
   ROL pn_dst+1
   ASL A
   ROL pn_dst+1
   ASL A
   ROL pn_dst+1
   STA pn_dst
   LDA pn_dst+1
   CLC
   ADC #>MPANEL
   STA pn_dst+1
   LDY #23                              ; character row 17
:  LDA (pn_src),Y
   STA (pn_dst),Y
   DEY
   BPL :-
   LDA pn_src                           ; row 18: the glyph's next 24,
   CLC                                  ;  the panel's next 512
   ADC #24
   STA pn_src
   BCC :+
   INC pn_src+1
:  LDA pn_dst+1
   CLC
   ADC #2
   STA pn_dst+1
   LDY #23
:  LDA (pn_src),Y
   STA (pn_dst),Y
   DEY
   BPL :-
   RTS

pn_t:    .res 2                         ; (bank 6 RAM: MB6C is RW)
pn_f:    .res 1
pn_c:    .res 1
pn_i:    .res 1
pn_d:    .res 3
.include "hpanel_tab.s"
