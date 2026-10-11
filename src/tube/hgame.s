; ============================================================================
; tube/hgame.s -- the game's first rules on the Tube host (docs/
; tube_master.md H8): pickups and nukage, into the panel's numbers.
; Included by hdraw.s in bank 6 (MB6C). game_ref.py is the spec;
; test_game.py holds this to it.
;
; gm_tick (bank 6 paged, once a frame after the move; A = the player's
; subsector, the raws in zp_br_pxraw/pyraw, ANIM_FIELDS the fields since
; the last frame):
;   pickups  each one not GONE whose raws are within GM_TOUCH of the
;            player's on both axes is given (gm_give); if it was taken its
;            object goes into OBJ_GONE, which obj_subsector skips
;   nukage   a clock of fields wraps every GM_PERIOD; each wrap with the
;            player in a nukage subsector is a hit of 5 (armour saves 1
;            for type 1, 2 for type 2; health stops at 0)
;   panel    health and armour into pn_val (pn_update draws them)
; ============================================================================
gm_ss   = pn_c                          ; (scratch: pn_update runs later)
gm_i    = pn_i

gm_tick:
   STA gm_ss
   LDA gm_ready                         ; the first frame: nothing gone
   BNE @go
   LDX #GONE_LEN - 1
:  STZ OBJ_GONE,X
   DEX
   BPL :-
   INC gm_ready
@go:
   LDX #0                               ; (in the table's order)
@pk:
   STX gm_i
   LDA gm_po,X                          ; gone already?
   TAY
   LSR A
   LSR A
   LSR A
   STA gm_t
   TYA
   AND #7
   TAY
   LDA gm_bit,Y
   LDY gm_t
   AND OBJ_GONE,Y
   BNE @nx
   SEC                                  ; |px - x| < GM_TOUCH
   LDA zp_br_pxraw_l
   SBC gm_pxl,X
   TAY
   LDA zp_br_pxraw_h
   SBC gm_pxh,X
   JSR gm_near
   BCC @nx
   SEC                                  ; |py - y| < GM_TOUCH
   LDA zp_br_pyraw_l
   SBC gm_pyl,X
   TAY
   LDA zp_br_pyraw_h
   SBC gm_pyh,X
   JSR gm_near
   BCC @nx
   LDA gm_pt,X
   JSR gm_give                          ; C = 1: taken
   BCC @nx
   LDX gm_i                             ; GONE: obj_subsector skips it
   LDA gm_po,X
   TAY
   LSR A
   LSR A
   LSR A
   STA gm_t
   TYA
   AND #7
   TAY
   LDA gm_bit,Y
   LDY gm_t
   ORA OBJ_GONE,Y
   STA OBJ_GONE,Y
@nx:
   LDX gm_i
   INX
   CPX #GM_NPK
   BCS :+
   JMP @pk
:
; --- nukage: the field clock ---
   CLC
   LDA gm_clock
   ADC ANIM_FIELDS
   STA gm_clock
   LDA #0
   ROL A
   STA gm_t                             ; the clock's 9th bit
@clk:
   LDA gm_t                             ; clock >= GM_PERIOD?
   BNE :+
   LDA gm_clock
   CMP #GM_PERIOD
   BCC @panel
:  SEC
   LDA gm_clock
   SBC #GM_PERIOD
   STA gm_clock
   LDA gm_t
   SBC #0
   STA gm_t
   LDA gm_ss                            ; a wrap: in nukage?
   LSR A
   LSR A
   LSR A
   TAY
   LDA gm_ss
   AND #7
   TAX
   LDA gm_bit,X
   AND gm_nuke,Y
   BEQ @clk
   LDA gm_health
   BEQ @clk
   LDA #5
   JSR gm_hurt
   BRA @clk
@panel:
   LDA gm_health                        ; the panel's values (pn_update)
   STA pn_val+2
   STZ pn_val+3
   LDA gm_armor
   STA pn_val+4
   STZ pn_val+5
   RTS

; gm_near: C = 1 iff the s16 (A hi, Y lo) is within (-GM_TOUCH, GM_TOUCH)
gm_near:
   BMI @neg
   BNE @no                              ; >= 256
   CPY #GM_TOUCH
   BCC @yes
@no:
   CLC
   RTS
@neg:
   CMP #$FF
   BNE @no
   CPY #<(1 - GM_TOUCH)                 ; > -GM_TOUCH: lo >= $DD
   RTS                                  ; (C as the compare left it)
@yes:
   SEC
   RTS

; gm_give: pickup type A to the player; C = 1 if taken
gm_give:
   CMP #2
   BCS @nh
   TAX                                  ; stimpack (0) / medikit (1)
   LDA gm_health
   CMP #100
   BCS @no                              ; (not taken at 100 or more)
   ADC gm_heal,X                        ; (C = 0)
   CMP #100
   BCC :+
   LDA #100
:  STA gm_health
   SEC
   RTS
@no:
   CLC
   RTS
@nh:
   BNE @nb
   LDA gm_health                        ; potion: +1 to 200
   CMP #200
   BCS @yes
   INC gm_health
@yes:
   SEC
   RTS
@nb:
   CMP #3
   BNE @arm
   LDA gm_armor                         ; helmet: +1 to 200, type 1 if none
   CMP #200
   BCS :+
   INC gm_armor
:  LDA gm_atype
   BNE @yes
   INC gm_atype
   BRA @yes
@arm:
   TAX                                  ; green (4): 100, type 1; blue (5):
   LDA gm_armor                         ;  200, type 2 -- not taken at or
   CMP gm_atop-4,X                      ;  above it
   BCS @no
   LDA gm_atop-4,X
   STA gm_armor
   TXA
   SEC
   SBC #3
   STA gm_atype
   SEC
   RTS

; gm_hurt: A damage, less what the armour saves
gm_hurt:
   STA gm_t
   LDX gm_atype
   BEQ @hp
   LDA gm_t                             ; saved = d // 3 (type 1), d // 2
   LDY #0                               ;  (type 2): by subtraction
:  SEC
   SBC gm_div-1,X
   BCC :+
   INY
   BRA :-
:  CPY gm_armor                         ; armour <= saved: all of it, and
   BCC :+                               ;  no type left
   LDY gm_armor
   STZ gm_atype
:  STY gm_s
   LDA gm_armor
   SEC
   SBC gm_s
   STA gm_armor
   LDA gm_t
   SEC
   SBC gm_s
   STA gm_t
@hp:
   LDA gm_health                        ; health - d, 0 at least
   SEC
   SBC gm_t
   BCS :+
   LDA #0
:  STA gm_health
   RTS

gm_heal: .byte 10, 25
gm_atop: .byte 100, 200
gm_div:  .byte 3, 2
gm_bit:  .byte $01, $02, $04, $08, $10, $20, $40, $80
gm_ready:  .byte 0                      ; (bank 6 RAM: MB6C is RW)
gm_health: .byte 100
gm_armor:  .byte 0
gm_atype:  .byte 0
gm_clock:  .byte 0
gm_t:      .byte 0
gm_s:      .byte 0
.include "hgame_tab.s"
