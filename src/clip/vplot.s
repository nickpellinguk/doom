; ============================================================================
; clip/vplot.s — clipper fragment 11 of 13 (module map: clip/header.s).
; plot_v: the vertical emit entry. On the Master it is an RTS emit stub: the
; fill (src/master/mfill.s) draws from the span pool, and the py65 rig traps
; this PC and reads the staged RASTER_ZP args (the engine's line list).
; ============================================================================
SEG_BANKC
plot_v:
   RTS
