; ============================================================================
; tube/fserve.s -- THE FILL SERVER (docs/tube_master.md H3): the second
; processor's half of the host-led Tube Master. It takes a frame's fill
; requests (tube_req.py: what the Master's fill is called with) and
; answers with the frame's display list (tube_dl.py), byte for byte what
; tube_dl.encode makes of tube_req.FillServer's list (test_tube_server.py
; holds it to that).
;
; The fill itself is the Master's: master/mfill.s assembled with SERVER,
; its screen code gone. What drew now records:
;   tr_screen -> fs_wall   a wall run: its WALL record, straight out
;   hz_run    -> fs_mark   a solid run (sky, a shade): the cell grid
;   prun      -> fs_mark   a textured plane run: the cell grid, with the
;                          plane fs_planes named for the seg
; and at the frame's end the grid becomes the list's SPANs (fs_spans) and
; FILLs (fs_fills), in tube_dl.encode's order.
;
; The MARKS: each fs_mark (a run of one byte column, lines y0 .. y1, one
; code) goes into its column's list, kept sorted by y0 -- solid runs and
; plane runs in two lists. A code is
;   < $80      a solid run's shade byte (WB_SKY, WB_CEIL, WB_FLOOR)
;   $80 | i    a textured plane: entry i of the frame's plane table
;              (fs_pd, fs_pf: its D and flat)
; At the frame's end a sweep over the columns (fs_sweep) compares each
; column's plane list with the one before: only the lines whose code
; changes open or close a run, into the line's run list (fs_rn); the
; lines' runs then make the SPANs (fs_spans), the solid lists the FILLs
; (fs_fills). A frame has ~140 marks, at most ~10 in a column.
;
; On the second processor (H4, fs_main) the host's register 1 bytes come
; in by IRQ (fs_irq) into a ring the frame reads (fs_get); the list is
; written to one of two buffers while the other's goes out through
; register 1, topped up (fs_poll) from the frame's own loops -- so the
; host draws frame N while this builds N + 1. Register 1's FIFO never
; interrupts either side. The py65 rig (tube_server.py) fills the ring
; itself and reads the list back from its buffer.
; ============================================================================
.setcpu "65C02"

ZP_OWNER = 1                            ; (this link's zero page and WORK)
.include "../zp.inc"

.import mf_frame, mf_fill, pl_rowc, mf_ep, pl_p, pl_d, pl_kind, pl_df, pl_dc
.import pl_ff, pl_fc, pc_uc, pc_du, pc_vc, pc_dv, pc_lv, far_tone, mf_xt
.import tw_ll, tw_rl, tw_sh, l_v, l_step, t_v, t_tid, zw_dl, zw_ddl
.import sn_xs, sn_xe, sn_xlo, sn_den, sn_tl, sn_tr, sn_bl, sn_br, sn_bxlo
.import sn_bden, sn_n, tx_slot, mf_x, r_ys, r_ye
.importzp zw_ddh
.export fs_mark, fs_wall, fs_planes, fs_frame, fs_poll, fs_main, fs_ring
.export fs_lista, fs_listb, fs_ob, fs_drop, fs_ringsz
.exportzp fs_rp, fs_rw, fs_op

PAIR_Y   = $88                          ; tube_dl.PAIR_Y: a pair group's y
WB_SKY   = $28                          ; (master/mfill.s) the solid shades
SKY_EV   = $3D                          ; the sky's FILL byte (SKY_BYTE)
VIEW_PAIRS = VIEW_LINES / 2

fs_rp = zp_tmp0                         ; the request ring's read and
fs_rw = zp_pm_p                         ;  write (fs_irq) pointers
fs_sp = zp_anim_p                       ; the list going out: next byte, end
fs_se = zp_anim_w
RN = 16                                 ; runs a line (fs_rn; 9 seen)
PN = 16                                 ; plane marks a column (9 seen)
SN = 8                                  ; solid marks a column (5 seen)
.ifndef RING
RING = $0800                            ; fs_ring's size (page-aligned at a
.endif                                  ;  multiple of it; -D for the gates)
fs_ringsz = RING                        ; (for tube_server's py65 feed)
LISTSZ = $1A00                          ; each list buffer's size: records
.ifndef LISTCAP                         ;  stop past LISTCAP (fs_drop), so a
LISTCAP = LISTSZ - $100                 ;  record, a header and the $00 fit
.endif                                  ;  (-D for the gates)
NPLANE = 48                             ; planes a frame (29 seen)
PTR = RASTER_ZP_X1                      ; (scratch pointer: mfill's screen one)
fs_cp = pa_dx                           ; a mark list: its column's block,
fs_cq = pa_dy                           ;  and + 4 (the angle module's zero
fs_pp = pa_res                          ;  page: not on the server); the
fs_lp = pa_ptr                          ;  sweep's column before; a line's runs
fs_op = zp_prod_l                       ; the list write pointer
.assert zp_prod_h = zp_prod_l + 1, error, "fs_op is two bytes"

.macro GETB                             ; A = the next request byte (flags: not A's)
   JSR fs_get
.endmacro

.macro DROP                             ; one more dropped (fs_drop1)
   JSR fs_drop1
.endmacro

.macro PUTO                             ; A -> the list
   STA (fs_op)
   INC fs_op
   BNE :+
   INC fs_op+1
:
.endmacro

.segment "FSIV"                         ; page-aligned
fs_ivp:  .res 64 * 4 * PN               ; per byte column k: PN plane marks
fs_ivf:  .res 64 * 4 * SN               ;  at + k * 64 / SN solid marks at
                                        ;  + k * 32 (y0, y1, code, -),
                                        ;  sorted by y0
fs_rn:   .res $2200                     ; per line y: 16 runs (k0, k1, code,
                                        ;  -) at + y * 64, in k order
.segment "FSLA"
fs_lista: .res LISTSZ                   ; the lists: one being built, the
.segment "FSLB"                         ;  other going out (5.7K seen)
fs_listb: .res LISTSZ
.segment "FSRING"                       ; RING-aligned
fs_ring: .res RING                      ; the host's request bytes

.segment "FSBSS"
fs_pn:   .res 1                         ; planes in the table
fs_pd:   .res NPLANE                    ; plane i: D
fs_pf:   .res NPLANE                    ;  and flat
fs_idf:  .res 1                         ; this seg's floor / ceiling plane
fs_idc:  .res 1                         ;  codes ($80 | i; 0 none)
fs_wn:   .res 1                         ; WALL group: entries so far (0 none),
fs_wt:   .res 1                         ;  texture, last byte column, header
fs_wk:   .res 1
fs_wh:   .res 2
fs_t:    .res 2                         ; scratch
fs_ty0:  .res 1                         ; fs_trim: the lines drawn over,
fs_ty1:  .res 1                         ;  the column, its list (0 plane, 64
fs_tk:   .res 1                         ;  solid), marks left to look at, a
fs_tl:   .res 1                         ;  mark's place, the shift's end
fs_tn:   .res 1
fs_ty:   .res 1
fs_te:   .res 1
fs_k:    .res 1
fs_y:    .res 1
fs_n:    .res 1
fs_gh:   .res 2                         ; a SPAN group's header
fs_gn:   .res 1                         ;  and its count
; a line's plane runs, even line at re_* + 0.., odd at re_* + RO..: k0,
; k1, the row's U V at column 32 and steps (4.4), flat (| $80: far), far
; tone
RO = 16                                 ; (a line's runs: at most RN)
re_k0:   .res 2 * RO
re_k1:   .res 2 * RO
re_uc:   .res 2 * RO
re_du:   .res 2 * RO
re_vc:   .res 2 * RO
re_dv:   .res 2 * RO
re_fl:   .res 2 * RO
re_fb:   .res 2 * RO
fs_ne:   .res 1                         ; runs on the even / odd line
fs_no:   .res 1
fs_sel:  .res 1                         ; fs_singles: even ($00) / odd ($80)
fs_np:   .res 1                         ; pairs: the even and odd runs,
fs_pi:   .res RO                        ;  columns
fs_po:   .res RO
fs_pa:   .res RO
fs_pb:   .res RO
fs_cnt:  .res 128                       ; marks per column: plane 0..63, solid
                                        ;  64..127
fs_rnn:  .res VIEW_LINES                ; runs per line
fs_opc:  .res VIEW_LINES                ; the sweep: each line's open run's
fs_opk:  .res VIEW_LINES                ;  code (0: none) and first column
fs_c:    .res 1
fs_pn2:  .res 1                         ; the sweep: marks in the column
fs_cn:   .res 1                         ;  before / this one, cursors
fs_i:    .res 1
fs_j:    .res 1
fs_e:    .res 1                         ;  the segment's end, codes
fs_ca:   .res 1
fs_cb:   .res 1
fs_kc_ep: .res NPLANE                      ; fs_key's cache per plane: the epoch
fs_kc_p: .res NPLANE                       ;  and pair it holds, and the key
fs_kc_uc: .res NPLANE
fs_kc_du: .res NPLANE
fs_kc_vc: .res NPLANE
fs_kc_dv: .res NPLANE
fs_kc_fl: .res NPLANE
fs_kc_fb: .res NPLANE
fs_ob:   .res 2                         ; the list being built's buffer
fs_drop: .res 1                         ; marks / runs / planes / records
                                        ;  dropped (full)
fs_cap:  .res 1                         ; fs_mark: its list's size (PN / SN)
fs_lim:  .res 1                         ; the list being built's cap: page
fs_ma:   .res 1                         ; mul8lo operands
fs_mb:   .res 1

.segment "FSCODE"

; ---- fs_main: the second processor's program (H4). Entered once, by
; the host (a Tube execute); never returns. Its own IRQ takes the
; host's register 1 bytes; then frame after frame: build the list, wait
; for the last one to be out, send this one (fs_poll does, in the next
; frame's loops).
fs_main:
   SEI
   CLD
   LDX #$FF
   TXS
   LDX #0                               ; zero page (the client's leftovers:
:  STZ $00,X                            ;  py65 starts from zeros, so does
   INX                                  ;  this)
   BNE :-
   LDX #10                              ; the workspace clear: $0200-$0BFF
   LDA #2                               ;  (pages 2-7, the pool) and the
                                        ;  high BSS, fs_ivp .. $FCFF
   STA PTR+1
   STZ PTR
   LDY #0
   JSR @clr
   LDA #>fs_ivp
   STA PTR+1
   LDX #>$FD00 - >fs_ivp
   JSR @clr
   LDA #<fs_ring
   STA fs_rp
   STA fs_rw
   LDA #>fs_ring
   STA fs_rp+1
   STA fs_rw+1
   LDA #<fs_irq                         ; the host's bytes, from here on
   STA $FFFE                            ;  (the IRQ / BRK vector: no BRK here)
   LDA #>fs_irq
   STA $FFFF
   LDX #0                               ; the host is in the MOS's Tube loop
:  BIT $FEFA                            ;  (it started this with a Tube
   BVC :-                               ;  execute): an OSCLI over register 2
   LDA fs_cli,X                         ;  sends it back to its boot loader
   STA $FEFB
   INX
   CMP #13
   BNE :-
:  LDA $FEF8                            ; which sets I, the IRQ this takes its
   AND #$02                             ;  requests by: then frame -1's list
   BEQ :-                               ;  may go (the MOS loop would have
   CLI                                  ;  taken it for a character)
   LDA #<fs_listb                       ; frame -1's list: empty ($00)
   STA fs_sp
   STA fs_se
   LDA #>fs_listb
   STA fs_sp+1
   STA fs_se+1
   INC fs_se
   LDA #<fs_lista
   STA fs_ob
   LDA #>fs_lista
   STA fs_ob+1
@frame:
   JSR fs_frame
@wait:                                  ; the last list out first
   JSR fs_poll
   LDA fs_sp
   CMP fs_se
   BNE @wait
   LDA fs_sp+1
   CMP fs_se+1
   BNE @wait
   LDA fs_ob                            ; this one goes out
   STA fs_sp
   LDA fs_ob+1
   STA fs_sp+1
   LDA fs_op
   STA fs_se
   LDA fs_op+1
   STA fs_se+1
   LDA fs_ob+1                          ; and the next is built in the other
   CMP #>fs_lista
   BEQ :+
   LDA #>fs_lista
   BRA :++
:  LDA #>fs_listb
:  STA fs_ob+1
   LDA #<fs_lista
   STA fs_ob
   .assert <fs_lista = 0 .and <fs_listb = 0 .and <fs_ivp = 0, error, "buffers page-aligned"
   BRA @frame
@clr:                                   ; X pages from (PTR), (BSS: Y = 0)
   LDA #0
:  STA (PTR),Y
   INY
   BNE :-
   INC PTR+1
   DEX
   BNE :-
   RTS

fs_cli:  .byte 2, "GOIO 1903", 13        ; R2: OSCLI (2), the command, CR

; fs_irq: a request byte from the host (register 1), into the ring. An
; IRQ, not register 3's NMI: the next byte (which the host may write the
; moment this one is read) cannot land inside this one. At a page's start
; it takes the page only if the reader is in neither it nor the next:
; otherwise the byte stays in register 1 (the host waits on its room) and
; this returns with IRQs masked until fs_get frees a page.
fs_irq:
   PHA
   LDA fs_rw
   BEQ @page
@put:
   LDA $FEF9
   STA (fs_rw)
   INC fs_rw
   BNE :+
   LDA fs_rw+1
   INC A
   AND #>(RING - 1)
   ORA #>fs_ring
   STA fs_rw+1
:  PLA
   RTI
@page:
   LDA fs_rw+1
   CMP fs_rp+1
   BEQ @same
   INC A
   AND #>(RING - 1)
   ORA #>fs_ring
   CMP fs_rp+1
   BNE @put
@hold:                                  ; the stacked P's I: masked on return
   PHX
   TSX
   LDA $0103,X
   ORA #$04
   STA $0103,X
   PLX
   PLA
   RTI
@same:
   LDA fs_rp                            ; the reader at this page's start is
   BEQ @put                             ;  the ring empty; inside it, ahead
   BRA @hold

; fs_drop1: one more mark, run, plane or record dropped (saturating at 255,
; so a count is never taken for none)
fs_drop1:
   INC fs_drop
   BNE :+
   DEC fs_drop
:  RTS

; fs_get: A = the next request byte, waiting for the host (X, Y kept).
; A page read frees it for fs_irq: IRQs on (fs_irq may have held them).
fs_get:
   LDA fs_rp
   CMP fs_rw
   BNE @g
   LDA fs_rp+1
   CMP fs_rw+1
   BNE @g
   CLI                                  ; (empty: nothing can be held)
   JSR fs_poll                          ; (nothing yet: the list goes out)
   BRA fs_get
@g:
   LDA (fs_rp)
   INC fs_rp
   BNE :+
   PHA
   LDA fs_rp+1
   INC A
   AND #>(RING - 1)
   ORA #>fs_ring
   STA fs_rp+1
   PLA
   CLI
:  RTS

; fs_poll: the list going out (fs_sp .. fs_se) into register 1 while its
; FIFO has room (X, Y kept)
fs_poll:
   LDA fs_sp
   CMP fs_se
   BNE :+
   LDA fs_sp+1
   CMP fs_se+1
   BEQ @rts
:  BIT $FEF8                            ; (status bit 6: room)
   BVC @rts
   LDA (fs_sp)
   STA $FEF9
   INC fs_sp
   BNE fs_poll
   INC fs_sp+1
   BRA fs_poll
@rts:
   RTS

; ---- fs_frame: one frame's requests (fs_get) -> its list (fs_ob .. fs_op) -
fs_frame:
   LDA fs_ob
   STA fs_op
   LDA fs_ob+1
   STA fs_op+1
   CLC
   ADC #>LISTCAP
   STA fs_lim
   GETB                                 ; the view: px88, py88 (24-bit)
   STA zp_br_px
   GETB
   STA zp_br_px_h
   GETB
   STA zp_br_px_x
   GETB
   STA zp_br_py
   GETB
   STA zp_br_py_h
   GETB
   STA zp_br_py_x
   GETB                                 ; vz, ab
   STA zp_br_vz
   GETB
   STA bca_ab
   GETB                                 ; the trig: |sin|, |cos|, flags
   STA zp_br_smag
   GETB
   STA zp_br_cmag
   GETB
   LSR A
   STZ zp_br_sneg
   ROL zp_br_sneg
   LSR A
   STZ zp_br_sone
   ROL zp_br_sone
   LSR A
   STZ zp_br_cneg
   ROL zp_br_cneg
   LSR A
   STZ zp_br_cone
   ROL zp_br_cone
   JSR mf_frame                         ; the epoch and the view terms
   STZ fs_pn
   STZ fs_wn
   LDX #127                             ; no marks, no runs
:  STZ fs_cnt,X
   DEX
   BPL :-
   LDX #VIEW_LINES                      ; (136: not a BPL loop)
:  STZ fs_rnn-1,X
   STZ fs_opc-1,X
   DEX
   BNE :-
@req:
   GETB
   CMP #0                               ; (GETB's flags are the pointer's)
   BNE :+
   JMP fs_end
:  GETB                                 ; ($01) slot | solid | c1 | c2
   STA tx_slot
   GETB
   TAX
   AND #3
   STA tx_slot+1
   TXA
   AND #$04                             ; solid: zp_seg_flags V
   ASL A
   ASL A
   ASL A
   ASL A
   STA zp_seg_flags
   TXA
   AND #$08
   STA zp_seg_v1_clipped
   TXA
   AND #$10
   STA zp_seg_v2_clipped
   GETB                                 ; subsector, ch - vz, fh - vz
   STA zp_node_ch_l
   GETB
   STA zp_seg_top_dlt
   GETB
   STA zp_seg_bot_dlt
   GETB                                 ; lo, hi (mf_range makes them again)
   GETB
   LDX #0                               ; sx1 sx2 ft1 ft2 fb1 fb2 (s16); the
@ln:                                    ;  y ends take the engine's Y_BIAS
   LDY fs_lof,X                         ;  (fs_get keeps no flags)
   GETB
   STA VX1,Y
   GETB
   STA VX1+1,Y
   CPX #2
   BCC @raw
   CLC
   LDA VX1,Y
   ADC #Y_BIAS
   STA VX1,Y
   LDA VX1+1,Y
   ADC #0
   STA VX1+1,Y
@raw:
   INX
   CPX #6
   BNE @ln
   GETB                                 ; the reciprocal terms
   STA zp_seg_v1_r_m8
   GETB
   STA zp_seg_v1_r_s
   GETB
   STA zp_seg_v2_r_m8
   GETB
   STA zp_seg_v2_r_s
   LDA zp_seg_v1_clipped                ; t when exactly one end is clipped
   BEQ @c1n
   LDA zp_seg_v2_clipped
   BNE @sn
   BRA @xt
@c1n:
   LDA zp_seg_v2_clipped
   BEQ @sn
@xt:
   GETB
   STA mf_xt
   GETB
   STA mf_xt+1
@sn:
   GETB                                 ; before: the snapshot
   STA sn_n
   LDX #0
   CPX sn_n
   BEQ @pool
@snl:
   GETB
   STA sn_xs,X
   GETB
   STA sn_xe,X
   GETB
   STA sn_xlo,X
   GETB
   STA sn_den,X
   GETB
   STA sn_tl,X
   GETB
   STA sn_bl,X
   GETB
   STA sn_tr,X
   GETB
   STA sn_br,X
   GETB
   STA sn_bxlo,X
   GETB
   STA sn_bden,X
   INX
   CPX sn_n
   BNE @snl
@pool:
   GETB                                 ; after: the live spans, as the pool
   STA fs_n                             ;  (slots 1..n, linked in order)
   STZ zp_head
   LDX #0
   CPX fs_n
   BNE :+
   JMP @fill
:
   INX
   STX zp_head
@pl:
   GETB
   STA POOL_XSTART,X
   GETB
   STA POOL_XEND,X
   GETB
   STA POOL_TXLO,X
   GETB
   STA POOL_TDEN,X
   GETB
   STA POOL_TL,X
   GETB
   STA POOL_BL,X
   GETB
   STA POOL_TR,X
   GETB
   STA POOL_BR,X
   GETB
   STA POOL_BXLO,X
   GETB
   STA POOL_BDEN,X
   TXA
   INC A
   CPX fs_n
   BNE :+
   LDA #0                               ; the last: the list's end
:  STA POOL_NEXT,X
   INX
   CPX fs_n
   BCC @pl
   BNE :+
   JMP @pl
:
@fill:
   JSR mf_fill
   JMP @req

; the six line ends' offsets in the VX1 / VX2 blocks: sx1 sx2 ft1 ft2 fb1 fb2
fs_lof:
   .byte zp_seg_sx1_l - VX1, zp_seg_sx2_l - VX1, zp_seg_sy1_top_l - VX1
   .byte zp_seg_sy2_top_l - VX1, zp_seg_sy1_bot_l - VX1, zp_seg_sy2_bot_l - VX1

fs_end:
   JSR fs_wclose
   JSR fs_sweep
   JSR fs_spans
   JSR fs_fills
   LDA #0
   PUTO
   RTS

; ---- fs_planes: (pl_seg's end) the seg's floor and ceiling planes ---------
fs_planes:
   STZ fs_idf
   STZ fs_idc
   LDA pl_df                            ; a textured floor: D > 0
   BEQ @c
   LDX pl_ff
   JSR fs_pid
   STA fs_idf
@c:
   LDA pl_dc                            ; a textured ceiling: D > 0, no sky
   BEQ @r
   LDX pl_fc
   CPX #$FF
   BEQ @r
   JSR fs_pid
   STA fs_idc
@r:
   RTS

; fs_pid: A = D, X = flat -> A = $80 | the plane's entry (made if new)
fs_pid:
   STA fs_t
   STX fs_t+1
   LDY #0
@lp:
   CPY fs_pn
   BEQ @new
   LDA fs_pd,Y
   CMP fs_t
   BNE @nx
   LDA fs_pf,Y
   CMP fs_t+1
   BEQ @got
@nx:
   INY
   BRA @lp
@new:
   LDA fs_t
   STA fs_pd,Y
   LDA fs_t+1
   STA fs_pf,Y
   CPY #NPLANE - 1
   BCC :+
   DROP                          ; (full: the last entry, rewritten)
   BRA @got
:  INC fs_pn
@got:
   TYA
   ORA #$80
   RTS

; ---- fs_mark: A = the run's code ($80: the plane of kind pl_kind) for
; lines r_ys .. r_ye (unbiased) of byte column mf_x >> 2: into the
; column's plane or solid list, in y0 order
fs_mark:
   PHA
   LDA mf_x
   LSR A
   LSR A
   TAX
   LDA fs_cnt,X                         ; marks in the column: what the run
   ORA fs_cnt+64,X                      ;  is drawn over is its (fs_trim)
   BEQ :+
   LDA r_ys
   STA fs_ty0
   LDA r_ye
   STA fs_ty1
   JSR fs_trim
:  PLA
   CMP #$80
   BNE @code
   LDA pl_kind
   BEQ :+
   LDA fs_idf
   BRA @code
:  LDA fs_idc
@code:
   STA fs_c
   LDA mf_x
   LSR A
   LSR A
   TAX                                  ; k
   BIT fs_c
   BMI @pl
   ASL A                                ; solid: (k & 7) * 32 into page
   ASL A                                ;  k >> 3 of fs_ivf, its count at
   ASL A                                ;  fs_cnt + 64 + k, SN a column
   ASL A
   ASL A
   STA fs_cp
   TXA
   LSR A
   LSR A
   LSR A
   CLC
   ADC #>fs_ivf
   STA fs_cp+1
   TXA
   ORA #64
   TAX
   LDA #SN
   BRA @cap
@pl:
   ASL A                                ; plane: (k & 3) * 64 into page
   ASL A                                ;  k >> 2 of fs_ivp, PN a column
   ASL A
   ASL A
   ASL A
   ASL A
   STA fs_cp
   TXA
   LSR A
   LSR A
   CLC
   ADC #>fs_ivp
   STA fs_cp+1
   LDA #PN
@cap:
   STA fs_cap
   LDA fs_cp
   CLC
   ADC #4
   STA fs_cq
   LDA fs_cp+1
   STA fs_cq+1
   LDA fs_cnt,X
   CMP fs_cap
   BCC :+
   DROP                          ; (full: the mark is dropped)
   RTS
:  INC fs_cnt,X
   ASL A
   ASL A
   TAY                                  ; the new mark's place: past every
@ins:                                   ;  mark below it (marks never overlap)
   CPY #0
   BEQ @put
   DEY
   DEY
   DEY
   DEY
   LDA (fs_cp),Y
   CMP r_ys
   BCC @aft
   STA (fs_cq),Y                        ; it moves up one
   INY
   LDA (fs_cp),Y
   STA (fs_cq),Y
   INY
   LDA (fs_cp),Y
   STA (fs_cq),Y
   DEY
   DEY
   BRA @ins
@aft:
   INY
   INY
   INY
   INY
@put:
   LDA r_ys
   STA (fs_cp),Y
   INY
   LDA r_ye
   STA (fs_cp),Y
   INY
   LDA fs_c
   STA (fs_cp),Y
   RTS

; ---- fs_trim: lines fs_ty0 .. fs_ty1 of byte column X are drawn over (a
; wall run, or a later mark): the column's plane and solid marks lose them,
; as the Master's later write wins the cell. A mark inside them goes, one
; across an end is cut, one around them both is split in two (dropped if
; the column's list is full). Each list stays in y0 order, its marks apart.
fs_trim:
   STX fs_tk
   STZ fs_tl                            ; the plane list, then the solid
   TXA                                  ; PTR: the column's block, (k & 3) *
   ASL A                                ;  64 into page k >> 2 of its list
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA PTR
   TXA
   LSR A
   LSR A
   CLC
   ADC #>fs_ivp
   STA PTR+1
@list:
   LDA fs_tk
   ORA fs_tl
   TAX
   LDA fs_cnt,X
   BEQ @next
   STA fs_tn
   LDY #0
@m:                                     ; Y: the mark's place
   LDA (PTR),Y                          ; y0 > ty1: this and all after clear
   CMP fs_ty1
   BEQ :+
   BCS @next
:  INY
   LDA (PTR),Y                          ; y1 < ty0: clear
   DEY
   CMP fs_ty0
   BCC @keep
   LDA (PTR),Y
   CMP fs_ty0
   BCC @below
   INY                                  ; y0 >= ty0: y1 <= ty1 goes whole,
   LDA fs_ty1                           ;  else it starts past ty1
   CMP (PTR),Y
   DEY
   BCS @del
   INC A
   STA (PTR),Y
   BRA @next                            ; (those after start past it)
@below:                                 ; y0 < ty0: y1 <= ty1 is cut to end
   INY                                  ;  before ty0, else split
   LDA fs_ty1
   CMP (PTR),Y
   BCC @split
   LDA fs_ty0
   DEC A
   STA (PTR),Y
   DEY
@keep:
   INY
   INY
   INY
   INY
   DEC fs_tn
   BNE @m
@next:
   LDA fs_tl
   BNE @rts
   LDA #64
   STA fs_tl
   LDA fs_tk                            ; the solid list: (k & 7) * 32
   ASL A                                ;  into page k >> 3 of fs_ivf
   ASL A
   ASL A
   ASL A
   ASL A
   STA PTR
   LDA fs_tk
   LSR A
   LSR A
   LSR A
   CLC
   ADC #>fs_ivf
   STA PTR+1
   BRA @list
@rts:
   LDX fs_tk
   RTS
@del:                                   ; the marks after it move down one
   STY fs_ty
   LDA fs_tn
   DEC A
   ASL A
   ASL A
   CLC
   ADC fs_ty
   STA fs_te
:  CPY fs_te
   BEQ :+
   INY
   INY
   INY
   INY
   LDA (PTR),Y
   DEY
   DEY
   DEY
   DEY
   STA (PTR),Y
   INY
   BRA :-
:  LDY fs_ty
   LDA fs_tk
   ORA fs_tl
   TAX
   DEC fs_cnt,X
   DEC fs_tn
   BEQ :+
   JMP @m
:  JMP @next
@split:                                 ; Y: its y1. The part past ty1 is a
   DEY                                  ;  new mark after it (the marks after
   STY fs_ty                            ;  move up one)
   LDA fs_tk
   ORA fs_tl
   TAX
   LDA fs_tl                            ; (the list's size: PN plane, SN
   BEQ @pn                              ;  solid)
   LDA fs_cnt,X
   CMP #SN
   BRA @full
@pn:
   LDA fs_cnt,X
   CMP #PN
@full:
   BCC :+
   DROP                                 ; (full: only the part before kept)
   BRA @cut
:  INC fs_cnt,X
   ASL A                                ; the list's end: count * 4 - 1
   ASL A
   TAY
   DEY
   LDA fs_ty                            ; down to the next mark's first byte
   CLC
   ADC #4
   STA fs_te
:  CPY fs_te
   BCC :+
   LDA (PTR),Y
   INY
   INY
   INY
   INY
   STA (PTR),Y
   DEY
   DEY
   DEY
   DEY
   DEY
   CPY #$FF
   BNE :-
:  LDY fs_ty
   INY                                  ; the new mark: ty1 + 1 .. y1, code
   LDA (PTR),Y
   INY
   INY
   INY
   INY
   STA (PTR),Y
   DEY
   DEY
   DEY
   LDA (PTR),Y
   INY
   INY
   INY
   INY
   STA (PTR),Y
   DEY
   DEY
   LDA fs_ty1
   INC A
   STA (PTR),Y
@cut:
   LDY fs_ty
   INY
   LDA fs_ty0
   DEC A
   STA (PTR),Y
   JMP @next

; ---- fs_wall: (tr_screen) the wall run's WALL record ----------------------
; Byte column mf_x >> 2, lines r_ys .. r_ye (biased), texture t_tid; the
; columns tw_ll / tw_rl (tcol: SERVER), v l_v and step l_step, shared
; (tw_sh) or the right strip's own row: dh0 = its v's high byte less the
; left's, ddh = zw_ddh . zw_ddl. Consecutive byte columns of one texture
; are one group (at most 64).
fs_wall:
   LDA fs_op+1                          ; the list full: the record dropped
   CMP fs_lim
   BCC :+
   DROP
   RTS
:  LDA mf_x
   LSR A
   LSR A
   STA fs_k
   PHX                                  ; (the caller's X and Y kept)
   TAX
   LDA fs_cnt,X                         ; marks in the column: what the run
   ORA fs_cnt+64,X                      ;  is drawn over is its (fs_trim)
   BEQ @nt
   SEC
   LDA r_ys
   SBC #Y_BIAS
   STA fs_ty0
   SEC
   LDA r_ye
   SBC #Y_BIAS
   STA fs_ty1
   PHY
   JSR fs_trim
   PLY
@nt:
   PLX
   LDA fs_wn
   BEQ @open
   CMP #64
   BEQ @close
   LDA t_tid
   CMP fs_wt
   BNE @close
   LDA fs_wk
   INC A
   CMP fs_k
   BEQ @add
@close:
   JSR fs_wclose
@open:
   LDA fs_op                            ; the header ($80 | n - 1, patched
   STA fs_wh                            ;  at the close), tid, k
   LDA fs_op+1
   STA fs_wh+1
   PUTO
   LDA t_tid
   STA fs_wt
   PUTO
   LDA fs_k
   PUTO
@add:
   INC fs_wn
   LDA fs_k
   STA fs_wk
   SEC                                  ; y0, y1
   LDA r_ys
   SBC #Y_BIAS
   PUTO
   SEC
   LDA r_ye
   SBC #Y_BIAS
   PUTO
   LDA tw_ll                            ; cl (| $80: the right strip's own row)
   BIT tw_sh
   BMI :+
   ORA #$80
:  PUTO
   LDA tw_rl                            ; cr
   PUTO
   LDA l_v                              ; v0, step
   PUTO
   LDA l_v+1
   PUTO
   LDA l_step+1                         ; (the record's is the line step:
   LSR A                                ;  l_step is the pair's, twice it)
   STA fs_t+1
   LDA l_step
   ROR A
   PUTO
   LDA fs_t+1
   PUTO
   BIT tw_sh
   BMI @rts
   SEC                                  ; dh0, ddh
   LDA t_v+1
   SBC l_v+1
   PUTO
   LDA zw_ddl
   PUTO
   LDA zw_ddh
   PUTO
@rts:
   RTS

; fs_wclose: the open WALL group's header, $80 | n - 1
fs_wclose:
   LDA fs_wn
   BEQ @rts
   LDA fs_wh
   STA PTR
   LDA fs_wh+1
   STA PTR+1
   LDA fs_wn
   DEC A
   ORA #$80
   STA (PTR)
   STZ fs_wn
@rts:
   RTS

; ---- fs_sweep: the plane marks as each line's runs ---------------------
; Column k against column k - 1 (k = 0 against none, k = 64 -- none --
; against 63): a walk over both sorted lists in y, segment by segment
; where both codes hold; the lines of a segment whose code changes close
; their open run (into the line's list) and open the new one.
fs_sweep:
   STZ fs_k
   STZ fs_pn2                           ; (column -1: no marks)
@col:
   JSR fs_poll
   LDX fs_k                             ; this column's list (64: none)
   STZ fs_cn
   CPX #64
   BEQ :+
   LDA fs_cnt,X
   STA fs_cn
:  TXA
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA fs_cp
   TXA
   LSR A
   LSR A
   CLC
   ADC #>fs_ivp
   STA fs_cp+1
   LDA fs_cn                            ; the same marks as the column
   CMP fs_pn2                           ;  before (none, often): no line
   BNE @walk                            ;  changes
   ASL A
   ASL A
   TAY
@same:
   DEY
   BPL :+
   JMP @next
:  LDA (fs_cp),Y
   CMP (fs_pp),Y
   BEQ @same
@walk:
   STZ fs_y
   STZ fs_i
   STZ fs_j
@seg:
   ; the column before: its code at y and where that holds to
   LDA fs_i
   CMP fs_pn2
   BCS @pnone
   ASL A
   ASL A
   TAY
   LDA (fs_pp),Y                        ; y0
   CMP fs_y
   BEQ @pin
   BCC @pin
   DEC A                                ; before it: none, to y0 - 1
   STA fs_e
   STZ fs_ca
   BRA @cur
@pin:
   INY
   LDA (fs_pp),Y                        ; inside: its code, to y1
   STA fs_e
   INY
   LDA (fs_pp),Y
   STA fs_ca
   BRA @cur
@pnone:
   LDA #VIEW_LINES - 1
   STA fs_e
   STZ fs_ca
@cur:                                   ; this column: the same
   LDA fs_j
   CMP fs_cn
   BCS @cnone
   ASL A
   ASL A
   TAY
   LDA (fs_cp),Y
   CMP fs_y
   BEQ @cin
   BCC @cin
   DEC A
   CMP fs_e
   BCS :+
   STA fs_e
:  STZ fs_cb
   BRA @chg
@cin:
   INY
   LDA (fs_cp),Y
   CMP fs_e
   BCS :+
   STA fs_e
:  INY
   LDA (fs_cp),Y
   STA fs_cb
   BRA @chg
@cnone:
   STZ fs_cb
@chg:
   LDA fs_ca                            ; lines fs_y .. fs_e: a change?
   CMP fs_cb
   BEQ @adv
   LDX fs_y
@ln:
   JSR fs_change
   CPX fs_e
   INX
   BCC @ln
@adv:
   LDA fs_i                             ; past a mark's end: the next
   CMP fs_pn2
   BCS :+
   ASL A
   ASL A
   INC A
   TAY
   LDA (fs_pp),Y
   CMP fs_e
   BNE :+
   INC fs_i
:  LDA fs_j
   CMP fs_cn
   BCS :+
   ASL A
   ASL A
   INC A
   TAY
   LDA (fs_cp),Y
   CMP fs_e
   BNE :+
   INC fs_j
:  LDA fs_e
   INC A
   STA fs_y
   CMP #VIEW_LINES
   BCS :+
   JMP @seg
:
@next:
   LDA fs_cp                            ; this column is the next's before
   STA fs_pp
   LDA fs_cp+1
   STA fs_pp+1
   LDA fs_cn
   STA fs_pn2
   INC fs_k
   LDA fs_k
   CMP #65
   BCS :+
   JMP @col
:  RTS

; fs_change: line X's code becomes fs_cb at column fs_k: its open run
; closes at fs_k - 1 (into its list), the new one opens (X kept)
fs_change:
   LDA fs_opc,X
   BEQ @open
   STA fs_c
   TXA                                  ; the line's runs: + y * 64
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA fs_lp
   TXA
   LSR A
   LSR A
   CLC
   ADC #>fs_rn
   STA fs_lp+1
   LDA fs_rnn,X
   CMP #RN
   BCC :+
   DROP                          ; (full: the run is dropped)
   BRA @open
:  INC fs_rnn,X
   ASL A
   ASL A
   TAY
   LDA fs_opk,X
   STA (fs_lp),Y
   INY
   LDA fs_k
   DEC A
   STA (fs_lp),Y
   INY
   LDA fs_c
   STA (fs_lp),Y
@open:
   LDA fs_cb
   STA fs_opc,X
   LDA fs_k
   STA fs_opk,X
   RTS

; ---- fs_spans: the lines' runs as SPANs, a line pair at a time -----------
; Each line's runs join where neighbours have the same row maths, flat
; and far tone (tube_dl._display_list); where the pair's two lines' runs
; of one plane (the same flat or far tone, steps and U, V) overlap, the
; overlap is a PAIR span and the rest of each line its own. Out: line
; 2p's own spans, the pair's, line 2p + 1's own (tube_dl.encode's order).
fs_spans:
   STZ pl_p
@pair:
   JSR fs_poll
   LDA pl_p
   ASL A
   STA fs_y
   LDX #0                               ; the even line's runs
   JSR fs_runs
   STX fs_ne
   INC fs_y
   LDX #RO                              ; the odd line's
   JSR fs_runs
   TXA
   SEC
   SBC #RO
   STA fs_no
   ORA fs_ne
   BEQ @next                            ; (no planes on the pair)
   JSR fs_pairs
   DEC fs_y                             ; the even line's own spans
   LDA fs_y
   JSR fs_ghead
   LDX #0
   LDY fs_ne
   STZ fs_sel                           ; (even runs: fs_pi)
   JSR fs_singles
   JSR fs_gclose
   LDA pl_p                             ; the pairs
   CLC
   ADC #PAIR_Y
   JSR fs_ghead
   LDY #0
@pp:
   CPY fs_np
   BEQ @ppd
   PHY
   LDX fs_pi,Y
   LDA fs_pb,Y
   STA fs_t+1
   LDA fs_pa,Y
   JSR fs_span
   PLY
   INY
   BRA @pp
@ppd:
   JSR fs_gclose
   INC fs_y                             ; the odd line's own
   LDA fs_y
   JSR fs_ghead
   LDX #RO
   LDA fs_no
   CLC
   ADC #RO
   TAY
   LDA #$80                             ; (odd runs: fs_po)
   STA fs_sel
   JSR fs_singles
   JSR fs_gclose
@next:
   INC pl_p
   LDA pl_p
   CMP #VIEW_PAIRS
   BEQ :+
   JMP @pair
:  RTS

; fs_runs: line fs_y's runs (pair pl_p) -> its joined runs at re_* + X;
; X = the end
fs_runs:
   LDY fs_y
   LDA fs_rnn,Y
   BNE :+
   RTS
:  STA fs_n
   TYA                                  ; its list
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA fs_lp
   TYA
   LSR A
   LSR A
   CLC
   ADC #>fs_rn
   STA fs_lp+1
   LDY #0
@r:
   PHY
   INY
   INY
   LDA (fs_lp),Y                        ; its code: the key
   JSR fs_key
   PLY
   LDA (fs_lp),Y
   STA fs_k                             ; k0
   CPX #0                               ; (no run yet on this line?)
   BEQ @new
   CPX #RO
   BEQ @new
   LDA re_k1-1,X                        ; the last run ends at k0 - 1 with
   INC A                                ;  the same key: one run
   CMP fs_k
   BNE @new
   LDA fs_kuc
   CMP re_uc-1,X
   BNE @new
   LDA fs_kdu
   CMP re_du-1,X
   BNE @new
   LDA fs_kvc
   CMP re_vc-1,X
   BNE @new
   LDA fs_kdv
   CMP re_dv-1,X
   BNE @new
   LDA fs_kfl
   CMP re_fl-1,X
   BNE @new
   LDA fs_kfb
   CMP re_fb-1,X
   BNE @new
   INY                                  ; extend it to k1
   LDA (fs_lp),Y
   STA re_k1-1,X
   DEY
   BRA @nx
@new:
   LDA fs_k
   STA re_k0,X
   INY
   LDA (fs_lp),Y
   STA re_k1,X
   DEY
   LDA fs_kuc
   STA re_uc,X
   LDA fs_kdu
   STA re_du,X
   LDA fs_kvc
   STA re_vc,X
   LDA fs_kdv
   STA re_dv,X
   LDA fs_kfl
   STA re_fl,X
   LDA fs_kfb
   STA re_fb,X
   INX
@nx:
   INY
   INY
   INY
   INY
   DEC fs_n
   BEQ @rts
   JMP @r
@rts:
   RTS

; fs_key: A = a plane code -> fs_kuc .. fs_kfb, its key on pair pl_p: the
; row maths, flat (| $80: far) and far tone (X, Y kept). Cached per plane
; for the pair.
fs_key:
   PHX
   PHY
   AND #$7F
   TAY
   LDA fs_kc_ep,Y
   CMP mf_ep
   BNE @make
   LDA fs_kc_p,Y
   CMP pl_p
   BNE @make
   LDA fs_kc_uc,Y
   STA fs_kuc
   LDA fs_kc_du,Y
   STA fs_kdu
   LDA fs_kc_vc,Y
   STA fs_kvc
   LDA fs_kc_dv,Y
   STA fs_kdv
   LDA fs_kc_fl,Y
   STA fs_kfl
   LDA fs_kc_fb,Y
   STA fs_kfb
   PLY
   PLX
   RTS
@make:
   PHY
   LDA fs_pd,Y
   STA pl_d
   LDA fs_pf,Y
   STA fs_kfl
   JSR pl_rowc                          ; pc_*[pl_p] for D pl_d
   LDX pl_p
   LDA pc_uc,X
   STA fs_kuc
   LDA pc_du,X
   STA fs_kdu
   LDA pc_vc,X
   STA fs_kvc
   LDA pc_dv,X
   STA fs_kdv
   STZ fs_kfb
   LDA pc_lv,X
   BEQ :+
   LDX fs_kfl                           ; far: the flat's one byte, and the
   LDA far_tone,X                       ;  key that tone alone (H4h: no row
   STA fs_kfb                           ;  maths, no flat; bit 7 of the flat
   LDA #$80                             ;  says far, a tone may be $00), so
   STA fs_kfl                           ;  far runs of one tone join
   STZ fs_kuc
   STZ fs_kdu
   STZ fs_kvc
   STZ fs_kdv
:  PLY
   LDA mf_ep
   STA fs_kc_ep,Y
   LDA pl_p
   STA fs_kc_p,Y
   LDA fs_kuc
   STA fs_kc_uc,Y
   LDA fs_kdu
   STA fs_kc_du,Y
   LDA fs_kvc
   STA fs_kc_vc,Y
   LDA fs_kdv
   STA fs_kc_dv,Y
   LDA fs_kfl
   STA fs_kc_fl,Y
   LDA fs_kfb
   STA fs_kc_fb,Y
   PLY
   PLX
   RTS

; fs_pairs: the overlaps of the even runs (0 .. fs_ne) and the odd ones
; (RO ..) whose planes match -> fs_pi / fs_po (the two runs) and fs_pa /
; fs_pb (the columns), in even-run then odd-run order
fs_pairs:
   STZ fs_np
   LDA fs_ne
   BEQ @none
   LDA fs_no
   BNE :+
@none:
   RTS
:  LDX #0
@e:
   LDY #RO
@o:
   LDA re_k0,X                          ; a = max(k0), b = min(k1)
   CMP re_k0,Y
   BCS :+
   LDA re_k0,Y
:  STA fs_t
   LDA re_k1,X
   CMP re_k1,Y
   BCC :+
   LDA re_k1,Y
:  STA fs_t+1
   CMP fs_t
   BCC @on                              ; b < a: no overlap
   LDA re_du,X                          ; the same steps
   CMP re_du,Y
   BNE @on
   LDA re_dv,X
   CMP re_dv,Y
   BNE @on
   LDA re_fl,X                          ; far (bit 7) or not alike
   EOR re_fl,Y
   BMI @on
   LDA re_fl,X
   BPL @tx
   LDA re_fb,X                          ; far: the same tone (U, V unread)
   CMP re_fb,Y
   BNE @on
   BRA @match
@tx:
   LDA re_fl,X                          ; else the flat and U, V
   CMP re_fl,Y
   BNE @on
   LDA re_uc,X
   CMP re_uc,Y
   BNE @on
   LDA re_vc,X
   CMP re_vc,Y
   BNE @on
@match:
   PHY
   TYA
   LDY fs_np
   STA fs_po,Y
   TXA
   STA fs_pi,Y
   LDA fs_t
   STA fs_pa,Y
   LDA fs_t+1
   STA fs_pb,Y
   INC fs_np
   PLY
@on:
   INY
   TYA
   SEC
   SBC #RO
   CMP fs_no
   BCS :+
   JMP @o
:  INX
   CPX fs_ne
   BCS :+
   JMP @e
:  RTS

; fs_singles: runs X .. Y-1 (re_* + X; fs_pi or fs_po names them in the
; pairs, by fs_sel): each run's columns outside its pair spans, as spans
fs_singles:
   STY fs_n
@r:
   CPX fs_n
   BEQ @rts
   LDA re_k0,X
   STA fs_k                             ; the next column not yet out
   LDY #0
@p:
   CPY fs_np                            ; its pairs, in column order
   BEQ @tail
   TXA
   BIT fs_sel                           ; (before the compare: BIT sets Z)
   BMI :+
   CMP fs_pi,Y
   BRA :++
:  CMP fs_po,Y
:  BNE @pn
   LDA fs_pa,Y                          ; a piece before the pair?
   CMP fs_k
   BEQ :+
   DEC A
   STA fs_t+1
   JSR @out
:  LDA fs_pb,Y
   INC A
   STA fs_k
@pn:
   INY
   BRA @p
@tail:
   LDA re_k1,X                          ; the piece after the last
   CMP fs_k
   BCC @nr
   STA fs_t+1
   JSR @out
@nr:
   INX
   BRA @r
@rts:
   RTS
@out:                                   ; columns fs_k .. fs_t+1 of run X
   PHX
   PHY
   LDA fs_k
   JSR fs_span
   PLY
   PLX
   RTS

; fs_span: run X (re_* + X) cut to columns A .. fs_t+1: the span's bytes
; (k0 k1 flat u0 v0 du dv, or k0 k1 $FF tone)
fs_span:
   STA fs_k
   LDA fs_op+1                          ; the list full: the span dropped
   CMP fs_lim
   BCC :+
   DROP
   RTS
:  LDA fs_k
   INC fs_gn
   PUTO
   LDA fs_t+1
   PUTO
   LDA re_fl,X
   BPL @tex
   LDA #$FF
   PUTO
   LDA re_fb,X
   PUTO
   RTS
@tex:
   LDA re_fl,X
   PUTO
   SEC                                  ; u0 = Uc + (k0 - 32) * dU (mod 2^8)
   LDA fs_k
   SBC #32
   STA fs_ma
   LDA re_du,X
   STA fs_mb
   JSR mul8lo
   CLC
   ADC re_uc,X
   PUTO
   LDA re_dv,X                          ; v0 = Vc + (k0 - 32) * dV
   STA fs_mb
   JSR mul8lo
   CLC
   ADC re_vc,X
   PUTO
   LDA re_du,X
   PUTO
   LDA re_dv,X
   PUTO
   RTS

; mul8lo: A = (fs_ma * fs_mb) & $FF (quarter squares: f(a + b) - f(|a - b|))
mul8lo:
   PHX
   PHY
   LDA fs_ma
   SEC
   SBC fs_mb
   BCS :+
   EOR #$FF
   ADC #1
:  TAY                                  ; |a - b|
   CLC
   LDA fs_ma
   ADC fs_mb
   TAX                                  ; a + b (mod 256)
   BCS @big
   SEC
   LDA SQR_LO,X
   SBC SQR_LO,Y
   BRA @out
@big:
   SEC
   LDA SQR2_LO,X
   SBC SQR_LO,Y
@out:
   PLY
   PLX
   RTS

; fs_ghead: A = a SPAN group's y byte: its header placeholder
fs_ghead:
   PHA
   LDA fs_op
   STA fs_gh
   LDA fs_op+1
   STA fs_gh+1
   LDA #0
   PUTO
   PLA
   PUTO
   STZ fs_gn
   RTS

; fs_gclose: the group's header ($40 | n - 1), or no group if n = 0
fs_gclose:
   LDA fs_gn
   BNE :+
   LDA fs_gh                            ; empty: take the header back
   STA fs_op
   LDA fs_gh+1
   STA fs_op+1
   RTS
:  DEC A
   ORA #$40
   LDX fs_gh
   STX PTR
   LDX fs_gh+1
   STX PTR+1
   STA (PTR)
   RTS

; ---- fs_fills: the solid marks as FILLs, a byte column at a time (a
; column's touching marks of one shade joined)
fs_fills:
   STZ fs_k
@col:
   JSR fs_poll
   LDX fs_k
   LDA fs_cnt+64,X
   BNE :+
   JMP @cn
:  STA fs_n
   TXA
   ASL A
   ASL A
   ASL A
   ASL A
   ASL A
   STA fs_cp                            ; (k & 7) * 32 into page k >> 3
   TXA
   LSR A
   LSR A
   LSR A
   CLC
   ADC #>fs_ivf
   STA fs_cp+1
   LDY #0
@m:
   LDA (fs_cp),Y                        ; a FILL from this mark's y0
   STA fs_y
   INY
   INY
   LDA (fs_cp),Y
   STA fs_t                             ; its shade
@join:
   DEY                                  ; Y: its y1
   DEC fs_n
   BEQ @emit
   LDA (fs_cp),Y                        ; the next mark: y0 = y1 + 1 and
   INC A                                ;  the same shade joins
   INY
   INY
   INY
   CMP (fs_cp),Y
   BNE @emitn
   INY
   INY
   LDA (fs_cp),Y
   CMP fs_t
   BEQ @join
   DEY
   DEY
@emitn:                                 ; (Y: the next mark's y0; it is not
   DEY                                  ;  taken yet: fs_n counts it)
   DEY
   DEY
@emit:                                  ; Y: the FILL's y1
   LDA fs_op+1                          ; the list full: the FILL dropped
   CMP fs_lim
   BCC :+
   DROP
   BRA @emitd
:  LDA #$01
   PUTO
   LDA fs_k
   PUTO
   LDA fs_y
   PUTO
   LDA (fs_cp),Y
   PUTO
   LDA fs_t
   CMP #WB_SKY
   BNE :+
   LDA #SKY_EV                          ; the sky: its cross-hatch byte
   BRA :++
:  LSR A                                ; a shade: both pixels
   ORA fs_t
:  PUTO
@emitd:
   INY
   INY
   INY
   LDA fs_n
   BNE @m
@cn:
   INC fs_k
   LDA fs_k
   CMP #64
   BEQ :+
   JMP @col
:  RTS

.segment "FSBSS"
fs_kuc:  .res 1                         ; fs_key's result
fs_kdu:  .res 1
fs_kvc:  .res 1
fs_kdv:  .res 1
fs_kfl:  .res 1
fs_kfb:  .res 1

.segment "FSEND"                        ; (fserve.cfg: the code area's last)
fs_imgend:                              ; the file's end (tube_server.image)
