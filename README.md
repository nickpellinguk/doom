# Textured DOOM E1M1 on the BBC Master 128

A textured port of DOOM's E1M1 for the **BBC Master 128** (65C12 at 2 MHz,
128K: shadow RAM, sideways RAM banks 4-7, ANDY, HAZEL). The 3D view is
Mode 2 (8 colours, 160x136 in 4x2 fat pixels) and the control panel is
Mode 1, joined by a raster split. BSP traversal, angle-space culling,
trapezoid clip spans, textured walls, textured floors and ceilings, doors
and lifts, the gun overlay, and walking with collision.

The design, memory map and step-by-step history are in
[`docs/master_textured_spec.md`](docs/master_textured_spec.md).

## Running it

`doom_master.ssd` is the disc. Boot it on a Master 128 (or jsbeeb's Master
128 model) with SHIFT-BREAK, or `*RUN !BOOT`. Cursor keys turn and walk,
SPACE opens doors.

## Building

Needs `ca65` / `ld65` (cc65), Python 3 with `pygame`, `numpy` and `py65`,
and `DOOM1.WAD` (in the repo).

    python3 tools/build_master_ssd.py      # -> build/master/doom_master.ssd

## Testing

The Python models are the spec; the 6502 must match them byte for byte.

    python3 run_regression.py              # every gate + the frame-cycle gate
    python3 run_regression.py --disc       # ...and boot the disc on jsbeeb
    python3 run_regression.py --rebaseline # accept the current cycle counts

| Gate | What it proves |
|---|---|
| `test_master_assets.py` | texture and flat packing, byte formats, bank invariants |
| `test_master_engine.py` | the engine emits the recorded line list at 31 poses |
| `test_textured_ref.py` | the float textured reference's frames obey the byte rules |
| `test_fill_ref.py` | the span-diff fill model covers every on-map pixel |
| `test_tex_ref.py` | the integer wall model agrees with the float reference |
| `test_plane_ref.py` | the floor / ceiling model's maths |
| `test_master_tex.py` | the 6502 filler draws exactly what the models draw |
| `test_master_gun.py` | the gun overlay |
| `test_master_div.py` | the 6502 divides against Python |
| `tools/bakedscan.py --gate` | no new literal addresses |
| `test_master_disc.py` | the disc boots and runs on jsbeeb (needs a jsbeeb clone) |

`tools/master_profile.py` profiles a frame by routine and by job.

## Layout

| Path | Contents |
|---|---|
| `src/` | the engine (BSP walk, transform, clipper, movement) and `src/master/` (the fill, texturers, boot, HAZEL code) |
| `e1m1.py`, `wad_packed.py`, `colmap.py`, `anim_sectors.py` | the map: WAD load, packing, collision, movers, the Python traversal |
| `fp.py`, `angle_bbox.py`, `angle_seg.py`, `endpoint_spans.py`, `clip_math.py` | the fixed-point and clipping models |
| `textured_ref.py`, `fill_ref.py`, `tex_ref.py`, `plane_ref.py` | the rendering models (the spec) |
| `master_assets.py`, `master_walls.py`, `master_panel.py`, `master_gun.py`, `wall_art.py` | asset conversion |
| `banked_bsp.py`, `banked_mem.py`, `bsp_render_6502.py`, `span_clip_6502.py` | the py65 rig |
| `art/` | the converted wall, flat and gun art |
