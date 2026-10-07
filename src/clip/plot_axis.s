; ============================================================================
; clip/plot_axis.s — clipper fragment 9 of 13 (module map: clip/header.s).
; plot_h and RASTER_ENTRY (raster_stub_m): the horizontal and general emit
; entries, RTS emit stubs on the Master (see clip/vplot.s). The py65 rig
; traps these PCs and reads the staged RASTER_ZP args.
;
; The mask tables below are dead (the Model B plotters' masks); they stay
; for now so the Model B excision left the Master binary byte-identical.
; ============================================================================
plot_lmask:
   .byte $FF, $7F, $3F, $1F, $0F, $07, $03, $01
; right-edge masks: pixels from the byte's left through bit (x&7)
plot_rmask:
   .byte $80, $C0, $E0, $F0, $F8, $FC, $FE, $FF
; single-pixel masks
plot_bmask:
   .byte $80, $40, $20, $10, $08, $04, $02, $01

plot_h:
   RTS
raster_stub_m:                             ; RASTER_ENTRY (clip/arith.s)
   RTS
