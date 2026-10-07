; ============================================================================
; clip/fbclear.s — clipper fragment 13 of 13, last in the link (module
; map: clip/header.s). The shadow buffers are cleared by HAZEL code with
; ACCCON X set (src/master); the engine-side clears are RTS stubs so the
; driver still links against them.
; ============================================================================
SEG_BANKC
fb_clr0:
fb_clr1:
fb_clr_back:
   RTS
