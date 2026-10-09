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
2. **Measure.** The list's size per frame over the poses and a compact
   encoding; the Tube's transfer cost on jsbeeb (65C102 second
   processor); the host's drawing cycles from the list. Go / no-go.
3. **The host drawer** in 6502: the existing inner loops fed from the
   list, gated against `tube_dl.draw`.
4. **The second processor**: the engine and the fill's set-up relinked
   flat for the 64K map, emitting the list; the two halves joined over
   the Tube.

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
