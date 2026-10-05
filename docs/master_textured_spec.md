# Textured DOOM on the BBC Master — spec and plan

A textured E1M1 for the **BBC Master 128 only**, built as a delta from this
repo's wireframe engine. The BSP walk, angle-space culling, vertex pipeline,
caches, movers, trapezoid clip spans and the bit-exact Python reference are
kept. The line clipper/rasteriser (DCL + NJ linedraw), Mode 4 display and the
Model B memory map are replaced. The Model B build is not maintained.

## 1. Display

- 256×160, 4 colours, Mode 1 byte format (4 interleaved 2-bit pixels per byte).
- Palette: **black, magenta, cyan, white**.
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
  (kept as a tunable). On E1M1 this uses all 10 shades at 5–23% each.

## 3. Drawing

All writes are whole bytes; each texel row writes byte `B` to line 2r and
`FLIP[B]` to line 2r+1 (a 4×2 block).

- **Walls**: two independent strips per byte, giving 2×2 fat pixels:
  `byte = (TEX1 << 2) OR TEX2`, where each texel byte carries its pixel pair in
  the `$33` positions (TEX1 is shifted into `$CC`).
- **Floors and ceilings**: one texel per byte, a full Mode 1 byte with
  pixel #0 = #2 and #1 = #3.
- **Sky** (F_SKY1): solid cyan, no texture.
- Drawing code runs from **HAZEL** with ACCCON E set, so it reaches shadow RAM
  while all other code sees main RAM.

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
  once. Each texture has a 16-bit pointer (its first column page + slot) and a
  bank byte, followed by one **index byte per column**; index i → page i ÷ 8,
  slot i mod 8 from the texture's base. Textures may start mid-page, so small
  textures fill gaps.
- **Each texture's column data lies within a single sideways RAM bank.**
- **Pointer, bank and index tables live in HAZEL**, readable whichever bank is
  paged in.

## 5. Floor and ceiling format

- 16×16, one byte per texel, 256 bytes (one page) per flat.
- E1M1 needs **23 flats**: 11 floors + 13 ceilings, minus FLAT20 and FLAT5_5
  (shared) and F_SKY1 (cyan), plus NUKAGE1 and NUKAGE2 so the slime animates
  through all **three NUKAGE frames** (frame = page swap).

## 6. Memory map

| Area | Contents |
|---|---|
| Main RAM | All engine code; per-frame caches and workspaces moved out of the banks as needed |
| Shadow RAM (20K) | The two screen buffers, &3000 and &5800 |
| HAZEL (8K) | Unrolled wall and floor drawing loops, texture pointer/bank/index tables (~1.1K), flip table (256 B) |
| Sideways RAM banks 4–7 (64K) | Level data and tables (~24K), wall column data (~23.8K), flats (5.75K) |

**Budget (E1M1, measured with the converter prototype):**

- Wall column data: 824 unique columns at 32 high; stacking the four short
  textures brings the total to ~23.8K.
- Flats: 23 × 256 B = 5.75K.
- Level data and tables: ~24K (banks A and B of the current build: 10.3K +
  13.8K, part of which is cache workspace).
- **Total in the banks: ~54K of 64K.** ANDY (4K) is spare.

**Boot order**: the filing system uses HAZEL for workspace, so the disc loads
everything first, then copies the drawing code and tables into HAZEL. No disc
access after that.

## 7. Plan

Each step ends in something bootable or comparable, and is checked against the
Python reference byte for byte, as the existing engine is.

**0. Asset converter (Python).** WAD → scaled, quantised walls (32 high,
stacked 16-high pairs), per-texture column dedup, interleaved pages, bank
packing with no texture crossing a bank, HAZEL tables; flats incl. NUKAGE1–3;
PNG previews and a memory report. *Done when*: deterministic output, budget
fits, previews approved.

**1. Master bring-up (no textures).** 65C02 build; new ld65 configs (code in
main RAM, drawers in HAZEL, data in banks 4–7); boot sequence (load all, then
claim HAZEL); 256×160 four-colour CRTC setup and palette; double buffer in
shadow with ACCCON D/E/Y; vsync timer retuned for the new mode. *Done when*:
boots on jsbeeb's Master 128, the engine walks the BSP each frame, a test
pattern flips cleanly, and the HUD shows cycles per frame.

**2. Python textured reference.** Column renderer: 128 wall columns, 80 texel
rows; per-column wall top/bottom from the clip spans; perspective-correct u,
per-column v step; upper/middle/lower with WAD offsets and pegging; floors and
ceilings; sky; output in the exact target bytes, incl. the flip table.
*Done when*: reference frames exist at the 18 regression positions and
`play.py` has a textured mode.

**3. Column emission + solid shades on the 6502.** Replace line-fragment
emission with per-seg, per-span column ranges (start/end column + linear
top/bottom edges). Fill walls with a solid shade, sky cyan, floors flat.
*Done when*: framebuffer identical to Python at all 18 positions; boots.

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

- **Stacked-texture safety under movers**: NUKE24 borders the lift
  (special 88); the converter must check it at full lift travel. If more than
  24 units can show, NUKE24 cannot be stacked.
- **Per-seg texture data** (texture ids, x/y offsets, pegging) is not yet in
  the budget.
- **Frame rate**: filling 10,240 screen bytes at roughly 25–30 cycles per byte
  on top of ~156K cycles of BSP work suggests a few frames per second;
  step 7 decides whether lower-detail options are needed.
- The full-colour dither preview tool is a prototype in the session
  scratchpad; step 0 turns it into a repo tool.
