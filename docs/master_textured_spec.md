# Textured DOOM on the BBC Master — spec and plan

A textured E1M1 for the **BBC Master 128 only**, built as a delta from the
Model B wireframe engine. The BSP walk, angle-space culling, vertex pipeline,
caches, movers, trapezoid clip spans and the bit-exact Python reference are
kept. The line clipper/rasteriser (DCL + NJ linedraw), Mode 4 display and the
Model B memory map are replaced. Since step 7l (branch `master-only`) the
Model B build, its wireframe renderer and its gates are gone from the tree.

## 1. Display

- **Since step 6c** the control panel (lines 136–159) is **Mode 1**: a
  timer interrupt switches the video ULA and palette at its first line and
  back at vsync (see 6c).
- **Since step 6a: Mode 2** (shadow MODE 130), 128×160, the **8 solid
  colours** (black, red, green, yellow, blue, magenta, cyan, white;
  logical = physical, the default palette). A byte is 2 pixels: the left
  pixel's colour bits in bits 7, 5, 3, 1, the right's in 6, 4, 2, 0. A
  pixel is exactly one wall strip. (Steps 1–5: 256×160 Mode 1, black / red /
  cyan / white with a cross-hatch; see below for what that was.)
- 64 bytes per line × 160 lines = 10K per buffer. **Both buffers live in the
  20K shadow RAM**, at &3000 and &5800. The display always shows shadow; a
  flip rewrites the CRTC start address (R12/R13).
- Effective resolution: every texel row is 2 screen lines (see 3), so the view
  is **80 texel rows** high; walls are 128 columns wide, floors/ceilings 64.
- Since step 5o the 3D view is the top **136 lines** (68 texel rows, horizon
  at line 68); the bottom 24 lines of both buffers are a static control
  panel (DOOM's status bar, `master_panel.py`).

## 2. Colours

**Since step 6a** (Mode 2): wall texels are solid colours; no sector
light (the demo is for texturing, not atmospherics). **Since step 6d**
floors, ceilings and the sky cross-hatch again: a flat texel is one of
20 *tones*, the 8 solid colours or one of 12 pairs (`master_assets.PAIRS`),
black + a colour or white + a colour (a colour darkened or paled); the
sky is cyan + white. Walls and flats are hand-drawn pixel art (step 6b,
`art/walls`, `art/flats`), a colour family per material.

Steps 1–5 (Mode 1):

- **10 shades**: the 4 solid colours plus the 6 two-pixel mixes. A shade is a
  pixel pair (a, b).
- **Cross-hatch**: textures store only the top row of the pattern (a, b). The
  second screen line is produced through a **256-byte flip table** that swaps
  the two pixels of every pair (a, b → b, a). One table serves walls and floors.
- **Conversion**: match mainly on brightness. Source RGB × 2.0 gain, then
  nearest shade by luminance (Rec. 601 weights), chroma weight 0 by default
  (kept as a tunable). Red + cyan averages to exactly mid-grey, the same
  colour as black + white, so brightness matching only ever picks one of
  the two: textures use **9 distinct shades**. On E1M1 solid red is the
  most used shade (24%): Doom's dark greys and browns land there.

## 3. Drawing

All writes are whole bytes; each wall texel row writes byte `B` to lines
2r and 2r+1 (step 6a; in Mode 1 the second line was `FLIP[B]`); floors
and ceilings write `FLIP[B]` on the odd line (step 6d).

- **Walls**: two independent strips per byte, one Mode 2 pixel each:
  `byte = TEX1 OR (TEX2 >> 1)`, where each texel byte carries its colour in
  the left pixel's bits (`$AA`): one shift (Mode 1 was `(TEX1 << 2) OR
  TEX2`, two).
- **Floors and ceilings**: one texel per byte, a full byte (a tone: left
  pixel a, right b) on a pair's even line and `FLIP[B]` (b, a) on its odd
  line, a checkerboard (step 6d). A solid tone is its own FLIP.
- **Sky** (F_SKY1): no texture; cyan + white cross-hatched (step 6d).
- Drawing code runs from **HAZEL** and reaches the shadow buffers by setting
  **ACCCON X** for the duration of a draw. The E bit cannot be used: Master
  "VDU driver" shadow access only works while HAZEL is paged out (Y = 0);
  with HAZEL in, E has no effect (measured on jsbeeb's Master 128, step 1).
  Consequence: **while drawing, nothing may be read from main RAM at
  &3000–&7FFF.** The drawers read textures from sideways RAM, tables from
  HAZEL, and their parameters from zero page / low RAM (below &3000).
  Engine code in main RAM is unaffected because it never runs mid-draw.

## 4. Wall texture format

- Every texture is scaled to **32 texels high**, keeping its proportions
  (width = W × 32 / H, nearest-neighbour when enlarging, box filter when
  shrinking).
- **Exception — the four short textures** STEP1 (32×8), STEP6 (32×16),
  EXITSIGN (64×16) and NUKE24 (64×24) are scaled to **16 high** and **stacked
  in pairs** inside 32-high columns: one texture in rows 0–15, the other in
  rows 16–31 (start row offset Y + 128). Pairing: NUKE24 + STEP6,
  EXITSIGN + STEP1. Valid because nothing in E1M1 shows more than one height
  of these textures; see open items for movers.
- **Demo clip — COMPUTE2** (256×56): only its first 64-unit panel module
  is stored and tiled (`master_assets.CLIP`), 37 columns instead of 139:
  5 pages instead of 18.
- **Columns**: 32 bytes, one texel per byte. A 256-byte page holds 8 columns,
  **interleaved every 8th byte**: byte = row × 8 + slot. A column's base is
  page + slot; stepping down adds 8 to Y (last row Y = 248), so `LDA (p),Y`
  never crosses a page.
- **Deduplication within each texture only**: identical columns are stored
  once. Each texture has a header: 16-bit pointer (page-aligned base), bank,
  width, row offset (0, or 128 for the bottom half of a stack), followed by
  one **index byte per column**. Column c lives at
  `ptr + (idx >> 3) * 256 + (idx & 7)`; rows step +8 from Y = row offset.
  Index values carry the starting slot, so textures may start mid-page and
  small textures fill gaps.
- A texture directory (`tex_dir`, one word per texture) points at the
  headers. Every stored texture is exactly 32 (or 16) rows covering its whole
  source height, so the vertical wrap is always `v AND 31`; horizontal wrap
  is modulo the texture's width (not a power of two in general).
- **Each texture's column data lies within a single sideways RAM bank.**
- **Pointer, bank and index tables live in HAZEL**, readable whichever bank is
  paged in.

## 5. Floor and ceiling format

- 16×16, one byte per texel, 256 bytes (one page) per flat:
  byte = row × 16 + column. Two HAZEL byte tables give each flat's bank and
  page (`flat_bank`, `flat_page`).
- E1M1 needs **21 flats**: 11 floors + 13 ceilings, minus FLAT20 and FLAT5_5
  (shared) and F_SKY1 (cyan). NUKAGE1 is static: its animation frames
  NUKAGE2 and NUKAGE3 were dropped for memory (step 5f).

## 6. Memory map

| Area | Contents |
|---|---|
| Main RAM | All engine code; per-frame caches and workspaces moved out of the banks as needed |
| Shadow RAM (20K) | Step 7w: `gun_b0` (the buffer-0 gun overlay, 1,195 B since 7ab, run with ACCCON X set) &3000–&34AA, free to &35FF (341 B); buffer 0's view &3600–&57FF; the one control panel &5800–&5DFF (character rows 17–19 of both buffers: buffer 0 runs into it, buffer 1 wraps onto it at the 10K screen size); buffer 1's view &5E00–&7FFF |
| Main $0200–$07FF | Model B: the quarter-square quad. Master (step 6c): page 2 keeps the MOS IRQ1V ($0204), which points at the raster-split handler; step 7ac: the music player (`MUS_ORG`, 1,201 B) $0300–$07B0, copied from bank 5 by the boot stub |
| HAZEL (8K) | Boot pattern + HUD at $C000–$C27F; span snapshot, plane spans + row cache $C280–$C69F (with `pc_lv`, step 7n; free to $C7FF); the fill's hot code and tables (x16 tables `hi16` / `lo16` and the floor cross-hatch `flip`, page-aligned at $C800, $C900, $CA00; texel and span loops, `mf_frame`, sky map, the raster-split handler) $C800–$D685 (with `cyc_tab`, step 6e; 7c, 7d; `HZM` ends at $D6FF since 7ac), **free $D686–$D6FF (122 B)**; the music ring (`MUS_RING`, step 7ac) $D700–$D7FF; the quarter-square quad + mirrors (`sqr_quad_m`, MSQR, step 6c) $D800–$DDFF; BSS $DE00–$DFFF |
| Sideways RAM banks 4–7 (64K) | Level data and tables (~24K), wall column data (8.75K, step 7m), flats (5.25K), all in bank 5 to $B7FF; bank 6 $8000–$8F3F: the wall step's reciprocal tables and per-part m / table page (step 7v; free since 7m, textures and flats to $82FF before); bank 6 $9000–$B8D9 (step 7i: was from $9500): the fill's cold set-up code (steps 5f–5h; `tx_seg` since 7i), the gun overlay (`gun_draw` + the compiled `gun_b1`, steps 6f, 7b; `gun_b0` in shadow since 7w), `rm_patch` (7c), `mul16` (7k) and the unrolled divides `dq_core` (7f) and `dv8f` / `d8_fast` (7h), the far-tone span loops and `far_dm` / `far_tone` (7n), free $B360–$B8FF (1,440 B, step 7w: `gun_b0` moved to shadow); bank 6 tail $B900–$BE23: wall part records + texture constants; step 7ac: bank 5 $B800–$BCB0 the music player's boot image, bank 7 $A600–$AF20 the tune stream |
| ANDY (4K) | Per-seg wall tables (slot planes, dressings, merged-seg pieces) + per-subsector flats, 3.9K |
| Main $7A00–$7E1F | Texture column index bytes (996 B) |
| Main $7E20–$7F57 | The fill's multiply and divide routines (step 5c; `pl_hq` / `pl_dh` / `pl_dhn`, step 7o; `mf_mul8` removed, 7p) |
| Main $6D38–$6FFF | Free (step 7i: the cold per-seg set-up `tx_seg` moved to bank 6) |
| Main $4FB8–$55B5 | Plane row maths, pending-plane logic and their tables (step 5e; `MFILLC`, `MFMAIN` — `rw`, not `bss`, so `SHTAB` keeps $5600) |

**Budget (E1M1, measured by `master_assets.py`):**

- Wall column data: 280 stored columns × 32 B = 8,960 B (step 7m; 367
  columns, 11,744 B, before; 634 columns, 19.8K, at step 4, with
  COMPUTE2 clipped and the BRNBIG masked middles dropped).
- Flats: 21 × 256 B = 5,376 B (5.25K).
- Textures and flats together: bank 5 $8000–$B7FF (14,336 B, step 7m);
  bank 6 $8000–$8F3F holds the wall step tables (step 7v) and $9000–$B35F the fill's cold code.
- Level data and tables: ~24K (banks A and B of the current build: 10.3K +
  13.8K, part of which is cache workspace).
- **Total in the banks: ~54K of 64K.** Since step 4 ANDY holds the per-seg
  wall tables (3.6K of 4K).

**Boot order**: the filing system uses HAZEL for workspace, so the disc loads
everything first, then copies the drawing code and tables into HAZEL. No disc
access after that.

## 7. Plan

Each step ends in something bootable or comparable, and is checked against the
Python reference byte for byte, as the existing engine is.

**0. Asset converter (Python). — DONE:** `master_assets.py`, gated by
`test_master_assets.py` (in `run_regression.py`). WAD → scaled, quantised walls (32 high,
stacked 16-high pairs), per-texture column dedup, interleaved pages, bank
packing with no texture crossing a bank, HAZEL tables; flats incl. NUKAGE1–3;
PNG previews and a memory report. *Done when*: deterministic output, budget
fits, previews approved.

**1. Master bring-up (no textures). — DONE.**

*Display*: `tools/build_master_display.py` + `src/master/mdisplay.s` boot a
disc that sets the 256×160 four-colour window (CRTC R1 64, R2 90, R6 20,
R7 28, R8 0), the palette, both shadow buffers drawn from HAZEL with X set,
and a vsync flip; `tools/master_rig.mjs display` checks shadow RAM byte for
byte, the flip, the window size and the colours.

*Engine*: the MASTER build (asmbuild variant 2, `src/engine_master.cfg`,
`-D BANKED=1 -D MASTER=1`, 65C02) is the Model B engine with bank C's
content laid linear in main RAM at `CBITS_M` = &5800 (clipper code
&5800–&6FFF, its data &7000–&78FF, object dispatch tables &7900). Banks A
(4) and B (7) and MAIN (&0F00–&57FF) are exactly the Model B images; bank
6 is free for textures. The bank-C data homes are `CBANK_ORG` + offset in
both builds. The rasteriser, plotters, clears and bank-C HUD compile out
(`RASTERHW` = BANKED and not MASTER): `plot_h`, `plot_v` and
`RASTER_ENTRY` are RTS emit stubs that step 3 replaces. The flat and Model
B builds are byte-identical to before.

*Driver and disc*: `walk_drv.s` has MASTER arms for the CRTC widths,
ACCCON D, the &3000/&5800 buffers, no plot queue, and a HUD drawn by
HAZEL code (`src/master/mhazel.s`: test pattern + frame time in 1MHz
ticks and fields, args in `MHZ_ARGS`). `tools/build_master_ssd.py` writes
`build/master/doom_master.ssd`; its loader (`src/master/mboot.s`) loads
banks 4 and 7, MAIN, MCBITS and the HAZEL block, sets MODE 129 and the
palette, and copies the HAZEL block up after the last disc access.

*Gates*: `test_master_engine.py` (in `run_regression.py`) required the
Master engine's emitted line list to equal the Model B C02 engine's at 29
poses; since step 7l it compares against the lists recorded from the
Master link (`golden/master_engine_lines.json.gz`, 31 poses); `test_master_disc.py` boots the disc
on jsbeeb's Master 128 and checks frames flip, LEFT turns, UP walks and only
palette colours appear (needs the jsbeeb clone; prints SKIP without it).
Measured: about 13 frames per second walking the BSP with no drawing (the
HUD reads &ED4F µs = 3 fields at the spawn view).

**2. Python textured reference. — DONE:** `textured_ref.py` (`TexturedRef`),
gated by `test_textured_ref.py` (in `run_regression.py`); `play.py` has a
textured mode (key T). A classic DOOM column renderer at the target
resolution, written for clarity: 128 wall strips × 80 texel rows,
floors/ceilings per byte column (4×2), the engine's camera (focal 128 /
153.6 at 256×160, eye at floor + 41) and the engine's own map tables
(alternate BSP, `seg_sectors`). Upper/middle/lower walls with sidedef
offsets and DOOM pegging; two-sided middle textures (the 7 BRNBIG/BROWNGRN
panels) drawn once, back to front; flats tile every 64 units; sky is solid
cyan and sky-to-sky uppers are not drawn. Texels are read from the packed
bank bytes and assembled HAZEL tables; output is the exact 10K buffer image.
Reference images for 22 poses land in `build/master/ref/`. The gate checks
the FLIP rule on every line pair, that every byte is two valid shades, and
that on-map poses leave nothing undrawn (four poses look off the map edge
and are listed). About 0.1 s per frame in Python.
*Decision*: the engine's one-way courtyard windows (`_ONEWAY_WALLED_SIDE`:
the ledge back wall is solid seen from the room) have no texture on that
side; the reference shows **sky** there. The 6502 port adds its own
bit-exact mirror in steps 3–5; this renderer is what those are judged
against.

**3. Filled walls, floors and ceilings on the 6502. — DONE (solid shades).**
The rule (`fill_ref.py`, the executable spec): the clip spans are the
unfilled screen, so whatever a seg's span updates remove (`span_mark_solid`
for a solid wall; the fused top/bottom walk for a portal) is what that seg
fills. Diff the pool before and after the seg. Per BYTE COLUMN (owned by
the seg whose range holds its first pixel 4k, sampled there), fill the
removed bands, split at the seg's own front ceiling and floor lines:
ceiling above, floor below, wall between. Every byte column is filled
once, front to back, with no gaps and no overdraw, so no screen clear is
needed. **Every write is a whole byte, a 4×2 fat pixel; nothing is read
back.** Fills are per screen line (even lines the byte, odd lines its
FLIP), so band edges may fall on any line. (Until the step-4b revision the
unit was the 2-pixel strip, written with read-modify-write.)
`src/master/mfill.s` (HAZEL, $C800) implements it: `mf_snap` at the
cascade head, `mf_fill` after the seg's updates, exact steppers for the six
interpolated edges, `hz_run` writing whole-byte runs with ACCCON X set
only for the run. Sky ceilings come from a per-subsector bitmap. The driver holds
ACCCON Y on for the whole render and turns billboard objects **off** (they
apply span lines outside any seg's fill window; sprites are not in the
spec yet). Gates: `test_fill_ref.py` (full coverage; 96.1% surface
agreement with the float `textured_ref` at byte columns), and the 6502
back buffer byte for byte against the model (since step 4,
`test_master_tex.py`), both in `run_regression.py`;
`test_master_disc.py` boots the disc on jsbeeb.
*Not yet fast*: at step 3, 1.0–2.7M cycles a frame in py65 (about 1.4 s a
frame on the emulated Master); stepper set-up uses generic 32-bit maths.
Step 7 territory.

**4. Textured walls. — DONE.** *4a, the bit-exact model*: `tex_ref.py`
(`TexRef`, on top of `fill_ref`) textures the wall runs of step 3's fill
in integer arithmetic the 6502 can reproduce; gated by `test_tex_ref.py`
(in `run_regression.py`). The rules:

- *v*: 5.11 fixed point, as asked: the 5 integer bits are the texel row
  (wrap at 32 for free), stepped once per line pair (a texel is the byte
  line and its FLIP line):
  - `step = K // (B−T)` per screen line, with
    `K = 2048·th·(fc−fh) // src_h` precomputed per wall part;
  - `v = Vtop + (y_even − T)·step`, so a pair moves 2·step;
  - `Vtop = floor(2048·th·(ztop − fc + yoff) / src_h)`;
  - T and B are the filler's own floored line ends; ztop follows DOOM's
    pegging rules.
- *u*: perspective-correct at each strip's centre. d is the distance
  along the seg, in 1/16 world units.
  - Weights: the engine's own endpoint reciprocals, (256+M8)/2^S. The
    nearer end is shifted left by the S difference, and both are kept to
    16 bits.
  - Per seg: d is exact at the first and last visible strip centres:
    `(d1·a + d2·b) / (a + b)`, with `a = wa·(sx2−xc)` and
    `b = wb·(xc−sx1)` shifted together below $8000. A near-clipped end
    takes d1 or d2 from the engine's own crossing fraction
    (`fp_cross_t16`).
  - Per strip: a projective map between those two ends, with their raw
    weights normalised to 8 bits (A, B):
    `d = (dL·A·dj + dH·B·dk) / (A·dj + B·dk)`. Numerator and denominator
    step by constants. Since step 5d the right strip's d extrapolates from
    the previous byte's, `dr = d + ((d − d_prev) >> 1)`, when that byte
    computed one, and is exact otherwise. Since step 5g the left strip's d
    is exact (one 32/16 division) on the seg's even bytes only; on its odd
    bytes it is the midpoint of the exact d either side,
    `(d(x − 3) + d(x + 5)) >> 1` (the look-ahead is the next byte's d).
  - Column: index = `(u & (16·src_w − 1)) >> shift` into a power-of-two
    column index of n entries (n = the next power of two ≥ tw, shift =
    log2(16·src_w / n)), logical column = index·tw // n. Every E1M1 source
    width is a power of two, so the texture period is an AND, and there
    is no multiply. 25 of the 32 textures have n = tw (shift 6).
- *Pieces*: the engine merges colinear neighbour segs with the same
  sectors. All 20 merges in E1M1 join **different linedefs** (pillars,
  switches, light strips), so the texture and its u restart at the
  joint. Rather than change the engine's seg set (and so layout.inc),
  each merged seg's texture record carries its pieces: start distance, u
  base, textures, pegging. The strip picks the piece holding d. Only the
  20 merged segs pay for the check.
- *Parts*: solid seg → middle texture (else lower, else upper); portal →
  the top band's wall rows take the upper texture, the bottom band's the
  lower. A sky-to-sky upper draws sky.

- *Byte columns*: the unit is the byte (4×2 fat pixels, written whole).
  The run extents, part and piece are sampled at the byte's first pixel.
  Its two strips keep their own u (at x+1 and x+3) and their own v (from
  their own T and B, at x and x+2). A merged seg's joint therefore lands
  on a byte boundary. Since step 5g the right strip's T and B are the
  midpoints of the byte's and the next byte's (`T + ((T(x+4) − T) >> 1)`)
  and its step extrapolates from the left strip's steps,
  `stepR = stepL + ((stepL − stepL_prev) >> 1)` (the same band kind and
  part on the previous byte), else K / (Br − Tr).

Agreement with the float `textured_ref` over the on-map poses: 95.0% of
wall cells within one texel on both axes, 77.6% the exact byte since step
5g (95.2% and 77.9% before it; 96.8% and
79.4% with 2-pixel strips; the 4-pixel edges cost the difference). The rest
is quantisation, plus close-up walls inheriting the engine's 1–2 pixel
edge differences; the texture follows the engine's drawn edges, as it
must. Two-sided masked middles are not drawn yet.
Rejected: perspective weights from the projected wall heights (integer
line ends: 6-column errors on far walls), and shifting k/W to 8 bits (a
near-clipped end projects far off-screen, leaving ~16 u steps across the
screen).

*4b, the 6502 side — DONE*: `src/master/mfill.s` textures the wall runs
of step 3's fill, byte-exact against `tex_ref`, gated by
`test_master_tex.py` (in `run_regression.py`; it replaces step 3's
`test_master_fill.py`). The disc boots on jsbeeb's Master 128 with
textured walls (`test_master_disc.py`).

- *One generator*: `master_walls.py` builds every table from the WAD, for
  both the model and the machine. E1M1 needs:
  - 157 wall parts (texture, K, Vtop);
  - 197 dressings (upper, lower and solid part, u base);
  - 17 merged segs with 37 pieces in all;
  - per header slot, a dressing byte and the length L16.
- *Memory homes*: `ld65` memory areas in `src/engine_master.cfg`
  (`ANDYM`, `B6TM`, `IXM`). The tables are labelled in `mfill.s`, and the
  image builders find them through `symmap`, so no address is baked.
  - **ANDY** ($8000–$8FFF, ROMSEL bit 7): the slot planes, the dressing
    table and the piece lists, 3.6K. They are read once per seg, in
    `tx_seg`. No OS runs after the loader, so ANDY is ours; the loader
    copies it in after the last disc access.
  - **Bank 6 tail** ($B900–$BDFF, `mb6_*`): the part records and the
    per-texture constants. Bank 6 is BANK_C, paged for the whole emit
    cascade.
  - **Main RAM** `mtex_ix` ($7A00): the column index bytes, 937 B. Read
    with ACCCON X clear.
  - **HAZEL**: the code. MFILL now runs $C800–$D9DA (4.5K); its BSS moved
    to $DC00.
- *Per seg* (`tx_seg`):
  - the header slot comes from `zp_seg_hdr_p`;
  - ANDY gives the dressing or pieces and L16;
  - d1/d2 come from the crossing t, which `reproject_at_crossing` now
    stores in `mf_xt` (MASTER only);
  - the weights come from the endpoint reciprocals in the VX structs;
  - exact d and raw weights at the visible ends;
  - then the per-strip numerator and denominator and their constant
    steps.
- *Per byte column*, on demand: d for both strips (step 5d: one division,
  the right strip extrapolated), and the piece holding the left one.
- *Per run*, for each of the byte's two strips:
  - its own step = K / h and v0 = Vtop + (y_even − T)·step, from its own T
    and B (at x and x+2) (`tvstep`);
  - its column (`tcol`, step 5d): index = hi((u & mask) << (8 − shift)),
    index byte, texel column pointer (the stacked textures' 128 row
    offset is folded in).
- *Writing*: the run extents, part and bank are the byte's. Every line is
  written whole, `(TEX1<<2) OR TEX2` (FLIP on odd lines), with nothing
  read back. v steps 2·step per line pair, and row = (v_hi & rowmask).
- *Cycles* (py65, 18 poses): 50.4M in total, 1.9–4.8M per on-map frame
  (step 3: 30.2M). The rest is per-strip and per-run arithmetic:
  shift-add multiplies and divides, which step 7 tables and unrolls.
- *Reference gap, found and reported, not fixed here*:
  - The Python reference's projector rounds sx where the engine's count
    projector (`bsp/project.s`, net shift S−3 = 0 at S = 3) truncates.
    This is a pre-existing Python/6502 mismatch.
  - It shows on very near walls' off-screen endpoints: 1 seg fill in the
    18-pose corpus.
  - Step 3's clamped T/B hid it. The texture's v exposes it.
  - `test_master_tex.py` reads the engine's own geometry at every
    `mf_fill`. Such a seg may differ only in its own cells; the count is
    capped at 2.
  - Fixing the reference (mirroring the truncating kernel in
    `fp.fp_project_x` for S ≤ 3) touches every Python gate. It is left
    for a separate change.

**5. Floors and ceilings. — DONE.** The model is `plane_ref.py`
(`PlaneRef`, on top of `tex_ref`), gated by `test_plane_ref.py`. The 6502
side is `src/master/mfill.s`, byte-exact against it in
`test_master_tex.py`. Both gates are in `run_regression.py`.

- *One texel read per 4×2 fat pixel*: floors are largely decorative, so a
  plane byte is the flat's byte (pixels #0 = #2, #1 = #3). It is sampled
  at the byte column's centre and the line PAIR's centre, and written
  whole (FLIP on the pair's odd line).
- *Depth*: a pair centred k lines off the horizon (k odd, 1–79) sees a
  plane D above or below the eye at 1024·D/k world units. D is the
  engine's own prescaled height difference, `vz − fh` or `ch − vz`
  (`zp_seg_bot_dlt` / `zp_seg_top_dlt`): the same heights the walls'
  floor and ceiling lines come from, so the texture meets them. It also
  moves with the movers. Hence `E = D·(2²⁰ // k)`, from a 40-entry table.
- *4.12 fixed point* (4.4 since step 5k): u and v are in texels (4 world units each), 16
  bits, wrapping at 16 texels for free. Along a line pair a plane is
  affine. With c and s the engine's 8-bit cos and sin magnitudes (unity
  256) and their signs, Pc = E·|c| and Ps = E·|s|:
  - `A = ±(Pc>>8)`, `hV = ±(Pc>>14)`;
  - `Bs = ±(Ps>>8)`, `hU = ±(Ps>>14)`;
  - `U0 = Up + A − 63·hU`, `dU = 2·hU`;
  - `V0 = Vp − Bs − 63·hV`, `dV = 2·hV`.

  Up and Vp are 1024 × the eye's world x and −y (DOOM flats run −y down).
  The texel is `flat[(V>>12)·16 + (U>>12)]`.
- *6502 structure* (per-seg horizontal spans, DOOM's visplane idea cut
  down to one seg):
  - `mf_frame` runs at `render_frame` entry (MASTER only): the frame
    epoch and copies of the view terms.
  - While a seg's byte columns are walked, `prun` only RECORDS each
    column's whole line-pair interval, ceiling (`pe_ct`/`pe_cb`) and
    floor (`pe_ft`/`pe_fb`), 64 columns each. A partial pair (odd first
    or even last line) is drawn on the spot (`pl_line`); so is a second
    run in the same column (`pl_pair`). A plane the eye is on the wrong
    side of keeps its shade (`pl_shade`).
  - At the seg's end (`mf_planes`), `mk_spans` turns the column
    intervals into horizontal spans (DOOM's `R_MakeSpans` on pairs,
    empty = ($FF, 0), with a sentinel column). `sp_start` keeps each
    pair's open span's first column.
  - `sp_draw` draws one span: U and V at its first byte (U0 + kb·dU, a
    short multiply) and the per-byte steps patched into the ADC
    immediates. Then a single loop along the pair: one texel read, the
    even line `AND maskEven`, the odd line `FLIP AND maskOdd`, Y += 8
    (page step on carry). Every write is a whole byte (4×2).
  - `pl_row` computes U0/dU/V0/dV per line pair and plane height, cached
    for the frame (`pc_*`, 80 entries).
  - Per-subsector flat ids live in ANDY; flat bank and page and the
    map-centre terms in the bank-6 tail.
- *NUKAGE*: animated through flats 0–2 until step 5f; now NUKAGE1 is a
  static flat.
- *Sky* ceilings stay solid cyan. A plane the eye is not on the right
  side of keeps its shade.
- *Accuracy*:
  - The 4.12 maths matches a float evaluation of the same geometry
    within one texel on 100% of plane cells.
  - Against the world-height float reference, about 60% agree (reported,
    not gated). The prescaled eye height (41 → 6 units = 40 world) and
    plane heights (quantum ~6.7 world units) scale depth by a few
    percent, which grows to whole texels in the distance. Visually the
    two match closely.
- *Cycles* (py65, 18 poses): 63.3M in total (68.5M with the earlier
  per-column passes, 50.4M with solid planes). At (1056, −3616, 32) there
  are 332 spans of 10.5 bytes on average and 143 partial cells. The span
  loop is about 122 cycles per 4×2 on absolute variables; zero page and a
  nibble table should bring it to about 100, which is step 7 work. Wall
  set-up (mul16 and div32) then cost more than the planes; step 5c
  fixed the arithmetic. On jsbeeb the
  disc boots and walks: 3 frames in 400 fields at the start pose, up from
  2.
- *Memory*:
  - HAZEL: span-time code $C8CD–$DD53, plane constants $C800–$C8CC; the
    span snapshot, column intervals, `sp_start` and row cache are at
    $C280–$C7EF (mhazel must stay under $280); BSS is at $DE00–$DF7A.
  - Main $6D38–$6FE9 (the tail of the clipper area): the cold per-seg
    set-up. The $79xx VPTAB tail is treated as taken: with the plane
    constants there, the jsbeeb disc stopped turning.
  - ANDY: plus 392 B of per-subsector flats.

**5b. Sector light. — DONE.** Each sector's light darkens everything a seg
draws: its walls, floor and ceiling, all from its front sector. Two
zero-page mask bytes, `maskEven` and `maskOdd`, are ANDed into every byte
written. maskEven applies on even lines, and maskOdd on odd lines, after
the FLIP. There are five levels, getting progressively darker
(maskEven.maskOdd):

| Level | Masks | E1M1 lights |
|---|---|---|
| 0 | FF.FF | 255, 224 |
| 1 | AA.FF | 208, 192 |
| 2 | AA.AA | 176, 160 |
| 3 | 0A.AA | 144, 128 |
| 4 | 0A.0A | none |

- *Mapping*: `level = min(4, (255 − light) >> 5)` (`master_walls.light_level`,
  one function, easy to retune).
- *Sky* is never masked.
- *Model*: `tex_ref`'s compose lights each byte by its owner seg (so
  `plane_ref` is lit too). `textured_ref` stays the unlit float reference.
- *6502*:
  - `maskEven` and `maskOdd` are the rasteriser's unused cnt pair in
    zero page.
  - Each seg loads them in `pl_seg` from the light level, which is packed
    into the top 3 bits of the subsector's floor-flat byte in ANDY.
  - `hz_run` masks its two bytes once per run (unless they are the sky
    shade). `trun` masks per texel pair. `prun` masks per line.
  - Cost: about 0.3M cycles over the corpus (68.8M in total).
  - Byte-exact at all 18 poses and both NUKAGE frames.
- *Not yet*: lighting effects (E1M1's blinking and flickering sector
  specials) and DOOM's distance fade. A mover-style table per frame could
  drive the first.

**5c. Fast arithmetic. — DONE.** The fill's general-purpose shift-and-add
multiply and 32-step divide had become more than half the frame. All of
them now use fast exact routines, so the Python models are unchanged and
the 6502 stays byte-exact (`test_master_tex.py`):
- *Multiplies* (`mul16`, `mul8x32`, `kbmul`) are built from `umul8`, a
  quarter-square 8×8 on the boot-built `SQR_*` tables at $0200–$07FF
  (main RAM, readable whatever ACCCON X). A zero operand byte skips its
  partial product.
- *Divides*: every E1M1 `div32` has a quotient under 2¹⁶ and 95% have an
  8-bit divisor.
  - 8-bit divisor: two 8-step byte divides (`d8_byte`), each skipped
    outright while the remainder is 0 and the byte is below the divisor
    (a quarter of the calls divide 0).
  - 16-bit divisor: `divq16` and `div32` share a 16-step core
    (`dq_core`) with the divisor patched into its immediates.
  - Anything else falls back to the 32-step loop.
- *Cycles*: 42.3M over the 18 poses, down from 63.3M. At (1056, −3616,
  32) the frame is 2.97M, down from 4.62M. On jsbeeb the start pose
  draws 5 frames in 400 fields, up from 3.
- *Memory*: the routines live in main $7E20–$7FFC (`MARITH`, loaded
  with the rest of $5800–$7FFF as MCBITS) and `d8_byte` in HAZEL.

**5d. Wall set-up restructure. — DONE.** The per-strip wall maths was
the largest set-up cost. The model (`tex_ref`, `master_walls`) changed
first, then the 6502, byte-exact against it. Each strip keeps its own
u and its own v (its own T and B lines, step and fractional v stepping):
- *One division per byte for u*: the right strip's d extrapolates from
  the previous byte's exact d when there is one,
  `dr = d + ((d − d_prev) >> 1)`; otherwise it is exact.
- *No multiply for the column*: a power-of-two column index per texture
  (`mtex_ix` grows to 996 B); the 25 textures with R = 1024 sample
  exactly as before.
- *Steppers step once per byte* (4 pixels, Q and R from 4|D|): still
  exact, half the steps. The right strip's T/B steppers start at x0 + 2;
  the d stepper's constants double the same way.
- *v0*: no multiply when a run starts at T or one line above it.
- Float agreement is unchanged in effect (95.2% within one texel, 77.9%
  exact; the gate stays at 95%).
- *Rejected*: one shared v per byte (2.41M at the reference pose, 33.7M
  over the poses). It loses the strips' independent texture stepping.
- *Cycles*: 38.1M over the 18 poses, down from 42.3M.

**5e. Plane spans: merged, partial lines, row terms. — DONE.** Three
6502-only changes; the model is untouched and the output byte-exact:
- *P1, pending planes*: a seg's floor and ceiling spans are no longer
  drawn at its end. Per kind, the pending spans (`pd_*`: D, flat, light
  level, byte columns) stay open while later segs are the same plane:
  `pe_init` widens them, clearing only the new columns. Another plane
  draws them first (`pd_flush`, in their own light, the current seg's
  masks kept), and `render_frame` (MASTER) ends with `mf_flush`: its
  seed is now `JSR rf_seed` so the walk's normal end and its unwind
  both return there. At (1056, −3616, 32): 332 spans → 202.
- *P2, partial lines as line spans*: an odd first or even last line of
  a run is recorded per column (`pp_all`, a slot per kind and line
  parity; a taken slot still draws on the spot) and drawn at the flush
  as runs along the line (`sl_go`: one texel and one write a byte, its
  FLIP and mask patched for the parity). 143 single cells → 78 line
  spans.
- *P3, row terms per frame*: ZC[j] = Zk·|c| and ZS[j] = Zk·|s| once per
  frame per row (`pl_zrow`), so a (row, D) needs two multiplies, not
  three; the same integers.
- *Cycles*: 36.1M over the 18 poses, down from 38.1M (−5.3%). Less than
  estimated: a span's set-up is dominated by building its (row, D)
  terms (112 builds at ~1.9K, 213K, at the pose above), which merging
  does not reduce; the U/V multiplies at span starts went from 950 to
  560.
- *Memory*: the span sweep, `sp_setup` and `pl_part` are in HAZEL with
  the loops; the pending-plane logic, the row maths and their tables
  (`pp_all` 256 B, ZC/ZS 360 B) in main $4FB1–$5592 ($55AA since step 5k).

**5f. Bank 6 holds the fill's cold code. — DONE.** HAZEL is for the hot
loops; most of the fill's code is set-up that runs while bank 6 (BANK_C)
is paged for the emit cascade, so it now runs from bank 6:
- *Room*: NUKAGE2/3 (512 B) and the BRNBIG masked middles (1.5K; never
  drawn, no wall part uses them) were dropped. The model's frames are
  unchanged at all 18 poses (checked byte for byte); the float reference
  skips masked middles with no stored texture. Textures and flats end at
  $A4FF; `master_assets` packs bank 6 only below $A500.
- *Bank 6 code* (`MB6C` + `MFILLV`, $A500–$B675, 4.3K; ld65 area `B6CM`,
  file `engine_b6c_m.bin`, laid into the bank-6 image by the rig and so
  by the disc): the column walk, steppers, bands, wall and plane set-up,
  span sweeps and set-up, row maths helpers, `pl_wr1` and `hz_run` (ACCCON
  X only maps $3000–$7FFF, so they write from bank 6).
- *The rule*: bank-6 code never pages another sideways bank and keeps
  running. What does stays in HAZEL and puts bank 6 back before it
  returns: `trun`'s screen side (`tr_screen`), `pl_cell`'s flat read
  (`pc_go`), the span loops (`sp_go2`, `sl_go`, which now page the flat
  themselves; `sp_setup` hands them its bank in A) and `mf_frame`
  (entered with bank 7 paged). ANDY (`tx_seg`) only overlays $8000–$8FFF.
  The cascade enters `mf_snap` / `mf_fill` with bank 6 paged (checked
  over the poses).
- *HAZEL*: the fill's code is now $C800–$CB37 (824 B); **$CB38–$DDFF,
  4.8K, is free** for faster loops and their tables.
- Cycles unchanged (36.1M over the 18 poses; the NUKAGE frame test went
  with the animation).

**5g. Wall set-up: fewer divides and steppers. — DONE.** Three model
changes (`tex_ref`), the 6502 byte-exact against them; each strip keeps
its own u and v:
- *W1*: the right strip's v step extrapolates from the left strip's
  steps (per band kind: its byte, part and step, `ss_*`), else it is
  exact. One K / h division per run instead of two.
- *W2*: the right strip's T and B are midpoints with the next byte's
  lines, read by `st_peek` (a stepper's value one step on, its state
  kept) once per byte with a wall (`tr_lines`). The right strip's two
  steppers, their per-seg set-up and their per-byte steps and reads go.
- *W3*: the left strip's d is exact on even bytes only; odd bytes take
  the midpoint of the previous byte's d and a look-ahead exact d
  (`tx_dat`), which the next byte reuses: about one division per two
  bytes.
- *Accuracy*: 95.04% within one texel (gate 95%), 77.6% exact.
- *Cycles*: 34.4M over the 18 poses, down from 36.0M (−4.5%);
  (1056, −3616, 32) 2.62M → 2.49M. jsbeeb: 6 frames in 400 fields at the
  start pose.
- *Memory*: bank-6 code now ends at $B88E (113 B left before the tables);
  the state is 19 B of HAZEL BSS.

**5h. x16 tables, per-piece u base, h63. — DONE.** Three exact 6502
changes (no model change; byte-exact):
- *x16 tables* in HAZEL, page-aligned: `lo16[x] = (x << 4) & $FF`,
  `hi16[x] = x >> 4` (so `x·16 = lo16[x] + 256·hi16[x]`); `mf_flip`
  moved to the next page so its indexed reads never cross one.
- *Flat texel index*: `hi16` replaces four LSRs and a store in `sp_lp`,
  `sl_lp` and `pl_cell`: `LDX u_hi / LDA v_hi / AND #$F0 / ORA hi16,X`,
  12 cycles less per span byte.
- *u base per piece*: `set_cur` keeps `ub·16 − start` (via the tables);
  `tcol` only adds d.
- *h63*: `63·h = (h << 6) − h` with `h << 6` as `(h << 8) >> 2`, a byte
  move and two shifts instead of a six-pass loop.
- *Cycles* (`tools/master_profile.py`, 14 on-map poses): the mean frame
  2.119M → 2.054M (−3.0%): span loops −26K, wall set-up per run −26K
  (`tcol`), arithmetic −11K (`h63`). 18 poses: 34.4M → 33.3M.

**5i. Wall write loop: unrolled pair bodies. — DONE.** The wall texel
loop was 27% of the frame at ~190 cycles per 4×2: per line it tested the
parity, counted, and checked the character row's end.
- *Four versions, each four pairs unrolled* (`tv_0`..`tv_3`): a whole
  character row, version s's pairs on lines 2s, 2s + 2, ... mod 8, with
  PTR moved on a row after line 6's pair. One count per block of four
  (`DEC zw_np`); the blocks loop on themselves.
- *16 entry points* (`te_sj`, one `JMP (tr_ent,X)`, X = Y·4 +
  (pairs & 3)·2): the version and position are chosen so the first pair
  lands on the starting line and the first pass does pairs & 3 of them
  (or 4), Duff's-device style.
- *Each body is [v step] te_sj: [fetch, combine, write]*: an entry skips
  its step (the first pair's v is current), so the last pair's step never
  runs. A run's odd first line and even last line go through `tr_fetch`
  once.
- *Texel reads* through two zero-page pointers (`LDA (zw_tl),Y`,
  `(zw_tr),Y`; Y the texel row, then `LDY #K` for the writes): nothing is
  patched per run but the two pointers.
- *Zero page*: both strips' v, the row mask, the texel pointers, the
  combined texel and the block count (`zw_*`): bytes no code in the
  MASTER link references (`zp.inc` notes the reuse), and TP, dead while
  the loop runs.
- *Cycles* (`tools/master_profile.py`): the loop 569K → 361K per frame
  (~125 cycles per pair with each run's set-up); the mean frame 2.054M →
  1.847M (−10%). Byte-exact (no model change).

**5j. Floor and ceiling pair loop: Duff's device. — DONE.** The span
pair loop (`sp_go2`, ~2,070 bytes a frame) paid a count, a page-carry test
and patched 16-bit U, V steps on every byte.
- *Four versions, four bytes each* (`sf_0`..`sf_3`; one version since
  step 5l), entered at one of 16
  points (`sf_sq`, one `JMP (sf_ent,X)`). The position q = −n & 3 makes
  the span's last byte end a block. The version s = (q − kb) & 3 puts each
  body on a known column mod 4, so only the column 3 mod 4 body adds
  (Y + 7, and `INC PTR+1` past column 31 mod 32). The other three move Y
  to the next column with one EOR (#$09 or #$19, clearing the odd line's
  bit). One count per block.
- *Each body is [U, V stepped] sf_sq: [texel read, both lines written]*:
  an entry skips its step, so the last byte's step never runs. V (its high
  byte, before step 5k) is left in A for the texel index.
- *Zero page*: U, V and dU, dV (`su_*`, `sv_*`, `sdu_*`, `sdv_*`) and
  the block count share the wall loop's `zw_*` bytes. Both loops are
  leaves, and `tr_screen` reloads its own per run. `sl_go` uses them too,
  so neither loop's steps are patched any more.
- *The flat's page* is patched into the 16 texel reads only when it changes
  (`sf_page`, from `sp_setup`).
- *Cycles* (`tools/master_profile.py`): the span loops 239K → 205K per
  frame (~93 cycles per pair byte with each span's set-up); the mean frame
  1.847M → 1.812M (−1.8%). Byte-exact (no model change). HAZEL +0.6K.

**5k. Floors and ceilings in 4.4. — DONE.** Floors are low value, so
their u and v drop from 4.12 to 4.4: one byte each, 1/16 texel, wrapping
at 16 texels for free.
- *Model* (`plane_ref.row`): the row maths stays 4.12. It is taken at
  byte column 32 (U0 + 32·dU), so `Uc = Up + A + hU` and
  `Vc = Vp − Bs + hV`, with no 63·h terms. Uc, Vc, dU and dV are each
  rounded to 4.4 (the high byte, plus one if the low byte is ≥ $80).
  Byte column kb has u = Uc + (kb − 32)·dU (mod 2⁸), the same whatever
  span it is in.
- *Accuracy*: a rounded step is off by at most 1/32 texel a column.
  Anchored at the centre, that stays within one texel at the screen's
  edges (anchored at column 0, it would reach two). Against a float
  evaluation of the same geometry, 99.99% of plane cells are within one
  texel (100.00% in 4.12); the gate is 99%.
- *6502*:
  - `pl_row` stores the four bytes, and the row cache halves (`pc_uc`,
    `pc_du`, `pc_vc`, `pc_dv`: 320 B of HAZEL freed). `h63` is gone.
  - `uvat` starts a span or cell with one 8×8 multiply per axis
    (`mf_mul8`, low byte of (kb − 32)·d), replacing the two 16-bit
    `kbmul`s.
  - The loops step u and v with two 8-bit adds, and the texel is
    `(v & $F0) | hi16[u]`. U, V, dU and dV are one zero-page byte each.
- *Cycles* (`tools/master_profile.py`): the span loops 205K → 167K per
  frame, arithmetic 410K → 365K. The mean frame is 1.812M → 1.730M
  (−4.6%), and the 18 poses 29.8M → 28.2M. Byte-exact against the
  revised model.

**5l. Floor pair loop: fixed Y offsets. — DONE.** Y is no longer stepped
across a span. PTR points at the block's first byte column, plus the line
within the character row, so the four bodies write at fixed offsets:
`LDY #0`, `INY`; `LDY #8`, `INY`; `LDY #16`, `INY`; `LDY #24`, `INY`.
The block end adds 32 to PTR, which also carries the screen page.
- *One version, four entry points* (`sf_lp`, `sf_e0`..`sf_e3`,
  `JMP (sf_ent,X)`, X = 2q). The entry is still q = −n & 3, and `sp_go2`
  backs PTR off by 8q, to the block's column 0. The page check no longer
  needs to fall on a known column, so the four rotated versions go.
- *Block end*: the count test (`DEC`, `BEQ sf_end`), PTR += 32, then
  body 0's U, V step and `JMP sf_lp`. The last byte's step still never
  runs.
- *Per byte* 2 cycles for `LDY #` instead of 6 for the Y move (`TYA`,
  `EOR`, `TAY`); per block 13 for PTR += 32 instead of the old page
  check. The flat's page goes into 4 texel reads, not 16.
- *Cycles* (`tools/master_profile.py`): the span loops 167K → 163K per
  frame; the mean frame 1.730M → 1.725M. HAZEL −0.6K. Byte-exact (no
  model change).

**5m. Solid runs (sky): unrolled character rows. — DONE.** `hz_run` (sky
ceilings and untextured shades) filled a byte column a line at a time:
parity test, reload, count and row-end check, ~29 cycles a line.
- *Unrolled rows* (HAZEL; bank 6 is full): body k is `LDY #k`,
  `STA (PTR),Y`, so the 8 bodies are a whole character row. Body 8 is the
  row tail: `INC PTR+1` twice, `DEC hz_r`, `BNE` back to body 0.
- *Two copies*: `h1` when the even and odd bytes are equal, with A
  holding the byte; that is sky (cyan $F0, FLIP-symmetric and never
  darkened). `h2` loads `r_ev` / `r_od` by line parity, for the
  light-masked shades.
- *Driver* (`HZ_DRIVE`, bank 6):
  - The run's last row goes first. A temporary RTS is laid over the body
    after its last line (the original opcode is saved and put back), and
    it is entered at line 0, or at the first line if the run is in one
    row.
  - Then the rest goes in one entry at the first line, the row tail
    looping once per row.
  - There is no per-line test anywhere.
- *Cycles*: sky 8 cycles a line plus 19 a row (~10.4 a line), against
  ~29. Each run costs a fixed ~150 cycles for the set-up and the patch.
  The profile poses see almost no sky: solid fills 2.5K → 1.8K per frame,
  mean frame 1.725M → 1.724M. Byte-exact (no model change).

**5n. Walls: one shared v when the strips agree. — DONE.** A wall byte's
two strips each step their own v. But they are 2 pixels apart, so for
most runs the two v values differ by a fraction of a texel. Each strip
keeps its own u (both texture columns stay); only v is shared.
- *Measured* (14 on-map poses, 1,680 runs): half of all runs differ by
  under 1/8 texel over their whole length; 80% by under 3/8.
- *Predictor* (`tex_ref.shared_limit`), from geometry, with nothing of the
  right strip computed. Both strips' v come from the seg's top and bottom
  lines, so between strips 2 pixels apart the gap is about step × the
  lines' rise over 2 pixels. Within a seg the lines are straight, so the
  rise is the seg's own:
  - per seg, s_lim = (384·w − 1) // max(|ΔT|, |ΔB|), with w = sx2 − sx1;
    $FFFF if w or the rise is 0, or if it overflows;
  - per run, share when the left step ≤ s_lim (step·m·2 < 768·w: 3/8 of
    a texel).
  It shares 76.6% of wall lines (an exact per-run test would share 80.1%).
- *Accuracy*: within one texel of the float reference 95.04% → 95.07%,
  exact 77.58% → 77.94%. Where the current right strip's v strays most,
  sharing the left's exact v is the better answer. The right strip's v is
  built from approximations: its step is extrapolated from the previous
  byte's, and its top and bottom are midpoints with the next byte's.
- *6502*:
  - `sh_lim` (HAZEL; bank 6 is full) runs once per seg after the T and B
    steppers: one `div32`.
  - `trun` compares `t_sl` with `tw_slim`. A shared run copies the left
    v and step to the right strip's and skips `tr_lines` (`st_peek`), the
    right step and its `tv_v0`. It keeps the step bookkeeping (`ss_*`) and
    the right strip's column.
  - `tr_screen` enters a second set of four Duff versions (`sv_0`..`sv_3`,
    entries `ts_sj`, `ts_ent`). Each body steps one v, and that row index
    (Y) reads both texel columns. Their exit (`ts_end`) copies the left v
    to the right strip's for `tb_end`; the odd first line uses the
    copies.
- *Cycles* (`tools/master_profile.py`):
  - the wall loop 361K → 299K per frame;
  - per-byte/run set-up 202K → 161K;
  - `sh_lim` +6.5K;
  - the mean frame 1.724M → 1.611M (−6.5%);
  - the 18 poses 28.1M → 26.5M.
  Byte-exact against the revised model. HAZEL +1.0K.

**5o. A 136-line view and a static control panel. — DONE.** The bottom 24
lines of both buffers are DOOM's status bar, drawn once at boot; the 3D
view is the top 136 lines (17 character rows), re-centred on line 68 as
DOOM does with its status bar (the vertical field of view shrinks; the
focal lengths and all wall and flat maths are unchanged).
- *The panel* (`master_panel.py`):
  - STBAR with a new game's state drawn on it as `st_stuff.c` places it:
    ammo 50, health 100%, the arms box with the pistol, the straight face,
    armour 0%, and the ammo counts.
  - Pixel art at the full Mode 1 resolution (256 × 24), not a texture
    match (`panel_pixels`). The bar is sampled at each pixel's centre;
    then:
    - grey stone is a red/cyan cross-hatch, by (x + y) parity; a lone
      dark or light speck in it (the stone texture's own) is stone too;
    - dark grey is black (dividers, shadows, the face box) and light grey
      white (labels, bevels);
    - the big numbers (`STTNUM`, `STTPRCNT`) are their red body only, pure
      red, with a 1-pixel black outline (its 8 neighbours) at screen
      resolution;
    - the face (`FACE`, DOOM's straight-ahead STFST01) is hand-drawn at
      19 × 22 (the bar's scale) from a tone map of the original, so it
      keeps its shape: the broad square head, a red/black hatch of hair,
      the lit brow ridge and cheeks (red/white hatch), eyes in dark
      sockets, gritted white teeth, the tapering chin and the vertical
      bar ears;
    - the small text is the panel's own 3×5 font (M 5 wide), white on
      black boxes (`lettering`): the section labels centred where DOOM's
      are (lines 18–22), the arms numbers (the owned pistol white, the
      rest red, for DOOM's yellow and grey) and the ammo table (a row
      every 6 lines from x 199).
  - 1,536 bytes (`MPANEL` on the disc).
- *Boot*:
  - The loader parks `MPANEL` in buffer 1's panel rows (shadow
    &7A00–&7FFF, clear of the parked HAZEL and ANDY blocks).
  - The stub copies it to buffer 0's (&5200), once ANDY has moved out.
  - `MHZ_PATTERN` clears and draws only character rows 0–16.
  - Nothing writes there again: the Master never clears a screen, and the
    fill stops at line 135.
- *Engine* (shared code, one MASTER-conditional `VIEW_LINES` in `zp.inc`;
  Model B's values and builds are unchanged):
  - `VIS_YMAX = Y_BIAS + VIEW_LINES − 1` feeds the clip pool's full-screen
    span (`pool.s`), the zero-record off-screen test (`tfr.s`) and the
    fill's clamps.
  - `project_y`'s bias constant is `VIEW_LINES / 2 + Y_BIAS`: 116 on the
    Master (128 before).
- *Fill*:
  - `HZ_LINE` (68), `HZ_PAIR` (34) and `VIEW_PAIRS` (68) replace the
    literal 80, 79, 40 and 160 in `prun`, `pl_row` and `mf_frame`.
  - The per-pair tables (`sp_start`, `pc_*`) and the per-row ones (`pl_zk`,
    `zr_ep`, `zc*`, `zs*`) shrink to match.
- *Models*:
  - `fill_ref.master_view` switches the Python engine twin (`fp`,
    `endpoint_spans`, `wad_packed`, `doom_wireframe`, `angle_seg`) to 136
    lines about 68, for the length of a Master render only. The Model B
    references keep 160 about 80.
  - `fill_ref`, `tex_ref` and `textured_ref` compose the panel into lines
    136–159; `plane_ref.HORIZON` and `textured_ref.CY` are 68.
- *Gates*:
  - `test_master_tex` compares all 10,240 bytes, panel included: the rig
    paints the panel into both buffers before each frame as the disc does.
  - `test_master_disc` checks both buffers' panel rows on jsbeeb after the
    engine has turned and walked.
  - `test_master_engine` links the Master with `-D MASTER_VIEW=160`
    (`DOOM_ASMDEFS`) to keep matching Model B's line list.
  - `test_tex_ref`'s float agreement is 94.83% (the gate is now 94.5%,
    from 95%). The maths is unchanged, but the lost lines were mostly easy
    near-floor and lower-wall cells; exact agreement rose to 77.98%.
- *Cycles* (`tools/master_profile.py`):
  - the mean frame 1.611M → 1.519M (−5.8%);
  - the 18 poses 26.5M → 24.9M;
  - the wall loop 299K → 275K, the span loops 163K → 127K, arithmetic
    356K → 340K.
  Per-column costs (column walk, per-seg and per-byte set-up, ~470K) do
  not shrink with the height.

**6a. Mode 2. — DONE.** In Mode 1 the colours made the demo unenjoyable,
so it becomes a Mode 2 demo for texturing, not atmospherics. The rendering
is otherwise identical.
- *Formats* (`master_assets`):
  - a texel is its colour in the left pixel's bits;
  - a wall byte is `TEX1 | (TEX2 >> 1)`;
  - a floor texel is both pixels;
  - there is no FLIP table.
- *No cross-hatch, no light*:
  - both lines of a texel row are the same byte;
  - the sector light masks (`maskEven` / `maskOdd`) and the `mf_flip` page
    are gone. The light level still keys the pending plane spans (a later
    clean-up can drop it).
- *6502*:
  - the wall bodies read the right texel, `LSR`, `ORA` the left, and store
    one byte twice: about 22 cycles a pair saved;
  - the span bodies, `sl_go`, `pl_wr1` and `tr_fetch` lose their masks and
    FLIP;
  - solid runs (`hz_run`) write `shade | shade >> 1` on every line, so only
    the one-value row copy is left;
  - shade constants: `WB_SKY` cyan $28, `WB_CEIL` blue $20, `WB_FLOOR` red
    $02.
- *Boot and HUD*:
  - MODE 130 with its default palette;
  - the boot pattern writes one byte per line;
  - the frame-time HUD's glyphs are 2 byte columns (16 bytes) each.
- *Textures*: a placeholder ramp conversion (section 2), to be redrawn as
  pixel art, a colour family per material, in step 6b.
- *Panel* (`master_panel.py`) redrawn as Mode 2 pixel art, 128 × 24:
  - the stone in the grey ramp (black, blue, cyan);
  - 4 × 10 big red numbers (seven segments) with a black outline;
  - white 3 × 5 labels on black (ARMS dropped: it doesn't fit at 0.4 scale);
  - the arms numbers (the pistol yellow);
  - a 10 × 22 face (red hair, yellow skin, white eyes, teeth and
    highlights, bar ears);
  - the ammo counts in yellow.
- *Gates*:
  - `test_textured_ref`: odd lines repeat even ones, and there are no
    flashing colours;
  - `test_fill_ref`: every byte is two fill shades;
  - `test_master_assets`: Mode 2 round trips and `wall_pair`;
  - the jsbeeb test allows the 8 colours.
  `test_tex_ref` exact agreement is 81.19% (no light changing bytes).
- *Cycles* (`tools/master_profile.py`):
  - the mean frame 1.519M → 1.447M (−4.7%);
  - the wall loop 275K → 225K;
  - the span loops 127K → 106K;
  - the 18 poses 24.9M → 23.8M.
  HAZEL −256 B (`mf_flip`).

**6b. Wall and flat textures as pixel art. — DONE.** All 29 wall
textures are hand-drawn grids, `art/walls/NAME.txt` (one letter per
texel, `.rgybmcw` = black red green yellow blue magenta cyan white), read
by `wall_art.wall_texels` in place of the nearest-colour conversion; edit
a grid and rebuild (`master_assets.py`) to change a texture. Style, per
material, flat colour areas with hard rims rather than shading:
- *tan / brown* (STARTAN1/3, SW1STRTN, BROWN1/96/144, BIGDOOR4, STEP1/6,
  TEKWALL4, EXITDOOR): red, black seams and grain, yellow rims, nails and
  traces;
- *grey metal* (STARGR1, DOOR3, BIGDOOR2, SUPPORT2, DOORSTOP, DOORTRAK,
  COMPTALL, COMPUTE2, COMPSPAN, COMPTILE, PLANET1, EXITSIGN): blue, black
  seams, cyan rims, white glints;
- *green* (STARG3: blue panels, green rims; BROWNGRN, SLADWALL, NUKE24:
  green, black, yellow);
- *accents* keep DOOM's signals: red lamps, yellow/black hazard stripes,
  green circuit boards and screen text, the red EXIT, white light tubes.

Clean art also dedups far better (columns are stored once per texture):
wall + flat data fell from 25.1K to 17.7K, freeing 7.4K of bank 6
($8500–$A4FF). `wall_art.convert` (an art-directed converter: per texture
a region ramp on the 3x3 mean brightness, local-contrast detail, hue
accents, speck clean-up) remains as the seed for a new texture
(`python3 wall_art.py` writes a grid for any texture without one).

*Flats* (all 21) are hand-drawn too: `art/flats/NAME.txt`, 16x16 TONES
(`wall_art.flat_tones`): `.rgybmcw` solid, `RGYBMC` black + a colour,
`123456` white + a colour, cross-hatched on screen. Floors and ceilings sit
back behind the walls: mostly dark black + colour tones (navy for grey,
dark red for brown, dark purple for the dark ceilings), with solid colour
only for features -- the ceiling lights (white diamonds and lamps with
pale yellow rims, red lamps), the UAC logo, nukage (green with pale
glints), hex-tile joints and lit edges (FLOOR4_8 grey, FLOOR5_1 rust,
FLOOR5_2 raised ridges), the step's pale frame.

**6c. Mode 1 control panel by raster split. — DONE.** The Mode 1 panel
art read better than its Mode 2 redraw, so the panel (lines 136–159) is
Mode 1 again and the 3D view stays Mode 2. A timer interrupt switches
the video ULA and palette mid-frame.
- *Timer*: the User VIA's T1, free-running, locked once to the vsync edge
  (`split_init`). Its two latches alternate:
  - `SPLIT_VP` = 14,161 µs, vsync → the end of line 135's picture (14,520
    until the 2026-10-06 fix below);
  - `SPLIT_PV` = 19,964 − `SPLIT_VP`, back to vsync.

  Each period is the latch + 2 µs, so the pair is exactly the 312-line
  field. The VIA counts the same 1 MHz clock as the CRTC, so it never
  drifts.
- *Handler* (`split_irq`, HAZEL):
  - At the panel event it writes the ULA control register (Mode 1, $D8)
    and then palette entries 1–6.
  - At the vsync event it writes Mode 2 ($F4) and those entries back.
  - The panel's logical colours are chosen (`master_panel.split_palette`)
    so only entries 1–6 differ between the modes: black 0 (ULA indices
    0, 1, 4, 5) and white 1 (2, 3, 6, 7). Red 2 (8, 9, 12, 13) and cyan 3
    (10, 11, 14, 15) sit in entries the Mode 2 view never uses, set once.
  - That makes 7 writes (~21 µs), which fit the 32 µs horizontal blank.
  - The panel bytes are Mode 1 art re-encoded to those logical colours.
- *The IRQ path*:
  - The MOS 3.20 entry ($E59E) is `STA $FC / PLA / PHA / AND #$10 / BNE /
    JMP ($0204)`. It is outside HAZEL, and $FC is free.
  - On the Master, page 2 held the quarter-square quad, so the quad moved
    to HAZEL: `sqr_quad_m`, a linker-placed MSQR segment at $D800.
    `abi.inc` resolves `SQR_MIR_LO` to it under MASTER for the engine link
    (asmbuild defines `ENGINE`), and the rig copies it there. Model B keeps
    $0200; its builds are byte-identical.
  - The handler sits in HAZEL, which is paged for the whole run, so it
    works whatever ACCCON X or ROMSEL hold when it lands.
  - Every other IRQ source is masked: both VIAs' IER (the engine polls
    their flags) and the ACIA (master reset).
- *Timing* was found on jsbeeb by sweeping `SPLIT_VP`:
  - 14,500 is early in 24 of 50 fields (line 135's right end decoded as
    Mode 1);
  - 14,548 is late in every field (line 136's left end decoded as Mode 2);
  - the clean window is about 14,504–14,536, the horizontal blank, and
    14,520 is its centre (about ±16 µs for interrupt-latency jitter).
- *Gates*:
  - `test_master_disc` samples 50 fields after the engine has run.
    Line 135 must keep its Mode 2 colours out to the same pixel (not
    early), and line 136 must show none (not late). The check fails at
    both 14,500 and 14,548.
  - `test_master_tex` compares the panel bytes.
  - `to_png` decodes lines 136+ as Mode 1 in the split palette.
- *Cost*: two interrupts a field, about 60 cycles each (well under 0.1% of
  a frame).
- *Fix, 2026-10-06: the split was 5 lines late.* The sweep and gates above
  read the frame buffer at row 188 + 2 * line; poking a line's bytes and
  diffing shows screen line L is row **176** + 2L. So "line 135 / 136" were
  really lines 141 / 142, and 14,520 put the Mode 1 switch at line 141 --
  the first panel lines showed Mode 1 bytes decoded as Mode 2 (reported on
  jsbeeb 2.3.2, both boot routes). Read off the CRTC (`vertCounter`,
  `scanlineCounter`, `horizCounter`) as the handler writes: the switch must
  come after line 135's visible part (character 64 on) and the panel's
  last palette write (entry 15, ~84 characters / 42 µs later) before line
  137 (line 136 is all zero bytes, black in either mode). 14,161 puts the
  switch at 135:117-121 (`*RUN`; SHIFT-BREAK 5 later) and the last
  palette write at 136:73-77 -- about 25 µs of margin each side.
  `tools/master_rig.mjs` now uses row 176 and fails unless every field's
  switch and last palette write fall in that window (content-independent;
  the old reference-pixel "early" check is information only, since the
  real line 135 moves with the view). It fails the old 14,520 disc.

**6d. Floor, ceiling and sky cross-hatch. — DONE.** Floors and ceilings
regain Mode 1's texture without its harshness: a flat texel is a TONE,
a solid colour or a pair of colours, and the odd line of each pair writes
it with its pixels swapped.
- *Tones* (`master_assets.TONES`): the 8 solids, then the 12 pairs
  black + a colour (black+red, +green, +yellow, +blue, +magenta, +cyan)
  and white + a colour (the same six): a colour darkened or paled, never
  two hues fighting. (A first cut used gentle-luma pairs between hues,
  e.g. red+magenta, red+green, magenta+green; they were dropped.)
- *Flats* (placeholder until 6b): per flat, brightness normalised to its
  5–95% range, nearest tone by luma on its material's ramp
  (`TONE_RAMPS`: grey and blue K, K+B, B, K+C, C, W+C, W; brown K, K+R,
  R, K+Y, Y, W+Y, W; green K, K+G, G, K+Y, Y, W+Y, W; red K, K+R, R, M,
  W+M, W); vivid texels keep the nearest saturated solid.
- *Sky* (F_SKY1): `SKY_BYTE` (cyan, white) on even lines, FLIP of it
  ($3D / $3E) on odd.
- *FLIP* (`flip`, HAZEL $CA00, page-aligned next to `hi16` / `lo16`):
  `((b << 1) & $AA) | ((b >> 1) & $55)`, generated by `.repeat`.
  - `sp_go2`: each body writes the even line, then `TAX / LDA flip,X`
    for the odd line (+6 cycles a byte).
  - `sl_go` (one line): `sl_fb`, a `BRA` over the flip that `sl_draw`
    patches to 0 on an odd line.
  - `pl_wr1` (a partial line drawn on the spot): flips on an odd line.
  - The sky's runs: `hz_run` hands a cyan run to `hz_sky` (HAZEL), whose
    unrolled bodies (`h2`, 7 bytes: `LDY #k / STA (PTR),Y / EOR hz_x`)
    alternate the two bytes; `HZ_DRIVE`'s SIZE-7 entry starts on the
    entry line's byte. Other solid runs keep `h1` (no cost).
  - Walls are unchanged.
- *Models*: `tex_ref._compose` writes `FLIP[b]` for plane bytes on odd
  lines, and treats the sky's solid cells as `SKY_BYTE`; `textured_ref`
  likewise per pixel half.
- *Gates*: `test_master_tex` byte-exact over the 18 poses (the first run
  caught `pl_wr1`); `test_master_assets` checks FLIP swaps every byte's
  pixels, that every pair is black or white + a colour, the sky byte, and
  that every flat byte is a tone.
- *Cycles*: the mean frame 1.447M → 1.459M (+0.8%; the span loops 106K →
  110K, the sky's EOR under 0.1K).
  HAZEL +256 B (`flip`) + the sky bodies and driver.

**6e. Colour cycling. — DONE.** Logical colours 8-15, which the Mode 2
view did not use, are cycled by the raster-split IRQ at vsync, so the
animation runs at 50 Hz whatever the frame rate, at no render cost.
- *The cycle* (`master_assets.cycle_colour`, the spec; `cyc_tab` in
  HAZEL, 8 phases x 8 palette register values, generated by `.repeat`
  from the same rules; `test_master_tex` compares the two). One phase
  every 16 fields (`(sp_tick >> 1) & $38`; 8 at first, softened after
  play on jsbeeb read as strobing):
  - 8-11 (`nopq`): the nukage wave, entry k at phase p showing
    green / green / yellow / green by (k + p) & 3 -- a travelling
    highlight (black / green / green / yellow at first);
  - 12 (`h`): the red lamps' halo, red, off one phase in four;
  - 13 (`t`): the lamps' glint, white red red red;
  - 14 / 15 (`z` / `x`): two blinks, yellow and red, each off one phase
    in four, out of step (alternate phases at first).
- *Art*: NUKAGE3 is drawn in the wave (its bubbles' brightness rings
  mapped to wave steps, adjacent steps hatched: tones `NOPQ`); NUKE24's
  glow ripples down from the dirt line; TLITE6_5's halo pulses and its
  glints flash; COMPTALL's and COMPUTE2's lamps blink out of step. The
  tones gain the 8 cycling solids and 4 wave pairs (32 in all); wall
  grids gain `nopqhtzx`.
- *The panel*: Mode 1's red and cyan are palette entries 8-15 (red 8 9 12
  13, cyan 10 11 14 15), so the panel event now rewrites them too: 15
  writes, ~45 us, more than the horizontal blank. The panel's top line
  (136) is now black, which in Mode 1 reads only entries 0 1 4 5: the
  blank takes the mode and entries 1 4 5, and white (2 3 6) and the
  red / cyan (8-15) land during line 136. The vsync event writes Mode 2,
  entries 1-6 and the phase's 8-15. `split_irq` was restructured (BMI to
  the panel path, an early RTI) for branch range.
- *Gates*: `test_master_disc` now also checks, over 50 fields, that the
  panel's lines 137-159 show only black, white, red and cyan (both of the
  last two), and that entries 8-15, sampled 5 ms into the field, take at
  least 3 states (4 seen: the cycle's period). The split stays clean
  (early 0, late 0). `textured_ref.to_rgb` decodes logical 8-15 at
  phase 0; `test_textured_ref` allows them only in drawn cells.
- *Memory*: HAZEL MFILL $C800-$D782 (`cyc_tab` 64 B + the handler), free
  $D783-$D7FF (125 B); `sp_tick` in MFILLBSS.

**6e fix. Holes: billboard objects were on. — DONE.** Played on jsbeeb,
patches of the view flashed. Both buffers, redrawn at the same pose,
differed in up to ~500 bytes: cells the engine never wrote, keeping each
buffer's own stale bytes, so they alternated at every flip. The Master
driver's init called `ok_flip` meaning "objects off", but it TOGGLES and
off is already the default (`ok_state` = 1): it turned the billboards
on, and their span lines left cells the Master's filler skips (present
since step 1; the py65 rig zeroes `OBJ_ANYB` itself, so it never saw
them). Now `ok_clear` (objects.s, MASTER only) forces them off; the O
key was already compiled out on the Master. Found by a jsbeeb probe:
filling the back buffer with a marker at each `render_frame` and
counting what survives to the flip (71 cells on the very first frame,
0 after the fix); replaying the recorded poses and eye heights in py65
showed none, which ruled out the renderer itself.
- *Gate*: `test_master_disc` now does the same during its walk: at each
  `render_frame` the back buffer's view is filled with `mode2_byte((13,
  0))` (no art makes glint-next-to-black) and any left at the flip fail
  the test. 28 frames, 0 holes; with the old `ok_flip` call restored it
  fails (2425 cells over 24 frames).
- *Note*: the jsbeeb driver's eye height is 8 at the start (the py65
  harness derives 6 from the floor + 41); poses compared across the two
  need the driver's `zp_br_vz`.

**6f. Gun overlay. — DONE.** DOOM's idle pistol (PISGA0) drawn over
the view, bottom centre, sitting on the panel's edge.
- *Art*: `art/gun/PISGA0.txt`, 24 x 50: one Mode 2 pixel by ONE line
  each (' ' transparent). DOOM draws PISGA0 at (126, 106) of 320 x 200
  over a 168-line view; scaled 0.4 x 0.81 that is pixel 50 (byte column
  25) and lines 87-135. The first cut (24 x 25, two lines a pixel, drawn
  as symbols) read as neither a hand nor a pistol; this one is sampled
  from PISGA0 and posterised -- skin black / red / yellow by brightness
  (cuts 75, 125), metal black / blue / cyan / white (22, 50, 95) -- then
  given a cyan rim on the gun's lit left and top edges (so it reads on the
  navy floors) and a black outline on the hand. Dithered skin was tried
  and read as a mesh.
- *Spec*: `master_gun.py` -- `table()` (per grid row, one line: the line,
  the first byte column, the count, and per byte a mask (the screen bits
  of its transparent pixels) and data), `apply(fb)`, and `source()`, which
  writes `src/master/mgun_tab.s` (870 B; compiled to code in step 7b).
- *6502*: `gun_draw` (bank 6, MB6C) walks `gun_tab` and writes each byte
  as (screen AND mask) OR data on its line, into the back
  buffer (DV_BACKHI) with ACCCON X; the driver calls it after
  `ENG_RENDER_FRAME`, before `flip_sched` (MASTER only). ~29.6K cycles a
  frame (~2%).
- *Room*: bank 6's texture region now ends at $9500 (B6CM starts there,
  $2400 long) -- the pixel-art textures need 1.3K of it.
- *Gate*: `test_master_gun` -- the table is what `master_gun.py`
  generates, and `gun_draw` on a random back buffer (both buffers) leaves
  exactly `master_gun.apply`'s bytes and touches nothing else; in
  `run_regression`. The jsbeeb disc test's hole check passes with it.
- *Note* (withdrawn 2026-10-06): this blamed `screenshotActive`'s
  resampling for view colours in the panel's first text row. They were
  real: the split was 5 lines late, hidden by a frame-buffer row offset
  error in the check (6c, fix).

**7c. Speed: the wall pair bodies' row mask. — DONE.** In the unrolled
wall pair bodies (`tv_*` / `sv_*`) the row mask `(th - 1) * 8` is now an
immediate (`RM_AND`: `AND #imm`, 48 sites) instead of `AND zw_rowm`.
`trun` compares the texture's mask with `rm_cur` and only on a change
calls `rm_patch` (bank 6: 48 unrolled `STA`s into HAZEL). And each v step
now ends with its row ready -- `STA zw_tvh / AND #m / TAY` (the shared
body: `zw_lvh`) -- so the pair reads that strip's texel first with no
reload; `tr_screen` does the same AND + TAY before jumping into a body.
Per pair: 94 -> 89 cycles (two strips), 58 -> 54 (shared v); HAZEL 54 B
smaller. 18 poses 23,034,317 -> 22,974,247 (-0.26%), byte-exact.
`tr_fetch` (odd first / last lines) keeps the zero-page mask.
Then the left row on the stack: the left strip's step ends `AND #m /
PHA`, the right's `AND #m / TAY`, and the fetch is `LDA (zw_tr),Y / LSR
A / PLY / ORA (zw_tl),Y` (no `zw_ev` temporary, no reload of the left v);
`tr_screen` pushes the first row before jumping in, so every PHA has its
PLY. 89 -> 85 cycles a pair, HAZEL 75 B smaller again; 18 poses
22,974,247 -> 22,939,163, byte-exact.
Then two more: the line-0 pair of each body stores `STA (PTR) / LDY #1 /
STA (PTR),Y` (13 cycles, not 16); and the left step has no CLC -- texels
use only the left pixel's bits ($AA, so bit 0 is 0: `test_master_assets`
checks every texel byte), so the previous pair's `LSR A` left carry
clear and nothing after it touches carry (the body starts are reached
only from the loop's JMP). Two strips 85 -> 83 cycles a pair (80 on line
0), shared v 54 -> 52 (49); 18 poses 22,939,163 -> 22,837,809,
byte-exact.
The span loop likewise: `sp_go2`'s U step ends `STA su / TAY`, and the
texel index is `AND #$F0 / ORA hi16,Y / TAX` (no `LDX su`); the entry
loads Y = U before `JMP (sf_ent,X)`. 59 -> 58 cycles a byte; 18 poses
22,837,809 -> 22,813,838, byte-exact. (`sl_go` keeps `LDX su`: its Y is
the screen column.) Its column-0 body then stores `STA (PTR) / LDY #1`
(3 cycles a block of four); 18 poses 22,813,838 -> 22,796,456,
byte-exact.

**7g. Fix: the right strip's step is exact again. — DONE.** Reported on
jsbeeb: the lower parts of close angled walls (step risers, lower
textures) combed -- in every byte the right pixel sat a row or so off the
left one. Cause: step 5g extrapolated the right strip's step from the
left strip's (stepL + ((stepL - stepL_prev) >> 1)); v is measured from
the wall's top line T, so the step's small error is multiplied by the
distance from T and shows on parts far below it. It predates 7d (the
5.3 delta carries the same start v), and the 18 gate poses have no such
wall. Measured over 40 random start-area poses as right pixels 2+ rows
off their neighbours' midpoint: the float reference ~0-1.5%, the exact
step close to it, the extrapolated step up to ~4x more (e.g. 70 -> 16,
23 -> 1 bytes). Now `sr = K // (Br - Tr)` always (model and 6502; the
7e reuse when Br - Tr = B - T still skips the division). Gate 94.82%
within one texel (94.79%); 18 poses 22,232,296 -> 22,398,078 (+0.75%),
byte-exact. Bank 6 code $9500-$B7FC.

**7ad. Strafing: Z and X. — DONE.** Z steps left and X right, with the
view held; with UP or DOWN they make the diagonals. The driver reads the
two keys (internal $61 and $42) into input bits 4 and 5 beside the
cursor keys. `colmap.walk_disp` takes the walk's direction from
`WALK_DIR`, indexed by forward | back << 1 | strafe left << 2 | strafe
right << 3: an offset on the 64-step angle grid (0 forward, 32 back, 16
left, 48 right, 8 / 56 / 24 / 40 the diagonals; opposed keys cancel), so
the frame's step is the unit at view + offset. Left is +angle, the same
sense as the LEFT turn key. One speed in every direction (DOOM strafes a
little slower and its diagonals are faster); collision, wall sliding and
the eye height (7z) are unchanged, since they act on whatever
displacement the frame makes. Back is now the unit at +32 rather than
the forward unit negated through `pm_bk`: on the sign-magnitude grid
these are bit-identical, at every angle and field count. On the 6502,
`pm_frame_i` looks the offset up in `PF_DIR` (16 B), adds it to
`DV_ANGIDX` before `pmf_unit`, and caches on the offset instead of the
key bits; `pm_bk` is gone. CODE +22 B, the driver +28 B (ends $1313).
*Gates*: `test_master_pmove.py`, now in `run_regression.py`, also runs
`pm_frame_i` up to the move for all 16 key sets x 64 angles x 10 field
counts, cache miss then hit, against `walk_disp`: 0 mismatches.
`test_master_disc.py` holds Z on jsbeeb and checks that the player moves
and the view doesn't turn. Engine cycles 27,577,771 -> 27,577,006 (code
moved; rebaselined).

**7ac. Music: the E1M1 tune on the SN76489. — DONE.** The disc plays
D_E1M1 ("At Doom's Gate") throughout, looping, from the beebtune BASIC
listing `music/e1m1.bas` (made by `music/beebtune.py` from DOOM1.WAD's lump
with its defaults: 3 voices + noise, 1,452 events in 34 blocks of 85 steps,
96 s a loop). `master_music.py` is the spec: `parse()` reads the listing
itself -- S%, K%, Q%, R%, the alphabet, the ENVELOPEs and the DATA (lengths,
play order, block texts) -- so a re-made or edited .bas plays with no code
change; `pack()` turns the blocks into byte tokens (173 distinct events in
a dictionary, 6 wait sizes, $FF a block end); `Player` is the 6502's
twin.

- *Timing* is the BASIC player's, exactly: W and Z in 1/256 cs (32 bits),
  a block starting at Z (Z += K% x S% a block, Z -= R% when the order
  wraps), each wait adding D x S% to W, a note sounding once TIME >= W DIV
  256 and never before the note ahead of it. TIME is the raster IRQ's 50 Hz
  tick (2 cs), so a note is due at tick ceil((W DIV 256) / 2).
  `basic_events()` runs the listing's own program line for line; the
  packed tune reproduces its first 4,000 SOUND commands (2.8 loops).
- *Sound* follows the MOS for what the tune uses: SOUND 1-3 a tone (pitch
  4 x note: equal-tempered from B2, period 125000 / f), length code 0 a
  silent note, else envelope 1; SOUND 0 noise, envelope note DIV 8 + 2,
  control note AND 7. Envelopes are amplitude-only with 1 cs steps
  (asserted): attack +AA to ALA, decay +AD down to ALD, sustain +AS, and
  release +AR to 0 once the duration (1/20 s units, 255 held) runs out;
  attenuation 15 - level / 8, written only when it changes. Two steps a
  tick.
- *6502* (`src/master/mmusic.s`, 1,201 B resident at main $0300-$07B0):
  `MUS_TICK`, called from `split_irq @top` (vsync), decodes tokens from the
  ring, starts the notes now due (a note not yet due waits in the ring),
  steps the four envelopes and writes the chip; `MUS_REFILL`, at the top
  of the driver's frame loop, copies tokens from bank 7 into the ring in
  play order; `MUS_INIT` (driver init, before the split's CLI) silences
  the chip and fills the ring. The ring is HAZEL $D700-$D7FF (a new
  `HZR` area; `HZM` ends at $D6FF), seen by both whatever ROMSEL holds:
  255 tokens are 8.6 s of the tune at its densest, against frames under
  1 s, so the IRQ never needs a bank. The resident image rides in bank 5
  at $B800 (after the textures) and the boot stub copies it down after
  the last OS call; the stream (play order, block addresses, 2,201 B of
  tokens; 2,337 B) is bank 7 $A600-$AF20, in the planes freed on
  2026-09-04, below COLIDX_BASE.
- *Port A*: the chip and the keyboard share the System VIA's port A, so
  the tick saves DDRA and the port, puts the keyboard on auto-scan (latch
  bit 3 high: off PA7) while it writes with DDRA $FF, pulses the write
  enable (latch bit 0) for 8 us a byte, and restores all three: a key scan
  interrupted between writing a key number and reading bit 7 reads the
  same key.
- *Cost* (py65): a tick 772 cycles on average (676 median, 2,325 worst),
  under 2% of the CPU; a refill 507 cycles (1,532 worst). Engine cycles
  unchanged (the filler's HAZEL code moved by the IRQ's 3-byte JSR:
  27,577,867 -> 27,577,771 over 24 poses, rebaselined).
- *Gates*: `test_master_music.py` (in `run_regression.py`) runs the
  resident player in py65 against `Player` for 11,000 ticks (2.3 loops)
  with refills at random frame gaps, some long enough to drain the ring:
  every tick's chip bytes equal, the port, DDRA and latch as the key scan
  left them, chip writes only with DDRA $FF and the keyboard off PA7; and
  the BASIC check above. `test_master_disc.py` captures every byte jsbeeb's
  SN76489 receives (through the VIA and latch) while the engine renders,
  turns and walks: 1,028 ticks (20.6 s), 1,668 bytes, all as `Player`.

**7ab. The gun overlay: TRB / TSB and one-byte value steps. — DONE.** The
compiled overlay (`master_gun.source`) loaded each opaque value with LDA #
(23 loads, ascending) and read-modify-wrote every edge byte (LDA abs, AND,
ORA, STA: 10 B, 12 cycles). Now the 65C02 does the edge bytes in place:
TRB abs with A = ~mask clears the gun's pixel (two masks, $55 then ASL A to
$AA), and after every TRB, TSB abs ORs the art in, grouped with the opaque
STAs of the same value (no TSB for black data); opaque black is STZ. The
value groups are ordered (`_order`) so that each load is a one-byte INC /
DEC / ASL / LSR A from the last where one exists: 9 LDA # per routine
(was 23 + 44 edge loads). Each routine 1,402 -> 1,195 B (-207); the overlay
1,846 -> 1,776 cycles. gun_b0 leaves 341 B of shadow $3000-$35FF free (134
before); bank 6 code ends at $B2BA. Byte-exact (test_master_gun).

**7aa. The panel split: black before Mode 1. — DONE.** A red sliver
flickered at the left of line 136, the panel's all-black top line, in every
field (4-20 emulated pixels as the IRQ jitters 2-4 characters). The handler
switched the video ULA to Mode 1 at the end of line 135 and only then
blacked palette entries 1, 4 and 5; in Mode 1 zero bytes show through
entries 0 1 4 5, and entry 1 still held the view's red until its write
landed at line 136, characters 1-5. Now the three black writes come first,
in line 135's blank (its visible part ends at character 64; they land at
123-127 on), then Mode 1 (136:13-17): line 136 opens in Mode 2, where zero
bytes are entry 0, black, and stays black through the switch. The write
count is unchanged, so the panel's last entry still lands by 136:83, long
before line 137. jsbeeb, 1,500 fields turning and walking: boundary lines
136-140 wrong in 0 fields (1,500 before). The disc gate now fails on any
colour on line 136 (its Mode 2 check ignored red) and on a black write
before line 135's visible part ends.

**7z. Movement: the eye height from four half-box probes. — DONE.** Walking
into room 24's narrow stair flight (west of the start: a 64-unit strip of
16-unit steps with room floor either side) off its centre line, the player
stuck at the first step -- long-standing, the build before 7x does the
same. DOOM stands the player on the highest floor under the whole 32-unit
box (tmfloorz); `pmove` used the centre's subsector, so with the box over
step 1 but the centre still over the room the eye stayed at room height,
and the next move met step 2's 32-unit side from too low. Now the eye
height is the max over the centre and four probes 12 units out on both
axes (`colmap.dest_check` CORNER_OFFS; `pm_corners`, 253 B at main
$6D38, segment PMCOR in the clipper's CBITS slack; scratch PM_SCRATCH+$A0):
a probe over a lift takes its live floor + 5, anything else its static
SS_VZ; a door's passability stays the centre's test. 12 is Eben's middle
ground: +-8 left a stuck band 6-14 units beside the flight, the full 16
removes it but lifts a player walking right alongside; 12 leaves a 3-unit
band (13-15 units out). The fast commit (box crosses no sector line) stays
valid: the probes are inside the box. Gates: `test_master_pmove.py` (new)
locksteps pmove_try against colmap.try_move -- 800 random moves, room 24
and the playable area, and around lift 59 at four heights: 0 mismatches
(20 with the model's probes off, so it sees them). jsbeeb: the four
off-centre approaches that stuck now climb or slide off the flight's side;
the straight climb and the helmet alcove are unchanged. Render cycles
unchanged (-0.01%, code shifted).

**7y. Floor and ceiling boundaries at equal heights. — DONE.** With the
nukage room's lift (sector 59) lowered to -48, level with the nukage
(NUKAGE3), its floor (FLOOR4_8) painted over the nearer nukage floor. Two
faults, both in shared code, the model and the 6502 alike:
- *No boundary.* A two-sided seg closed the floor at its own front line
  only for a drop (SF_STEPUP_B, back floor lower) and the ceiling only for
  a rise (SF_STEPUP_T): an equal height drew nothing in the wireframe
  engine, so the clipper left the opening alone and the farther sector's
  plane filled the nearer one. Bits 1/0 of the seg flags (the retired
  APEDGE1/2, shipped 0) now mark a floor / ceiling FLAT change across the
  seg (SF_FLATDIFF_F / _C, the packer), and STEPUP_B / _T is also set at an
  equal height across a flat change -- in the packer, the Python mover
  patcher and the 6502 anim worker (`anim.s`: the equal arm tests the bit;
  the worker's mask already kept bits 1/0). The cascade (Python) now
  dispatches on the flags as `seg_emit.s` always did.
- *The bottom line read with the top's anchor.* Since 2026-08-22 the span
  pool keeps an anchor per side (POOL_TXLO/TDEN, POOL_BXLO/BDEN), but the
  filler's snapshot (`mf_snap`, `sn_bxlo` / `sn_bden` new) and its OB / NB
  steppers, and the model's span reader, used the top's for both. Harmless
  while one seg wrote both lines or the top was flat; a bottom-only fused
  edge (as above) was misplaced. `span_clip_6502.read_spans` returns
  `Span` tuples carrying (bxlo, bxhi), `endpoint_spans._span_bot` uses
  them, and the engine's clip wrapper adopts the pool's spans after a
  mark_solid it agrees with (its own copies dropped the anchor).
Checked: the 6502 anim worker's flags equal the Python patcher's for both
lifts, half-way and at the bottom; with lift 59 lowered the 6502 renders 4
poses in the nukage room byte for byte as the model, and its floor edge
follows the float reference's. Agreement over the 24 poses: surfaces
95.03% -> 95.43%; walls 94.74% -> 94.50% within one texel over 784 more
wall cells (lower walls the floor used to cover), ~480 more cells right.
24 poses 27,413,592 -> 27,604,983 (+0.70%: more boundaries closed).

**7x. The start room's window ledges raised to 13 wu. — DONE.** Close to
the two windows by the thin STARTAN3 pillar, the 24 wu STARTAN3 sill under
each window was missing: the ledge's floor showed down to the walkway's.
Not the texturer (6502 = model): the engine's height quantum is 6.67 wu
(PRESCALE 8 with the 1.2 aspect baked in, `_prescale_height`), and from the
walkway (sector 39, floor -16) the eye, 25, is q4 = 26.7 while the ledges'
floor, 8, is q1 = 6.7 -- 20 wu below the eye, not 17. Close up only the
sill's top shows, as a wedge at the bottom of the view, and 3 wu there is
10-18 lines: the wedge fell off the view and the ledge floor behind took
its place. A `_STEP_EVEN` override (e1m1.py) puts sectors 14 and 15 at
13 wu = q2 (13.3): within ~1.3 wu of the engine from the walkway and from
the room (eye 41 = q6). The ledges are behind impassable window lines, so
no step rule applies; the sill is 29 wu where DOOM's is 24. Agreement with
the float reference: walls 94.79% within one texel (94.77%), surfaces
95.85% (95.81%). (1300, -3240, 208) joined the regression set; the engine
golden re-recorded (35 poses). 23 poses 25,785,136 -> 25,833,423 (+0.19%:
more sill drawn); 24 poses 27,413,592.

**7w. One control panel for both buffers; `gun_b0` in shadow RAM. — DONE.**
Each 10K buffer kept its own copy of the 1.5K panel (character rows 17-19).
Now there is one, at shadow $5800-$5DFF: buffer 0 moved to $3600 (view
$3600-$57FF, then straight into the panel) and buffer 1 to $5E00 (view
$5E00-$7FFF); with the System VIA latch's screen size at 10K (B4 = B5 = 1,
set in `cur_park`; MODE 130 leaves 20K) the CRTC wraps addresses past $7FFF
by -$2800, so buffer 1's rows 17-19 show $5800 too. Both bases stay
page-aligned and every row keeps its offset, so the flip (R12/R13 from the
base) and the fill are unchanged but for `MSCREEN0`/`MSCREEN1` (new: `MPANEL`,
`MGUN0`). That frees shadow $3000-$35FF, reachable only with ACCCON X set --
exactly when the gun overlay runs, so `gun_b0` (1,402 B, buffer 0's
straight-line body) now lives there (ld65 area `GUNM`, file `MGUN`; the rig
and the loader put it in shadow, never main RAM) and `gun_draw` (bank 6)
calls it with X set; `gun_b1` stays in bank 6. Bank 6 code ends at $B35F
(1,440 B free, was 38). The loader parks MPANEL at $7A00 and MGUN at $7400
(clear of the parked HAZEL and ANDY blocks) and its page-9 stub copies them
to $5800 and $3000 last (`s_copy`). Gates: the gun byte-exact into both
buffers (and `gun_b0` intact); jsbeeb boots the disc with the panel right
in 50 of 50 fields across both buffers, no holes, the split on time. The
step-1 display demo (`mdisplay.s`) keeps its own $3000 / $5800 buffers.
Cycles: 23 poses 25,793,616 -> 25,785,136 (bank 6 code moved).

**7v. The run's step from a reciprocal table. — DONE.** `tv_divm`'s
K // (B - T) was a 16-bit-quotient shift-subtract per run (~340 cycles for
B - T <= 128 after 7u, ~530 to 255). Every E1M1 K but two is an 8-bit
number of texel rows shifted left, so K is now kept to 8 significant bits,
K = m << z (128 <= m <= 255; `master_walls.step_mz`: COMPUTE2's 4/7 and
NUKE24's 2/3 scales move -0.2%, every other part is unchanged), and for
h = B - T <= 255
    step = (m * RT_z[h] + 128) >> 8,  RT_z[h] = (2^(8+z) + h // 2) // h
-- byte-aligned, two quarter-square 8x8s, no shifts. One 16-bit table per
exponent z in the level (E1M1: z = 7..12, six), RT 0 where it would not fit
16 bits (h <= 2^(z-8)): that h, and h > 255, divide K // h exactly as
before (so 7s's fine first v, h >= 512, is untouched). Model:
`master_walls.wall_step` (tex_ref's `_step`); the prototype's per-h
normalised table with a variable shift was dropped for this. Memory: the
tables (`mb6_rc`, 2 pages per z, room for 7) and per part `mb6_pt_m` and
`mb6_pt_rp` (its z's table page) fill bank 6 $8000-$8F3F, free since 7m
(ld65 area `B6RM`; ANDY covers it only during `tx_seg`'s dressing look-ups,
never in a wall run); master_assets' bank 6 region is now empty. The
inline 8x8s grew `tv_divm`: its divide exits sit ahead of it (branch
range) and `d8_fast` moved to MARITH (main RAM, beside `div32`, its only
caller's caller) to keep bank 6 code below $B900. Six `ST_*` stepper
offsets moved above their first use (ca65 had fallen back to absolute
addressing for them). Wall cells vs the float reference 94.77% within one
texel (94.76%), exact 80.02% (80.22%). The window view 1,933,865 ->
1,890,181 (-2.3%); 23 poses 26,170,363 -> 25,793,616 (-1.44%). Bank 6
code $9000-$B8D9 (38 B free); MARITH $7E20-$7FA2 (93 B free).

**7u. The run's step: the byte steps direct. — DONE.** Profiling the
slowest view found so far (just inside the four red pillars by the start,
looking out of the window to the slime-pool courtyard; (1000,-3350,16),
1,946,554 cycles, 53 fields) put `tv_divm` (the run's step K // (B - T))
at 104K over 224 calls, about 430 cycles each when B - T <= 128. Only
~265 of those are the 16 quotient bits (`d8_fast`, twice); the rest was
`div32`'s dispatch and `dv8f`'s shortcut tests. A reciprocal table does
not pay here: an exact K (20 bits) * 1/h needs 4-6 quarter-square 8x8s
plus a q * h correction (K // h exactly; `tv_fine` relies on the
remainder), ~220-300 cycles, no better than the shift-subtract. Instead,
when 0 < B - T <= 128 and K's top byte is below it (quotient < 2^16),
`tv_divm` calls `d8_fast` itself on K's bytes (the low byte parked in
`t_step`); anything else falls back to `div32` as before. Exact, no model
change. The window view 1,946,554 -> 1,933,865 (-0.65%); 23 poses
26,274,999 -> 26,170,363 (-0.40%). Bank 6 code $9000-$B89F (96 B free).

**7t. The right strip's delta: 8 fraction bits. — DONE.** Step 7d keeps a
non-shared run's right strip as a 5.3 delta from the left v, exact at the
run's first line and stepped by ddh once per character row; ddh was the
steps' 8-line difference rounded to 5.3, up to half a unit (1/16 texel)
off at every character row. A run clipped at the top of the view crosses
up to 17 rows, so at an angle the right strip drifted up to a texel by the
bottom: the same STARTAN3 wall's slanted row edges broke into teeth that
grew down the wall (registration good at the top). ddh now keeps its 8
fraction bits (`tr_ddh`: 4 * (pair stepR - stepL), 16 bits) and the delta a
fraction byte (`zw_dl`, starting at $80 so it rounds once); every
character-row step is a 16-bit add (six sites in the wall loop; `zw_dl`,
`zw_ddl` in HAZEL BSS). Model: dh(y) = hi((dh0 << 8) + $80 + ddh * rows).
Byte-exact; +0.25% cycles. (1337.5,-3193,229) joined the regression set.

**7s. Near walls: the first v with 8 more bits of step. — DONE.** Right
beside a wall (the thin STARTAN3 wall before the slime pool, seen at any
angle) B - T runs to thousands of lines and the step K // (B - T) to a few
units of 5.11, so its truncation is up to ~25%; a run's first v, Vtop +
(ys - T) * step with ys - T over a thousand lines, then carried that into
a row or more of error -- different in every column, so texture rows
broke into teeth. A run starting 512 or more lines below T now adds
((ys - T) * f) >> 8, f = ((K mod (B - T)) << 8) // (B - T), the step's
next 8 bits: under (ys - T) / 256 units of error (< 0.01 texel); the
stepping from there (at most 68 pairs) adds < 0.07 texel.
- *6502*: `tv_v0` takes the branch on ys - T >= 512 and B > T; `tv_fine`
  recomputes r = K - step * (B - T) (mod 2^16: the step may come from the
  band's or the left strip's cache, with no remainder to hand), runs 8
  restoring division steps for f and adds (ys - T) * f >> 8. The model
  (`tex_ref._v`, with the run's first line ys) is the same integers.
- Byte-exact; ~0.1% (corridor 0.3%). At 256 lines the same fix cost the
  corridor pose 5.6% with no visible gain. Two poses in front of the wall
  joined the regression set ((1300,-3232,0) takes the new path in 94 runs).

**7r. The flush's set-up: sweep, screen pointer, partial lines. — DONE.**
Three exact changes from the flush profile (open items):
- *Sweep* (`mk_spans`): the kind's interval arrays are patched into the
  reads once (floor `pe_ft` / `pe_fb` or ceiling `pe_ct` / `pe_cb`; the
  four share a page, asserted) instead of testing the kind per column; the
  column lives in X and an unchanged interval costs one INX / CPX / two
  reads and compares; a change runs `mk_step` (the old close / open
  loops as a subroutine). 128 -> 90 cycles a column swept: the close /
  open work (`mk_step`, ~12.7K a frame) is now most of it.
- *Screen pointer* (`sp_setup`): PTR hi = (line >> 3) * 2 + back buffer,
  with kb >> 5 as the carry of kb >= 32; no shift chain. 248 -> 227 a
  span with `uvat`.
- *Partial lines* (`pp_slot`): the scan keeps base | k in X against the
  last column base | k1 (no per-column reload, no wrap at slot 3's end).
  14.4K -> 8.9K a frame.
- *Result*: flushes 20.6% -> 19.3% of the frame (24.2K -> 22.3K each);
  24,073,671 -> 23,712,325 over the 20 poses (-1.5%).

**7q. `pl_row`: D patched once. — DONE.** Its two products share D:
`mul8x32` now falls into `m8_run` (pl_q = pl_e * the patched multiplier)
after patching, and the sin product calls `m8_run` directly, reusing the
cos product's patch. Exact; 24,131,421 -> 24,073,671 (-0.24%).

**7p. `uvat`: one multiplier, two products. — DONE.** A span's first
U, V are Uc + a * dU and Vc + a * dV (mod 256) with the same a = kb - 32,
and only the low bytes count. `uvat` now patches a into the quarter-
square reads once (as `mul8x32`: f(e + a) at SQR_LO + a, f(|e - a|) at
SQR_LO - a via the mirror page) and takes each product's low byte with
one LDA / SBC pair -- no `mf_mul8` calls (removed: no other caller).
~200 -> ~75 cycles a call. Exact; 24,347,419 -> 24,131,421 (-0.9%).
Other shared operands checked: `pl_row`'s D * ZC and D * ZS (step 7q,
below), `mul16`'s own pairs (two products per patch: no gain),
`tx_seg`'s dL * den0 and dL * A (per seg: small).

**7o. Row maths: skip far rows, fold the signs. — DONE.** The plane
flush measurement (open items) put `pl_row` at ~1.3K per row and height,
29% of the flushes: two 8x32 multiplies, then shifts, negation and
rounding around them. Two exact changes (the gates are byte-identical):
- *Far rows*: `pl_row` tests FAR_DM first and stamps the row (epoch, D,
  level) before any maths; a far row returns there -- its U, V are never
  read (`sp_setup` and `pl_cell` take the far tone) -- and skips this
  frame's `pl_zrow` for its j too. 18% of the row computes.
- *Signs folded into the sums*: Uc = Up +- A, Vc = Vp +- hV, Uc +- hU,
  Vc -+ Bs by add or subtract instead of negating A and h first
  (`neg_ah` gone); A and Bs read straight from the product (pl_q+1/+2),
  h = Pc >> 14 by `pl_hq`, dU / dV rounded by `pl_dh` / `pl_dhn`
  (`q_a_h` gone; the helpers in MARITH, main RAM).
- *Result*: 24,764,497 -> 24,347,419 over the 20 poses (-1.7%); the open
  areas most (1500,-3700,0 -4.1%, 3648,-4800,131 -5.0%), the start room
  -1.9 to -2.5%.

**7n. Floors and ceilings: a far tone. — DONE.** Past a few lines below
the horizon a plane byte covers many flat texels (64 D / k^1.5 of them,
the geometric mean of 8D/k across and 512D/k^2 into the view: at the
eye's D = 6, 4 texels at k = 21, 8 at k = 13), so the 16x16 flats alias
into noise there. There a line pair now draws its flat as ONE tone,
chosen by hand in `art/flats/far.txt` (seeded from each flat's mean
colour).
- *Tried first*: a hand-drawable 4x4 middle level (16 flats interleaved
  per page, read as (V & $C0) | (U >> 2 & $30) | slot by separate span
  loops; byte-exact, +0.31% cycles). It collapsed to mush while the
  16x16 flats still held up, so it was dropped for this.
- *Rule*: a line pair is far iff 4096 D^2 >= FAR_T^2 k^3, i.e. D >=
  FAR_DM[k >> 1] (`master_assets.FAR_T` = 4, `far_dm()`). `pl_row`
  stores it per row in `pc_lv` (HAZEL), once per row per frame.
- *6502*: `sp_setup` skips U, V (`uvat`) on a far row and returns C = 1
  with `sm_b` = `far_tone[f]`; `mk_close` / `sl_draw` then run `sm_go2`
  / `sml_go` (bank 6): the byte on the even line and FLIP of it on the
  odd, no texel reads. `sm_go2` copies both bytes to zero page (su, sv:
  a far row has no U, V) and is Duff's device like `sp_go2`: four bodies
  of LDA zp / STA (PTR),Y / INY / LDA zp / STA (PTR),Y at fixed offsets,
  22 cycles a byte column. (Tried: one page of 64 unrolled LDY #n /
  STA (ptr1|ptr2),Y entered at the first column with an RTS patched after
  the last -- 16 cycles a column, but far spans are short, ~27 a frame,
  and its set-up made it slower overall.) `pl_cell` returns the tone
  directly.
  `src/master/mfar_tab.s` (FAR_DM + the 21 tone bytes) is generated by
  `master_assets.py`; the assets gate fails when it is stale.
- *Model*: `plane_ref` returns the far tone byte for such rows; the gates
  stay byte-exact.
- *Cost*: -0.80% over the 20 poses (24,962,540 -> 24,764,497); 55 B of
  tables and ~150 B of code in bank 6, 68 B of HAZEL.
- *Choosing*: `python3 tools/flat_far_preview.py` rebuilds the assets and
  writes `build/master/far_preview.png`: every flat beside its far tone,
  and poses without / with it (`--t` previews another FAR_T, `--poses`
  picks poses). Then `python3 master_assets.py` refreshes `mfar_tab.s`.

**7m. Texture art: fewer unique columns. — DONE.** Columns are
deduplicated within a texture, so art that repeats costs nothing extra.
Six 32-row textures (and NUKE24) were redrawn to repeat (`art/walls/*.txt`, no code
or format change):
- *BIGDOOR2, BIGDOOR4*: mirrored about column 15 (column 31 stays the
  black seam): a centre split, 27 -> 16 columns each.
- *COMPTALL*: one 32-wide module shown twice: top, the small panel, the
  green circuit panel and the small slotted panel; bottom, the circuit
  panel and the blinking-lamp panel. 55 -> 30 columns.
- *COMPUTE2*: the lamp, light and keyboard rows repeat every 12 columns
  across the bank (the yellow box and the odd red lamp became lights; the
  window strip runs the width). 32 -> 21 columns.
- *EXITDOOR*: the machine mirrored about column 14; the tube bands sit
  under the tubes. 28 -> 22 columns.
- *PLANET1*: its first 16-column bank is a copy of the third (banks 2
  and 4 kept; bank 4's strips were already one column). 41 -> 30 columns.
- *NUKE24* (the slime-pool edge, little seen): mirrored about column 21,
  two near-identical column pairs merged; 24 -> 12 columns (its stacked
  partner STEP6 needs 5), -384 B.
- *Result*: 367 -> 280 stored columns, 11,744 -> 8,960 B (-2,784 B). All
  21 flats now fit bank 5 ($8000-$B7FF), so bank 6 $8000-$8FFF holds no
  textures. `master_assets` now writes every region's bank image, empty
  ones too (the rig reads both). Cycle totals unchanged.
- Considered, not done: storing the vertically periodic textures
  (COMPTILE, LITE3, SLADWALL, SUPPORT2, DOORTRAK) as 16 rows needs the v
  scale split from the row mask, and they total only 18 columns (576 B).

**7l. The Master-only tree (branch `master-only`). — DONE.** The Model B
build, its wireframe renderer and every Model B gate are excised; what is
left builds only the Master disc. The invariant throughout: the Master
engine binaries (`engine_*_m.bin`) are byte-identical, and the disc differs
only where noted below.
- *Assembly.* Every conditional on the build flags (BANKED, MASTER, C02,
  RASTERHW) is resolved for the Master (BANKED=1, MASTER=1, C02=1,
  RASTERHW=0) and the dead arms deleted; `src/boot/`, `src/raster.s`
  (the NJ rasteriser), `src/hud.s` (the Model B HUD) and the flat and
  banked ld65 configs are gone. `tools/gen_abi.py` emits the Master values
  only (`abi.py` lost its `_FLAT` twins). The emit stubs (plot_h, plot_v,
  RASTER_ENTRY, the frame-buffer clears) stay: the rig traps them.
- *Python.* `asmbuild` and `symmap` know one link (`engine_m.map` /
  `.dbg`); the rig is `banked_bsp.MasterBspRender` (the Model B
  `BankedBspRender` and the flat rig are folded away), always on py65's
  65C02 core. Its bank images are built from their Python sources (angle
  tables, bbox, recips), not copied out of a flat image. The Python
  reference traversal (`e1m1.py`, was `doom_wireframe.py`; its pygame
  wireframe game and debug renderers removed) drives the Master clipper
  instead of Model B's: `test_master_tex` now finds 2 reference-gap segs,
  not 4. `poses.py` holds the pose suite (from `compare_renders.py`).
- *Gates.* `run_regression.py` runs the nine Master gates plus
  `bakedscan`, then the frame-cycle gate on `poses.POSITIONS` against a
  Master `baseline.json` (24,962,540 cycles); `--disc` adds the jsbeeb
  boot. `test_master_engine` compares against recorded line lists. The
  whole run takes under a minute (it was about five).
- *A bug the old rig hid.* The mover jamb patch pointers in TABL0 (bank 4
  $BA7C-$BA9F) came from the Model B banked map: $A03F, $A0AB, $A0AC --
  Model B's bank-C VEXPL. On the Master VEXPL is in main RAM ($7800 /
  $7880), and $A0xx with bank 6 paged is the fill's cold code (`gun_b1` in
  the 7k build), so a moving door or lift jamb wrote its height into code
  and never moved its vertical span. The Master map gives $783F, $78AB,
  $78AC; `anim_sectors.gen_6502_tables` now asserts every jamb target lies
  in VEXPL. This is one of the two disc differences; the other is the free
  HAZEL tail $D648-$D7FF, which shipped stale flat-image bytes and now
  ships zeros.
- *No second processor.* The Tube build (the flat engine on a 6502
  second processor, with host-side emitters) is gone with it: its sources
  and gates went in the excision, and its remains after it -- the
  `BANKCHOST` segment, `bakedscan`'s Tube scan, and the comments and
  generated ABI notes that still described the flat / parasite map -- on
  2026-10-08. The Master build is the only one.
- *Left for later.* `clip/plot_axis.s` still assembles the Model B
  plotters' 24 bytes of edge masks (dead; kept so the binaries stayed
  identical), and many comments in the engine sources still tell Model B
  history.

**7k. Set-up: geometry-heavy views (the start position). — DONE.**
Profiled at the start (1056, -3616): the spawn view (angle $80, 838K
cycles) and the room it turns to (angle 64, 1,776K, 32 segs), where
per-span and per-seg set-up outweigh the inner loops. All exact, no model
change:
- *Partial-line slots.* `pl_part` marks its slot used (`pp_used`); a
  kind's two slots are marked empty when its pending range opens, and
  `pp_slot` skips a slot with nothing recorded instead of scanning every
  pending column. `pe_clr` tests the kind once, not per column.
- *mul16 inline.* The four quarter-square 8x8s are inline (`QMUL`: no
  call, no staging through `mq_b` / `mq_t`); moved from MARITH to bank 6
  (all 11 callers are bank-6 code). 217 -> 185 cycles a call.
- *at: one multiply fewer.* (d1 a + d2 b) / (a + b) = d1 + (d2 - d1) b /
  (a + b), so the floor is d1 + floor((d2 - d1) b / den), and for d2 < d1
  d1 - ceil((d1 - d2) b / den), the ceiling as (P + den - 1) / den. The
  dividend's hi word stays below den, so divq16 still applies. 2,285 ->
  1,819 cycles a call.
- *mk_spans.* A column whose pair interval equals the last one's closes
  and opens nothing: straight to the next column.
1056,-3616,64 1,776,391 -> 1,688,653 (-4.9%); 1056,-3616,128 838,076 ->
810,074. 20 poses 25,937,187 -> 24,962,540 (-3.8%). Bank 6 code
$9000-$B687 (632 B free); MARITH $7E20-$7F45.

**7j. Set-up: trun without the copying. — DONE.** trun spent ~495
cycles a run, most of it moving values between variables. Now:
- the part's K and Vtop are read from its record (`mb6_pt_k*`,
  `mb6_pt_v*`, indexed by `t_part`) by `tv_divm` / `tv_v0`, not copied
  into `t_kk` / `t_vt` (both gone);
- the run's B - T goes straight into the divisor `m_b` as it is made (the
  left strip with `t_hl` / `t_hh`, the right with its 7e compare), so the
  divide no longer subtracts again: `tv_div` became `tv_divm`; only T is
  copied to `q_t` (`q_b` is gone; it was a 4-byte loop);
- the shared-v path no longer copies `l_v` / `l_step` back into `t_v` /
  `t_step`: they still hold the left strip's;
- the 7e step cache is written only on a miss (`ss_x` on every run);
- `tcol` writes the texel column straight into `tw_ll`/`tw_lh` (Y = 0) or
  `tw_rl`/`tw_rh` (Y = 2) and folds the masks into the add (`t_cl` /
  `t_ch` gone).
trun 495 -> 338 cycles a run (1056,-3616,32: 58,882 -> 40,170). 20 poses
26,355,653 -> 25,937,187 (-1.6%); 1056,-3616,32 1,676,251 -> 1,654,363,
1792,-3351,108 1,361,790 -> 1,337,743, 2500,-2600,67 884,409 -> 867,280.
Byte-exact (no model change). Bank 6 code $9000-$B4F2 (1,037 B free).

**7i. Set-up: the edge steppers and the normalise loops. — DONE.**
Profiled by routine: the span-edge steppers (`next_col`, `adv`,
`st_val`, `st_peek`, `clamp_ln`) were 10-13% of a frame, and two
per-seg loops shifted 32-40-bit weights one bit at a time. All exact: no
model change, `MASTERTEX` byte-for-byte as before.
- *Biased remainder.* A stepper keeps rb = r - W + 2^16, so the wrap test
  r + R >= W is the carry out of rb + R (no compare); the wrap then does
  rb -= W (borrow out: C = 0) and y += Qs1, the step +/- 1 precomputed
  (fields 2/3, the old unused q). `ST_STEP` 71 / 119 cycles (no wrap /
  wrap) -> 55 / 73.
- *8-bit span-edge steppers.* OT, OB, NT, NB have W = the span's u8
  denominator and are read as a byte: `ST_STEP8` moves only the low bytes
  (rb = r - W + 2^8), 31 / 33 cycles.
- *No read correction.* A negative slope starts from |D| k + W - 1, the
  ceiling, so y is read as stored: `st_val` is gone and `adv` reads
  `st_f` directly (844 calls a frame on the floor-heavy pose); `st_peek`
  is a carry test and one add.
- *Inline clamps.* T and B's screen clamps are inline (`clamp_ln` gone).
- *Byte-at-a-time normalise.* `tx_seg`'s A, B weights (until both fit a
  byte) and `at`'s a, b (until both < 2^15) shift a whole byte while
  either is >= 2^16 (resp. 2^23): at least 9 more single shifts are due
  then, so the result is the same.
- *Memory.* `tx_seg` moved from MFILLM (main $6D38, 4 B free) to bank 6,
  and bank 6's code region now starts at $9000 (was $9500; the texels end
  at $8300, `master_assets` packs below $9000). $9000+ is above ANDY, so
  `tx_seg` runs with ANDY paged. Main $6D38-$6FFF (712 B) is free.
20 poses 27,828,978 -> 26,355,653 (-5.3%); 1056,-3616,32 1,775,380 ->
1,676,251, 1792,-3351,108 1,447,466 -> 1,361,790, 2500,-2600,67 929,579 ->
884,409. Bank 6 code $9000-$B540 (959 B free).

**7h. Maths: div32 picks a faster byte path by itself. — DONE.** The wall
step divisions (K // h, left strip and, since 7g, right) take div32's
8-bit-divisor path, ~500 cycles a call. div32 already chose its path per
call (8-bit, 16-bit, slow); now an 8-bit divisor <= 128 (a wall under 128
lines -- most) goes to `dv8f` (bank 6): the remainder stays below 128, so
each quotient bit is `ROL A / CMP / BCC / SBC / ROL` with no overflow test,
the quotient shifted in from the carry, unrolled (`d8_fast`) in the
set-up-only zero page (`dq_d0`, `dq_b0`); the zero-remainder byte skip is
kept. Mean 321 cycles a call for divisors <= 128. `test_master_div`
(new, in `run_regression`) runs 5,090 divisions on the 6502 -- every 8-bit
divisor with edge and random dividends, 16-bit divisors, quotients >= 2^16
-- against Python; routing divisors to 254 into the fast path fails it.
20 poses 28,040,724 -> 27,828,978 (-0.76%), byte-exact (no model change).
Bank 6 code $9500-$B895 (106 B free).

**Gate: reference gaps from the opening's lines (2026-10-07).** Two
start-area poses joined `compare_renders.POSITIONS` (now `poses.POSITIONS`): (1046.7, -3090.4,
157), the wall that combed (7g), and (1144.6, -3342.5, 153). At the
latter the 6502 drew 15 bytes of floor where `tex_ref` drew seg 156's
STEP6 riser. Not a 6502 fault: the float reference has floor there too.
The model takes its span pool from the Python traversal, which puts the
step's top edge (the opening's bottom line) on screen down to x 168; the
Master engine has it off screen from x 180. `test_master_tex` only called
a seg a reference gap when its FRONT lines differed. Now:
- a seg is also a gap when its span-diff bands at any column differ from
  the model's (`tex_ref` keeps `bands[si][x]`; the test reads the
  engine's at `band`), which carries the opening's lines;
- a differing byte is excused when a gap seg owns one of its differing
  pixels in the model OR in the engine (the engine's owners from those
  bands): a gap seg's extent one line out claims a neighbour's cell (the
  comb pose's one byte, seg 613 against 441);
- the gap-seg cap is 4 over the suite (4 now: 508, 613, 156, 311);
- the pixel masks are Mode 2's ($AA / $55; they were Mode 1's).
`MASTERTEX` passes on all 20 poses.

**7f. Maths: the 16-step divide unrolled. — DONE.** `dq_core` (the
quotient < 2^16 divide behind `divq16` -- the per-byte wall d -- and
`div32`'s 16-bit-divisor path) moved from the full main-RAM arithmetic
area to bank 6: every caller has bank 6 paged (they are all bank-6
set-up code, and `div32` already reached `d8_byte` there). Unrolled 16
times (`.repeat` / `.scope`): no counter, and no patched immediates (16
copies would need 64 patches a call), so the divisor, the dividend's lo
word and the remainder's lo byte are copied into zero page the wall and
span loops own (`zw_*`, dead in set-up code, which is the only caller):
5-cycle shifts instead of 6. `dq_set` is gone. Bank 6 code +573 B (now
$9500-$B840, 191 B free), ARITHM 74 B freer. 18 poses 22,452,010 ->
22,232,296, byte-exact (no model change); the jsbeeb disc test drew 35
frames in its run (32 before).

**7e. Maths: the per-run wall step, cached. — DONE.** Profiled by
caller first: the multiplies and divides are 21-25% of a frame (the
floor inner loop only 6-11%). The biggest exact saving: a run's left
step K // (B - T) is the same as the band's last run in this seg when the
part and B - T match (K is the part's; ss_* reset per seg, so a mover's
new K never meets an old step) -- 42% of left steps over the 18 poses.
And the right strip's exact fallback (no previous byte to extrapolate
from) reuses the left step when Br - Tr = B - T. `ss_hl`/`ss_hh` keep
each band's last B - T. 18 poses 22,688,518 -> 22,452,010, byte-exact
(no model change). Tried and rejected: the right strip's d fallback as
the left strip's d (no division) -- 93.05% within one texel, under the
gate's 94.5%.

**7d. Speed: the right strip as a stepped 5.3 delta. — DONE.** A wall
run that does not share one v (step 5n) used to step both strips' 5.11 v.
Now only the left v is stepped; the right strip's row is the left v's
high byte plus `zw_dh`, a one-byte 5.3 delta that is exact at the run's
first line ys and itself steps by `zw_ddh` once per character row (4 wall
pixels), so the right strip follows the slope difference down the run
(close-up angled walls stay right at the bottom, not only the top):
    dh0 = (vR(ys) >> 8) - (vL(ys) >> 8)                 (mod 256)
    ddh = (stepR - stepL + 16) >> 5     (line steps: 8 lines' worth, 5.3)
    dh(y) = dh0 + ddh * (y // 8 - ys // 8)               (mod 256)
    right row = ((vL >> 8) + dh(y)) mod 256 >> 3, & (th - 1)
- *6502*: the right v's own step is replaced by `LDA zw_lvh / CLC / ADC
  zw_dh / AND #m / TAY`; the step of each line-0 pair (a new row) first
  does `LDA zw_dh / CLC / ADC zw_ddh / STA zw_dh / CLC / ADC zw_lvh`, and
  the two other row crossings (an odd first line 7, `tb_end`'s last line)
  add it inline. `tr_screen` makes dh from `t_v`, `l_v` (shared: t_v =
  l_v, so 0); `tr_ddh` (bank 6, own-v runs only, after `tv_v0`) makes ddh
  from the pair steps, `(t_step - l_step + 32) >> 6`; shared runs get 0.
  `zw_ddh` is `zp_vs_cch` (the old `zw_tvh`).
- *Cost*: two strips 83 -> 69 cycles a pair (+8 a character row); 18
  poses 22,796,456 -> 22,688,518 (a fixed delta, tried first: 22,633,980).
- *Accuracy* (the right strip against its own exact v, 19,077 cells):
  exact row 85.3%, off by one 14.7%, off by two or more 0.0% (a fixed
  delta: 75.2% / 23.1% / 1.7%; rounding dh0 instead of truncating gained
  only 0.4 points). The float-reference gate: 94.77% within one texel
  (94.83% before), exact 79.40% (79.61%). `tex_ref._texel(..., dh=)` is
  the spec; byte-exact.

**7b. Speed: the gun as straight-line stores. — DONE.** 317 of the gun's
361 bytes have no transparent pixel, so they need no read or mask: they
are just written. `master_gun.py` (`cells()`, `source()`) now compiles the
table into code, one routine per screen buffer with absolute addresses
(`gun_b0` at MSCREEN0, `gun_b1` at MSCREEN1, in `mgun_tab.s`): the opaque
bytes sorted by value, each value loaded once then stored (`LDA #d`,
`STA abs` ...: 23 values, 4 cycles a byte), then the 44 edge bytes as
`LDA abs / AND #m / [ORA #d] / STA abs`. `gun_draw` sets ACCCON X and
calls the routine for DV_BACKHI. 29.6K -> 1.85K cycles a frame; 3.9K of
code (two copies) for the 870 B table, bank 6 code then $9500-$B4CF.
`test_master_gun` unchanged (both buffers, byte-exact against
`master_gun.apply`). Also fixed: `master_assets.py`'s previews indexed
the 8-colour palette with the cycling colours 8-15 (now `palette16()`).

**7a. Speed, phase 1: exact. — DONE.** Profiled first: on jsbeeb a
walking frame is ~1.80M cycles, 97% of it the renderer (gun 31K, movement
16K, flip 2K), so only the renderer matters. Exact changes only (the
byte-exact gate unchanged):
- *Edge steppers*: a stepper now holds y itself (y0 +/- q) with its
  remainder, stepping r += R, y += Qs (+/-Q), r >= W: r -= W, y += inc
  (+/-1); a negative slope reads y - (r != 0). A constant edge is y0 with
  W = $FFFF (its step never moves it), so no flag. The six steps per
  column are inlined (`ST_STEP` macro, absolute operands); `st_val` is a
  load and, when negative, one conditional decrement. Column walk + span
  edges 260K -> 173K a frame.
- *`mul8x32`*: the multiplier's quarter-square table offsets are patched
  once (f(e + A) at SQR_LO+A, f(|e - A|) at SQR_LO-A: the mirror page
  below SQR_LO serves e < A) and each byte is four indexed reads (238
  cycles a call, from ~340).
- Tried and dropped: the same for `mul16` (two products per multiplier:
  the set-up cost eats the gain, measured +0.05%); unrolled divides (16
  patched immediates per call, or zero page, which is full). The floor
  row maths were already cached per row per frame (step 5, P3).
- *Result*: mean frame (14 poses) 1.457M -> 1.401M (-3.8%); the 18
  test poses 23.99M -> 23.03M (-4.0%); jsbeeb 7 -> 8 frames in 400
  fields.

**6. Movers.** Doors, lift and moving floor with textures: alignment as they
move; invisible movers still cost nothing; cache-exactness gates rerun on
textured output.

**7. Performance and release.** Profile; extend `run_regression.py` with a
Master suite (framebuffer lockstep + cycle baseline); ship `doom_master.ssd`.

## 8. Open items

- **Thin walls (from the Tube's slow views, docs/tube_master.md H4g).**
  Three exact cuts to the per-seg set-up that a wall one byte column wide
  never uses: a seg with no byte column in [lo, hi) returns before its T
  / B steppers and `sh_lim`; `tx_seg` copies the first `at` when the last
  strip centre is the first (one `at`, not two); and on a seg's last
  byte column (`c_last`) its span-edge steppers skip their step divide
  (`si_ns`) and `next_col` leaves without stepping. The T / B steppers
  keep theirs: `tr_lines` peeks T and B one column on. Byte-exact; the
  regression's frames 27.58M -> 27.36M (-0.78%). Then `st_init8` (the
  clip spans' edges: every input a byte) has its own byte-sized path to
  the same stepper -- one inline quarter-square 8x8 (`QMUL`, moved up
  the file) for |D| k, `div32`'s 8-bit-divisor path, `st_init`'s step
  tail (`st_q`) only off a seg's last column: 27.36M -> 27.19M (-0.62%).
  Then a seg of one byte column (`tx_one`) skips `tx_seg`'s per-byte d
  stepper (four `mul16`) and `tx_getd`'s divides: its left strip's d is
  n0 / den0 = dL exactly, its right strip's (at x + 3 = xh) 2B dH / 2B =
  dH, dL when B = 0 (den 0) or past xh -- the stepper's own values.
  27.19M -> 27.09M (-0.36%).
  Then flat edges (`st_flat`): an edge with D = 0 -- the view's own top
  and bottom on untouched spans, narrow walls whose ends round to one
  line, and the clip edges they leave -- gets its stepper without the
  multiply and two divides: y = y0, rb = 0 - W, Qs = R = 0, Qs1 = 1, the
  values the divides give. 71% of span edges and 56% of wall T / B lines
  on the Tube's slow views (60% / 35% at random poses, so not a matter
  of cardinal view angles). 27.09M -> 26.67M (-1.57%).

- **Distant-wall fast path (prototype, `distant_ref.py`, not gated).** For
  segs shorter than 48 lines at both ends: T and B linear in 8.8 from a
  1/width table, u linear across the seg (one exact midpoint d when the
  ends' depths differ by more than 1.1x), the strips always sharing v.
  Pictures: 94% of byte columns keep their exact edge lines (the rest
  one line off); u within one texel on 87%, two on 98%; 0-360 view
  bytes change per pose, mostly one-texel shifts on distant walls.
  Measured (per-seg inclusive profile, 16 on-map poses): a distant seg
  costs ~34.7K, of which the fast path removes `at` (~3.3K), `tx_getd`
  (~2.9K), `sh_lim` (~0.4K) and the T / B `st_init` (~1.4K), less ~1K of
  its own: ~4.7% of the fill over the poses, 5-9% in the large rooms.
  Why so little (call-path profile, 143 distant segs, mean 34.7K): ~11K
  of a distant seg's time is FLOORS -- the pending plane spans of earlier
  segs flushed when this seg's plane differs (`tx_seg` > `pe_kind` /
  `pd_flush` > `mk_spans`, ~9.6K, plus `ceil_run` / `prun`); large rooms
  alternate planes (steps, light strips), so they flush often. The
  per-seg "fixed cost" fit (16.6K) had counted that as wall overhead.
  Of the ~23.7K of wall work the fast path removes a third; the rest is
  the per-run set-up (`tcol`, `trun`, `tv_divm`, `tv_v0`, `tr_screen`,
  ~4.5K over ~4.4 runs), the span-edge steppers of the clip state
  (`st_init8`, `adv`, `next_col`, ~3.9K), `tx_seg` itself (~2K) and the
  texel writes (~1.5K). Its floor and ceiling cells are 79% textured
  (not far tone). Not yet worth the 6502 work on its own.
- **The plane flush, measured** (every `pd_flush`, 16 on-map poses: 181
  flushes, 11.3 a frame, 22.3% of all frame cycles, 26.7K each): drawing
  38% (64 cycles a byte, both lines), set-up 62% -- row maths 29%
  (`pl_row`, ~1.3K per row and height; only 6% recompute a (row, D)
  already made that frame, so it is not cache thrash), per-span set-up
  14% (`sp_setup` + `uvat`, ~960 a span), the sweep 14% (`mk_spans`, 128
  a column swept), partial lines 5%. All-in 170 cycles per drawn byte.
  Flushes drawing over 64 bytes are 88% of the time; the 83 that draw no
  span bytes are pure sweep (1.7K each, 3%). Large rooms: flushes are
  27-33% of the frame, 12% drawing, 15-21% set-up. After 7o-7q: 24.2K a
  flush, 20.6% of the frame; drawing 42%, row maths 25% (`pl_row` code
  7%, the multiplies ~12%), the sweep 15% (`mk_spans`, still 128 a
  column), per-span set-up 12% (`sp_setup` 8%, `uvat` 2%, 248 a span),
  partial lines 6% (`pp_slot` 5%).

- **Stacked-texture safety under movers**: checked by the converter at full
  door and lift travel. NUKE24 shows at most 24 units at full lift travel
  (exactly its height), so stacking is safe; the build fails if that changes.
- **Dark sources go black**: FLAT14 (blue carpet) is entirely black and
  FLOOR1_1 nearly so; COMPTILE's blue panel too. Brightness matching with one
  global gain does this; a per-texture gain is the fix if it matters.
- **Wall parts use static sector heights**: step 6 must recompute K and
  Vtop for the parts of moving sectors (doors, lift), and the
  sky-to-sky rule if a mover's ceiling meets the sky.
- **Two-sided masked middles** (the 7 BRNBIG/BROWNGRN panels) are not drawn
  yet: the span-diff fill only fills what the span updates remove.
- **Reference sx rounding at S = 3** (see step 4b): a pre-existing
  Python/6502 projector mismatch, reported by `test_master_tex.py`.
- **Frame rate**: filling 10,240 screen bytes at roughly 25–30 cycles per byte
  on top of ~156K cycles of BSP work suggests a few frames per second;
  step 7 decides whether lower-detail options are needed.
- **Switch textures**: E1M1's exit switch SW1STRTN normally changes to
  SW2STRTN when pressed; not included (the spec lists 32 textures).
