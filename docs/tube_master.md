# Tube Master: DOOM E1M1 on a Master 128 with a 6502 second processor

Branch `tube-master`, from `master-only` at step 7ad. The Master build
(`docs/master_textured_spec.md`) is the starting point and stays the
reference: every step here is held byte for byte to its frames.

## Why

`tools/master_profile.py` (20 poses, mean frame 1.24M cycles) splits the
Master frame like this:

| Work | Share |
|---|---|
| BSP walk, transform, projection, clipping | 9.5% |
| Wall set-up per seg and per byte / run | 15.8% |
| Floor and ceiling span set-up and rows | 9.8% |
| Column walk and span edges | 11.6% |
| Shared arithmetic (divides, multiplies) | 4.6% |
| Other fill bookkeeping | 24.2% |
| Inner loops that write the screen (wall texels, spans, solid fills) | 24.3% |

Moving the geometry alone to a second processor saves at most a tenth.
The plan is to move everything but the screen-writing loops: the second
processor (3MHz 65C102, 64K) runs the BSP, clipping, movement and all
the fill's set-up, and sends each frame to the host as a **display list**;
the host only draws it, one frame behind, while the next is computed.

## Plan

1. **The display list, in the Python models. — DONE.** Define it on the
   spec models; a host-side model draws a frame from the list alone,
   byte for byte the Master's frame.
2. **Measure. — DONE: go.** The list's size per frame over the poses and
   a compact encoding; the Tube's transfer cost on jsbeeb (65C102 second
   processor); the host's drawing cycles from the list.
3. **The host drawer** in 6502: the existing inner loops fed from the
   list, gated against `tube_dl.draw`.
4. **The second processor**: the engine and the fill's set-up relinked
   flat for the 64K map, emitting the list; the two halves joined over
   the Tube.

## 2. Measure. — DONE: go

**The encoding** (`tube_dl.encode` / `decode`, documented there): WALL
records grouped by texture over consecutive byte columns (a 3-byte group
header, then 8 bytes a column, 11 with its own right row; the texture
column's spare bit carries the flag), SPANs grouped by line (2-byte
header, 7 bytes a span, 4 for a far tone), FILL 5 bytes, $00 to end. The
gate now also draws every frame from the decoded bytes: byte-exact at
all 35 poses. Mean 2,278 B a frame over the 35, worst 5,132 B (2,161 B
over the 24 baseline poses). 45% of span lines are one of a matching pair
(the same span on both lines of a line pair, as the Master draws them):
a pair flag would take the mean to about 1,950 B.

**The Tube** (`tools/tube_bench.mjs`, jsbeeb's Master 128 with its 65C102,
both CPUs taken over, interrupts off): the second processor writes 4,096
bytes through register 1 (24-byte FIFO, parasite to host; it never
interrupts the host) while the host reads them, polled. Host: **20.04
cycles a byte for a read-and-store loop** (BIT 4, BPL 2, LDA 4, STA abs,Y
5, INY 2, BNE 3): the Tube is on the 2MHz bus, no stretching, and the
read itself is **10 cycles** (BIT, BPL, LDA). Slowing the second processor
by 80 of its cycles a byte gives 66.0 host cycles a byte at 3MHz and 49.5
at 4MHz, exactly its clock, so the measure is sound. jsbeeb fits the 4MHz
Master Turbo 65C102; the bench scales it to the 3MHz Second Processor.
The list costs the host about 22K cycles a frame on average, 51K at
worst: under 2% of today's frame.

**The split frame** (`tools/tube_estimate.py`, the 24 baseline poses). The
host is priced at the Master's documented loop costs (a wall line pair
69 cycles with its own right row, +8 a character row, 52 shared; a plane
byte 58 for a matching line pair, 35 for a line alone; far tone 8 and fill
8 a line), each record's set-up (WALL 60, SPAN 50, FILL 30), 10 cycles a
list byte and 10K a frame for the rest. The second processor gets
today's measured frame less those loops, plus 12 of its cycles a list
byte to emit it. Means:

| | cycles a frame | ms |
|---|---|---|
| Today, one 2MHz CPU | 1,149,042 | 575 (1.7 fps) |
| Host: drawing loops 219,034 + set-up, list, frame | 262,591 | 131 |
| Second processor, today's frame less the loops + emitting | 955,942 | 319 at 3MHz, 239 at 4MHz |
| **Split, pipelined (the longer side)** | | **319 (3.1 fps) at 3MHz, 239 (4.2 fps) at 4MHz** |

Per pose the speed-up is 1.6-2.1x at 3MHz and 2.1-2.8x at 4MHz; the
heaviest pose (1046.7, -3090.4, 157) goes from 1,320 ms to 812 / 609 ms.

**Verdict: go.** The Tube is not the limit (2% of the host). The second
processor is: it carries 80% of the work and the host idles about 60% of
each frame. So:

- *Balance.* Give the host back some set-up that is cheap to describe in
  the list -- e.g. the plane rows' maths (`pl_row` ~1.3K a row) or the
  per-span u/v set-up -- until the two sides meet: balanced, a frame is
  the whole work over both clocks, about 245 ms at 3MHz (2.3x) and about
  205 ms at 4MHz (2.8x).
- *The second processor's own speed* now sets the frame rate: its flat
  64K drops the bank paging and frees table space, so its maths (the
  BSP walk, the clipper, the wall and plane set-up) is where step 4's
  optimisation goes.
- *Pair spans* in the encoding (one record for a line pair's matching
  spans): fewer bytes and records.

The loop costs are estimates from the Master's loops; step 3 measures the
host drawer itself.

## 1. The display list. — DONE

`tube_dl.py`. `DLRef` is `plane_ref.PlaneRef` recording, as it renders,
everything the frame's bytes come from; `draw(dl, ...)` makes the frame
from the list, the texture and flat bytes and the panel, and nothing
else. `tex_ref` gained one no-op hook, `_wall_run`, called once per wall
run with its parameters (it changes no output: every existing gate
passes). Three records, lines y 0-135, byte columns k 0-63:

- **WALL** (k, y0, y1): one byte column's wall rows in one band. The
  texture, the left and right strips' texture columns, v (5.11) at y0
  and its step per line pair; the right strip shares the left's row, or
  has its own as tex_ref step 7d's 5.3 delta (dh0 at y0, ddh more per
  character row). Each line is `wall_pair(left texel, right texel)`.
  Everything per seg and per byte (perspective u, the near-wall start,
  the reciprocal-table step, 7s's extra bits) is done before the list.
- **SPAN** (y, k0, k1): one line of floor or ceiling. The flat, or step
  7n's far tone; u and v (4.4) at k0 and their steps per byte column.
  Each byte is the flat's texel, FLIPped on odd lines. Neighbouring byte
  columns with the same row maths, flat and tone are one span, across
  segs.
- **FILL** (k, y0, y1): one byte column of one byte: sky (FLIPped on odd
  lines), an unseen plane's shade, a sky-to-sky upper.

The host's work per wall line is one 16-bit add, a shift and two texel
reads (three adds more for a non-shared right strip); per span byte two
8-bit adds and one texel read. That is what the Master's inner loops do
now.

*Gate* `test_tube_dl.py` (in `run_regression.py`): at the 35 regression
poses `DLRef`'s frame equals `PlaneRef`'s and `draw(dl)` equals both, byte
for byte. *Size* with a first, unoptimised encoding (WALL 11 B, +3 for
its own right row; SPAN 8 B, far 5 B; FILL 5 B): mean 2,467 B a frame,
worst 5,457 B (at (1046.7, -3090.4, 157): 271 WALL, 243 SPAN). Walls are
one record per byte column per band, 64-271 a frame; spans 0-455.
