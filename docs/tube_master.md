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

**Host-led (from H1).** Step 4a's census showed the second processor
cannot hold the whole renderer: its half came to 80K (72K after moving
movement off and packing planes) against about 64K. So control is
inverted: the **host keeps the engine** -- the BSP walk, transform,
clipping, movement, the map data, in its own large memory -- and the
drawing; the **second processor is the fill's set-up**, the bulk of the
frame's arithmetic. For each wall seg the host sends a request (what the
Master's fill is called with); the second processor answers with the
display list (step 1), which the host draws (step 3). The second
processor then needs only the fill's code and its static tables (about
26K), with room left for faster tables. Estimated split (Master profile,
step 3's host): host ~515K cycles a frame (engine ~120K, requests ~8K,
drawing ~377K, movement ~15K), second processor ~813K: 271 ms a frame
at 3MHz, 257 ms (host-bound) at 4MHz, against 575 ms today.

1. **The display list, in the Python models. — DONE.**
2. **Measure. — DONE: go.**
3. **The host drawer** in 6502. — **First version done: byte-exact;
   377K cycles a frame (188 ms).** Tuning to come.
4. *(superseded by the host-led plan: 4a's census below is why)*
5. **H1. The fill request, in the Python models. — DONE.**
6. **H2. The host side in 6502. — DONE: the requests draw the Master's
   frame byte for byte; host engine + send 164K cycles a frame.**
7. **H3.** The second processor's fill server in 6502: the Master's fill
   set-up fed from requests, emitting the list; gated against
   `tube_req.FillServer` / `tube_dl.encode`.
8. **H4.** The two halves joined over the Tube (requests through
   register 3, the list back through register 1), the disc and jsbeeb.

## H1. The fill request. — DONE

`tube_req.py`. The Master's fill is called once per wall seg the engine
draws, after the clipper's span update, with the seg's slot, its column
range, its projected ends and top / bottom lines, solid or not, and it
reads the engine's near-clip terms and the clip spans (`_span_at`,
`_span_top`, `_span_bot` on the pool before and after the update);
everything else it reads is static (wall parts, dressings and lengths by
slot; the segs' sector heights and flats; the flats' far tones) or once a
frame (the view). The request is exactly that:

- per frame: the view (px88, py88 24-bit, vz, ab): 8 bytes;
- per wall seg: slot + solid + the two clip flags (2 bytes), the seg's
  subsector (1: the fill's flats and sky are by subsector), the column
  range clamped to 0-255 (2), the six line ends (12), the ends'
  reciprocal terms m / S (4), the near-plane crossing t when exactly one
  end is clipped (2: what the engine leaves in `mf_xt`; `tex_ref` reads
  it through `_cross_t`, which the server overrides), and the pool's spans
  over the range before and after the update (a count, then 10 bytes a
  span: the clip pool's own fields XSTART XEND TXLO TDEN TL BL TR BR BXLO
  BDEN, so the host copies them straight out of the pool);
- a request only for a seg the fill can draw: [lo, hi) not empty and some
  span over it before the update (`mf_fill`'s own early-outs); `$00` ends
  the frame.

`ReqRef` (the host: PlaneRef rendering as before) records each frame's
requests; `FillServer` (the second processor) is the same fill with no
engine and no map, served from decoded requests. *Gate*
`test_tube_req.py` (in `run_regression.py`): at all 35 poses the served
frame equals PlaneRef's byte for byte and the served display list
encodes to exactly the host-side list's bytes. **Requests: mean 894 B a
frame, worst 2,495 B** (56 segs at (1046.7, -3090.4, 157)); 0-56 wall
segs a frame, 21 on average.

## H2. The host side in 6502. — DONE

A `TUBE` link of the Master engine (`DOOM_ASMDEFS=TUBE=1`): `walk.s`
calls `rq_frame` where it called `mf_frame`, and `seg_emit.s` calls
`rq_fill` where it called `mf_fill` (`FILLSEG`); `mf_snap` stays, so the
"before" spans are the snapshot exactly as the fill takes it. The sender
is in `src/master/mfill.s` under `.ifdef TUBE`:

- `rq_frame` (HAZEL): the view, from the walk's `zp_br_px/py`
  (24-bit), `zp_br_vz` and `bca_ab`;
- `rq_fill` (bank 6, beside `mf_snap`): `mf_range` and the snapshot count
  decide whether there is a request; then the slot (from `zp_seg_hdr_p`,
  as `tx_seg` computes it) with solid (`zp_seg_flags` V) and the clip
  flags, the subsector (`zp_node_ch_l`), lo / hi, the six line ends from
  the VX1 / VX2 blocks (the y ends less the engine's Y_BIAS of 48), the
  reciprocal terms, `mf_xt` when exactly one end is clipped, the
  snapshot, then the live pool spans over [lo, hi) (counted, then sent);
- `rq_end` (HAZEL): `$00`.

Each byte goes out through register 3 polled (`BIT $FEE4 / BVC / STA
$FEE5`, 10 cycles while there is room), inline.

*Gate* `test_tube_hreq.py` (in `run_regression.py`). The rig captures
writes to `$FEE5` (`$FEE4` reads room). At all 35 poses:
1. the decoded bytes have the Python host's requests: the same segs in the
   same order, and the same view, slot, flags, subsector, range,
   reciprocal terms and t;
2. served by `FillServer`, they draw **exactly the Master's own 6502
   frame** (the normal link, rendered in a subprocess).

The line ends and spans are the ENGINE's, and the gate holds them to the
frame they draw rather than to the Python host's bytes. 29 of 35 poses
are byte-identical to `tube_req.encode` anyway. Of 740 requests:
- 2 carry an off-screen sx one away from the model's. This is the
  reference's rounding where the engine truncates, the gap
  `test_master_tex.py` names; served, they draw the Master's pixels;
- 4 have a span the 6502 pool holds as two (a one-column fragment beside
  its neighbour) where the Python clipper holds one; they draw the same.

**Host engine + send: mean 164K cycles a frame, worst 454K** (the Master
renders a frame in 1,149K mean today). With step 3's drawer (377K) and
movement (~15K), the host's frame is about 556K cycles: **278 ms at 2MHz**,
against the plan's 515K estimate (the engine measured 164K with the send,
not 128K).

Open for H4: register 3 holds one byte (two in its two-byte mode), so the
host's polling runs at the parasite's reading rate. The parasite must
take bytes as they come (an NMI, or a poll between its own jobs) into a
request buffer, and serve from that, so the host never waits on the fill.

## 4a. Does the second processor's half fit? — not as it stands

`tools/tube_fit.py` counts everything the second processor would hold,
from the linker map (code and linked tables) and the disc images (the map
data in banks 4 and 7, measured as the bytes in use; the per-frame
caches, which ship as zeros, from the layout):

| | bytes |
|---|---|
| Code: engine CODE 13,552, clipper 5,432, fill set-up (bank 6 7,255, main 1,635, HAZEL 1,159), movement 1,921, arithmetic 387, driver logic ~400, list emitter + protocol ~1,500 (estimates) | 33,241 |
| Static data: bank 4 map data 10,752, bank 7 13,028 (as laid out: its node and bbox planes a page each), ANDY wall tables 3,977, wall step tables + part records 5,220, clipper data 2,339, quarter squares 1,536, view tables 755 | 37,607 |
| Workspace: vertex / rotation / projection caches 5,120, plane spans 1,120, fill BSS 405, low RAM 1,792, zero page + stack + WORK 714 | 9,151 |
| **Total** | **79,999 (78.1K)** |

Usable: 63,488 B keeping the Tube client's RAM ($0000-$F7FF), 65,278 B
overwriting it once loaded (all but its registers at $FEF8-$FEFF).

**A** (movement, collision and the use / walk lines on the host, which
keeps the banks; it sends position, angle and the movers with each
frame): 6,011 B -- the movement code 1,921, bank 7's SS_VZ page,
collision index, silent lines, y cells, ports and collision segs 3,702,
bank 4's use vectors and use / walk tables 388. **C** (bank 7's node and
bbox planes packed at their length, 194 / 195 bytes, rather than a page
each; they are read with assembled addresses, so a plane that crosses a
page costs a cycle on some reads): 1,878 B.

**After A + C: 72,110 B -- still 8.6K over (6.8K overwriting the Tube
client).** (An earlier count had bank 7 already packed and then took C
off again; corrected here.)

## 3. The host drawer. — first version done

`src/tube/hdraw.s` draws a frame's encoded display list, read off the
Tube's register 1 (polled: BIT / BPL / LDA), into the screen buffer, with
the Master build's own texture and flat images (bank 5) and tables made
from its asset manifest (`tube_host.py`: per texture its bank, page, row
offset and mask, and a logical-to-stored column index; per flat its bank
and page; `lsr4`, `flip`). `tube_host.py` also runs it in py65 with the
Tube's register 1 and ROMSEL modelled. *Gate* `test_tube_host.py` (in
`run_regression.py`): byte-exact at all 35 poses.

- *WALL*: per group the bank, the texture's row mask and offset are
  patched into the loops; per column the two stored columns into the
  self-modified texel reads (`LDA col,X`, X = (hi(v) AND mask) OR offset)
  and the doubled step into the v add (carry is clear after the right
  texel's `LSR`: texels are left pixels). A line pair's byte is made once
  and written to both lines; a lone first or last line on its own. The
  right strip's own row adds dh, which steps by ddh a character row.
- *SPAN*: separate loops for an even line, an odd line (FLIPped) and a
  line pair, du, dv, the flat's page and the end patched in; a span is
  split at byte column 32 so Y never carries.
- *FILL*: a column of one byte (the sky FLIPped on odd lines).

*List change (step 3).* Both lines of a line pair share their plane row
maths (`plane_ref`: one texel per line pair), so where the two lines'
spans of one plane overlap the overlap is now one PAIR span and only
what is left of either line is drawn alone, as the Master draws its
planes. Pair bytes went from 548 to 1,841 a frame (single-line bytes
2,694 to 107) and the host from 400K to 377K cycles. Mean list 2,002 B,
worst 5,350 B.

*Measured* (py65, the 24 baseline poses): **376,822 cycles a frame (188
ms)**, worst 625,897 (313 ms at (1046.7, -3090.4, 157)); step 2 assumed
262,591. Per unit, at four poses: a wall line pair about 68 cycles (the
Master: 52 shared, 69 own), a pair-span byte about 71 (58); per record
about 300 cycles for a wall column (its 8-11 list bytes, two column
look-ups, the screen address) and 150 for a span, where step 2 assumed
60 and 50. So the loops are within about a fifth of the Master's and the
set-up is what step 2 under-counted. The second processor still sets the
frame (319 ms at 3MHz, 239 ms at 4MHz, step 2), leaving the host 130 ms
(3MHz) or 50 ms (4MHz) a frame for billboards, monsters and the panel.

*Next (tuning):* the Master's unrolled loop forms (a cell's pair bodies
entered by line and left through a patched RTS, `LDY #n` in place of
stepping Y) for walls and pair spans; cheaper column set-up (the list
could send stored columns rather than logical ones, and the screen
address of consecutive columns is +8).

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

- *Balance: not now (decided).* Moving set-up back to the host (the plane
  rows' maths, the span set-up) would even the sides -- about 245 ms at
  3MHz, 205 ms at 4MHz -- but the host's idle time is earmarked for what
  the Master build lacks: billboards, monsters and the panel's duties. The
  split stays as it is.
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
