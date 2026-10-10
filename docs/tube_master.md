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
7. **H3. The second processor's fill server in 6502. — DONE: byte-exact
   display lists; 970K cycles a frame (323 ms at 3MHz, 242 ms at 4MHz).**
8. **H4. The two halves joined over the Tube. — DONE: the Tube disc
   runs on jsbeeb; 1.9 fps at 3MHz, 2.6 fps at 4MHz, against 1.1 fps
   for the Master disc.**

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

## H3. The fill server in 6502. — DONE

**H3a.** The request grew two things the server would otherwise need the
map or a table for:
- the view's trig: |sin|, |cos| and their sign and unity flags (3 bytes a
  frame);
- per seg, its sector's `ch - vz` and `fh - vz` (2 bytes: the engine's
  `zp_seg_top_dlt` / `zp_seg_bot_dlt`, which the host's movers change).

`PlaneRef` reads a plane's D through `_plane_d`, which `FillServer` takes
from the request. The requests are now mean 941 B, worst 2,610 B.

**The server** is `src/tube/fserve.s` linked with `src/master/mfill.s`
assembled with `SERVER` (`src/tube/fserve.cfg`; `tube_server.py` builds
it and runs it in py65). The fill is the Master's own code. Under
`SERVER` its screen code goes: the raster split, the gun, every write
loop, and the plane spans' sweep and flush. Where it drew, it now records:
- `tr_screen` → `fs_wall`: the run's WALL record, straight to the list
  (consecutive byte columns of one texture grouped as `tube_dl.encode`
  groups them);
- `hz_run` → `fs_mark`: a solid run (sky, shade) into its column's solid
  list;
- `prun`'s textured lines → `fs_mark`: into its column's plane list, coded
  with the seg's plane. `fs_planes`, at the end of `pl_seg`, names the
  frame's planes by (D, flat).

`tcol` keeps the texture column itself: the server's column index table
holds column numbers, not texel addresses. `pl_row` makes a far row's U
and V too, because the list joins neighbouring far cells on their row
maths. The request reader puts each request where the engine would have
left it: zero page, the snapshot `sn_*`, and the live spans as the pool.

At the frame's end:
- `fs_sweep` compares each column's plane list with the column before.
  Only lines whose plane changes open or close a run in that line's list,
  so the cost goes with the run edges, not the cells.
- `fs_spans` joins each line's runs where the row maths, flat and far
  tone match, pairs the two lines of each line pair where their runs
  match, and writes the line's own spans, the pair spans, then the odd
  line's own (`tube_dl.encode`'s order).
- `fs_fills` writes the solid lists as FILLs, joining touching marks of
  one shade.

A frame has about 140 marks (worst 384), and at most 9 in a column.

*Gate* `test_tube_server.py` (in `run_regression.py`). At all 35 poses
the Python host's requests, served by the 6502, give a display list
byte for byte `tube_dl.encode` of `FillServer`'s.

**Server: mean 970K cycles a frame, worst 2.28M** (56 segs at
(1046.7, -3090.4, 157)). That is 323 ms at 3MHz and 242 ms at 4MHz.
`tools/tube_server_prof.py` breaks it down:

| Part | Cycles a frame |
|---|---|
| The Master's fill set-up | ~660K |
| End-of-frame sweep, runs, pairs and spans | ~220K |
| Request reading, marks and WALL emission | ~85K |

An empty frame is 25K. The first version marked a 136 × 64 cell grid and
scanned it at the frame's end: 640K for an empty frame, 1.7M on average.

**Memory** (`fserve.cfg`), all below `$F800`:

| Area | Size |
|---|---|
| Code | 11.6K |
| Tables (quarter squares, column index, ANDY's wall tables, step tables, part records) | 11.6K |
| Fill workspace and the span pool | 2.1K |
| Marks and line runs | 16.5K |
| Display list buffer | 7K |
| Request buffer | 3K |
| Server workspace | 3.1K |

The two buffers become streams in H4.

Against the host's ~556K (278 ms at 2MHz), the server is the slower half
at 3MHz and the host at 4MHz. A frame overlapped across the two would
take about 323 ms at 3MHz and 278 ms at 4MHz, against 575 ms today.

## H4. The Tube disc. — DONE

`build_master_ssd.py --tube` writes `build/master/doom_tube.ssd`: the
host engine linked with `TUBE`, and the fill server as the file `SERVER`.

**The link.** Both directions use register 1:
- the host sends each request byte with `PUTB` in `mfill.s` (`$FEE0`
  room, `$FEE1` data);
- the server takes each byte by IRQ (`fs_irq` at `$FFFE`, flag I) into
  a 2K ring at `$D800`. At each page's start the IRQ takes the page only
  if the reader is in neither it nor the next. Otherwise it leaves the
  byte in register 1, so the host waits on its room, and returns with
  IRQs masked; `fs_get` turns them back on as it frees a page, or finds
  the ring empty;
- the server pushes the list back through register 1's 24-byte FIFO
  while there is room (`fs_poll`, called from `fs_get` and from the
  fill's column loop);
- the host reads it in `hd_frame`.

The first version sent the requests through register 3 by NMI. The host
writes the next byte before the handler's tail has finished, so the NMIs
nested until the stack overflowed. The IRQ is masked inside its own
handler, so it cannot nest.

**Pipelining.** Each frame the host:
1. runs the engine, sending frame N's requests as it goes (`rq_frame`,
   `rq_fill`, then `rq_end`'s `$00`);
2. draws frame N - 1's list (`hd_frame`, in the driver after
   `ENG_RENDER_FRAME`, before the gun).

The server serves frame N as its requests come in, then waits for list
N - 1 to finish going out before it swaps its two list buffers (A at
`$B400`, B at `$E000`, 7.25K each). The host can be up to two frames of
requests ahead (frame N + 2 while the server is on N + 1), and one frame's
requests reach 6.5K, which is why the ring needs its flow control.
Frame -1's list is a single `$00`, so the first frame shown is empty.

**The boot.** `!BOOT` (`mboot.s`) first does `*LOAD SERVER`; the image
loads to the second processor at `$0C00`-`$71FF`. Then it claims the
Tube (`$C0+$3D` at `&0406`) and executes the image (type 4) at
`fs_main`. Type 4 leaves the host in the MOS's Tube loop, so `fs_main`
sends `*GOIO 1903` back by register 2, as OSCLI, to return the host to
`!BOOT`'s `l_cont`. The server then:
- clears its zero page, `$0200`-`$0BFF` and its BSS;
- sets its ring;
- puts its IRQ vector into RAM at `$FFFE`;
- waits for flag I.

The host loads the rest. Under the Tube a bare load address means the
second processor, so the host's loads say `FFFF3000`. Then, in `s_go`,
it clears flags Q, J, M and V, sets I, and jumps to the driver.

**The second processor's memory** (`fserve.cfg`):

| Area | Contents |
|---|---|
| `$0000`-`$00FF` | Zero page |
| `$0200`-`$07FF` | Fill workspace |
| `$0A00`-`$0BFF` | Span pool (`SPAN_POOL`) |
| `$0C00`-`$71FF` | The file: tables and code |
| `$7200`-`$B3FF` | Marks (16 a column) and line runs (16 a line) |
| `$B400`-`$D0FF` | List buffer A |
| `$D100`-`$D7FF` | Server and fill workspace |
| `$D800`-`$DFFF` | Request ring |
| `$E000`-`$FCFF` | List buffer B (over the client OS, which the server has replaced) |
| `$FFFE` | IRQ vector |

**The list's cap.** The first disc had 5.5K list buffers, and a list in
the start room ran to 5.7K. It overran into the server's workspace, and
the host drew the garbled list, writing through the panel and into the
paged texture bank. Random poses put lists up to 5.5K in the model, and
the engine's own a little higher. The buffers are now 7.25K, and each
emitter (`fs_wall`, `fs_span`, the FILLs) drops its record once the list
passes `LISTCAP` (7K). The list stays whole, with its `$00`, and
`fs_drop` counts the records lost (saturating at 255). The server's mark,
run and plane tables drop the same way.

**Random poses (H4c).** `tube_fuzz.py` samples poses the player can
stand at: a point whose sector, and the player's box's, are reachable
from the spawn (`colmap`'s flood), any angle. Served on the 6502 and by
the model, about 2% of them differed or dropped. The model's list drew
the frame every time, so the server was wrong:
- *Overlapping runs.* The Master's fill sometimes writes a cell twice: a
  wall's last row over a solid run's first, or a plane's line over
  another's. The later write wins the frame, but the server kept every
  mark whole, and the host draws FILLs and SPANs after WALLs. Now
  `fs_trim` runs before each wall run and each new mark: the column's
  plane and solid marks lose the lines drawn over (a mark inside them
  goes, one across an end is cut, one around them is split). The model
  takes a plane cell only while the frame's grid still shows a plane
  there. It costs about 3.5% of the server's time.
- *Full line run lists.* Some views put 9 plane runs on a line, against
  the server's 8. The lists now hold 16 a line. The ring went to 2K to
  make room (flow control makes the size a matter of how far the host
  may run ahead, not of correctness).
- *A one-line run's step.* The model's step was the full value, the 6502's
  its 16-bit pair step halved. A one-line run never uses it; the model
  now keeps the 15 bits the 6502 does.

`test_tube_fuzz.py` holds every pose that caught the server out, and a
fixed batch of 150 random ones.

**Gates** (all in `run_regression.py`):
- `test_tube_fuzz.py`: the corpus and the random batch, byte for byte,
  nothing dropped.
- `test_tube_link.py`: the server on jsbeeb's second processor with a
  host pump (`src/tube/lpump.s`). At 37 poses (`poses.TUBE_BIG` adds
  the largest requests and list found) each list is byte for byte the
  model's. Host mean 796K cycles a frame at 3MHz (398 ms), which is the
  server's pace. Then again with a 1K ring, which most frames fill: the
  same lists.
- `test_tube_server.py`: also the two big poses, a request longer than
  the ring fed a page at a time, and a build capped at 2K, whose list must
  stop short and whole.
- `test_tube_hreq.py`: the TUBE engine's register 1 bytes decode to the
  Python host's requests, and `hd_frame` draws the encoded lists to the
  Master's frames.
- `test_master_disc.py --tube 3` (`--disc`): the Tube disc on jsbeeb with
  a 3MHz second processor. It walks, turns and strafes, with no holes,
  the panel and raster split intact, and the music as the model. The
  first flip shows frame -1's empty list, so it is left out of the hole
  check.

**Frame rate** on the disc test's walk, flips in 400 fields (8 s):

| Machine | Flips | Rate |
|---|---|---|
| Master, no Tube | 9 | 1.1 fps |
| Tube, 3MHz | 15 | 1.9 fps (533 ms) |
| Tube, 4MHz | 21 | 2.6 fps (381 ms) |

Those 400 fields are spent standing at the spawn, one of the map's
heaviest views. Walking (below) runs faster.

## H4d. Where each side waits. — MEASURED

`tools/tube_waits.py` boots the Tube disc on jsbeeb, walks a fixed key
plan from the spawn for 30 s and classes every cycle of both processors
(`tools/tube_waits.mjs`):
- **Host:** the engine and sending (`render_frame`), with its `PUTB`
  polls that loop counted as waiting to send; drawing (`hd_frame`), with
  its `GETB` polls that loop counted as waiting for list bytes;
  `flip_sched`; and the rest.
- **Second processor:** the fill; sending the list (`fs_poll` called from
  the fill); taking request bytes (`fs_irq`); waiting for requests
  (`fs_get` on an empty ring); and waiting for the host to take the last
  list (`fs_main` before it swaps buffers).

Movement is capped at 10 fields a frame, so the slower run covers less of
the walk. Each run's own split holds, but their per-frame fill and list
sizes are not the same views.

| A frame | 3MHz (2.73 fps, 365 ms) | 4MHz (3.63 fps, 275 ms) |
|---|---|---|
| Host: engine and sending | 66 ms | 43 ms |
| Host: waiting to send | 13 ms | 1 ms |
| Host: drawing | **210 ms** | **201 ms** |
| Host: waiting for list bytes | 61 ms | 17 ms |
| Host: flip, driver, gun, HUD, IRQs | 16 ms | 13 ms |
| Second: the fill | **322 ms** | 187 ms |
| Second: sending the list | 21 ms | 12 ms |
| Second: taking requests | 10 ms | 4 ms |
| Second: waiting for requests | 7 ms | 17 ms |
| Second: waiting for the host to take the list | 5 ms | 55 ms |

What it says:
- **At 3MHz the second processor is the bottleneck.** It is busy 97% of
  the frame, the fill alone 88%. The host waits 74 ms a frame on it,
  most of that inside `hd_frame` for list bytes still being made. A
  faster fill gains until the host's own 292 ms a frame, about 10%.
- **At 4MHz the host is the bottleneck.** It is busy 94% of the frame,
  and drawing is 73% of its time. The second processor sits idle for
  26%, mostly waiting for the host to drain the last list: the host reads
  it as it draws, so the drain takes the whole 200 ms of drawing.
- **The link itself is cheap:** sending and taking bytes cost the second
  processor 7-9%, and the host waits to send only on the slower one.
- **The drawer is the largest single cost.** It takes about 400K host
  cycles a frame, as it does over the regression poses (386K; see H4e).
  Beyond about 3MHz, it alone sets the frame rate.

So the next steps, in order of payoff on this walk: the host drawer
(both speeds; the only lever at 4MHz), then the fill (at 3MHz). (H4f: on
the slowest views the order reverses; the server bounds them at both.) Letting the server start
the next frame before the host has drained the last list would need a
third list buffer; it gains nothing while the host is the slower side.

## H4e. The host drawer against the Master's own writers. — MEASURED

On `tools/master_profile.py`'s 20 on-map poses, the Master-only fill's
screen-writing loops against the host drawer (`hd_frame`) drawing the
same frames from their lists, profiled by label in the engine link
(py65). The cells are the same: a frame has 5,616 wall byte-lines, 2,913
span byte-lines and 173 fill byte-lines on average.

| A frame | Master-only | Host drawer |
|---|---|---|
| Wall texel loops | 210K (37 a byte-line) | 200K (36) |
| Span loops | 88K (30 a byte-line) | 96K (33) |
| Solid fills | 3K | 4K |
| **Writing the screen** | **301K** | **300K** |
| Per wall column: reading its entry, the column's address | (in the 640K below) | 52K |
| Per span: reading it, patching the loop | (in the 640K below) | 29K |
| Cell pointers, next row, dispatch, the rest | | 41K |
| Set-up the Master does and the server now does | ~640K | - |
| **Total** | 941K of a 1,239K frame | **422K** |

- **The inner loops are at parity.** The Master's wall bodies are
  unrolled a character row at a time and step two strips' v; the
  drawer's loop is rolled, but its shared-row case reads one v. Both come
  to about 36 cycles a wall byte-line. The drawer's span loop is about
  10% slower than the Master's Duff's-device bodies (73 against 58
  cycles a line pair: its loop count, and U and V reloaded each byte).
- **The drawer's own cost is the list's decoding: 122K, 29% of it.** Each
  wall column reads 8 or 11 bytes off register 1, each with a status
  poll (about 10 cycles a byte), then finds its texture column (`colad`)
  and screen cell (`cellptr`). That is what replaced the Master's ~640K
  of set-up (wall v and steps per column, the column walk, plane rows,
  their arithmetic), which the second processor now does.
- So the host draws a frame in 422K where the Master-only build spends
  1,239K, and its engine and sending add about 165K: about 590K, under
  half the Master-only frame. Speeding the drawer means the decoding (the
  status polls, per-column addressing) and unrolling its loops as the
  Master's are; the writes themselves are no slower than the Master's.

**Two per-column trims, measured** (`test_tube_hreq.py`'s 35 poses, 70
of a frame's ~110 wall entries following one in their group):
- *The screen address stepped, not recomputed:* a WALL group keeps
  scr's low byte ((kk & 31) * 8) and its page of the screen's half
  (`wch`), stepped a byte column on in `wend`; a column works out only
  its character row's page. 385.6K to 384.1K a frame (0.4%). Kept.
- *Patches skipped where the column repeats:* the left or right texture
  column the same as the last entry's (12% each), the left the last
  right (19%), the step the same (37%). The compares run on every
  column, the skips save on few: 388.6K with them. Not kept. Flags the
  server set would make the test cheaper, but at these rates they would
  save about 1K a frame.

The per-column costs are the smaller part (about 75K together); the write
loops' own overheads (the rolled loops' counts, the span loop's U and V
reloads) and the status poll on every list byte are the larger levers.

`test_tube_hreq.py` reported the drawer at 221K until this: it timed
`hd_frame` from the engine's cycle count, which `_run` had reset. It now
reads 386K over its 35 poses, the host's frame 550K with the engine.

## H4f. The slow views. — MEASURED

Means over the regression poses, and a walk from the spawn, hide the
frames that set how the game feels. `tools/tube_slow.py` times both sides
of each pose on py65: the host (the TUBE engine with its sending, then
`hd_frame` drawing the list) at 2MHz, the fill server at 3MHz. A frame
takes as long as the slower side.

`scan 500 1` (the regression's poses and 500 random standable ones, 528
in all): the slower side's time has a median of 309 ms, 90% of poses
within 634 ms, 99% within 900 ms, and a worst of 1,106 ms. The worst 20
are `poses.TUBE_SLOW`, most in the south of the map (y below -4,400, the
big outdoor area): about 90 wall segs and 4.2K of requests a frame. The
yardstick for speed work is now `tools/tube_slow.py slow` on them:

| On `TUBE_SLOW` | Cycles a frame | ms |
|---|---|---|
| Host: engine and sending | 642K | 321 |
| Host: drawing | 410K | 205 |
| Host: total | 1,052K | 526 |
| Second processor | 2,663K | **888 at 3MHz, 666 at 4MHz** |

**On the slow views the second processor is the bottleneck at both
speeds.** That reverses H4d's walk, where the host bounds the frame at
4MHz: there the views are lighter. The drawer stays near 400K whatever
the view; the second processor and the host's engine grow with it.

Second processor, by job (`prof 0 server`):

| Job | Cycles | Share |
|---|---|---|
| The fill, without its arithmetic (`st_init`, `pl_row`, `at`, `adv`, `next_col`, `tx_seg`, `trun`, ...) | 928K | 35% |
| The frame-end pass (`fs_pairs`, `fs_singles`, `fs_runs`, `fs_key`, `fs_change`, `fs_sweep`, `fs_span`) | 735K | 28% |
| The fill's arithmetic (`mul16`, `dq_core`, `m8_m2`, `dv8f`, `m16_a1z`, `div32`) | 652K | 25% |
| Reading requests (`fs_get`, `fs_frame`) | 192K | 7% |
| Marks and WALL records | 145K | 5% |

Host engine, by source (`prof 0 engine`): the request emission
(`rq_fill`, `mf_snap` in `mfill.s`) 179K, 28%; the BSP, projection and
clipping the rest (`project.s` 81K, `seg_emit.s` 65K, `fusedw.s` 52K,
`dcl.s` 48K, `view.s` 43K, `seg_xform.s` 39K, `bca.s` 38K).

The requests are 44% clip spans (184 a frame at 10 bytes). They are each
seg's own columns of the pool before and after its update; a seg's
"after" is the next one's "before" only 138 times in 1,789, so there is
no repeat to drop. Keeping the pool on the server instead would move the
clip updates onto the side that is already slower.

So, for the slow frames: the server's frame-end pass (28%) and the fill's
set-up and arithmetic (60%) first, then the host's request emission and
engine (which bound the frame once the server is faster). The drawer is
not a slow-frame lever.

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
