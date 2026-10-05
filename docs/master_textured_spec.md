# Textured DOOM on the BBC Master — spec and plan

A textured E1M1 for the **BBC Master 128 only**, built as a delta from this
repo's wireframe engine. The BSP walk, angle-space culling, vertex pipeline,
caches, movers, trapezoid clip spans and the bit-exact Python reference are
kept. The line clipper/rasteriser (DCL + NJ linedraw), Mode 4 display and the
Model B memory map are replaced. The Model B build is not maintained.

## 1. Display

- 256×160, 4 colours, Mode 1 byte format (4 interleaved 2-bit pixels per byte).
- Palette: **black, red, cyan, white** (logical 0–3 = physical 0, 1, 6, 7;
  red replaced magenta for a more even brightness spread).
- 64 bytes per line × 160 lines = 10K per buffer. **Both buffers live in the
  20K shadow RAM**, at &3000 and &5800. The display always shows shadow; a
  flip rewrites the CRTC start address (R12/R13).
- Effective resolution: every texel row is 2 screen lines (see 3), so the view
  is **80 texel rows** high; walls are 128 columns wide, floors/ceilings 64.

## 2. Colours

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

All writes are whole bytes; each texel row writes byte `B` to line 2r and
`FLIP[B]` to line 2r+1 (a 4×2 block).

- **Walls**: two independent strips per byte, giving 2×2 fat pixels:
  `byte = (TEX1 << 2) OR TEX2`, where each texel byte carries its pixel pair in
  the `$33` positions (TEX1 is shifted into `$CC`).
- **Floors and ceilings**: one texel per byte, a full Mode 1 byte with
  pixel #0 = #2 and #1 = #3.
- **Sky** (F_SKY1): solid cyan, no texture.
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
| Shadow RAM (20K) | The two screen buffers, &3000 and &5800 |
| HAZEL (8K) | Boot pattern + HUD at $C000–$C27F; span snapshot, plane spans + row cache $C280–$C7EF; the fill's hot code and tables (x16 tables `hi16` / `lo16` and `mf_flip`, page-aligned at $C800, $C900, $CA00; texel and span loops, `mf_frame`, sky map) $C800–$CD29, **free $CD2A–$DDFF (4.2K)**; BSS $DE00–$DFFF |
| Sideways RAM banks 4–7 (64K) | Level data and tables (~24K), wall column data (19.8K), flats (5.25K); bank 6 $A500–$B867: the fill's cold set-up code (steps 5f–5h; free to $B8FF); bank 6 tail $B900–$BE23: wall part records + texture constants |
| ANDY (4K) | Per-seg wall tables (slot planes, dressings, merged-seg pieces) + per-subsector flats, 3.9K |
| Main $7A00–$7E1F | Texture column index bytes (996 B) |
| Main $7E20–$7FFC | The fill's multiply and divide routines (step 5c) |
| Main $6D38–$6FE9 | Cold per-seg wall and plane set-up |
| Main $4FB1–$5592 | Plane row maths, pending-plane logic and their tables (step 5e; `MFILLC`, `MFMAIN` — `rw`, not `bss`, so `SHTAB` keeps $5600) |

**Budget (E1M1, measured by `master_assets.py`):**

- Wall column data: 634 stored columns × 32 B = 20,288 B (19.8K), with
  COMPUTE2 clipped and the BRNBIG masked middles dropped (783 columns,
  24.5K, before).
- Flats: 21 × 256 B = 5,376 B (5.25K).
- Textures and flats together: all of bank 5 and bank 6 $8000–$A4FF
  (9,472 B); bank 6 $A500–$B8FF holds the fill's cold code (step 5f).
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

*Gates*: `test_master_engine.py` (in `run_regression.py`) requires the
Master engine's emitted line list to equal the Model B C02 engine's at 29
poses (bank 6 is poisoned in its rig); `test_master_disc.py` boots the disc
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
- *4.12 fixed point*: u and v are in texels (4 world units each), 16
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
  (`pp_all` 256 B, ZC/ZS 360 B) in main $4FB1–$5592.

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

**6. Movers.** Doors, lift and moving floor with textures: alignment as they
move; invisible movers still cost nothing; cache-exactness gates rerun on
textured output.

**7. Performance and release.** Profile; extend `run_regression.py` with a
Master suite (framebuffer lockstep + cycle baseline); ship `doom_master.ssd`.

## 8. Open items

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
