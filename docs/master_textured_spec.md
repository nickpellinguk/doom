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
- E1M1 needs **23 flats**: 11 floors + 13 ceilings, minus FLAT20 and FLAT5_5
  (shared) and F_SKY1 (cyan), plus NUKAGE1 and NUKAGE2 so the slime animates
  through all **three NUKAGE frames** (frame = page swap). The frames are
  stored on consecutive pages of one bank.

## 6. Memory map

| Area | Contents |
|---|---|
| Main RAM | All engine code; per-frame caches and workspaces moved out of the banks as needed |
| Shadow RAM (20K) | The two screen buffers, &3000 and &5800 |
| HAZEL (8K) | Unrolled wall and floor drawing loops, texture directory/headers/index tables and flat tables (1,316 B), flip table (256 B, page-aligned) |
| Sideways RAM banks 4–7 (64K) | Level data and tables (~24K), wall column data (~23.8K), flats (5.75K) |

**Budget (E1M1, measured by `master_assets.py`):**

- Wall column data: 783 stored columns × 32 B = 25,056 B (24.5K).
- Flats: 23 × 256 B = 5,888 B (5.75K).
- Textures and flats together: all of bank 5 and 14,592 B of bank 6
  (1.75K of bank 6 spare).
- Level data and tables: ~24K (banks A and B of the current build: 10.3K +
  13.8K, part of which is cache workspace).
- **Total in the banks: ~54K of 64K.** ANDY (4K) is spare.

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
fills. Diff the pool before and after the seg; per strip (owned by its even
pixel) fill the removed bands, split at the seg's own front ceiling and
floor lines: ceiling above, floor below, wall between. Every on-screen
pixel is filled once, front to back, with no gaps and no overdraw — no
screen clear is needed. Fills are per screen line (even lines the byte,
odd lines its FLIP), so band edges may fall on any line.
`src/master/mfill.s` (HAZEL, $C800) implements it: `mf_snap` at the
cascade head, `mf_fill` after the seg's updates, exact steppers for the six
interpolated edges, `hz_run` writing strip runs with ACCCON X set only for
the run. Sky ceilings come from a per-subsector bitmap. The driver holds
ACCCON Y on for the whole render and turns billboard objects **off** (they
apply span lines outside any seg's fill window; sprites are not in the
spec yet). Gates: `test_fill_ref.py` (full coverage, 96.4% surface
agreement with the float `textured_ref`), `test_master_fill.py` (the 6502
back buffer equals `fill_ref` byte for byte at the 18 poses), both in
`run_regression.py`; `test_master_disc.py` boots the disc on jsbeeb.
*Not yet fast*: 1.0–2.7M cycles a frame in py65 (about 1.4 s a frame on
the emulated Master): stepper set-up uses generic 32-bit maths and every
strip is written separately with read-modify-write. Step 7 territory, but
step 4's two-strips-per-byte writer replaces most of the write cost.

**4. Textured walls.** Unrolled two-strip loop in HAZEL; per-column u (one
reciprocal per column, or exact every N columns with linear interpolation);
texture/index/bank lookup; stacked-texture row offset. *Done when*:
framebuffer identical to Python; cycle baseline recorded.

**5. Floors and ceilings.** Row spans from the clip spans' edges; per-row
distance table and step; 16×16 lookup; NUKAGE frame cycling. *Done when*:
framebuffer identical to Python; playable Master disc.

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
- **Per-seg texture data** (texture ids, x/y offsets, pegging) is not yet in
  the budget.
- **Frame rate**: filling 10,240 screen bytes at roughly 25–30 cycles per byte
  on top of ~156K cycles of BSP work suggests a few frames per second;
  step 7 decides whether lower-detail options are needed.
- **Switch textures**: E1M1's exit switch SW1STRTN normally changes to
  SW2STRTN when pressed; not included (the spec lists 32 textures).
