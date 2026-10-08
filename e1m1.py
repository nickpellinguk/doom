#!/usr/bin/env python3
"""E1M1 for the Master engine: the WAD load, the alternate BSP, the packed map
layout (packed_layout / packed_rom_main / packed_rom_detail / packed_bbox_table)
that banked_bsp lays into the disc images, the vertical-span descriptors, the
mover (anim) discovery, and the Python BSP / seg traversal (packed_render_bsp,
packed_render_seg) that drives the 6502 span clipper for the reference models
(fill_ref, tex_ref, plane_ref).
"""

import os, struct, math, sys, random, pygame
import fp as fp_module
from endpoint_spans import EndpointClipSpans
from fp import (fp_mul8, fp_div8, s8,
                fp_sin, fp_cos, fp_sincos,
                fp_recip, fp_project_x, fp_project_y,
                fp_linfn, fp_eval, fp_eval_88, fp_view_context, fp_to_view, fp_near_clip,
                FP7, FP8, HALF_W, HALF_H, NEAR_FP, RECIP_FRAC_BITS,
                FP_RENDER_W, FP_RENDER_H, FP_FOCAL_X,
                MAP_CENTER_X, MAP_CENTER_Y, PRESCALE)

# ── 6502 span clipper shadow (cycle counting) ────────────────────────────────

_span_clip_6502 = None  # lazy-loaded span rig (see make_span_rig)
_span_rig_owner = None  # keeps the Master rig (and its BankedMemory) alive


def make_span_rig():
    """The span/clipper rig the Python traversal drives: a MasterBspRender's
    own `sc` (the Master images, the BankedMemory and the plot traps).
    Imported lazily -- banked_bsp imports this module.
    """
    global _span_rig_owner
    from banked_bsp import MasterBspRender
    _span_rig_owner = MasterBspRender(
        packed_layout, packed_rom_main, packed_rom_detail, packed_bbox_table,
        MAP_CENTER_X, MAP_CENTER_Y, PRESCALE)
    return _span_rig_owner.sc
_frame_clip_cycles = [0]
_frame_clip_match = [True]  # set False on any py/6502 span divergence
_clip_mismatch_reported = set()  # (x,y,a) tuples already printed
_show_integrated_fb = False  # F key: show 6502 framebuffer from integrated clip+raster
_show_seg_numbers = False    # I key: show seg indices on wireframe
_seg_annotations = []        # populated per frame: [(si, sx1, sx2, ft1, ft2)]

# Telemetry for the aperture-clip optimisation (need_bt/need_bb early skip).
# Reset by callers before a render frame; reported by diagnostic scripts.
_ap_skip_stats = {
    'bt_seen': 0, 'bt_skipped': 0,
    'bb_seen': 0, 'bb_skipped': 0,
    'solid_top_skipped': 0,    # solid top-horizontal above-clipped
    'solid_bot_skipped': 0,    # solid bot-horizontal below-clipped
    'solid_v1_skipped': 0,
    'solid_v2_skipped': 0,
    'pp_top_skipped': 0,       # portal-plain elif bch>ch line above-clipped
    'pp_bot_skipped': 0,       # portal-plain elif bfh<fh line below-clipped
    'apv_skipped': 0,          # solid aperture-edge vertical
    'step_v_skipped': 0,       # need_bt/bb step verticals (per-column)
    'fc_skipped': 0,           # front-ceil/floor line in need_bt/bb block
    'tighten_skipped': 0,      # tighten op skipped (seg fully contains spans)
    'bt_lines_saved': 0, 'bb_lines_saved': 0,
}
_AP_SKIP_DEBUG = False
_AP_SKIP_ENABLE = True   # toggle to compare on/off in tests

class Instrumented6502Spans(EndpointClipSpans):
    """EndpointClipSpans that shadows mutations to a 6502 span clipper for cycle counting.

    All Y values are biased by Y_BIAS at this wrapper level so both the
    Python reference (EndpointClipSpans) and the 6502 shadow work in
    biased coordinate space.  The bias is transparent to callers.
    """

    def __init__(self):
        super().__init__()
        # Bias the initial span Y values for 6502 u8 arithmetic.
        from endpoint_spans import Y_BIAS
        s = self.spans[0]
        self.spans = [(s[0], s[1], s[2], s[3],
                       s[4] + Y_BIAS, s[5] + Y_BIAS,
                       s[6] + Y_BIAS, s[7] + Y_BIAS)]
        self.y_display_offset = Y_BIAS
        self._update_bbox()
        global _span_clip_6502
        if _span_clip_6502 is None:
            _span_clip_6502 = make_span_rig()
        _span_clip_6502.clear_screen()
        _span_clip_6502.init()
        _frame_clip_match[0] = True

    def _check(self):
        saved = _span_clip_6502.total_cycles
        if _span_clip_6502.read_spans() != self.spans:
            _frame_clip_match[0] = False
        _span_clip_6502.total_cycles = saved  # don't count read_spans in HUD

    @staticmethod
    def _bias_y(*ys):
        """Add Y_BIAS. No clamp — _remap_seg_for_8bit handles s16 values
        via _interp_store_s16 and produces correct u8 at overlap endpoints.
        Clamping here would distort the interpolation slope for segs
        that extend far off-screen."""
        from endpoint_spans import Y_BIAS
        return tuple(y + Y_BIAS for y in ys)

    def mark_solid(self, lo, hi, sx1=None, sx2=None, yt1=None, yt2=None, yb1=None, yb2=None):
        if yt1 is not None:
            yt1, yt2, yb1, yb2 = self._bias_y(yt1, yt2, yb1, yb2)
        super().mark_solid(lo, hi, sx1=sx1, sx2=sx2, yt1=yt1, yt2=yt2, yb1=yb1, yb2=yb2)
        _span_clip_6502.mark_solid(lo, hi, sx1=sx1, sx2=sx2, yt1=yt1, yt2=yt2, yb1=yb1, yb2=yb2)
        self._check()

    def fused_begin(self):
        _span_clip_6502.fused_begin()

    def draw_fused(self, lx1, ly1, lx2, ly2, side):
        """FUSED (2026-08-25, sequential by decree): one armed aperture
        line — the 6502 clips, plots and applies in one walk; the twin
        is synced from the pool afterwards (the 6502 is the authority;
        the batch model is dead)."""
        from endpoint_spans import Y_BIAS
        _span_clip_6502.draw_fused_line(lx1, ly1 + Y_BIAS, lx2, ly2 + Y_BIAS,
                                        side)
        self._sync_from_6502()

    def fused_finish(self, lo, hi, yt1, yt2, yb1, yb2):
        yt1, yt2, yb1, yb2 = self._bias_y(yt1, yt2, yb1, yb2)
        _span_clip_6502.fused_finish(lo, hi, yt1, yt2, yb1, yb2)
        self._sync_from_6502()

    def _sync_from_6502(self):
        saved = _span_clip_6502.total_cycles
        self.spans = _span_clip_6502.read_spans()
        _span_clip_6502.total_cycles = saved
        self._update_bbox()

    def draw_clipped(self, lines, color, surface, stats=None, roles=None):
        """Forward lines to 6502 DCL for clipped emission.

        roles: optional dict mapping line index → records buffer address.
        When set, DCL writes per-span verdict records during emission for
        that line — used to feed the subsequent tighten() call without an
        extra DCL pass (records are 'free' since DCL is running anyway).
        """
        from endpoint_spans import Y_BIAS
        biased = [(lx1, ly1 + Y_BIAS, lx2, ly2 + Y_BIAS) for lx1, ly1, lx2, ly2 in lines]
        for (lx1, ly1, lx2, ly2) in biased:
            _span_clip_6502.draw_clipped_line(lx1, ly1, lx2, ly2)
        super().draw_clipped(biased, color, surface, stats)

    def line_above_spans(self, lx1, ly1, lx2, ly2, _dbg=False):
        # Bias line Y to match the biased spans before delegating.
        from endpoint_spans import Y_BIAS
        return super().line_above_spans(lx1, ly1 + Y_BIAS, lx2, ly2 + Y_BIAS, _dbg=_dbg)

    def line_below_spans(self, lx1, ly1, lx2, ly2):
        from endpoint_spans import Y_BIAS
        return super().line_below_spans(lx1, ly1 + Y_BIAS, lx2, ly2 + Y_BIAS)

    def vertical_outside_spans(self, sx, y_lo, y_hi):
        from endpoint_spans import Y_BIAS
        return super().vertical_outside_spans(sx, y_lo + Y_BIAS, y_hi + Y_BIAS)

    def has_gap(self, lo, hi):
        # Return the 6502's verdict (not the Python model's): the engine's
        # traversal descends on ITS pool state, and the two span
        # representations drift ±1 row where the records-driven 6502 tighten
        # and the legacy u8-interp Python tighten pick different split
        # anchors. Taking the 6502 answer keeps the Python reference's
        # traversal decisions engine-exact; the Python model is still
        # queried so lockstep drift remains observable via _check.
        super().has_gap(lo, hi)
        return _span_clip_6502.has_gap(lo, hi)

    def is_full(self):
        super().is_full()
        return _span_clip_6502.is_full()


# ── WAD parsing ──────────────────────────────────────────────────────────────

def load_wad(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, numlumps, dirofs = struct.unpack_from("<4sII", data, 0)
    directory = []
    for i in range(numlumps):
        off = dirofs + i * 16
        fpos, size = struct.unpack_from("<II", data, off)
        name = data[off+8:off+16].split(b"\x00")[0].decode("ascii", "replace")
        directory.append((name, fpos, size))
    return data, directory

def find_map_lumps(directory, mapname):
    for i, (name, _, _) in enumerate(directory):
        if name == mapname:
            return {directory[i+j][0]: (directory[i+j][1], directory[i+j][2])
                    for j in range(1, 11)}
    sys.exit(f"Map {mapname} not found")

def parse_lump(data, lumps, name, fmt):
    pos, size = lumps[name]
    sz = struct.calcsize(fmt)
    return [struct.unpack_from(fmt, data, pos + i * sz) for i in range(size // sz)]

# ── Load E1M1 ────────────────────────────────────────────────────────────────

data, directory = load_wad("DOOM1.WAD")
lumps = find_map_lumps(directory, "E1M1")

vertexes  = parse_lump(data, lumps, "VERTEXES",  "<hh")
linedefs  = parse_lump(data, lumps, "LINEDEFS",  "<HHHHHHH")
sidedefs  = parse_lump(data, lumps, "SIDEDEFS",  "<hh8s8s8sH")
sectors   = parse_lump(data, lumps, "SECTORS",   "<hh8s8sHHH")
segs      = parse_lump(data, lumps, "SEGS",      "<HHhHHH")
ssectors  = parse_lump(data, lumps, "SSECTORS",  "<HH")
nodes     = parse_lump(data, lumps, "NODES",     "<hhhhhhhhhhhhHH")
things    = parse_lump(data, lumps, "THINGS",    "<hhHHH")

# ── BSP source (default: the checked-in zokumbsp depth tree) ────────────
# e1m1_zkdepth.wad = zokumbsp -na=d on the PRE-SHRUNK map (coords already
# in engine wu = (orig - MAP_CENTER)/PRESCALE): 220 nodes / 221 ss / 717
# segs / 69 splits, measured -3.3%..-3.5% engine cycles vs the id BSP
# (adopted 2026-07-14; bake-off in project_bsp_tree_algorithm). Geometry
# + BSP lumps are substituted UPSCALED back to original units (x8 +
# center); the normal quantization below then reproduces the builder's
# wu coords bit-exactly (integer roundtrip: _prescale_round(v*8,8) == v),
# so the packer, the float reference and the NOVT preprocessing all run
# unchanged. THINGS (player spawn) stay from the original WAD.
# Override: DOOM_ALT_WAD=<path> for another tree, DOOM_ALT_WAD=orig for
# the id BSP. Known follow-up: the 31 residual quantized side violations
# have a solved fixup (side-preserving split nudges, zk_fixup.json).
_ALT_WAD = os.environ.get('DOOM_ALT_WAD', 'e1m1_zkdepth.wad')
if _ALT_WAD in ('orig', 'none', '0', ''):
    _ALT_WAD = None
if _ALT_WAD:
    _ad, _adir = load_wad(_ALT_WAD)
    _al = find_map_lumps(_adir, "E1M1")
    vertexes = [(v[0] * PRESCALE + MAP_CENTER_X,
                 v[1] * PRESCALE + MAP_CENTER_Y)
                for v in parse_lump(_ad, _al, "VERTEXES", "<hh")]
    linedefs = parse_lump(_ad, _al, "LINEDEFS", "<HHHHHHH")
    sidedefs = parse_lump(_ad, _al, "SIDEDEFS", "<hh8s8s8sH")
    sectors  = parse_lump(_ad, _al, "SECTORS",  "<hh8s8sHHH")
    segs     = parse_lump(_ad, _al, "SEGS",     "<HHhHHH")
    ssectors = parse_lump(_ad, _al, "SSECTORS", "<HH")
    # NODES: x,y,dx,dy, right bbox [top,bot,left,right] (y,y,x,x),
    # left bbox [4], child_r, child_l
    nodes = [(n[0] * PRESCALE + MAP_CENTER_X,
              n[1] * PRESCALE + MAP_CENTER_Y,
              n[2] * PRESCALE, n[3] * PRESCALE,
              n[4] * PRESCALE + MAP_CENTER_Y,
              n[5] * PRESCALE + MAP_CENTER_Y,
              n[6] * PRESCALE + MAP_CENTER_X,
              n[7] * PRESCALE + MAP_CENTER_X,
              n[8] * PRESCALE + MAP_CENTER_Y,
              n[9] * PRESCALE + MAP_CENTER_Y,
              n[10] * PRESCALE + MAP_CENTER_X,
              n[11] * PRESCALE + MAP_CENTER_X,
              n[12], n[13])
             for n in parse_lump(_ad, _al, "NODES", "<hhhhhhhhhhhhHH")]
    # Optional sibling-leaf merges (DOOM_ALT_MERGE=N) to squeeze a tree
    # under the engine's 256-node/256-ss SoA pages: a node whose children
    # are both subsectors becomes a leaf (children's seg runs
    # concatenated). Approximation note: the merged pair drains its
    # deferred ops once instead of twice, so the second child's draws see
    # spans the first child has not yet tightened — pairs are picked by
    # smallest combined seg count to minimize that surface. Engine, pyref
    # and the float reference all consume the same merged data, so every
    # exactness gate still binds.
    for _merge in range(int(os.environ.get('DOOM_ALT_MERGE', '0'))):
        _best = None
        for _ni, _n in enumerate(nodes):
            _cr, _cl = _n[12], _n[13]
            if (_cr & 0x8000) and (_cl & 0x8000):
                _sr, _sl = _cr & 0x7FFF, _cl & 0x7FFF
                _sz = ssectors[_sr][0] + ssectors[_sl][0]
                if _best is None or _sz < _best[0]:
                    _best = (_sz, _ni, _sr, _sl)
        _sz, _ni, _sr, _sl = _best
        # Rebuild the seg array with the left child's run appended to the
        # right child's; recompute every ss (count, first).
        _new_segs, _new_ss, _remap = [], [], {}
        for _si, (_cnt, _first) in enumerate(ssectors):
            if _si == _sl:
                continue
            _nf = len(_new_segs)
            _new_segs.extend(segs[_first:_first + _cnt])
            if _si == _sr:
                _lc, _lf = ssectors[_sl]
                _new_segs.extend(segs[_lf:_lf + _lc])
            _remap[_si] = len(_new_ss)
            _new_ss.append((len(_new_segs) - _nf, _nf))
        segs, ssectors = _new_segs, _new_ss
        # Drop node _ni, retarget its parent at the merged subsector,
        # renumber node ids above it and remap every ss reference.
        _new_nodes = []
        for _i, _n in enumerate(nodes):
            if _i == _ni:
                continue
            _ch = []
            for _c in (_n[12], _n[13]):
                if _c & 0x8000:
                    _ch.append(0x8000 | _remap[_c & 0x7FFF])
                elif _c == _ni:
                    _ch.append(0x8000 | _remap[_sr])
                else:
                    _ch.append(_c - 1 if _c > _ni else _c)
            _new_nodes.append(_n[:12] + tuple(_ch))
        nodes = _new_nodes
        print(f"ALT MERGE: node {_ni} -> merged ss ({_sz} segs combined)")
    print(f"ALT BSP: {_ALT_WAD} — {len(vertexes)} verts, "
          f"{len(nodes)} nodes, {len(ssectors)} ss, {len(segs)} segs")

for t in things:
    if t[3] == 1:
        player_x, player_y, pangle = float(t[0]), float(t[1]), t[2]
        break

# ── Node sense normalization (axis 2026-07-16, diagonal 2026-08-20) ─────
# Swapping a node's children is BSP-equivalent to negating its partition
# direction (D flips sign, the side flips, children + per-side bboxes
# swap), so every axis-aligned node is normalized to the '>' sense:
# dx==0 -> dy>0 (side0 iff px>nx), dy==0 -> dx<0 (side0 iff py>ny); and
# every GENERAL node to dy>0, which pins DSGN b7 = 0 — the walk's P1
# sign is then sign(dx) directly and the EOR died. Node DIR entries are
# magnitude-keyed at pack time, so this costs no table growth. Ties flip
# on swapped nodes (D==0 -> side1 -> what used to be the right child) —
# an arbitrary-tie decision by decree; every consumer (float arbiter,
# fp/packed mirrors, packer bbox + leaf-flag bakes) reads THIS list, so
# all implementations move together.
nodes = [
    (n[0], n[1], -n[2], -n[3],
     n[8], n[9], n[10], n[11],           # left bbox -> right slot
     n[4], n[5], n[6], n[7],             # right bbox -> left slot
     n[13], n[12])                       # children swap
    if ((n[2] == 0 and n[3] < 0) or (n[3] == 0 and n[2] > 0)
        or (n[2] != 0 and n[3] < 0))
    else tuple(n)
    for n in nodes]

# ── Always-descend policy (2026-08-20, tools/adesc_sweep.py) ────────────
# (node, boxside) check sites whose bbox_visible cost exceeds what their
# rare rejects save — measured never-worse at every corpus location. The
# packer bakes them as DSGN b2 (right box) / b3 (left box); the walk and
# BOTH python mirrors skip the check and descend. Node ids are
# POST-transform (this list).
# 2026-09-04: the bake is OFF by default (wad_packed.py) — DSGN b2/b3 are
# runtime state for the walk's DYNAMIC always-descend bits, and the static
# policy measured a net loss once those run.  The mirrors follow the packer,
# so DOOM_ADESC_ON restores both together for a measurement run.
try:
    import json as _json
    if not os.environ.get('DOOM_ADESC_ON'):
        ADESC = set()                        # bits are runtime state now
    else:
        ADESC = {tuple(p) for p in _json.load(
            open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'adesc_policy.json')))['wins']}
except Exception:
    ADESC = set()


# ── Prescaled data for 8-bit fixed-point path ───────────────────────────
#
# Center on map and divide by 8 so all vertex/height values fit in 8 bits.
# Heights are prescaled by (PRESCALE / 1.2) instead of PRESCALE, baking in
# the 1.2x aspect ratio correction.  This allows a single reciprocal table
# for both X and Y projection.

from fp import ASPECT_NUM, ASPECT_DEN

def _prescale_round(val, ps):
    """Divide by ps with round-to-nearest (reduces max quantization error
    from ~0.875 to 0.500 prescaled units vs floor division)."""
    if val >= 0:
        return (val + ps // 2) // ps
    else:
        return -((-val + ps // 2) // ps)

fp_vertexes = [
    (_prescale_round(v[0] - MAP_CENTER_X, PRESCALE),
     _prescale_round(v[1] - MAP_CENTER_Y, PRESCALE))
    for v in vertexes
]

def _prescale_height(h):
    """Prescale a height value with 1.2x aspect baked in.

    ROUND-TO-NEAREST (2026-08-28): the bias used to be ASPECT_DEN//2 = 2
    against a divisor of PRESCALE*ASPECT_DEN = 40 — effectively
    truncation (72 wu -> 10.80q -> 10). Screen error of a height is the
    quantization residual x rows-per-quantum (~17 rows/q at 60 wu), so
    the wrong bias alone cost up to ~8 rows on near walls (the 009C.9A
    displaced-edge class). Proper half bias: 10.80q -> 11.
    """
    return (h * ASPECT_NUM + (PRESCALE * ASPECT_DEN) // 2) // (PRESCALE * ASPECT_DEN)

# ── Step-evening height overrides (Eben, 2026-09-05) ─────────────────────
#
# APPLIED HERE, not at the SECTORS parse: the alt-BSP reload above
# re-reads SECTORS out of e1m1_zkdepth.wad and would silently drop an
# override written any earlier.  This is the last write to the table, and
# everything below — fp_sectors, player_floor, fp_objects, the packer, the
# float arbiter — reads it from here.  Objects therefore need no separate
# treatment: fp_objects takes each thing's z from player_floor(), so the
# four armour bonuses in the alcove ride with the floor they stand on.
#
# THE DEFECT.  The engine height quantum is
# PRESCALE*ASPECT_DEN/ASPECT_NUM = 8*5/6 = 6.667 wu.  E1M1 builds this
# alcove from 8 wu risers, which is 1.20 quanta — not a whole number, so
# _prescale_height's round-to-nearest has to ALTERNATE the riser it emits:
# 1, 1, 2.  A 2-versus-1 quantum step is twice its neighbour, and that is
# what reads as an uneven top step on screen.
#
# THE FIX is to re-cut the flight to a whole number of quanta per step —
# its PITCH.  Pitch 1 here: three risers of one quantum, 7 wu each, which
# is uniform in the world AND in quanta.  The alcove floor comes down 3 wu
# and the two steps with it; the foot is pinned by room 7.
#
# WHY THE ARMOUR STAIR IS NOT IN THIS TABLE.  Its risers are 16 wu = 2.40
# quanta, and re-cutting it runs into the step rule.  DOOM blocks a climb
# over MAXSTEPHEIGHT = 24 wu; the engine has only quantised heights, so it
# tests against colmap's STEP_PS = 4 quanta, and because the two floors
# round INDEPENDENTLY a true 24 wu gap can come out as 3 or 4 quanta.  No
# integer STEP_PS reproduces the rule exactly, so colmap asserts that the
# two agree on every passable pair.  Room 24 touches all six steps
# directly — the flight is a set of strips in the middle of the room, not
# a corridor — so the rule is checked room-to-step six times.  Room 24 is
# at q-1, and a step at q3 sits in the world band 17..23, i.e. 25..31 wu
# above it: always over DOOM's 24, yet exactly 4 quanta, which the engine
# would allow.  Any q3 step touching room 24 is therefore a build error.
# Pitch 2 from q1 lands step 2 on q3 and is refused; pitch 2 from q0 and
# pitch 3 from q2 both skip q3 and are legal.  See project_step_evening.
_STEP_EVEN = {
    # --- helmet alcove off room 7, PITCH 1: three risers of 1 quantum ---
     8: (    7, None),   # step 1        was   8   q1  (was q1)
    51: (   14, None),   # step 2        was  16   q2  (was q2)
    52: (   21, None),   # alcove floor  was  24   q3  (was q4)
    # --- the start room's window ledges (2026-10-08): from the walkway
    # in front of them (floor -16, eye 25 -> q4 = 26.7) the 8 wu ledge
    # (q1) sat 20 wu below the eye, not 17, so close to the windows the
    # visible top of the sill fell off the bottom of the view and the
    # ledge floor showed instead. 13 wu is q2 (13.3): within ~1.3 wu of
    # the engine from the walkway and from the room (eye 41 -> q6). The
    # ledges sit behind impassable window lines: no step rule applies.
    14: (   13, None),   # south ledge   was   8   q2  (was q1)
    15: (   13, None),   # north ledge   was   8   q2  (was q1)
}
sectors = [
    (_ov[0] if (_ov := _STEP_EVEN.get(_i)) else _s[0],
     _ov[1] if _ov and _ov[1] is not None else _s[1],
     *_s[2:])
    for _i, _s in enumerate(sectors)
]

fp_sectors = [
    (_prescale_height(s[0]), _prescale_height(s[1]), *s[2:])
    for s in sectors
]

# ── Helpers ──────────────────────────────────────────────────────────────────

# ── One-way windows (Eben, 2026-08-14): the three windows into the main
# courtyard are blocked at the ROOM side of their windowledge sectors
# (14/15 off the spawn room 39, 46 off room 61 across the yard): from the
# courtyard the window still reads as an opening onto the ledge, but the
# wall at the back of the ledge is SOLID — you see out, never in, and the
# long-range across-the-courtyard views die.  Implemented as a per-seg
# back-sector override in this one resolver: front == the walled (ledge)
# side => back=None (one-sided), so the packer bakes SF_SOLID, occlusion
# mark_solids the full columns, and every world (float arbiter, fp,
# packed, 6502) inherits identically.
# Master textured port (fill_ref.py): called by packed_render_seg with each
# drawn seg's column range and front ceiling/floor lines, before its clip ops.
_seg_fill_hook = None

_ONEWAY_WALLED_SIDE = {26: 14, 29: 15, 275: 46}   # linedef -> blind-side sector

def seg_sectors(seg):
    ld = linedefs[seg[3]]
    right_side, left_side = ld[5], ld[6]
    if seg[4] == 0:
        front = sidedefs[right_side][5]
        back  = sidedefs[left_side][5] if left_side != 0xFFFF else None
    else:
        front = sidedefs[left_side][5] if left_side != 0xFFFF else sidedefs[right_side][5]
        back  = sidedefs[right_side][5] if left_side != 0xFFFF else None
    if back is not None and _ONEWAY_WALLED_SIDE.get(seg[3]) == front:
        return front, None
    return front, back

NF_SUBSECTOR = 0x8000

def point_on_side(x, y, node):
    dx, dy = x - node[0], y - node[1]
    return 0 if (node[3] * dx - node[2] * dy) > 0 else 1

def find_subsector(x, y):
    nid = len(nodes) - 1
    while not (nid & NF_SUBSECTOR):
        node = nodes[nid]
        nid = node[12] if point_on_side(x, y, node) == 0 else node[13]
    return nid & 0x7FFF

def player_floor(x, y):
    ss = ssectors[find_subsector(x, y)]
    s = segs[ss[1]]
    ld = linedefs[s[3]]
    sd_idx = ld[5] if s[4] == 0 else ld[6]
    if sd_idx == 0xFFFF: sd_idx = ld[5]
    return sectors[sidedefs[sd_idx][5]][0]

# ── Animatable (mover) sectors — DOOM_ANIM=1 (prototype) ────────────────
#
# Sectors whose floor or ceiling moves at runtime (doors, lifts, one-shot
# floors), discovered from linedef specials.  Build passes that bake
# height assumptions (seg stripping, NOVT suppression, VWH dedup) treat
# their bounding segs conservatively so heights become pure runtime
# inputs for them.  Off by default: the build is byte-identical unless
# DOOM_ANIM=1 (see anim_sectors.py for the runtime patcher).
# 2026-07-10: the static ("walk") variant is DISCARDED — animated sectors
# are the only build. One packed layout (662 segs, 469 verts) everywhere,
# which is what lets the ROM layout be assembly-time constant.
_ANIM_ENABLED = True
_DOOR_SPECIALS = {1, 26, 27, 28, 31, 46, 61, 63, 86, 90, 103, 117, 118}
_TAG_MOVER_SPECIALS = {          # sector found via tag; moving part:
    88: 'floor', 62: 'floor', 10: 'floor', 21: 'floor',   # lifts
    36: 'floor', 37: 'floor', 38: 'floor', 70: 'floor',   # floor lowers
    102: 'floor', 82: 'floor', 23: 'floor',
}
ANIM_SECTORS = {}                # sector idx -> 'ceil' (doors) | 'floor'
if _ANIM_ENABLED:
    for _ld in linedefs:
        _spec, _tag = _ld[3], _ld[4]
        if _spec in _DOOR_SPECIALS and _ld[6] != 0xFFFF:
            ANIM_SECTORS[sidedefs[_ld[6]][5]] = 'ceil'
        elif _spec in _TAG_MOVER_SPECIALS and _tag:
            for _si2, _sec2 in enumerate(sectors):
                if _sec2[6] == _tag:
                    ANIM_SECTORS[_si2] = _TAG_MOVER_SPECIALS[_spec]
    print(f"ANIM: {len(ANIM_SECTORS)} mover sectors: {sorted(ANIM_SECTORS)}")

def _seg_touches_anim(s):
    fi, bi = seg_sectors(s)
    return fi in ANIM_SECTORS or (bi is not None and bi in ANIM_SECTORS)

# ── Strip invisible segs for fixed-point path ────────────────────────────
#
# Two-sided segs with identical floor AND ceiling on both sides are pure
# lighting/trigger boundaries.  They produce no draws and their tighten is
# a no-op.  Strip them and rebuild the subsector seg table.
# Mover-bounding segs are never stripped: their heights change at runtime
# (a lift flush with the adjacent floor still needs its boundary segs).

def _is_renderable(s):
    if ANIM_SECTORS and _seg_touches_anim(s):
        return True
    fi, bi = seg_sectors(s)
    if bi is None: return True
    return sectors[fi][0] != sectors[bi][0] or sectors[fi][1] != sectors[bi][1]

# ── Dead-seg elimination (2026-08-29, memo-dead-seg-proofs.md) ──────────
#
# A seg whose ENGINE draw half-plane contains no reachable floor never
# renders and never occludes (occlusion comes only from drawn segs), so
# it can be stripped with proof rather than sampling:
#   R+  = sectors flood-reachable from the player spawn; a two-sided edge
#         is passable iff step <= 24 AND opening >= 56 at SOME mover
#         phase (conditions are monotone in each mover height, so the
#         [far, rest] endpoint OR is an exact exists-phase test — this
#         covers falls of any depth AND riding a lift between poses).
#         One-sided lines always block; ML_BLOCKING is IGNORED, radius
#         is IGNORED (both over-approximate: strictly safe).
#   cert = the draw predicate is linear and half-planes are convex, so
#         "no reachable point draws" reduces to checking every VERTEX of
#         every R+ sector, in exact integers.  The predicate mirrors
#         wad_packed's C-form bake: folded primitive dir (pdx,pdy) =
#         ±linedef delta (side 1 negates); draw side is CLOSED (tie
#         draws) for verticals with pdy>0, horizontals with pdx<0 and
#         diagonals with pdy>0 — the pack-time C-1 / tie-rule fold —
#         and STRICT otherwise.
# Audit: tools/prove_dead_segs.py re-proves the list against the PACKED
# bytes of a DOOM_NO_DEADSTRIP=1 build.  Kill switch: DOOM_NO_DEADSTRIP.

def _dead_seg_set():
    import collections as _cl
    movers = ANIM_SECTORS or {}
    def _far(s):
        fh, ch = sectors[s][0], sectors[s][1]
        nb = set()
        for _ld in linedefs:
            _ss = [sidedefs[sd][5] for sd in (_ld[5], _ld[6]) if sd != 0xFFFF]
            if s in _ss: nb.update(x for x in _ss if x != s)
        if movers[s] == 'ceil': ch = min(sectors[n][1] for n in nb) - 4
        else:                   fh = min(sectors[n][0] for n in nb)
        return fh, ch
    def _phases(s):
        rest = (sectors[s][0], sectors[s][1])
        return [rest, _far(s)] if s in movers else [rest]
    adj = _cl.defaultdict(set)
    for _ld in linedefs:
        r, l = _ld[5], _ld[6]
        if r == 0xFFFF or l == 0xFFFF: continue
        sr, sl = sidedefs[r][5], sidedefs[l][5]
        for fr, cr in _phases(sr):
            for fl, cl in _phases(sl):
                if min(cr, cl) - max(fr, fl) < 56: continue
                if fl - fr <= 24: adj[sr].add(sl)
                if fr - fl <= 24: adj[sl].add(sr)
    _spawn = next((t for t in things if t[3] == 1), None)
    if _spawn is None: return set(), 0
    _sx, _sy = _spawn[0], _spawn[1]
    _nid = len(nodes) - 1
    while not (_nid & NF_SUBSECTOR):
        _n = nodes[_nid]
        _nid = _n[12] if (_n[3]*(_sx-_n[0]) - _n[2]*(_sy-_n[1])) > 0 else _n[13]
    _s0 = segs[ssectors[_nid & 0x7FFF][1]]
    _sec = seg_sectors(_s0)[0]
    reach = {_sec}; _work = [_sec]
    while _work:
        s = _work.pop()
        for t in adj[s]:
            if t not in reach: reach.add(t); _work.append(t)
    _rverts = set()
    for _ld in linedefs:
        if any(sidedefs[sd][5] in reach for sd in (_ld[5], _ld[6]) if sd != 0xFFFF):
            _rverts.add(vertexes[_ld[0]]); _rverts.add(vertexes[_ld[1]])
    dead = set()
    for _si, s in enumerate(segs):
        if ANIM_SECTORS and _seg_touches_anim(s): continue
        _ld = linedefs[s[3]]
        (lx, ly), (mx, my) = vertexes[_ld[0]], vertexes[_ld[1]]
        sgn = 1 if s[4] == 0 else -1
        pdx, pdy = sgn * (mx - lx), sgn * (my - ly)
        if pdx == 0 and pdy == 0: continue
        closed = (pdy > 0) if pdy != 0 else (pdx < 0)
        ok = True
        for (vx, vy) in _rverts:
            dot = pdy * (vx - lx) - pdx * (vy - ly)
            if dot > 0 or (dot == 0 and closed):
                ok = False; break
        if ok: dead.add(_si)
    return dead, len(reach)

import os as _os_ds
_dead_segs = set()
if not _os_ds.environ.get('DOOM_NO_DEADSTRIP'):
    _dead_segs, _n_reach = _dead_seg_set()
    if _dead_segs:
        print(f"Dead-seg elimination: {len(_dead_segs)} segs certified "
              f"back-facing from all reachable floor (R+ = {_n_reach} sectors)")

_stripped_segs = []
_stripped_ssectors = []
_strip_count = 0
for _ssi, _ss in enumerate(ssectors):
    _first = len(_stripped_segs)
    for _si in range(_ss[1], _ss[1] + _ss[0]):
        if _si not in _dead_segs and _is_renderable(segs[_si]):
            _stripped_segs.append(segs[_si])
        else:
            _strip_count += 1
    _stripped_ssectors.append((len(_stripped_segs) - _first, _first))

# ── Vertex With Height (VWH) table ──────────────────────────────────────
#
# Each VWH is a unique (vertex_index, prescaled_height) pair.
# Segs reference VWH indices for cached Y projection.

_vwh_map = {}   # (vertex_idx, height) -> vwh_idx
_vwh_table = [] # vwh_idx -> (vertex_idx, height)

def _vwh(vertex_idx, height):
    """Get or create a VWH index for (vertex, height)."""
    key = (vertex_idx, height)
    idx = _vwh_map.get(key)
    if idx is None:
        idx = len(_vwh_table)
        _vwh_table.append(key)
        _vwh_map[key] = idx
    return idx

# Mover heights get PRIVATE (un-shared) VWH slots: the dedup above shares
# a (vertex, height) entry across sectors, so mutating a mover's entry
# would corrupt every other user of that height at that vertex.  One slot
# per (mover sector, vertex, floor|ceil) — segs bounding the same mover
# share it (they move together).  ANIM_VWH_SLOTS[(sector, 'f'|'c')] lists
# the slot indices the runtime patcher must rewrite on height change.
_anim_vwh_map = {}
ANIM_VWH_SLOTS = {}

def _vwh_anim(sector_idx, which, vertex_idx, height):
    key = (sector_idx, vertex_idx, which)
    idx = _anim_vwh_map.get(key)
    if idx is None:
        idx = len(_vwh_table)
        _vwh_table.append((vertex_idx, height))
        _anim_vwh_map[key] = idx
        ANIM_VWH_SLOTS.setdefault((sector_idx, which), []).append(idx)
    return idx

# Build VWH-augmented seg table: original seg fields + 4 front VWH indices
# + 4 back VWH indices (or -1 if one-sided) + linedef deltas (ldx, ldy).
#
# Invariant: ldx and ldy must fit in s8.  The 6502 back-face test reads
# them as single-byte signed values and uses an s8×s16 multiply.  The
# Python FP renderer uses the same values so the two paths agree.
# A linedef exceeding this bound would require a wider multiply primitive
# on the 6502 and would silently diverge from the reference — assert here
# so the failure is loud at load time instead of a subtle rendering bug
# on some other map.
_fp_segs_vwh = []
for _i, _s in enumerate(_stripped_segs):
    _fi, _bi = seg_sectors(_s)
    _fs = fp_sectors[_fi]
    _fh, _ch = _fs[0], _fs[1]
    _v1, _v2 = _s[0], _s[1]
    if _fi in ANIM_SECTORS:
        _vwh_ft1 = _vwh_anim(_fi, 'c', _v1, _ch)
        _vwh_fb1 = _vwh_anim(_fi, 'f', _v1, _fh)
        _vwh_ft2 = _vwh_anim(_fi, 'c', _v2, _ch)
        _vwh_fb2 = _vwh_anim(_fi, 'f', _v2, _fh)
    else:
        _vwh_ft1 = _vwh(_v1, _ch)
        _vwh_fb1 = _vwh(_v1, _fh)
        _vwh_ft2 = _vwh(_v2, _ch)
        _vwh_fb2 = _vwh(_v2, _fh)
    if _bi is not None:
        _bs = fp_sectors[_bi]
        if _bi in ANIM_SECTORS:
            _vwh_bt1 = _vwh_anim(_bi, 'c', _v1, _bs[1])
            _vwh_bb1 = _vwh_anim(_bi, 'f', _v1, _bs[0])
            _vwh_bt2 = _vwh_anim(_bi, 'c', _v2, _bs[1])
            _vwh_bb2 = _vwh_anim(_bi, 'f', _v2, _bs[0])
        else:
            _vwh_bt1 = _vwh(_v1, _bs[1])
            _vwh_bb1 = _vwh(_v1, _bs[0])
            _vwh_bt2 = _vwh(_v2, _bs[1])
            _vwh_bb2 = _vwh(_v2, _bs[0])
    else:
        _vwh_bt1 = _vwh_bb1 = _vwh_bt2 = _vwh_bb2 = -1
    # Linedef delta (for back-face test), asserted s8 — matches 6502 packing.
    _ld = linedefs[_s[3]]
    _lv1 = fp_vertexes[_ld[0]]
    _lv2 = fp_vertexes[_ld[1]]
    _ldx = _lv2[0] - _lv1[0]
    _ldy = _lv2[1] - _lv1[1]
    assert -128 <= _ldx <= 127, (
        f"seg {_i} linedef {_s[3]}: prescaled ldx={_ldx} exceeds s8; "
        f"raw linedef longer than {PRESCALE * 127} units on this axis")
    assert -128 <= _ldy <= 127, (
        f"seg {_i} linedef {_s[3]}: prescaled ldy={_ldy} exceeds s8; "
        f"raw linedef longer than {PRESCALE * 127} units on this axis")
    _fp_segs_vwh.append((_s, _fi, _bi, _fh, _ch,
                         _vwh_ft1, _vwh_fb1, _vwh_ft2, _vwh_fb2,
                         _vwh_bt1, _vwh_bb1, _vwh_bt2, _vwh_bb2,
                         _ldx, _ldy))

fp_segs = _stripped_segs
fp_segs_vwh = _fp_segs_vwh
fp_ssectors = _stripped_ssectors
vwh_table = _vwh_table

# ── Colinear seg merge pass ─────────────────────────────────────────────
#
# Some maps have adjacent linedefs that are colinear (a long wall built
# as multiple linedefs in the editor). Within a subsector, if two
# consecutive segs (a, b) lie on the same infinite line (cross product
# zero), are contiguous (a.v2 == b.v1), and share the same front/back
# sectors (so identical heights, flags, VWH indices at the shared
# vertex), they can be merged into a single seg spanning v1_a → v2_b.
# This saves one seg header + detail record in ROM and one run through
# render_seg / tighten at runtime per merged pair in a visible frame.
#
# Safe because:
#  - Convex subsector + single colinear line → no geometric issue.
#  - The back-face test uses linedef direction sign, which is preserved
#    when we pick either original linedef's (lv1, ldx, ldy).
#  - VWH indices at v1/v2 come from the original segs at those vertices;
#    the intermediate vertex's VWH entries stay in the table (still
#    referenced by other segs in other subsectors).

def _colinear(v1, v2, v3):
    # True if v3 lies on the line through v1, v2 (cross product zero).
    return (v2[0] - v1[0]) * (v3[1] - v1[1]) == (v2[1] - v1[1]) * (v3[0] - v1[0])

def _try_merge_svwh(a, b):
    """Merge two seg-with-vwh tuples if they're colinear/contiguous with
    matching sectors. Returns merged tuple, or None if not mergeable."""
    sa, ba_front, ba_back = a[0], a[1], a[2]
    sb, bb_front, bb_back = b[0], b[1], b[2]
    # Same front/back sectors
    if ba_front != bb_front or ba_back != bb_back:
        return None
    # Contiguous: a.v2 == b.v1
    if sa[1] != sb[0]:
        return None
    # Colinear: does b.v2 lie on the line (a.v1, a.v2)?
    av1 = fp_vertexes[sa[0]]
    av2 = fp_vertexes[sa[1]]
    bv2 = fp_vertexes[sb[1]]
    if not _colinear(av1, av2, bv2):
        return None
    # Build merged seg tuple: v1 from a, v2 from b, other fields from a.
    # (angle/linedef/side come from a — the back-face test uses a's
    # lv1/ldx/ldy, which describe the same line as b's since colinear.)
    merged_s = (sa[0], sb[1]) + sa[2:]
    # VWH: front/back top/bot at new v1 (from a) and new v2 (from b).
    # Layout: (seg, front_sector, back_sector, fh, ch,
    #          vwh_ft1, vwh_fb1, vwh_ft2, vwh_fb2,
    #          vwh_bt1, vwh_bb1, vwh_bt2, vwh_bb2,
    #          ldx, ldy)
    return (merged_s, a[1], a[2], a[3], a[4],
            a[5], a[6], b[7], b[8],           # front VWH: v1 from a, v2 from b
            a[9], a[10], b[11], b[12],        # back VWH: same
            a[13], a[14])                      # ldx, ldy from a (same line as b)

_merged_segs = []
_merged_ssectors = []
_merge_count = 0
import os as _os_mod
_os_env_nochain = bool(_os_mod.environ.get('DOOM_NO_CHAINORD'))  # A/B probe
for _ssi, (_count, _first) in enumerate(fp_ssectors):
    _out_first = len(_merged_segs)
    # Copy segs for this subsector, attempting to merge consecutive pairs.
    _pending = list(fp_segs_vwh[_first:_first + _count])
    _out = []
    for _seg in _pending:
        if _out:
            _merged = _try_merge_svwh(_out[-1], _seg)
            if _merged is not None:
                _out[-1] = _merged
                _merge_count += 1
                continue
        _out.append(_seg)
    # ── Chain-order the subsector's segs (2026-07-10): reorder so that
    # consecutive segs share a vertex (v2 of one == v1 of the next)
    # wherever the boundary allows. The 6502 seg loop reuses the whole
    # shared-vertex setup (transform, sx, recip, front sy pair) for a
    # chained v1, so maximising chains is a straight cycle win. Greedy:
    # chain heads are segs whose v1 is no other seg's v2; walk each chain;
    # closed loops (fully bounded subsectors) break at the lowest index
    # for determinism. Both pipelines consume the same reordered list, so
    # lockstep is preserved by construction.
    if len(_out) > 2 and not _os_env_nochain:
        _by_v1 = {}
        for _k, _sv in enumerate(_out):
            _by_v1.setdefault(_sv[0][0], _k)
        _v2set = set(_sv[0][1] for _sv in _out)
        _order = []
        _left = set(range(len(_out)))
        # chain heads first (v1 not produced by any v2), in index order
        _heads = [_k for _k in range(len(_out))
                  if _out[_k][0][0] not in _v2set]
        while _left:
            if _heads:
                _k = _heads.pop(0)
                if _k not in _left:
                    continue
            else:
                _k = min(_left)              # closed loop: break at min idx
            while _k is not None and _k in _left:
                _order.append(_k)
                _left.discard(_k)
                _nk = _by_v1.get(_out[_k][0][1])
                _k = _nk if (_nk is not None and _nk in _left) else None
        _out = [_out[_k] for _k in _order]
    _merged_segs.extend(_out)
    _merged_ssectors.append((len(_out), _out_first))

fp_segs_vwh = _merged_segs
fp_segs = [svwh[0] for svwh in _merged_segs]
fp_ssectors = _merged_ssectors
print(f"Merged {_merge_count} colinear seg pair(s) "
      f"({len(_stripped_segs)} → {len(_merged_segs)} segs)")

# ── Page-slotting (2026-07-15): every subsector's seg-header run must sit
# inside ONE 256-byte page (SEG_HDR_PER_PAGE slots of SEG_HDR_SIZE bytes;
# 18x14 = 252, so the last 4 bytes of each page are dead), so the engine's
# seg loop never crosses a page and the header base page is a
# subsector-level constant. Best-fit-decreasing bin packing (runs are
# located purely through the SS pointer pages, so run order in the
# header array is free): ~7 pads on E1M1 vs 38 sequential — the array
# must stay clear of the flat VRCACHE planes at $9800 (layout.inc asserts).
# Pads clone a neighbouring seg — valid data that no subsector run
# references (DIR dedupe absorbs the duplicates). This runs BEFORE every
# per-seg derivation (NOVT, anim, packing), so slot indices ARE the seg
# indices everywhere downstream.
from wad_packed import SEG_HDR_PER_PAGE as _SLOTS_PER_PAGE
_pages = []          # each: [space_left, [(ss_index, run)...]]
for _ssi in sorted(range(len(fp_ssectors)),
                   key=lambda i: -fp_ssectors[i][0]):
    _cnt, _first = fp_ssectors[_ssi]
    assert _cnt <= _SLOTS_PER_PAGE, \
        f"subsector run of {_cnt} segs cannot fit one page"
    if _cnt == 0:
        continue
    _best = -1
    for _j, _pg in enumerate(_pages):
        if _pg[0] >= _cnt and (_best < 0 or _pg[0] < _pages[_best][0]):
            _best = _j
    if _best < 0:
        _pages.append([_SLOTS_PER_PAGE, []])
        _best = len(_pages) - 1
    _pages[_best][0] -= _cnt
    _pages[_best][1].append((_ssi, fp_segs_vwh[_first:_first + _cnt]))
_slotted = []
_slot_first = {}
for _pi, (_left, _runs) in enumerate(_pages):
    for _ssi, _run in _runs:
        _slot_first[_ssi] = len(_slotted)
        _slotted.extend(_run)
    if _pi != len(_pages) - 1 and _left:
        _slotted.extend([_slotted[-1]] * _left)   # pad the page out full
_slotted_ss = [(fp_ssectors[_i][0], _slot_first.get(_i, 0))
               for _i in range(len(fp_ssectors))]
print(f"Page-slotted seg headers: {len(fp_segs_vwh)} → {len(_slotted)} "
      f"slots ({len(_slotted) - len(fp_segs_vwh)} pads)")
fp_segs_vwh = _slotted
fp_segs = [svwh[0] for svwh in _slotted]
fp_ssectors = _slotted_ss

# ── Vertex renumbering: single-byte chain key (2026-08-13) ─────────────
# Engine invariant: within any subsector no two vertices share an id LOW
# byte, and no vertex id has low byte $FF (the chain-kill sentinel) — so
# the v1 chain probe compares ONE byte.  New id space: 0..254 and
# 256..454 (dummy hole at 255).  The 199 twin pairs (k, k+256) are
# chosen non-adjacent in the subsector co-residence graph; 56 singles
# fill lo 199..254.  Deterministic greedy; asserted below AND at pack.
_n_old = len(fp_vertexes)
_co = [set() for _ in range(_n_old)]
for _cnt, _first in fp_ssectors:
    _vs = set()
    for _svwh in fp_segs_vwh[_first:_first + _cnt]:
        _vs.add(_svwh[0][0]); _vs.add(_svwh[0][1])
    for _a in _vs:
        _co[_a] |= (_vs - {_a})
_npairs = _n_old - 255
_unpaired = set(range(_n_old))
_tpairs = []
for _a in sorted(range(_n_old), key=lambda v: (-len(_co[v]), v)):
    if _a not in _unpaired:
        continue
    _cands = sorted(_unpaired - {_a} - _co[_a],
                    key=lambda v: (len(_co[v]), v))
    if _cands:
        _tpairs.append((_a, _cands[0]))
        _unpaired -= {_a, _cands[0]}
    if len(_tpairs) == _npairs:
        break
assert len(_tpairs) == _npairs,     f"chain renumber: only {len(_tpairs)}/{_npairs} twin pairs found"
_vperm = {}
for _k, (_a, _b) in enumerate(_tpairs):
    _vperm[_a] = _k
    _vperm[_b] = _k + 256
for _k, _a in enumerate(sorted(_unpaired)):
    _vperm[_a] = _npairs + _k               # singles: lo 199..254
assert len(_vperm) == _n_old and 255 not in _vperm.values()
_new_fp = [(0, 0)] * (_n_old + 1)           # hole at 255: fp (0,0) = centre
_new_raw = [(MAP_CENTER_X, MAP_CENTER_Y)] * (_n_old + 1)
for _o, _nw in _vperm.items():
    _new_fp[_nw] = fp_vertexes[_o]
    _new_raw[_nw] = vertexes[_o]
fp_vertexes = _new_fp
vertexes = _new_raw
fp_segs_vwh = [((_vperm[_svwh[0][0]], _vperm[_svwh[0][1]])
                + tuple(_svwh[0][2:]),) + tuple(_svwh[1:])
               for _svwh in fp_segs_vwh]
fp_segs = [_svwh[0] for _svwh in fp_segs_vwh]
linedefs = [(_vperm[_l[0]], _vperm[_l[1]]) + tuple(_l[2:])
            for _l in linedefs]
segs = [(_vperm[_s[0]], _vperm[_s[1]]) + tuple(_s[2:])
        for _s in segs]                    # FLOAT segs too (2026-08-27):
# the float reference read OLD vertex ids against the renumbered
# vertexes table from 2026-08-13 until today — every float frame was
# scrambled sparse phantom geometry, and no gate consumes the float
# render, so nobody saw it. The fp/packed worlds were consistent all
# along; the FLOAT was the broken one.
for _cnt, _first in fp_ssectors:            # the load-bearing invariant
    _bylo = {}
    for _svwh in fp_segs_vwh[_first:_first + _cnt]:
        for _v in (_svwh[0][0], _svwh[0][1]):
            assert (_v & 0xFF) != 0xFF
            assert _bylo.setdefault(_v & 0xFF, _v) == _v,                 f"subsector lo-byte collision at ss first={_first}"
print(f"Chain renumber: {_npairs} twin pairs + {_n_old - 2 * _npairs} "
      f"singles -> {_n_old + 1} id slots (hole at 255)")
# ── layout.inc drift gate (2026-07-10): the engine assembles against the
# GENERATED constants in src/layout.inc; if the packed layout ever moves,
# fail HERE (first harness import), never silently in the binary. ──
def _layout_inc_gate():
    import re as _re
    _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'layout.inc')
    _t = open(_p).read()
    def _val(name):
        m = _re.search(rf'^{name}\s*=\s*(\$?[0-9A-Fa-f]+)', _t, _re.M)
        v = m.group(1)
        return int(v[1:], 16) if v.startswith('$') else int(v)
    _n_segs = len(fp_segs_vwh)
    if _os_gate.environ.get('DOOM_LAYOUT_REGEN'):
        return                              # gen_layout_inc.py bootstrap
    assert _val('LAY_N_SEGS') == _n_segs, \
        f"layout.inc stale: LAY_N_SEGS {_val('LAY_N_SEGS')} != {_n_segs} — run tools/gen_layout_inc.py and rebuild"
import os as _os_gate
_layout_inc_gate()


# ── Suppressed-vertical bookkeeping ────────────────────────────────────
#
# Two complementary rules decide whether a seg's vertical line at one
# endpoint corresponds to a real wall edge or is a phantom seam:
#
# RULE 1 — BSP-internal vertex.  A sub-seg of a single linedef shares a
# BSP-inserted vertex with the linedef's other sub-seg(s).  Both have
# identical front and back sectors, so the wall is continuous through
# the split point and the vertical there is fake.  Detected via
# `_ld_endpoint_verts`: vertices NOT in this set are BSP-internal.
#
# RULE 2 — Colinear solid neighbour.  At a real linedef-end vertex, two
# distinct linedefs may meet end-to-end with the same front sector and
# parallel direction vectors.  If at least one of them is solid, the
# solid wall's full (fh, ch) Y range covers any vertical the other seg
# would draw at the joint, so the vertical is fake.  This applies both
# to two-sided segs (the upper/lower step verticals frame an aperture
# whose edges fall inside a continuous solid wall) and to solid segs
# meeting another colinear solid wall.
#
# The two rules are independent — neither subsumes the other.
_ld_endpoint_verts = set()
for _ld in linedefs:
    _ld_endpoint_verts.add(_ld[0])
    _ld_endpoint_verts.add(_ld[1])

_SF_NOVT1 = 0x10
_SF_NOVT2 = 0x20

# Vertex → list of seg indices that have a vertex at that point.
_vert_to_segs = {}
for _i, _svwh in enumerate(fp_segs_vwh):
    _s = _svwh[0]
    for _vidx in (_s[0], _s[1]):
        _vert_to_segs.setdefault(_vidx, []).append(_i)

def _seg_is_solid_svwh(svwh):
    """True iff a seg svwh has no aperture (solid wall)."""
    bi = svwh[2]
    if bi is None:
        return True
    bs = fp_sectors[bi]
    fh, ch = svwh[3], svwh[4]
    return bs[1] <= fh or bs[0] >= ch

def _is_continuation_vertex(vidx, front_idx):
    """True if all same-front segs at vidx are colinear (single direction).

    At a continuation vertex the wall passes straight through — no
    junction, no corner.  Returns False when same-front segs go in two
    or more distinct directions (a junction/corner where verticals are
    real).  Segs from other front sectors are ignored — a perpendicular
    wall facing a different sector doesn't create a corner visible from
    this sector.
    """
    segs_at_v = _vert_to_segs.get(vidx, ())
    ref_dx = ref_dy = None
    for j in segs_at_v:
        s_j = fp_segs_vwh[j]
        if s_j[1] != front_idx:
            continue
        ldx_j, ldy_j = s_j[13], s_j[14]
        if ref_dx is None:
            ref_dx, ref_dy = ldx_j, ldy_j
        else:
            if ref_dx * ldy_j - ref_dy * ldx_j != 0:
                return False  # second distinct direction → junction
    return ref_dx is not None

def _portal_has_steps(svwh):
    """True if a two-sided seg has need_bt or need_bb (visible aperture step)."""
    bi = svwh[2]
    if bi is None:
        return False
    bs = fp_sectors[bi]
    fh, ch = svwh[3], svwh[4]
    return bs[1] < ch or bs[0] > fh

def _should_suppress_vertical(seg_idx, vidx):
    """Rule 2: should this seg's vertical at vidx be suppressed?

    Two cases, both requiring a colinear same-front neighbour at a
    continuation vertex (no perpendicular same-front walls):

      A. solid+solid: identical verticals, suppress both.
      B. portal + solid (colinear): suppress both; the portal draws
         an aperture edge (bt→bb) at NOVT endpoints.  Portal-without-
         steps + solid: only suppress the portal (solid is sole framing).

    Plus one case that does NOT require colinearity:

      C. solid + portal-with-steps (any angle, same front): suppress
         the solid.  Its full-height vertical punches through the
         portal's aperture.  The back-sector's perpendicular solid
         seg provides the aperture edge naturally (its full vertical
         covers exactly bfh→bch).
    """
    s_i = fp_segs_vwh[seg_idx]
    front_i = s_i[1]
    this_solid = _seg_is_solid_svwh(s_i)

    # C-perp: solid at a perpendicular junction where a same-front
    # portal draws an aperture edge at the shared vertex.  The aperture
    # edge covers the full [bt, bb] at that column, which always contains
    # (or equals) the solid's own [ft, fb] vertical since the shared
    # vertex puts both in the same sector.  Two sub-cases:
    #
    #   C-perp-cont: solid has a colinear continuation at vidx (mid-wall
    #     junction).  Original rule; fires only for floor-only steps
    #     because the ceiling-side handling was asymmetric.
    #   C-perp-corner: solid has NO colinear continuation at vidx (genuine
    #     corner end).  The portal's aperture edge still draws the same
    #     column + Y range, so suppressing the solid's corner vertical is
    #     safe — the aperture edge replaces it pixel-for-pixel.
    #
    # Both cases require the portal neighbour to actually have steps
    # (_portal_has_steps: bt < ch or bb > fh) so an aperture edge is
    # actually emitted.
    if this_solid:
        fh_i, ch_i = s_i[3], s_i[4]
        ldx_i, ldy_i = s_i[13], s_i[14]
        # Check colinear continuation (any sector)
        _has_continuation = False
        for k in _vert_to_segs.get(vidx, ()):
            if k == seg_idx:
                continue
            ldx_k, ldy_k = fp_segs_vwh[k][13], fp_segs_vwh[k][14]
            if ldx_i * ldy_k - ldy_i * ldx_k == 0:
                _has_continuation = True
                break
        # Look for a same-front portal neighbour at vidx with any steps.
        # The aperture edge it draws at vidx covers this solid's vertical.
        for j in _vert_to_segs.get(vidx, ()):
            if j == seg_idx:
                continue
            s_j = fp_segs_vwh[j]
            if s_j[1] != front_i:
                continue
            if _seg_is_solid_svwh(s_j):
                continue
            bi = s_j[2]
            if bi is None:
                continue
            bs = fp_sectors[bi]
            # Check the portal emits an aperture edge at this vertex.
            # Must have steps (need_bt or need_bb) AND vidx must be a
            # linedef endpoint (aperture edge only fires there).
            portal_has_steps = bs[1] < ch_i or bs[0] > fh_i
            if not portal_has_steps:
                continue
            if vidx not in _ld_endpoint_verts:
                continue
            if _has_continuation:
                # C-perp-cont: original rule (floor-only step).  Keep
                # the narrower condition here to avoid regressing the
                # case the old rule hand-tuned for.
                if bs[1] >= ch_i and bs[0] > fh_i:
                    return True
            # C-perp-corner DISABLED 2026-05-01.  Suppressing the
            # solid's corner vertical relied on the portal-with-steps
            # neighbour drawing its step verticals at the shared column.
            # The portal is only visible from its OWN front sector
            # (back-face culled from the back side), so when the player
            # views the corner from the back side the column is left
            # empty.  Keep the solid's vertical so the corner is always
            # framed; the portal's step verticals overlap it when both
            # are visible, which is harmless.

    # All remaining cases require a colinear continuation vertex.
    if not _is_continuation_vertex(vidx, front_i):
        return False
    ldx_i, ldy_i = s_i[13], s_i[14]
    for j in _vert_to_segs.get(vidx, ()):
        if j == seg_idx:
            continue
        s_j = fp_segs_vwh[j]
        if s_j[1] != front_i:
            continue
        ldx_j, ldy_j = s_j[13], s_j[14]
        if ldx_i * ldy_j - ldy_i * ldx_j != 0:
            continue
        neigh_solid = _seg_is_solid_svwh(s_j)
        # A: solid+solid — identical verticals, suppress.
        if this_solid and neigh_solid:
            return True
        # B: portal + solid — portal suppressed; aperture edge drawn
        #    at NOVT endpoints.
        if not this_solid and neigh_solid:
            return True
        # C: solid + portal-with-steps — solid's full vertical punches
        #    through the aperture; portal's steps frame the opening.
        if this_solid and not neigh_solid and _portal_has_steps(s_j):
            return True
    return False

# Per-seg aperture edge info for solid segs suppressed by Rule 2.
# Maps (seg_idx, 1_or_2) → (bch, bfh) from the colinear portal's back
# sector.  The solid seg draws this aperture edge itself, so it's
# visible regardless of BSP subsector ordering (the solid's draw phase
# runs before its own mark_solid).
_seg_novt_aperture = {}

def _find_portal_back_heights(seg_idx, vidx, novt_flags):
    """For a solid seg suppressed at vidx, find a same-front portal-
    with-steps whose NOVT is set.  Returns the aperture range as
    (top_height, bot_height): bch if need_bt else ch for top,
    bfh if need_bb else fh for bottom.  This matches what the portal's
    own aperture-edge code would draw."""
    s_i = fp_segs_vwh[seg_idx]
    front_i = s_i[1]
    fh_i, ch_i = s_i[3], s_i[4]
    for j in _vert_to_segs.get(vidx, ()):
        if j == seg_idx:
            continue
        s_j = fp_segs_vwh[j]
        if s_j[1] != front_i:
            continue
        if _seg_is_solid_svwh(s_j):
            continue
        if not _portal_has_steps(s_j):
            continue
        bi = s_j[2]
        if bi is None:
            continue
        bs = fp_sectors[bi]
        need_bt = bs[1] < ch_i
        need_bb = bs[0] > fh_i
        ap_top = bs[1] if need_bt else ch_i
        ap_bot = bs[0] if need_bb else fh_i
        # Check portal's NOVT at this vertex (may not be computed yet
        # if the portal has a higher seg index than us in the first pass)
        sj_s = s_j[0]
        if j < len(novt_flags):
            if sj_s[0] == vidx and (novt_flags[j] & _SF_NOVT1):
                return (ap_top, ap_bot)
            if sj_s[1] == vidx and (novt_flags[j] & _SF_NOVT2):
                return (ap_top, ap_bot)
    return None

_seg_novt_flags = []
for _i, _svwh in enumerate(fp_segs_vwh):
    _s = _svwh[0]
    _f = 0
    # Rule 1: BSP-internal vertex.
    if _s[0] not in _ld_endpoint_verts:
        _f |= _SF_NOVT1
    if _s[1] not in _ld_endpoint_verts:
        _f |= _SF_NOVT2
    # Rule 2: colinear same-front joint where at least one side is solid.
    if not (_f & _SF_NOVT1) and _should_suppress_vertical(_i, _s[0]):
        _f |= _SF_NOVT1
        # If this is a solid seg, record the aperture heights so it can
        # draw the aperture edge itself (immune to cross-subsector clipping).
        if _seg_is_solid_svwh(_svwh):
            _bh = _find_portal_back_heights(_i, _s[0], _seg_novt_flags)
            if _bh:
                _seg_novt_aperture[(_i, 1)] = _bh
    if not (_f & _SF_NOVT2) and _should_suppress_vertical(_i, _s[1]):
        _f |= _SF_NOVT2
        if _seg_is_solid_svwh(_svwh):
            _bh = _find_portal_back_heights(_i, _s[1], _seg_novt_flags)
            if _bh:
                _seg_novt_aperture[(_i, 2)] = _bh
    _seg_novt_flags.append(_f)

# Vertices where a solid seg will draw an aperture edge.  Portals
# sharing the same vertex skip their own aperture edge emission to
# avoid double-drawing the same (column, [bt,bb]) line.  The solid's
# aperture-edge draw runs in its own draw phase (before mark_solid),
# so it's guaranteed to land; the portal's would be redundant.
_vert_covered_by_solid_ap = set()
for (_i, _side), _bh in _seg_novt_aperture.items():
    _s = fp_segs_vwh[_i][0]
    _vidx = _s[0] if _side == 1 else _s[1]
    _vert_covered_by_solid_ap.add(_vidx)

# Rule 3 (REMOVED 2026-05-01).  Previously suppressed a back-sector
# solid's vertical at a vertex where a colinear portal-with-steps had
# visible step verticals, on the theory that the step verticals already
# framed the aperture.  This was wrong: the portal seg is only visible
# from its OWN front sector (back-face culled from the opposite side),
# so when the player is in the back sector, suppressing the back-sector
# solid leaves no vertical at all in that column.  Removing Rule 3 keeps
# the back-sector solid's vertical so the opening's back-side corner is
# always framed.

# Rule 4: same-front corner suppression.  At a linedef-endpoint vertex
# shared by multiple segs of the same front sector, the vertical is
# geometrically a single edge of that sector but each incident seg would
# otherwise draw its own copy (solids: full ft→fb; portals-with-steps:
# step segment).  Suppress all but the lowest-idx unsuppressed seg at
# each (vertex, front_sector) group.  Cross-role corners (solid+portal)
# are already handled by Rule 2 Case C, so this fires mainly for
# solid+solid and portal+portal corners.
#
# NOTE: for portals, NOVT flag set here should ALSO skip the aperture-
# edge emission (the owner's step vertical already covers the same
# column; aperture edge would over-emit relative to the pre-Rule-4
# rendering).  Tracked in _novt_rule4 so downstream code can distinguish
# from Rule 2 Case C NOVTs (which DO need aperture edges).
# Build seg → subsector index for same-ssector check below.
_seg_to_ssect = [0] * len(fp_segs_vwh)
for _ssi, (_count, _first) in enumerate(fp_ssectors):
    for _k in range(_first, _first + _count):
        _seg_to_ssect[_k] = _ssi

_novt_rule4 = set()  # set of (seg_idx, side) where side ∈ {1, 2}
for _i, _svwh in enumerate(fp_segs_vwh):
    _s = _svwh[0]
    _fi = _svwh[1]
    _bi = _svwh[2]
    _we_solid = _bi is None
    _we_ss = _seg_to_ssect[_i]
    for _bit, _vidx, _side in ((_SF_NOVT1, _s[0], 1), (_SF_NOVT2, _s[1], 2)):
        if _seg_novt_flags[_i] & _bit:
            continue
        # Coverage rule: owner must cover the range we'd draw.
        #  - we solid (full ft-fb)      : owner must be solid (same range)
        #  - we portal (step bb-fb etc) : owner solid OK (full ⊃ step),
        #                                  portal OK only if same back
        #                                  sector (bt/bb match at vertex)
        #
        # Also require same subsector — BSP traversal either renders both
        # or neither, so Rule 4 suppression is safe from frame-dependent
        # visibility.  Cross-ssector owner might not render this frame.
        for _j in _vert_to_segs.get(_vidx, ()):
            if _j >= _i:
                continue
            if _seg_to_ssect[_j] != _we_ss:
                continue
            _sj = fp_segs_vwh[_j]
            if _sj[1] != _fi:
                continue
            _jbi = _sj[2]
            _j_solid = _jbi is None
            if _we_solid and not _j_solid:
                continue  # portal owner doesn't cover solid's full vertical
            if (not _we_solid) and (not _j_solid) and _jbi != _bi:
                continue  # different back sector → step heights may differ
            _sjs = _sj[0]
            _jbit = _SF_NOVT1 if _sjs[0] == _vidx else _SF_NOVT2
            if _seg_novt_flags[_j] & _jbit:
                continue
            # j owns; suppress ours
            _seg_novt_flags[_i] |= _bit
            _novt_rule4.add((_i, _side))
            break

# Movers: suppression decisions reason about heights that now change at
# runtime (solidity, steps, aperture frames).  Conservatively draw ALL
# verticals for any seg sharing a vertex with a mover-bounding seg — a
# superset of every rule's owner/neighbour dependency (rules only reason
# about segs at the shared vertex).
if ANIM_SECTORS:
    _anim_verts = set()
    for _svwh in fp_segs_vwh:
        if _svwh[1] in ANIM_SECTORS or (_svwh[2] is not None and _svwh[2] in ANIM_SECTORS):
            _anim_verts.add(_svwh[0][0])
            _anim_verts.add(_svwh[0][1])
    _n_anim_novt_cleared = 0
    for _i, _svwh in enumerate(fp_segs_vwh):
        _s = _svwh[0]
        if _s[0] in _anim_verts or _s[1] in _anim_verts:
            if _seg_novt_flags[_i]:
                _n_anim_novt_cleared += 1
            _seg_novt_flags[_i] = 0
            _seg_novt_aperture.pop((_i, 1), None)
            _seg_novt_aperture.pop((_i, 2), None)
            _novt_rule4.discard((_i, 1))
            _novt_rule4.discard((_i, 2))
    print(f"ANIM: NOVT cleared on {_n_anim_novt_cleared} mover-adjacent segs")

_n_novt1 = sum(1 for f in _seg_novt_flags if f & _SF_NOVT1)
_n_novt2 = sum(1 for f in _seg_novt_flags if f & _SF_NOVT2)
print(f"NOVT flags: {_n_novt1} v1 + {_n_novt2} v2 "
      f"= {_n_novt1 + _n_novt2} verticals suppressed (RETIRED by descriptors)")


# ============================================================================
# STATIC VERTEX-SPAN DESCRIPTORS (Eben's scheme, 2026-07-24). Replaces
# per-seg verticals + the NOVT/APEDGE rule web in ALL renderers (and in
# the 6502 engine). One byte per vertex:
#   $00 none / $01 fh->ch / $02 fh->bfh / $03 bch->ch / $04 frame pair
#   $80|i explicit -> vspan_expl[i] = (h_lo, h_hi, cont)
# Codes evaluate against the TRIGGERING seg's four heights with the
# solid alias bfh=fh, bch=ch (the code subsumes the runtime clamp);
# explicit spans clamp world heights to the trigger's [fh, ch]. Drawn
# once per vertex per frame at FIRST TOUCH by a rendering seg.
# Mover-adjacent explicit vertices are FORCED to code $01 (their
# heights are runtime values; $01 reads the live header heights).
# ============================================================================
def _vs_faceset(sj, ov=None):
    # ov: optional {sector: (fh, ch)} height override (prescaled) — used by
    # the mover-jamb pose analysis to evaluate faces at a posed mover.
    sv = fp_segs_vwh[sj]
    fh_, ch_ = sv[3], sv[4]
    if ov and sv[1] in ov:
        fh_, ch_ = ov[sv[1]]
    bi_ = sv[2]
    bs_ = None if bi_ is None else (
        ov[bi_] if (ov and bi_ in ov) else fp_sectors[bi_][:2])
    if bs_ is None or bs_[1] <= fh_ or bs_[0] >= ch_:
        return [(fh_, ch_)]
    F = []
    if bs_[1] < ch_: F.append((bs_[1], ch_))
    if bs_[0] > fh_: F.append((fh_, bs_[0]))
    return F

def _vs_union(iv):
    iv = sorted(p for p in iv if p[1] > p[0])
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], b))
        else: out.append((a, b))
    return out

def _vs_symdiff(fa, fb):
    ys = sorted({y for p in fa + fb for y in p})
    out = []
    for a, b in zip(ys, ys[1:]):
        m = (a + b) / 2
        if (any(p[0] <= m < p[1] for p in fa)) != \
           (any(p[0] <= m < p[1] for p in fb)):
            out.append((a, b))
    return _vs_union(out)

def _vs_colinear(i, j):
    if fp_segs_vwh[i][0][3] == fp_segs_vwh[j][0][3]:
        return True
    return (fp_segs_vwh[i][13] * fp_segs_vwh[j][14]
            - fp_segs_vwh[i][14] * fp_segs_vwh[j][13]) == 0

def _vs_heights(t):
    sv = fp_segs_vwh[t]
    fh_, ch_ = sv[3], sv[4]
    bi_ = sv[2]
    if bi_ is None or fp_sectors[bi_][1] <= fh_ or fp_sectors[bi_][0] >= ch_:
        return {'fh': fh_, 'ch': ch_, 'bfh': fh_, 'bch': ch_}
    bs_ = fp_sectors[bi_]
    return {'fh': fh_, 'ch': ch_, 'bfh': bs_[0], 'bch': bs_[1]}


_vs_groups = {}
for _i, _svwh in enumerate(fp_segs_vwh):
    for _v in (_svwh[0][0], _svwh[0][1]):
        _vs_groups.setdefault(_v, []).append(_i)

VSPAN_CODES = {1: (('fh', 'ch'),), 2: (('fh', 'bfh'),), 3: (('bch', 'ch'),),
               4: (('bch', 'ch'), ('fh', 'bfh'))}

def _vs_spans_at(v, sjs, ov=None):
    """Union of per-colinear-run symdiff spans at vertex v, optionally with
    mover sectors posed via the ov height-override dict."""
    runs = []
    for sj in sjs:
        for r in runs:
            if _vs_colinear(sj, r[0]): r.append(sj); break
        else:
            runs.append([sj])
    spans = []
    for r in runs:
        pos, neg = [], []
        for sj in r:
            s0 = fp_segs_vwh[sj][0]
            oth = s0[1] if s0[0] == v else s0[0]
            dd = ((fp_vertexes[oth][0] - fp_vertexes[v][0]) * fp_segs_vwh[r[0]][13]
                  + (fp_vertexes[oth][1] - fp_vertexes[v][1]) * fp_segs_vwh[r[0]][14])
            (pos if dd >= 0 else neg).extend(_vs_faceset(sj, ov))
        spans.extend(_vs_symdiff(_vs_union(pos), _vs_union(neg)))
    return _vs_union(spans)

# ── Mover pose table for the jamb analysis: sec -> (role, pose(t)) where
# pose(t) returns the {sec: (fh, ch)} override at travel fraction t (0 =
# pack/rest pose, 1 = far pose) and role names the MOVING bound of a jamb
# span ('hi' = door ceiling rises into VEXPL_HI, 'lo' = lift floor falls
# into VEXPL_LO). Rest heights follow the DOOM rules (anim_sectors):
# door opens to lowest neighbour ceiling - 4, lift descends to lowest
# neighbour floor.
_vs_mover_pose = {}
if ANIM_SECTORS:
    for _sec, _kind in ANIM_SECTORS.items():
        _nb = set()
        for _ld in linedefs:
            _ss = [sidedefs[_sd][5] for _sd in (_ld[5], _ld[6]) if _sd != 0xFFFF]
            if _sec in _ss:
                _nb.update(_x for _x in _ss if _x != _sec)
        _fh0, _ch0 = fp_sectors[_sec][:2]
        if _kind == 'ceil':
            _far = _prescale_height(min(sectors[_n][1] for _n in _nb) - 4)
            _vs_mover_pose[_sec] = ('hi', _ch0, _far,
                                    lambda t, s=_sec, a=_fh0, b=_ch0, c=_far:
                                    {s: (a, b + (c - b) * t)})
        else:
            _far = _prescale_height(min(sectors[_n][0] for _n in _nb))
            _vs_mover_pose[_sec] = ('lo', _fh0, _far,
                                    lambda t, s=_sec, a=_fh0, b=_ch0, c=_far:
                                    {s: (a + (c - a) * t, b)})

ANIM_JAMB = {}          # mover sector -> [(vspan_expl index, 'lo'|'hi')]
vspan_desc = [0] * 512
vspan_expl = []
_vs_forced = 0
_vs_jambs = 0
for _v, _sjs in _vs_groups.items():
    _spans = _vs_spans_at(_v, _sjs)
    if not _spans:
        # ── Mover jamb (2026-08-14): an edge set EMPTY at pack pose can
        # GROW an edge when an adjacent mover moves — the in-plane
        # door/lift jamb (flat wall while the exit door is shut; a
        # floor -> door-underside edge once it opens). Re-evaluate the
        # span set at the mover's far pose; if an edge appears, emit an
        # explicit entry whose MOVING bound is patched at runtime (python
        # Mover._apply / the 6502 anim worker's VEXPL list) while the
        # static bound is baked. Baked value = rest pose, so the entry
        # starts empty and self-annuls until the mover moves.
        _hits = []
        for _msec, (_role, _rest, _far, _pose) in _vs_mover_pose.items():
            if not any(_msec in (fp_segs_vwh[_t][1], fp_segs_vwh[_t][2])
                       for _t in _sjs):
                continue
            _sf = _vs_spans_at(_v, _sjs, _pose(1))
            if _sf:
                _hits.append((_msec, _role, _rest, _far, _pose, _sf))
        if not _hits:
            continue
        assert len(_hits) == 1, f'jamb vertex {_v}: multiple movers {_hits}'
        _msec, _role, _rest, _far, _pose, _sf = _hits[0]
        assert len(_sf) == 1, f'jamb vertex {_v}: multi-span {_sf}'
        _jlo, _jhi = _sf[0]
        # the far-pose span must carry the mover height on the moving
        # bound; the mid-pose span must interpolate the SAME static bound
        # (this is what licenses a single runtime-patched byte)
        _mid = _rest + (_far - _rest) * 0.5
        _sm = _vs_spans_at(_v, _sjs, _pose(0.5))
        if _role == 'hi':
            assert _jhi == _far, (_v, _sf)
            assert _sm == ([(_jlo, _mid)] if _mid > _jlo else []), (_v, _sm)
            _entry = (_jlo, _rest)
        else:
            assert _jlo == _far, (_v, _sf)
            assert _sm == ([(_mid, _jhi)] if _jhi > _mid else []), (_v, _sm)
            _entry = (_rest, _jhi)
        _ix = len(vspan_expl)
        assert _ix < 0x80
        vspan_expl.append((_entry[0], _entry[1], False))
        vspan_desc[_v] = 0x80 | _ix
        ANIM_JAMB.setdefault(_msec, []).append((_ix, _role))
        _vs_jambs += 1
        continue
    def _code_of(span):
        for _cn, _pairs in ((1, ('fh', 'ch')), (2, ('fh', 'bfh')), (3, ('bch', 'ch'))):
            _ok = True
            for _t in _sjs:
                _H = _vs_heights(_t)
                _cl = max(span[0], fp_segs_vwh[_t][3])
                _chh = min(span[1], fp_segs_vwh[_t][4])
                _el, _eh = _H[_pairs[0]], _H[_pairs[1]]
                if _chh <= _cl:
                    if _eh > _el: _ok = False; break
                elif (_el, _eh) != (_cl, _chh):
                    _ok = False; break
            if _ok:
                return _cn
        return None
    _codes = [_code_of(_sp) for _sp in _spans]
    if all(_codes):
        if len(_codes) == 1:
            vspan_desc[_v] = _codes[0]
        else:
            assert sorted(_codes) == [2, 3], (_v, _codes)
            vspan_desc[_v] = 4
    else:
        assert not any(_codes), f"mixed coded/explicit at v{_v}"
        if ANIM_SECTORS and any(
                fp_segs_vwh[_t][1] in ANIM_SECTORS or
                (fp_segs_vwh[_t][2] is not None and fp_segs_vwh[_t][2] in ANIM_SECTORS)
                for _t in _sjs):
            vspan_desc[_v] = 1          # mover joint: live header heights
            _vs_forced += 1
            continue
        _ix = len(vspan_expl)
        assert _ix < 0x80
        for _k, _sp in enumerate(_spans):
            vspan_expl.append((_sp[0], _sp[1], _k + 1 < len(_spans)))
        vspan_desc[_v] = 0x80 | _ix
print(f"VSPANS: {sum(1 for d in vspan_desc if d)} vertices, "
      f"{len(vspan_expl)} explicit entries, {_vs_forced} mover-forced, "
      f"{_vs_jambs} mover-jamb")
# VDONE wipe bound (walk.s wipes 60 bitmap bytes = 480 ids — the old
# 48-byte/ids<384 dependence died when the wipe widened): every vertex
# with a descriptor must sit inside the wiped range.
assert max((_i for _i, _d in enumerate(vspan_desc) if _d), default=0) < 480, \
    'VDONE wipe bound: desc!=0 vertex id >= 480 (widen the 60-byte wipe in walk.s)'

# (HALF-UNIT tier note, 2026-08-25: the mover BACK PAIR ships half-
# prescaled — see wad_packed/_apply. VEXPL jamb entries STAY integer this
# pass: the vsx H2 plumbing cost ~100 B the banked CODE region does not
# have. Jamb edges keep integer granularity; a follow-up can revisit
# once bytes are found — the design is in the 2026-08-25 session notes.)

def vexpl_bytes(i, lo, hi, cont):
    """The 6502 VEXPL byte triple for entry i (loaders share this —
    bsp_render_6502 / banked_bsp / trace_compare must bake identically)."""
    return lo & 0xFF, hi & 0xFF, (1 if cont else 0)

_vspan_done = set()

def vspan_frame_reset():
    _vspan_done.clear()

def emit_vertex_spans(vidx, sx, proj, H, clips, surface, draw_stats,
                      on_screen):
    """First-touch descriptor emission for one endpoint. proj(h) -> y
    at this vertex's reciprocal; H = trigger's four heights dict
    (solid-aliased); on_screen = endpoint usable (not near-clipped,
    column in [0,255])."""
    d = vspan_desc[vidx]
    if vidx in _vspan_done:
        return
    _vspan_done.add(vidx)          # marks desc-0 too (6502 parity: the
    if not d:                      # bit upgrades later touches to the
        return                     # inline fast exit)
    if not on_screen:
        return
    lines = []
    if d & 0x80:
        i = d & 0x7F
        while True:
            h_lo, h_hi, cont = vspan_expl[i]
            c_lo, c_hi = max(h_lo, H['fh']), min(h_hi, H['ch'])
            if c_hi > c_lo:
                lines.append((sx, proj(c_hi), sx, proj(c_lo)))
            if not cont:
                break
            i += 1
    else:
        for rl, rh in VSPAN_CODES[d]:
            lo, hi = H[rl], H[rh]
            if hi > lo:
                lines.append((sx, proj(hi), sx, proj(lo)))
    if lines:
        clips.draw_clipped(lines, GREEN, surface, draw_stats)


# Annotation mode: when True, suppressed verticals are drawn in RED and
# labelled with seg/vertex/rule info so problem cases can be identified.
# Toggle with 'V' key in the interactive viewer.
_novt_annotate = False
_novt_annotations = []  # populated per frame: [(sx, sy, label), ...]

# ── Build packed byte arrays for 8-bit processor simulation ──────────────
from wad_packed import build_packed
print(f"PRESCALE={PRESCALE} (set DOOM_PRESCALE env var to override; 8 or 16)")
# ── Static objects (billboards) ────────────────────────────────────────
# Map THINGS that never move AND stand off the floor: solid decorations
# and barrels.  The 68 pickups/gibs are h=16 and would render as floor
# litter, so they are left out; monsters are excluded because they are
# not static in principle, even with no AI to move them.
# type -> (radius, height, art, k) in world units, from DOOM's mobjinfo.
#
# `art` picks the billboard template (see wad_packed): the octagonal prism
# is BARREL art and stays barrel-only -- a floor lamp drawn as a squat
# eight-sided drum reads as a barrel, not a lamp.  Everything else keeps the
# plain outline rectangle it had before the barrel work.
#
# `k` is the width ratio, 64ths: half_width_screen = H * k / 64, where H is
# the object's projected screen height.  Both scale by the same 1/depth, so
# k = 64 * radius / height is exact -- no projection of the radius needed.
# THE ASPECT BYTE IS A KIND INDEX (2026-08-31, the pickup landing): eight
# billboard kinds, and k -- the width ratio in 64ths -- became a PER-KIND
# CONSTANT the moment every kind had exactly one geometry, so it moved to
# the engine's obj_ktab and the byte no longer packs bits.  Engine mirror:
# obj_ktab in objects.s; the two MUST match (test_pickup_ladders gates it).
# (the techno pillar was kind 2 until 2026-08-31 -- Eben: "it just
# doesn't work" -- thing 48 packs nothing now and the kinds renumbered)
K_HEX, K_LAMP, K_POTION, K_HELMET, K_BOXS, K_BOXM, K_VEST = range(7)
_KTAB = [23, 15, 25, 34, 30, 47, 58]
def _obj_kind(r, h, kind):
    kk = round(64 * r / h)
    assert kk == _KTAB[kind], f'kind {kind}: k={kk} != obj_ktab {_KTAB[kind]}'
    return (r, h, kind)
# NB `h` is the height the billboard is DRAWN at (the barrel's 32 is its
# sprite box, not its 42-unit collision cylinder).  Radii are the DRAWN
# rmax from doc/billboard, not collision radii: lamp 11.5 not 16, pillar
# 19 not 16, stim/medikit/potion/helmet/vest off their sprites.
# THE CANDELABRA IS GONE (Eben, 2026-08-31): it borrowed the floor lamp
# for a while, but thing 35 now packs nothing at all.
_LAMP = _obj_kind(11.5, 48, K_LAMP)
# PICKUP GROWTH IS IN THE ENGINE (2026-09-04, Eben: +15%, was +25%):
# billboard heights are stored in the prescaled height unit (40/6 = 6.67
# wu per quantum) and these figures are only 2-3 quanta tall, so no table
# height is within 15% of the ask -- objects.s scales the PROJECTED height
# per kind (obj_gtab, 256ths) before the width derives from it, base
# anchored.  The table heights below are the sprite boxes, unchanged.
_OBJ_KINDS = {2011: _obj_kind(7, 15, K_BOXS),       # Stimpack
              2012: _obj_kind(14, 19, K_BOXM),      # Medikit
              2014: _obj_kind(7, 18, K_POTION),     # Health potion
              2015: _obj_kind(8, 15, K_HELMET),     # Armour bonus (helmet)
              2018: _obj_kind(15.5, 17, K_VEST),    # Green armour
              2019: _obj_kind(15.5, 17, K_VEST),    # Blue armour (same art)
              2028: _LAMP,                          # Floor lamp
              2035: _obj_kind(11.5, 32, K_HEX)}     # Barrel (BAR1A0 23x32)
# THE ARMOUR ROOM IS THE ARMOUR'S (Eben, 2026-09-01): the green-armour
# zigzag room kept its 4 potions + 2 helmets as clutter around the prize;
# all six go, the armour stays.  Keyed by exact (thing, x, y) so a map
# rebuild that moves them trips the census gate rather than silently
# missing.
_ARMOUR_ROOM_DROP = {(2014, 144, -3136), (2014, 144, -3328),
                     (2014, 96, -3392), (2014, 96, -3072),
                     (2015, 32, -3232), (2015, -32, -3232),
                     # 2026-09-03 (Eben): the two floor lamps at the west end
                     # of the spawn hall, by the armour-room approach, and
                     # the two hoplites on the approach itself
                     (2028, 528, -3312), (2028, 528, -3152),
                     (2015, 432, -3040), (2015, 432, -3424)}
# ADDED THINGS (2026-09-03, Eben): two floor lamps flanking the opening
# onto the steps east of the courtyard.  Each is CENTRED IN Y on the end
# cap of the wall it stands by -- the 32-unit segments at x=2112 spanning
# y -2592..-2560 (linedef 411) and -2304..-2272 (linedef 401) -- at one
# shared x, 18 units off the cap face (lamp r 11.5 clears it by 6.5).
# Same (x, y, angle, type, flags) shape as a WAD thing.
_ADDED_THINGS = [(2094, -2576, 0, 2028, 0), (2094, -2288, 0, 2028, 0)]
fp_objects = []
for _th in list(things) + _ADDED_THINGS:
    _tx, _ty_, _ta, _tt, _tfl = _th
    if _tt not in _OBJ_KINDS or (_tfl & 0x10):   # skip multiplayer-only
        continue
    if (_tt, _tx, _ty_) in _ARMOUR_ROOM_DROP:
        continue
    _r, _h, _kind = _OBJ_KINDS[_tt]
    _fz = player_floor(_tx, _ty_)
    fp_objects.append(dict(
        ss=find_subsector(_tx, _ty_),
        x=_prescale_round(_tx - MAP_CENTER_X, PRESCALE),
        y=_prescale_round(_ty_ - MAP_CENTER_Y, PRESCALE),
        # The KIND INDEX, whole byte.  This is the plane that used to
        # hold the radius in counts, then the art|k pack; k is per-kind
        # now (obj_ktab), so the byte is just the kind.
        asp=_kind,
        zb=_prescale_height(_fz),
        zt=_prescale_height(_fz + _h)))
fp_objects.sort(key=lambda o: o['ss'])           # 6502 scans a run per ss

packed_rom_main, packed_rom_detail, packed_rom_recip, packed_bbox_table, packed_layout = build_packed(
    vertexes, fp_vertexes, nodes, fp_ssectors, fp_segs,
    fp_segs_vwh, vwh_table, fp_sectors, linedefs, sidedefs,
    PRESCALE, MAP_CENTER_X, MAP_CENTER_Y,
    fp_objects=fp_objects,
    seg_novt_flags=_seg_novt_flags,
    seg_novt_aperture=_seg_novt_aperture,
    novt_rule4=_novt_rule4,
    vert_covered_by_solid_ap=_vert_covered_by_solid_ap,
    anim_vert_set=(_anim_verts if ANIM_SECTORS else None),
    anim_sector_set=set(ANIM_SECTORS))
# THE OBJECT-COUNT STALENESS TRAP (2026-09-03): the engine's object planes
# are strided by LAY_N_OBJ, so a stale count reads every plane but the
# first at the wrong offset -- kinds and heights turn to garbage while the
# build stays green.  Same rule as LAY_N_SEGS above: regenerate and rebuild.
if not _os_gate.environ.get('DOOM_LAYOUT_REGEN'):
    import re as _re_obj
    _m = _re_obj.search(r'^LAY_N_OBJ\s*=\s*(\d+)', open(_os_gate.path.join(
        _os_gate.path.dirname(_os_gate.path.abspath(__file__)), 'src', 'layout.inc')).read(), _re_obj.M)
    assert int(_m.group(1)) == packed_layout['n_obj'], \
        f"layout.inc stale: LAY_N_OBJ {_m.group(1)} != {packed_layout['n_obj']} — run tools/gen_layout_inc.py and rebuild"

# ---- |h| <= 64 PROJECTION BOUND FENCE (2026-07-12) -------------------------
# project_y's raw tail (src/bsp/project.s) assumes |height - vz| <= 64 for
# every height the renderer can project: then |h*m9| < 2^15, the s24 ext byte
# is pure sign of the s16 product, and the old carry/sign bookkeeping is gone.
# This fence makes that a PACK-TIME contract: a map (or mover travel) that
# violates it fails HERE, not by rendering garbage. Consumed heights = the
# flags-gated header slots +10..15 plus anim-mover travel extremes; vz = any
# sector floor + 41 (eye height), prescaled exactly as the runtime does.
def _projection_bound_fence():
    # flag bits and the slot mapping come from the packer, never from local
    # literals: this function's private copies went stale at the 2026-08-11
    # SOLID/APEDGE1 bit swap, so it had been testing 0x02 for SOLID (and the
    # aperture arm, which the descriptors retired, for 0x40)
    from wad_packed import (SF_SOLID, SF_NEEDBT, SF_NEEDBB, seg_hdr_off,
                            SH_FLAGS, SH_BPAL)
    L, rm = packed_layout, packed_rom_main
    def s8(v): return v - 256 if v >= 128 else v
    consumed = set()
    # front heights are per SUBSECTOR now; the back pair (or its solid alias)
    # stays per seg and is flag-gated
    for ssi in range(L['n_ss']):
        consumed.add(s8(rm[L['off_ss_fh'] + ssi]))
        consumed.add(s8(rm[L['off_ss_ch'] + ssi]))
    consumed_h2 = set()                     # HALF-UNIT tier (2026-08-25):
    for i in range(L['n_segs']):            # mover-valued palette entries
        o = L['off_seg_hdr'] + seg_hdr_off(i)   # (id bit 6) hold 2h and
        f = rm[o + SH_FLAGS]                    # project as h2 - 2vz — the
        pid = rm[o + SH_BPAL]                   # SAME |h| <= 64 bound applies
        bp = L['off_bpal'] + pid                # to the DOUBLED delta
        idx = []
        if f & SF_SOLID:
            idx += [0x00, 0x80]             # solid entry = fh/ch (see packer)
        else:
            if f & SF_NEEDBB: idx.append(0x00)
            if f & SF_NEEDBT: idx.append(0x80)
        tgt = consumed_h2 if pid & 0x40 else consumed
        for k in idx: tgt.add(s8(rm[bp + k]))
    mover_h2 = set()
    for sec, kind in ANIM_SECTORS.items():             # mover travel extremes
        srec = sectors[sec]
        if kind == 'ceil':                             # door: ceil floor..open
            mover_h2.add(2 * _prescale_height(srec[0]))
            mover_h2.add(2 * _prescale_height(srec[1]))
        else:                                          # lift: floor bottom..top
            mover_h2.add(2 * _prescale_height(srec[0]))
    vzs = {_prescale_height(srec[0] + 41) for srec in sectors}
    worst = max(abs(h - v) for h in consumed for v in vzs)
    # half tier: the byte is 2h, the delta is 2h - 2vz
    worst2 = max((abs(h2 - 2 * v) for h2 in (consumed_h2 | mover_h2)
                  for v in vzs), default=0)
    # BOUND RE-DERIVED 2026-08-25 (the half-unit tier pushed deltas past
    # the old 64): the raw body is exact for |h| <= 127, not 64. Both
    # arms compute the mid byte mod-256 exactly (the fused ADC identity
    # (P>>8) === h + ~|hi| + (lo==0) is carry-independent), and the
    # constant ext bytes stay right for the whole s8 range: h > 0 gives
    # P = h*m9 <= 127*511 = 64,897 < 2^16 so ext = 0; h < 0 gives
    # P >= -64,897 > -2^16 so ext = $FF. The old "P fits s16 / ext is
    # the sign of mid" justification was sufficient but not necessary.
    # Verified exhaustively: tools/test_projy_range.py sweeps every
    # (recip, h in [-127,127]) pair against fp_project_y, S=1..11.
    assert worst <= 127 and worst2 <= 127, (
        f"projection |h| bound violated: worst int {worst} / half {worst2} "
        f"> 127 — beyond the s8 raw-body domain; this needs real s24 ext "
        f"bookkeeping in project_y (src/bsp/project.s)")
_projection_bound_fence()

# Build ROM banks for sideways ROM paging
packed_rom_banks = [
    bytearray(16384),  # bank 0: rom_main
    bytearray(16384),  # bank 1: rom_detail
    bytearray(16384),  # bank 2: bbox_table + rasteriser + clipper
]
packed_rom_banks[0][:len(packed_rom_main)] = packed_rom_main
packed_rom_banks[1][:len(packed_rom_detail)] = packed_rom_detail
packed_rom_banks[2][:len(packed_bbox_table)] = packed_bbox_table
_rast_path = os.path.join(os.path.dirname(__file__) or '.', 'linedraw_bank2.bin')
if os.path.exists(_rast_path):
    with open(_rast_path, 'rb') as _f:
        _rast_bin = _f.read()
    _rast_offset = len(packed_bbox_table)
    packed_rom_banks[2][_rast_offset:_rast_offset + len(_rast_bin)] = _rast_bin

# ── Analytical 2D trapezoid clip spans ───────────────────────────────────────
#
# The visible region is a list of non-overlapping half-open spans [xlo, xhi).
# Each span's top and bottom boundaries are linear functions stored as
# (slope, intercept): y = a*x + b.  This avoids accumulated interpolation
# error — every evaluation uses the original tighten parameters.
#
# Lines are clipped analytically against each span's trapezoid using
# Cyrus-Beck (4 half-planes).  No per-column iteration.

SCREEN_W, SCREEN_H = 1024, 640     # display window size
WIDTH, HEIGHT = SCREEN_W, SCREEN_H  # rendering resolution (float mode)
FP_WIDTH, FP_HEIGHT = 256, 160      # rendering resolution (fixed-point mode)
FP_SCALE = SCREEN_W // FP_WIDTH     # = 4, nearest-neighbour upscale factor
HFOV = math.pi / 2
FOCAL_X = (WIDTH / 2) / math.tan(HFOV / 2)   # 512
FOCAL_Y = FOCAL_X * 1.2                        # 614.4
NEAR = 1.0

ZERO_FN = (0.0, 0.0)                   # y = 0 everywhere
BOT_FN  = (0.0, float(HEIGHT - 1))     # y = 599 everywhere

# ── Map trace (populated during BSP traversal, drawn in map mode) ─────────

map_trace = {
    "subsectors": set(),      # indices of traversed subsectors
    "ss_order": [],           # traversal order of subsectors
    "segs_processed": set(),  # indices of segs that passed back-face test
    "segs_drawn": set(),      # indices of segs that produced visible lines
    "vertices": set(),        # vertex indices that were projected
    "nodes_visited": set(),   # BSP node indices visited
    "vertex_muls": {},        # vertex_idx -> [v_muls, p_muls]
}














# ── View-space transform ────────────────────────────────────────────────────





_USE_ANGLE_COL = False
# M2: full rotation-free angle-space bbox (2-corner checkcoord). Needs the
# view angle byte, set per frame in _VIEW_AB by the render harness.
_USE_ANGLE_BBOX = False
# (EV16 _NC88 flag RETIRED 2026-08-09: the 8.8 crossing is THE path in
# both python renderers — gate passed toward-float, 6502 landed.)
_T16 = True     # TRUE16 (2026-08-10): s16 count-scale totals (K=32/unit,
                # rot output >>3 RN — fp.fp_to_view_t16 family) — THE
                # 6502 pipeline since the same date. Flag-off = the old
                # 8.8 pipeline (kept for A/B archaeology only).
_VIEW_AB = 0
# Option 2b: full angle-space SEG (no per-vertex rotation). X from world angle,
# Y from wall-distance scale. Reference path in packed_render_seg. Mirrors the
# 6502 seg_c/seg_project pipeline exactly (angle_seg.py is the shared math).
_USE_ANGLE_SEG = False
_seg2b_stats = {'cull': 0, 'segs': 0}   # diagnostics; no silent fallback
_seg2b_debug = None                     # {si: (sx1, sx2)} capture for debugging
_seg2b_debug_bt = None                  # {si: (bt1,bt2)} ; _bb separate


def fp_bbox_visible_fixed(node, far_side, ctx):
    """Fixed-point bbox visibility — matches the 6502 native implementation.

    Prescales the raw bbox corners, runs them through fp_to_view / fp_recip /
    fp_project_x with the same precision the 6502 pipeline uses, and returns
    (min_sx, max_sx) or None.  Both Python and 6502 call into this routine,
    so verify_exact stays bit-exact.

    Args:
        node: Python node tuple (raw WAD coords at node[4..11])
        far_side: 0 (right) or 1 (left)
        ctx: fp_view_context result (prescaled player + rotated frac)
    """
    from fp import (PRESCALE as _PRESCALE, MAP_CENTER_X as _MCX,
                         MAP_CENTER_Y as _MCY, FP_RENDER_W as _FPW,
                         NEAR_FP as _NEAR, fp_to_view as _fp_to_view,
                         fp_recip as _fp_recip, fp_project_x as _fp_project_x,
                         m8 as _m8)
    from angle_bbox import view_col as _view_col
    base = 4 + far_side * 4
    rt_raw, rb_raw, rl_raw, rr_raw = (
        node[base], node[base + 1], node[base + 2], node[base + 3])
    # Prescale bbox corners into the same 8.0 frame as the player in ctx.
    # OUTWARD rounding + 1-unit inflation, identical to the packed bbox
    # table (wad_packed.py) — see the note there.
    top = -((-(rt_raw - _MCY)) // _PRESCALE) + 1
    bot = (rb_raw - _MCY) // _PRESCALE - 1
    left = (rl_raw - _MCX) // _PRESCALE - 1
    right = -((-(rr_raw - _MCX)) // _PRESCALE) + 1

    px_int, py_int = ctx[0], ctx[1]

    if _USE_ANGLE_BBOX:
        from angle_bbox import bbox_check_angle
        return bbox_check_angle(top, bot, left, right, px_int, py_int, _VIEW_AB)

    # Trivial inside test (prescaled).
    if left <= px_int <= right and bot <= py_int <= top:
        return 0, _FPW - 1

    # Transform the 4 bbox corners to view space using the shared prescaled
    # fp_to_view routine (same precision as fp_render_seg).
    corners = ((left, top), (right, top), (right, bot), (left, bot))
    pts = []
    for wx, wy in corners:
        _, evx, evy, _, evy_idx = _fp_to_view(wx, wy, ctx)
        pts.append((evx, evy, evy_idx))

    # Entirely behind near plane → not visible.
    if all(p[1] < _NEAR for p in pts):
        return None

    # View-space frustum reject (pre-projection).
    # Left frustum plane:  evx + evy < 0  (all corners to the left)
    # Right frustum plane: evx > evy      (all corners to the right)
    # These work correctly for behind-the-viewer points too: a point
    # behind-and-left is on the left side of the left plane's extension.
    if all(p[0] + p[1] < 0 for p in pts):
        return None
    if all(p[0] > p[1] for p in pts):
        return None

    sxs = []
    for i in range(4):
        vx0, vy0, vy_idx0 = pts[i]
        vx1, vy1, _ = pts[(i + 1) % 4]
        if vy0 >= _NEAR:
            if _USE_ANGLE_COL:
                sxs.append(_view_col(vx0, vy0))
            else:
                rxh, rxl = _fp_recip(vy_idx0)
                sxs.append(_fp_project_x(vx0, 0, rxh, rxl))
        # Edge crossing NEAR plane → project the crossing point at NEAR.
        if (vy0 < _NEAR) != (vy1 < _NEAR):
            dvy = vy1 - vy0
            if dvy != 0:
                # Parametric t in 0.8: (NEAR - vy0) << 8 / dvy
                t = ((_NEAR - vy0) << 8) // dvy
                dvx = vx1 - vx0
                # t is 0.8 fixed-point: the product must be >>8 (fp_mul8),
                # exactly like the seg path's fp_near_clip. This was _m8
                # (the RAW 8x8 product) for a long time — the crossing then
                # landed ~256x too far out, usually just off-screen
                # (harmless over-descend) but sometimes on the WRONG SIDE,
                # collapsing the extent and pruning a VISIBLE subtree
                # (973,-3367,239 node 113: true crossing cx=-3, raw cx=+4830
                # -> extent (64,255) instead of (0,255) -> ss105 culled ->
                # missing room + far geometry drawn through its walls).
                cx = vx0 + fp_mul8(t, dvx)
                if _USE_ANGLE_COL:
                    sxs.append(_view_col(cx, _NEAR))
                else:
                    # 6502's use_ey1 path passes ey1 (=NEAR_FP) directly as a
                    # raw integer index with averaging flag = 0.  In fp_recip's
                    # 9.1 convention that's NEAR_FP << 1 (even → no averaging).
                    rxh, rxl = _fp_recip(_NEAR << 1)
                    sxs.append(_fp_project_x(cx, 0, rxh, rxl))

    if not sxs:
        return None
    # Conservative ±1: bbox corners are integer-prescaled and project
    # through a different rounding path than seg vertices (8.8 fractional
    # player-relative), so the raw extent can be a column narrower than
    # the subtree's real seg extent (893,-3218,123 node 80: extent
    # (122,171) missed ss86 geometry at column 121). The angle-space bbox
    # has carried the same ±1 guard from day one; a visibility test must
    # be conservative.
    sx_lo = max(0, min(sxs) - 1)
    sx_hi = min(_FPW - 1, max(sxs) + 1)
    if sx_lo > sx_hi:
        return None
    return sx_lo, sx_hi

# ── BSP rendering ────────────────────────────────────────────────────────────

GREEN = (0, 200, 0)


_frame_nj_lines = []  # captured line coords for NJ raster mode





# ── Fixed-point clip spans ────────────────────────────────────────────────────
#
# 8-bit screen coordinates throughout.  Slopes are 0.8, intercepts are 8.0.
# All multiplies are 8x8.





# ── Fixed-point BSP rendering (prescaled 8-bit) ──────────────────────────────

def fp_render_seg(si, clips, ctx, vz, surface, vxcache, vwh_cache, deferred=None):
    """Render a seg from the stripped fp_segs table.

    vxcache: frame-global vertex transforms.
    vwh_cache: frame-global Y projections indexed by VWH.
    """
    # Reset records buffers so a seg with no yt/yb edge draw (e.g.,
    # back[1]==ch) doesn't consume stale records from a previous seg.
    if _span_clip_6502 is not None:
        _span_clip_6502.reset_records()
    svwh = fp_segs_vwh[si]
    s = svwh[0]
    v1_idx, v2_idx = s[0], s[1]

    # Back-face test using prescaled linedef vertices.  ldx/ldy are taken
    # from the precomputed s8-asserted table (matches 6502 packed format).
    ld = linedefs[s[3]]
    ldx, ldy = svwh[13], svwh[14]
    # EXACT back-face (2026-08-25): full-precision dot against the TRUE
    # reference point — the raw world vertex in 8.8 prescaled (x32 is
    # exact). This subsumes the old truncated dot + frac-corr tie repair:
    # the rounded fp_vertexes reference sat up to half a unit off the
    # line and culled front walls near edge-on (the 9C.C9/4E.F8/F4
    # witness). Ties keep the '>'-form convention (matches the packed
    # C-1 bake for axis forms and dy' > 0 for diagonals).
    _rv = vertexes[ld[0]]
    _lx88 = (_rv[0] - MAP_CENTER_X) * 32
    _ly88 = (_rv[1] - MAP_CENTER_Y) * 32
    dot = (ldy * (fp_module.VIEW_PX88 - _lx88)
           - ldx * (fp_module.VIEW_PY88 - _ly88))
    tie_sign = ldy
    tie_sx = ldx
    if s[4] == 1:
        dot = -dot
        tie_sign = -ldy
        tie_sx = -ldx
    if dot < 0 or (dot == 0 and (tie_sign < 0 or
                                 (tie_sign == 0 and tie_sx > 0))):
        return

    map_trace["segs_processed"].add(si)
    map_trace["vertices"].add(v1_idx)
    map_trace["vertices"].add(v2_idx)

    front_idx, back_idx = svwh[1], svwh[2]
    fh, ch = svwh[3], svwh[4]

    # Lazily compute + cache view-space transforms (only for segs that pass back-face)
    fp_module.mul_cat("view")
    vm = map_trace["vertex_muls"]
    for vi in (v1_idx, v2_idx):
        if vi not in vm:
            vm[vi] = [0, 0]
    v_before = fp_module.mul_counts["view"]
    _tv = fp_module.fp_to_view_t16 if _T16 else fp_to_view
    if vxcache[v1_idx] is None:
        vxcache[v1_idx] = _tv(fp_vertexes[v1_idx][0], fp_vertexes[v1_idx][1], ctx)
        vm[v1_idx][0] += fp_module.mul_counts["view"] - v_before
        v_before = fp_module.mul_counts["view"]
    if vxcache[v2_idx] is None:
        vxcache[v2_idx] = _tv(fp_vertexes[v2_idx][0], fp_vertexes[v2_idx][1], ctx)
        vm[v2_idx][0] += fp_module.mul_counts["view"] - v_before
    vc1_full = vxcache[v1_idx]
    evx1_t, evx1_r, evy1, fvx1, vy_idx1 = vc1_full[:5]
    vc2_full = vxcache[v2_idx]
    evx2_t, evx2_r, evy2, fvx2, vy_idx2 = vc2_full[:5]
    # Sub-pixel uses truncated vx (frac compensates); otherwise use rounded
    evx1 = evx1_t
    evx2 = evx2_t

    # Near clip — EV16 (2026-08-09): verdicts + crossing on full 8.8
    # totals, same block as packed_render_seg (fp_near_clip retired
    # from the seg paths; verdicts are bit-identical, crossing gains
    # its real fraction).
    cxf1 = cxf2 = None
    _totals = fp_module.fp_to_view_totals_t16 if _T16 else fp_module.fp_to_view_totals
    _cross = fp_module.fp_cross_t16 if _T16 else fp_module.fp_cross_88
    _nearv = fp_module.T16_NEAR_VERDICT if _T16 else 128
    tvx1, tvy1 = _totals(
        fp_vertexes[v1_idx][0], fp_vertexes[v1_idx][1], ctx)
    tvx2, tvy2 = _totals(
        fp_vertexes[v2_idx][0], fp_vertexes[v2_idx][1], ctx)
    c1 = tvy1 < _nearv
    c2 = tvy2 < _nearv
    if c1 and c2:
        return
    if not (c1 or c2):
        ex1, ey1, ex2, ey2 = evx1, evy1, evx2, evy2
    elif c1:
        cx = _cross(tvx1, tvy1, tvx2, tvy2)
        if cx is None:
            return
        cx88 = (cx << 3) if _T16 else cx
        ex1, ey1 = cx88 >> 8, None
        cxf1 = cx88 & 0xFF
        ex2, ey2 = evx2, evy2
    else:
        cx = _cross(tvx2, tvy2, tvx1, tvy1)
        if cx is None:
            return
        cx88 = (cx << 3) if _T16 else cx
        ex2, ey2 = cx88 >> 8, None
        cxf2 = cx88 & 0xFF
        ex1, ey1 = evx1, evy1

    # Reciprocals and X projection — cached per vertex (non-near-clipped only)
    idx1 = vy_idx1 if ey1 == evy1 else 2   # crossing idx = NEAR at
    idx2 = vy_idx2 if ey2 == evy2 else 2   # half-unit granularity, both scales
    rxh1, rxl1 = fp_recip(idx1)
    rxh2, rxl2 = fp_recip(idx2)

    fp_module.mul_cat("proj")
    p_before = fp_module.mul_counts["proj"]
    # Cache key 'sx' in vxcache: (sx, rxh, rxl) appended on first X projection.
    # Near-clipped endpoints always recompute (different ex/ey).
    vc1 = vxcache[v1_idx]
    if ey1 == evy1 and len(vc1) > 5:
        sx1, rxh1, rxl1 = vc1[5], vc1[6], vc1[7]
    else:
        fvx1_c = fvx1 if ey1 == evy1 else (cxf1 or 0)
        sx1 = fp_project_x(ex1, fvx1_c, rxh1, rxl1)
        if ey1 == evy1:
            vxcache[v1_idx] = vc1 + (sx1, rxh1, rxl1)
    vm[v1_idx][1] += fp_module.mul_counts["proj"] - p_before
    p_before = fp_module.mul_counts["proj"]

    vc2 = vxcache[v2_idx]
    if ey2 == evy2 and len(vc2) > 5:
        sx2, rxh2, rxl2 = vc2[5], vc2[6], vc2[7]
    else:
        fvx2_c = fvx2 if ey2 == evy2 else (cxf2 or 0)
        sx2 = fp_project_x(ex2, fvx2_c, rxh2, rxl2)
        if ey2 == evy2:
            vxcache[v2_idx] = vc2 + (sx2, rxh2, rxl2)
    vm[v2_idx][1] += fp_module.mul_counts["proj"] - p_before

    if _USE_ANGLE_COL:
        from angle_bbox import view_col as _view_col
        sx1 = _view_col(ex1, ey1)
        sx2 = _view_col(ex2, ey2)

    x_lo = min(sx1, sx2)
    x_hi = max(sx1, sx2)

    # Tie drop (2026-08-22), mirroring seg_emit.s and packed_render_seg:
    # under the half-open decree sx1 == sx2 spans ZERO columns, so the
    # seg occludes nothing and only drew a 1px sliver. (Reversed segs
    # are NOT dropped here — the float path has always kept them; that
    # is a separate, pre-existing divergence from the engine.)
    if sx1 == sx2:
        return

    fp_module.mul_cat("clip")   # has_gap may call fp_eval → don't pollute "proj"
    if not clips.has_gap(x_lo, x_hi):
        return

    map_trace["segs_drawn"].add(si)

    # Front-sector Y projections via VWH cache (flat array, O(1) lookup).
    # Near-clipped endpoints bypass the cache (different recip).
    fp_module.mul_cat("proj")
    p_before = fp_module.mul_counts["proj"]
    _vft1, _vfb1 = svwh[5], svwh[6]
    ryh1, ryl1 = fp_recip(idx1)
    if ey1 == evy1 and vwh_cache[_vft1] is not None and vwh_cache[_vfb1] is not None:
        ft1 = vwh_cache[_vft1]
        fb1 = vwh_cache[_vfb1]
    else:
        ft1 = fp_project_y(ch - vz, ryh1, ryl1)
        fb1 = fp_project_y(fh - vz, ryh1, ryl1)
        if ey1 == evy1:
            vwh_cache[_vft1] = ft1
            vwh_cache[_vfb1] = fb1
    vm[v1_idx][1] += fp_module.mul_counts["proj"] - p_before
    p_before = fp_module.mul_counts["proj"]
    _vft2, _vfb2 = svwh[7], svwh[8]
    ryh2, ryl2 = fp_recip(idx2)
    if ey2 == evy2 and vwh_cache[_vft2] is not None and vwh_cache[_vfb2] is not None:
        ft2 = vwh_cache[_vft2]
        fb2 = vwh_cache[_vfb2]
    else:
        ft2 = fp_project_y(ch - vz, ryh2, ryl2)
        fb2 = fp_project_y(fh - vz, ryh2, ryl2)
        if ey2 == evy2:
            vwh_cache[_vft2] = ft2
            vwh_cache[_vfb2] = fb2
    vm[v2_idx][1] += fp_module.mul_counts["proj"] - p_before

    solid = back_idx is None
    if back_idx is not None:
        back = fp_sectors[back_idx]
        if back[1] <= fh or back[0] >= ch:
            solid = True
    else:
        back = None

    # Suppress verticals where the wall is continuous through the
    # endpoint — see _seg_novt_flags above for the two rules.
    _novt = _seg_novt_flags[si]
    no_vt1 = bool(_novt & _SF_NOVT1)
    no_vt2 = bool(_novt & _SF_NOVT2)

    # Annotation mode: record ALL verticals for overlay.
    if _novt_annotate:
        s = svwh[0]
        r1_v1 = s[0] not in _ld_endpoint_verts
        r1_v2 = s[1] not in _ld_endpoint_verts
        def _vt_rule(suppressed, is_r1):
            if not suppressed: return ""
            return "R1" if is_r1 else "R2"
        _novt_annotations.append((sx1, ft1, fb1,
            f"s{si} v{s[0]}", _vt_rule(no_vt1, r1_v1), solid, no_vt1))
        _novt_annotations.append((sx2, ft2, fb2,
            f"s{si} v{s[1]}", _vt_rule(no_vt2, r1_v2), solid, no_vt2))

    _RED = (255, 0, 0)

    fp_module.mul_cat("clip")
    _sH = _vs_heights(si)               # trigger heights (solid-aliased)
    _s0 = svwh[0]
    if solid:
        _lines = []
        if ch > vz: _lines.append((sx1, ft1, sx2, ft2))   # subsector eyeline
        if fh < vz: _lines.append((sx1, fb1, sx2, fb2))   # rule (2026-08-13)
        clips.draw_clipped(_lines, GREEN, surface, draw_stats)
        # VERTEX-SPAN DESCRIPTORS replace per-seg verticals + APEDGE
        emit_vertex_spans(_s0[0], sx1,
                          lambda h: fp_project_y(h - vz, ryh1, ryl1),
                          _sH, clips, surface, draw_stats,
                          ey1 == evy1 and 0 <= sx1 <= 255)
        emit_vertex_spans(_s0[1], sx2,
                          lambda h: fp_project_y(h - vz, ryh2, ryl2),
                          _sH, clips, surface, draw_stats,
                          ey2 == evy2 and 0 <= sx2 <= 255)
        if deferred is not None:
            deferred.append(('solid', x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2))
        else:
            clips.mark_solid(x_lo, x_hi, sx1=sx1, sx2=sx2, yt1=ft1, yt2=ft2, yb1=fb1, yb2=fb2)
    elif back:
        # Only project back heights when needed (saves up to 8 muls)
        need_bt = back[1] < ch   # ceiling drops: need bt for upper step + tighten
        need_bb = back[0] > fh   # floor rises: need bb for lower step + tighten

        if need_bt:
            fp_module.mul_cat("proj")
            _vbt1, _vbt2 = svwh[9], svwh[11]  # back ceil VWH at v1, v2
            if ey1 == evy1 and vwh_cache[_vbt1] is not None:
                bt1 = vwh_cache[_vbt1]
            else:
                bt1 = fp_project_y(back[1] - vz, ryh1, ryl1)
                if ey1 == evy1: vwh_cache[_vbt1] = bt1
            if ey2 == evy2 and vwh_cache[_vbt2] is not None:
                bt2 = vwh_cache[_vbt2]
            else:
                bt2 = fp_project_y(back[1] - vz, ryh2, ryl2)
                if ey2 == evy2: vwh_cache[_vbt2] = bt2
            fp_module.mul_cat("clip")
            lines = [(sx1, bt1, sx2, bt2)]
            if ch <= vz:  # face below eyeline: top edge (ft) always clipped
                yt_idx = 0  # bt at index 0
            else:
                lines.insert(0, (sx1, ft1, sx2, ft2))
                yt_idx = 1  # bt at index 1 (ft at 0)
            clips.draw_clipped(lines, GREEN, surface, draw_stats,
                               roles={yt_idx: 'top'})   # (ignored: python-only path)
        elif back[1] > ch:
            clips.draw_clipped([(sx1, ft1, sx2, ft2)], GREEN, surface,
                               draw_stats, roles={0: 'top'})

        if need_bb:
            fp_module.mul_cat("proj")
            _vbb1, _vbb2 = svwh[10], svwh[12]  # back floor VWH at v1, v2
            if ey1 == evy1 and vwh_cache[_vbb1] is not None:
                bb1 = vwh_cache[_vbb1]
            else:
                bb1 = fp_project_y(back[0] - vz, ryh1, ryl1)
                if ey1 == evy1: vwh_cache[_vbb1] = bb1
            if ey2 == evy2 and vwh_cache[_vbb2] is not None:
                bb2 = vwh_cache[_vbb2]
            else:
                bb2 = fp_project_y(back[0] - vz, ryh2, ryl2)
                if ey2 == evy2: vwh_cache[_vbb2] = bb2
            fp_module.mul_cat("clip")
            lines = [(sx1, bb1, sx2, bb2)]
            if fh >= vz:  # face above eyeline: bottom edge (fb) always clipped
                pass
            else:
                lines.insert(1, (sx1, fb1, sx2, fb2))
            clips.draw_clipped(lines, GREEN, surface, draw_stats,
                               roles={0: 'bot'})  # bb is yb-line at idx 0
        elif back[0] < fh:
            clips.draw_clipped([(sx1, fb1, sx2, fb2)], GREEN, surface,
                               draw_stats, roles={0: 'bot'})

        # VERTEX-SPAN DESCRIPTORS replace frame verticals + APEDGE
        emit_vertex_spans(_s0[0], sx1,
                          lambda h: fp_project_y(h - vz, ryh1, ryl1),
                          _sH, clips, surface, draw_stats,
                          ey1 == evy1 and 0 <= sx1 <= 255)
        emit_vertex_spans(_s0[1], sx2,
                          lambda h: fp_project_y(h - vz, ryh2, ryl2),
                          _sH, clips, surface, draw_stats,
                          ey2 == evy2 and 0 <= sx2 <= 255)

        # Tighten: use back heights only if computed, otherwise front = tighter
        tt1 = bt1 if need_bt else ft1
        tt2 = bt2 if need_bt else ft2
        tb1 = bb1 if need_bb else fb1
        tb2 = bb2 if need_bb else fb2
        yt1, yt2 = max(ft1, tt1), max(ft2, tt2)
        yb1, yb2 = min(fb1, tb1), min(fb2, tb2)
        # top_dom/bot_dom were the existing-spans dominance signal.  In the
        # current Python+6502-shadow path they are dead — `EndpointClipSpans.
        # tighten` ignores them, `_span_clip_6502.tighten` (span_clip.asm)
        # doesn't accept them, and the only consumer was the now-superseded
        # 6502 front-end's queue_tighten which has its own ASM line_survives.
        # The new line_above_spans/line_below_spans checks (used by the
        # tighten no-op skip) provide the actually-needed information.
        # Stub these to False so downstream tuple positions stay aligned.
        top_dom = bot_dom = False
        # Emit flags mirror which lines Python's draw_clipped actually drew.
        emit_top = need_bt or (back[1] > ch)
        emit_bot = need_bb or (back[0] < fh)
        # Secondary edges: for steps (need_bt/need_bb), Python also draws
        # the front ceiling/floor line — pass ft/fb so tighten can emit
        # those lines at the overlap endpoints.
        emit_sec_top = need_bt and (ch > vz)
        emit_sec_bot = need_bb and (fh < vz)
        yt_sec1 = ft1 if emit_sec_top else None
        yt_sec2 = ft2 if emit_sec_top else None
        yb_sec1 = fb1 if emit_sec_bot else None
        yb_sec2 = fb2 if emit_sec_bot else None
        if deferred is not None:
            deferred.append(('tighten', x_lo, x_hi, sx1, sx2,
                             yt1, yt2, yb1, yb2, top_dom, bot_dom,
                             emit_top, emit_bot, emit_sec_top, emit_sec_bot,
                             yt_sec1, yt_sec2, yb_sec1, yb_sec2))
            clips.snapshot_tighten_records(*deferred[-1][1:])
        else:
            clips.tighten(x_lo, x_hi, sx1, sx2, yt1, yt2, yb1, yb2,
                          top_dom, bot_dom,
                          emit_top=emit_top, emit_bot=emit_bot,
                          emit_sec_top=emit_sec_top, emit_sec_bot=emit_sec_bot,
                          yt_sec1=yt_sec1, yt_sec2=yt_sec2,
                          yb_sec1=yb_sec1, yb_sec2=yb_sec2)


_anim_ss_hook = None    # anim_sectors installs a per-subsector visibility
                        # hook here (lazy mover patching); None = no cost

def render_subsector_fp(idx, clips, ctx, vz, surface, vxcache, vwh_cache):
    if _anim_ss_hook is not None:
        _anim_ss_hook(idx)
    """Render a subsector with frame-global vertex cache.

    ctx: view context tuple from fp_view_context.
    """
    ssec = fp_ssectors[idx]

    # Both caches are lazily populated by fp_render_seg:
    # vxcache (frame-global): view transforms, computed on first access per vertex
    # vwh_cache (frame-global): Y projections indexed by VWH
    # Deferral removed 2026-07-16: ops apply at seg end (deferred=None).
    for si in range(ssec[1], ssec[1] + ssec[0]):
        fp_render_seg(si, clips, ctx, vz, surface, vxcache, vwh_cache, None)


def render_bsp_fp(nid, clips, ctx, vz,
                   wx_full, wy_full, cos_f, sin_f, surface, vxcache, vwh_cache):
    """BSP traversal for the 8-bit fixed-point path."""
    if nid == len(nodes) - 1:
        vspan_frame_reset()                 # root call = new frame
    if clips.is_full():
        return
    if nid & NF_SUBSECTOR:
        ssid = 0 if nid == 0xFFFF else nid & 0x7FFF
        map_trace["subsectors"].add(ssid)
        map_trace["ss_order"].append(ssid)
        render_subsector_fp(ssid, clips, ctx, vz, surface, vxcache, vwh_cache)
        return
    map_trace["nodes_visited"].add(nid)
    node = nodes[nid]
    side = point_on_side(wx_full, wy_full, node)
    ch = (node[12], node[13])
    # DOOM'S SHAPE (2026-09-04, Eben): the NEAR child is descended
    # UNCONDITIONALLY, exactly as r_bsp.c does -- it tests only the
    # far box.  The near child is the half-space the viewer stands
    # in, so the test rejected 5% of the time (1.9 subtrees/frame)
    # while costing ~37.7 calls/frame; measured -2.8% over the
    # 3,960-pose grid.  Over-descent draws NOTHING extra (the segs
    # are clipped individually), so no pixel moves -- see
    # project_overtraversal_robustness.
    render_bsp_fp(ch[side], clips, ctx, vz,
                  wx_full, wy_full, cos_f, sin_f, surface, vxcache, vwh_cache)
    if clips.is_full():
        return
    far = side ^ 1
    if (nid, far) in ADESC:
        render_bsp_fp(ch[far], clips, ctx, vz,
                      wx_full, wy_full, cos_f, sin_f, surface, vxcache, vwh_cache)
        return
    br = fp_bbox_visible_fixed(node, far, ctx)
    if br is not None:
        if clips.has_gap(br[0], br[1]):
            render_bsp_fp(ch[far], clips, ctx, vz,
                          wx_full, wy_full, cos_f, sin_f, surface, vxcache, vwh_cache)


# ── Packed-ROM rendering (reads from byte arrays, writes cache to RAM) ────────
#
# Same algorithm as the classic FP pipeline above, but geometry comes from
# packed_rom_main / packed_rom_detail byte arrays (via read_u8/read_s16 etc.)
# and cached transforms live in a RAM byte array (via write_s16 / is_valid).
# The EndpointClipSpans back-end and all fp.py math helpers are shared.

from wad_packed import (read_u8, read_s8, read_u16, read_s16, write_u16, write_s16,
                        clear_valid, is_valid, set_valid,
                        VERTEX_SIZE, NODE_SIZE, SSECTOR_SIZE, SEG_HDR_SIZE, SEG_DTL_SIZE,
                        seg_hdr_off as _seg_hdr_off,
                        seg_hdr_slot as _seg_hdr_slot,
                        VWH_SIZE, VXCACHE_ENTRY,
                        SH_V1, SH_V2, SH_FORM, SH_C, SH_FLAGS, SH_DIAG,
                        SD_FH, SD_CH, SD_BFH, SD_BCH,
                        SD_VWH_FT1, SD_VWH_FB1, SD_VWH_FT2, SD_VWH_FB2,
                        SD_VWH_BT1, SD_VWH_BB1, SD_VWH_BT2, SD_VWH_BB2,
                        SF_SAMEDIR, SF_SOLID, SF_NEEDBT, SF_NEEDBB, SF_NOVT1, SF_NOVT2,
                        VC_VX, VC_VY, VC_VYIDX, VC_SX, VYCACHE_ENTRY)

use_packed = True    # packed ROM path is now the sole FP renderer

# Alias the already-built packed data
_p_rom_main   = packed_rom_main
_p_rom_detail = packed_rom_detail
_p_rom_recip  = packed_rom_recip
_p_layout     = packed_layout




def _packed_read_vxcache(ram, vi):
    """Read a vertex cache entry from RAM.

    Returns (evx_t, evy, vy_idx, sx, fvx) or None.
    VC_VX stores the 8.8 view-x as (evx_t << 8) | fvx.
    """
    if not is_valid(ram, _p_layout['ram_vxcache_valid'], vi):
        return None
    base = _p_layout['ram_vxcache'] + vi * VXCACHE_ENTRY
    vx88 = read_s16(ram, base + VC_VX)
    vy   = read_s16(ram, base + VC_VY)
    vyi  = read_u16(ram, base + VC_VYIDX)
    sx   = read_s16(ram, base + VC_SX)
    evx_t = vx88 >> 8                    # signed high byte
    fvx   = vx88 & 0xFF                  # unsigned low byte
    return (evx_t, vy, vyi, sx, fvx)


def _packed_write_vxcache(ram, vi, evx_t, fvx, vy, vy_idx, sx):
    """Write a vertex cache entry to RAM and set its valid bit.

    VC_VX stores the 8.8 view-x: (evx_t << 8) | fvx.
    """
    base = _p_layout['ram_vxcache'] + vi * VXCACHE_ENTRY
    vx88 = ((evx_t & 0xFF) << 8) | (fvx & 0xFF)
    write_s16(ram, base + VC_VX, vx88 if vx88 < 0x8000 else vx88 - 0x10000)
    write_s16(ram, base + VC_VY, vy)
    write_u16(ram, base + VC_VYIDX, vy_idx)
    write_s16(ram, base + VC_SX, sx)
    set_valid(ram, _p_layout['ram_vxcache_valid'], vi)


def _packed_read_vwh(ram, wi):
    """Read a VWH cache entry from RAM.  Returns screen Y (s16) or None."""
    if not is_valid(ram, _p_layout['ram_vwh_valid'], wi):
        return None
    base = _p_layout['ram_vwh_cache'] + wi * VYCACHE_ENTRY
    return read_s16(ram, base)


def _packed_write_vwh(ram, wi, sy):
    """Write a VWH cache entry to RAM and set its valid bit."""
    base = _p_layout['ram_vwh_cache'] + wi * VYCACHE_ENTRY
    write_s16(ram, base, sy)
    set_valid(ram, _p_layout['ram_vwh_valid'], wi)


def packed_render_seg(si, clips, ctx, vz, surface, ram, deferred=None):
    """Render seg #si reading geometry from packed ROM, caching in RAM.

    Produces IDENTICAL output to fp_render_seg when data is consistent.
    """
    # Per-seg fused-walker state (the zero-touch dispatch's scope)
    if _span_clip_6502 is not None:
        _span_clip_6502.fused_begin()
    layout = _p_layout
    rom = _p_rom_main
    rom_d = _p_rom_detail

    # ── Read seg header from rom_main ──
    seg_off = layout['off_seg_hdr'] + _seg_hdr_off(si)
    _w1 = read_u16(rom, seg_off + SH_V1)    # (A=idx&255, B=idx>>3) key form
    _w2 = read_u16(rom, seg_off + SH_V2)
    v1_idx = (_w1 >> 8) * 8 + (_w1 & 7)
    v2_idx = (_w2 >> 8) * 8 + (_w2 & 7)
    bf_form = rom[seg_off + SH_FORM]        # 0-3 axis, >=4 diagonal dir_id+4
    flags  = read_u8(rom,  seg_off + SH_FLAGS)

    # ── Back-face test (same arithmetic as classic path) ──
    px_int = ctx[0]
    py_int = ctx[1]
    if bf_form < 4:
        # axis C-form: one signed compare, SAMEDIR folded at pack time
        bf_c16 = read_s16(rom, seg_off + SH_C)
        # STRICT compares, same as the 6502: the tie is resolved at PACK
        # time (2026-08-25). Position truncation is a floor, so a
        # truncated tie always means the true position is on the '>'
        # side; the packer ships C-1 for forms 0/2, making this strict
        # '>' read as '>= C', and the '<' forms keep tie->back. The old
        # runtime frac refinement here is gone WITH the bake -- reading
        # the baked constant with a strict compare IS the refinement,
        # and the 6502 and this mirror are bit-identical by construction
        # again (they briefly disagreed: the mirror refined, the 6502's
        # axis arms did not -- the 1c.26/56.c3/fc show-through).
        d = (px_int if bf_form < 2 else py_int) - bf_c16
        if (d >= 0) if (bf_form & 1) else (d <= 0):
            return
    else:
        # diagonal DELTA form: primitives from the DIR tables, lv1 from the
        # DEDUPED LV1 records (u8 id at +SH_DIAG -> 4 planes at $00/$80/
        # $100/$180), SAMEDIR folded at pack
        od = layout['off_dirs']; md = layout['max_dirs']
        did = bf_form - 4
        dxm = rom[od + did]; dym = rom[od + md + did]
        sg  = rom[od + 2 * md + did]
        dxp = -dxm if (sg & 0x40) else dxm
        dyp = -dym if (sg & 0x80) else dym
        ol = layout['off_lv1']; rid = rom[seg_off + SH_DIAG]
        lv1_x = rom[ol + 0x000 + rid] | (rom[ol + 0x080 + rid] << 8)
        lv1_y = rom[ol + 0x100 + rid] | (rom[ol + 0x180 + rid] << 8)
        if lv1_x & 0x8000: lv1_x -= 0x10000
        if lv1_y & 0x8000: lv1_y -= 0x10000
        # EXACT banded verdict (2026-08-25, mirrors bf_band): the truncated
        # dot's error (viewpoint fraction + lv1 rounding) is < 256 dot
        # units, so out-of-band the truncated sign is exact and in-band
        # the full-precision dot — against the TRUE reference point
        # (the K plane carries its sub-prescale residues) — decides.
        # Python computes the full-precision form everywhere; the 6502's
        # banded evaluation equals it by construction (the witness class:
        # seg 121 at 9C.C9/4E.F8/F4, a front solid culled at dot_int -4,
        # exact +4612 — the maze bled through its columns).
        kx, ky, _cdx, _cdy = layout['lv1_krec'][rid]
        dot = (dyp * (fp_module.VIEW_PX88 - (256 * lv1_x + 32 * kx))
               - dxp * (fp_module.VIEW_PY88 - (256 * lv1_y + 32 * ky)))
        if dot < 0 or (dot == 0 and dyp <= 0):
            return

    # ── Read vertex positions from rom_main (page-split SoA planes:
    # OX/OY/PG, 512 bytes each — mirrors the page-decomposed fetch) ──
    verts_off = layout['off_verts']
    def _vplane(plane, i):
        return rom[verts_off + plane + (i >> 8) * 256 + (i & 0xFF)]
    def _vpg(i):
        # PAGE-DECOMPOSED (2026-08-11): w = ((nib+2sel)-2)<<8 + u8 offset
        ox, oy, pg = (_vplane(0x000, i), _vplane(0x200, i),
                      _vplane(0x400, i))
        return ((((pg & 3) - 2) << 8) + ox,
                ((((pg >> 2) & 3) - 2) << 8) + oy)
    wx1, wy1 = _vpg(v1_idx)
    wx2, wy2 = _vpg(v2_idx)

    if _USE_ANGLE_SEG:
        # ── Angle-space 2b projection (mirrors 6502 seg_c + seg_project) ──
        _seg2b_stats['segs'] += 1
        import angle_seg as _AS
        _na, _L = _AS.seg_consts(ldx, ldy)
        _r = _AS.seg_2b(wx1, wy1, wx2, wy2, ldx, ldy,
                        ctx[0], ctx[1], _VIEW_AB, _na, _L)
        if _r is None:
            _seg2b_stats['cull'] += 1
            if _seg2b_debug is not None:
                _seg2b_debug[si] = ('CULL',)
            return
        (sx1, _depth1), (sx2, _depth2) = _r
        _py1 = lambda h: _AS.proj_y(h, _depth1, vz)
        _py2 = lambda h: _AS.proj_y(h, _depth2, vz)
        # Force ey!=evy below so the vwh/vxcache fast-paths are bypassed
        # (depth is per-seg-endpoint here, recomputed via _py1/_py2).
        evy1, ey1, evy2, ey2 = 0, 1, 0, 1
    else:
        # ── View transform with RAM vxcache ──
        fp_module.mul_cat("view")
        vc1 = _packed_read_vxcache(ram, v1_idx)
        if vc1 is not None and vc1[3] == 0:
            # sx-less partial entry: the 6502 has NO such state (sx_vert
            # fills transform+clip+sx atomically) — serving it here used
            # to recompute sx from the cache's NARROWED 8.8 evx (s8
            # integer byte: evx -129 wraps to +127, sx flips sides, the
            # margin-cert staging-wrap class). Treat as a full miss.
            vc1 = None
        if vc1 is None:
            result = (fp_module.fp_to_view_t16 if _T16 else fp_to_view)(wx1, wy1, ctx)
            evx1_t, evx1_r, evy1, fvx1, vy_idx1 = result[:5]
            _packed_write_vxcache(ram, v1_idx, evx1_t, fvx1, evy1, vy_idx1, 0)
            _vc1_has_sx = False
        else:
            evx1_t = vc1[0]
            evy1   = vc1[1]
            vy_idx1 = vc1[2]
            fvx1   = vc1[4]
            evx1_r = evx1_t  # not used for projection, but set for consistency
            _vc1_has_sx = (vc1[3] != 0)

        vc2 = _packed_read_vxcache(ram, v2_idx)
        if vc2 is not None and vc2[3] == 0:
            vc2 = None                     # (mirror — see vc1)
        if vc2 is None:
            result = (fp_module.fp_to_view_t16 if _T16 else fp_to_view)(wx2, wy2, ctx)
            evx2_t, evx2_r, evy2, fvx2, vy_idx2 = result[:5]
            _packed_write_vxcache(ram, v2_idx, evx2_t, fvx2, evy2, vy_idx2, 0)
            _vc2_has_sx = False
        else:
            evx2_t = vc2[0]
            evy2   = vc2[1]
            vy_idx2 = vc2[2]
            fvx2   = vc2[4]
            evx2_r = evx2_t
            _vc2_has_sx = (vc2[3] != 0)

        evx1 = evx1_t
        evx2 = evx2_t

        # ── Near clip — EV16 (2026-08-09, de-flagged): verdicts and
        # crossing on the full 8.8 totals. Mirrors the 6502
        # cr_recover + reproject_at_crossing: the totals recompute is
        # bit-identical to the original fetch (position-independent
        # base + frame ref), and the crossing projects with its REAL
        # fraction. Toward-float gate: tools/nc88_verdict.py.
        cxf1 = cxf2 = None                  # crossing fracs
        _totals = fp_module.fp_to_view_totals_t16 if _T16 else fp_module.fp_to_view_totals
        _cross = fp_module.fp_cross_t16 if _T16 else fp_module.fp_cross_88
        _nearv = fp_module.T16_NEAR_VERDICT if _T16 else 128
        tvx1, tvy1 = _totals(wx1, wy1, ctx)
        tvx2, tvy2 = _totals(wx2, wy2, ctx)
        c1 = tvy1 < _nearv
        c2 = tvy2 < _nearv
        _fill_near = (c1, c2, tvx1, tvy1, tvx2, tvy2)   # Master texture model
        if c1 and c2:
            return
        if not (c1 or c2):
            ex1, ey1, ex2, ey2 = evx1, evy1, evx2, evy2
        elif c1:
            cx = _cross(tvx1, tvy1, tvx2, tvy2)
            if cx is None:
                return
            cx88 = (cx << 3) if _T16 else cx   # counts -> 8.8 EXACT
            ex1, ey1 = cx88 >> 8, None         # None: crossing marker (a
            cxf1 = cx88 & 0xFF                 # count-evy CAN equal 1 in
            ex2, ey2 = evx2, evy2              # t16 — token compare unsafe)
        else:
            cx = _cross(tvx2, tvy2, tvx1, tvy1)
            if cx is None:
                return
            cx88 = (cx << 3) if _T16 else cx
            ex2, ey2 = cx88 >> 8, None
            cxf2 = cx88 & 0xFF
            ex1, ey1 = evx1, evy1

        # ── Reciprocals + X projection ──
        # (crossing idx = NEAR at half-unit granularity = 2, both scales)
        idx1 = vy_idx1 if ey1 == evy1 else 2
        idx2 = vy_idx2 if ey2 == evy2 else 2
        rxh1, rxl1 = fp_recip(idx1)
        rxh2, rxl2 = fp_recip(idx2)
        _fill_near += (rxh1, rxl1, rxh2, rxl2)   # 1/depth (M8, S) per end

        fp_module.mul_cat("proj")

        # sx1
        if ey1 == evy1 and _vc1_has_sx:
            sx1 = _packed_read_vxcache(ram, v1_idx)[3]
        else:
            fvx1_c = fvx1 if ey1 == evy1 else (cxf1 or 0)
            sx1 = fp_project_x(ex1, fvx1_c, rxh1, rxl1)
            if ey1 == evy1:
                # Update vxcache with sx
                base = _p_layout['ram_vxcache'] + v1_idx * VXCACHE_ENTRY
                write_s16(ram, base + VC_SX, sx1)

        # sx2
        if ey2 == evy2 and _vc2_has_sx:
            sx2 = _packed_read_vxcache(ram, v2_idx)[3]
        else:
            fvx2_c = fvx2 if ey2 == evy2 else (cxf2 or 0)
            sx2 = fp_project_x(ex2, fvx2_c, rxh2, rxl2)
            if ey2 == evy2:
                base = _p_layout['ram_vxcache'] + v2_idx * VXCACHE_ENTRY
                write_s16(ram, base + VC_SX, sx2)

        if _USE_ANGLE_COL:
            from angle_bbox import view_col as _view_col
            sx1 = _view_col(ex1, ey1)
            sx2 = _view_col(ex2, ey2)
        _py1 = lambda h: fp_project_y(h - vz, ryh1, ryl1)
        _py2 = lambda h: fp_project_y(h - vz, ryh2, ryl2)

    x_lo = min(sx1, sx2)
    x_hi = max(sx1, sx2)

    if _seg2b_debug is not None:
        _seg2b_debug[si] = (sx1, sx2)

    fp_module.mul_cat("clip")
    if not clips.has_gap(x_lo, x_hi):
        return

    # Reversed projection (the 1px edge-on 8F.1F class): DROPPED rather
    # than canonicalized (2026-07-15, Eben's call): measured cost is the
    # degenerate slivers only (5px at 1/18 suite positions), no
    # occlusion damage — the zero-records arm covers the aperture.
    # TIES join them 2026-08-22: under the half-open decree sx1 == sx2
    # covers ZERO columns, so a tie seg occludes nothing — it only drew
    # a 1px sliver. Mirrors seg_emit.s's BCS cull_jmp.
    if sx2 <= sx1:
        return

    # ── Read seg detail from rom_detail ──
    dtl_off = si * SEG_DTL_SIZE
    fh  = read_s8(rom_d, dtl_off + SD_FH)
    ch  = read_s8(rom_d, dtl_off + SD_CH)

    # ── Front-sector Y projections via VWH cache in RAM ──
    fp_module.mul_cat("proj")
    vwh_ft1 = read_u16(rom_d, dtl_off + SD_VWH_FT1)
    vwh_fb1 = read_u16(rom_d, dtl_off + SD_VWH_FB1)
    ryh1, ryl1 = fp_recip(idx1) if not _USE_ANGLE_SEG else (0, 0)

    cached_ft1 = _packed_read_vwh(ram, vwh_ft1) if ey1 == evy1 else None
    cached_fb1 = _packed_read_vwh(ram, vwh_fb1) if ey1 == evy1 else None
    if cached_ft1 is not None and cached_fb1 is not None:
        ft1 = cached_ft1
        fb1 = cached_fb1
    else:
        ft1 = _py1(ch)
        fb1 = _py1(fh)
        if ey1 == evy1:
            _packed_write_vwh(ram, vwh_ft1, ft1)
            _packed_write_vwh(ram, vwh_fb1, fb1)

    vwh_ft2 = read_u16(rom_d, dtl_off + SD_VWH_FT2)
    vwh_fb2 = read_u16(rom_d, dtl_off + SD_VWH_FB2)
    ryh2, ryl2 = fp_recip(idx2) if not _USE_ANGLE_SEG else (0, 0)

    cached_ft2 = _packed_read_vwh(ram, vwh_ft2) if ey2 == evy2 else None
    cached_fb2 = _packed_read_vwh(ram, vwh_fb2) if ey2 == evy2 else None
    if cached_ft2 is not None and cached_fb2 is not None:
        ft2 = cached_ft2
        fb2 = cached_fb2
    else:
        ft2 = _py2(ch)
        fb2 = _py2(fh)
        if ey2 == evy2:
            _packed_write_vwh(ram, vwh_ft2, ft2)
            _packed_write_vwh(ram, vwh_fb2, fb2)

    if _seg2b_debug is not None and _USE_ANGLE_SEG:
        _seg2b_debug[si] = (sx1, sx2, ft1, fb1, ft2, fb2)

    # ── Determine solid / two-sided ──
    solid = bool(flags & SF_SOLID)
    # NOVT moved out of the packed byte ($10/$20 now SF_STEPUP_T/B):
    # read the python-side list the fp path already uses
    no_vt1 = bool(_seg_novt_flags[si] & _SF_NOVT1)
    no_vt2 = bool(_seg_novt_flags[si] & _SF_NOVT2)
    # trigger heights for the vertex-span descriptors (solid alias;
    # dtl_off is assigned later — index the detail stream directly)
    # HALF-UNIT mover tier (2026-08-25): mover backs ship half-prescaled
    # in SD_BFH/BCH. The python packed tier stays INTEGER — normalize at
    # read (exact at rest: half bytes are 2h there; while a mover sits
    # at a half step the python reference floors — a documented gap,
    # the same class as the OBJ_DRAW reference gap).
    _h2b = (not solid) and fp_segs_vwh[si][2] in ANIM_SECTORS
    if solid:
        _pH = {'fh': fh, 'ch': ch, 'bfh': fh, 'bch': ch}
    else:
        _bfh_r = read_s8(rom_d, si * SEG_DTL_SIZE + SD_BFH)
        _bch_r = read_s8(rom_d, si * SEG_DTL_SIZE + SD_BCH)
        if _h2b:
            _bfh_r //= 2
            _bch_r //= 2
        _pH = {'fh': fh, 'ch': ch, 'bfh': _bfh_r, 'bch': _bch_r}

    # Annotation: record verticals for the V-key overlay (mirrors
    # fp_render_seg).  Rule string here is approximate — packed flags
    # don't preserve which rule fired, so we just say "Rn" if suppressed.
    if _novt_annotate:
        svwh = fp_segs_vwh[si]
        s = svwh[0]
        r1_v1 = s[0] not in _ld_endpoint_verts
        r1_v2 = s[1] not in _ld_endpoint_verts
        def _vt_rule(suppressed, is_r1):
            if not suppressed: return ""
            return "R1" if is_r1 else "Rn"
        _novt_annotations.append((sx1, ft1, fb1,
            f"s{si} v{s[0]}", _vt_rule(no_vt1, r1_v1), solid, no_vt1))
        _novt_annotations.append((sx2, ft2, fb2,
            f"s{si} v{s[1]}", _vt_rule(no_vt2, r1_v2), solid, no_vt2))

    if _seg_fill_hook is not None:      # Master textured port (fill_ref.py)
        _seg_fill_hook(si, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid,
                       near=locals().get('_fill_near'))
    fp_module.mul_cat("clip")
    if solid:
        lines = []
        # Subsector eyeline rule (2026-08-13): no top edges when the
        # subsector ceiling is at/below the sightline, no bottom edges
        # when the floor is at/above it — all seg classes uniformly.
        if ch <= vz:
            pass
        # Top horizontal: skip if line is above visible spans everywhere.
        elif _AP_SKIP_ENABLE and clips.line_above_spans(sx1, ft1, sx2, ft2):
            _ap_skip_stats['solid_top_skipped'] += 1
        else:
            lines.append((sx1, ft1, sx2, ft2))
        if fh >= vz:
            pass
        # Bottom horizontal: skip if line is below visible spans everywhere.
        elif _AP_SKIP_ENABLE and clips.line_below_spans(sx1, fb1, sx2, fb2):
            _ap_skip_stats['solid_bot_skipped'] += 1
        else:
            lines.append((sx1, fb1, sx2, fb2))
        if lines:
            clips.draw_clipped(lines, GREEN, surface, draw_stats)
        # VERTEX-SPAN DESCRIPTORS replace per-seg verticals + APEDGE
        emit_vertex_spans(v1_idx, sx1, _py1, _pH, clips, surface, draw_stats,
                          ey1 == evy1 and 0 <= sx1 <= 255)
        emit_vertex_spans(v2_idx, sx2, _py2, _pH, clips, surface, draw_stats,
                          ey2 == evy2 and 0 <= sx2 <= 255)
        if deferred is not None:
            deferred.append(('solid', x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2))
        else:
            clips.mark_solid(x_lo, x_hi, sx1=sx1, sx2=sx2, yt1=ft1, yt2=ft2, yb1=fb1, yb2=fb2)
    else:
        # Two-sided: read back heights from seg detail
        need_bt = bool(flags & SF_NEEDBT)
        need_bb = bool(flags & SF_NEEDBB)
        bfh = read_s8(rom_d, dtl_off + SD_BFH)
        bch = read_s8(rom_d, dtl_off + SD_BCH)
        if _h2b:
            bfh //= 2                     # half-unit tier normalization
            bch //= 2                     # (see the _pH note above)

        if True:
            # ==================== FUSED cascade =========================
            # Reordered cascade: no-record companion edges first, then
            # verticals (both against the pre-tighten pool), then each
            # ARMED aperture edge drawn and APPLIED as it goes
            # (sequential by decree), then the seg-end merge pass /
            # zero-touch dispatch.
            # --- projections (VWH-cached, identical to pipeline A) ---
            bt1 = bt2 = bb1 = bb2 = None
            if need_bt:
                fp_module.mul_cat("proj")
                vwh_bt1 = read_u16(rom_d, dtl_off + SD_VWH_BT1)
                vwh_bt2 = read_u16(rom_d, dtl_off + SD_VWH_BT2)
                bt1 = _packed_read_vwh(ram, vwh_bt1) if ey1 == evy1 else None
                if bt1 is None:
                    bt1 = _py1(bch)
                    if ey1 == evy1: _packed_write_vwh(ram, vwh_bt1, bt1)
                bt2 = _packed_read_vwh(ram, vwh_bt2) if ey2 == evy2 else None
                if bt2 is None:
                    bt2 = _py2(bch)
                    if ey2 == evy2: _packed_write_vwh(ram, vwh_bt2, bt2)
                fp_module.mul_cat("clip")
            if need_bb:
                fp_module.mul_cat("proj")
                vwh_bb1 = read_u16(rom_d, dtl_off + SD_VWH_BB1)
                vwh_bb2 = read_u16(rom_d, dtl_off + SD_VWH_BB2)
                bb1 = _packed_read_vwh(ram, vwh_bb1) if ey1 == evy1 else None
                if bb1 is None:
                    bb1 = _py1(bfh)
                    if ey1 == evy1: _packed_write_vwh(ram, vwh_bb1, bb1)
                bb2 = _packed_read_vwh(ram, vwh_bb2) if ey2 == evy2 else None
                if bb2 is None:
                    bb2 = _py2(bfh)
                    if ey2 == evy2: _packed_write_vwh(ram, vwh_bb2, bb2)
                fp_module.mul_cat("clip")
            # --- ap-skip whole-side gates (inert in the reference) ---
            _skip_top = (need_bt and _AP_SKIP_ENABLE and
                         clips.line_above_spans(sx1, bt1, sx2, bt2))
            _skip_bot = (need_bb and _AP_SKIP_ENABLE and
                         clips.line_below_spans(sx1, bb1, sx2, bb2))
            # --- companions (no records), pre-tighten pool ---
            if need_bt and not _skip_top:
                if ch <= vz:
                    pass
                elif (_AP_SKIP_ENABLE and
                        clips.line_above_spans(sx1, ft1, sx2, ft2)):
                    pass
                else:
                    clips.draw_clipped([(sx1, ft1, sx2, ft2)], GREEN,
                                       surface, draw_stats)
            if need_bb and not _skip_bot:
                if fh >= vz:
                    pass
                elif (_AP_SKIP_ENABLE and
                        clips.line_below_spans(sx1, fb1, sx2, fb2)):
                    pass
                else:
                    clips.draw_clipped([(sx1, fb1, sx2, fb2)], GREEN,
                                       surface, draw_stats)
            # --- verticals, still pre-tighten ---
            emit_vertex_spans(v1_idx, sx1, _py1, _pH, clips, surface,
                              draw_stats, ey1 == evy1 and 0 <= sx1 <= 255)
            emit_vertex_spans(v2_idx, sx2, _py2, _pH, clips, surface,
                              draw_stats, ey2 == evy2 and 0 <= sx2 <= 255)
            # --- armed aperture edges: each clips, plots and APPLIES as
            # it draws (sequential by decree — Eben accepted the
            # crossing-quantization divergence class for simplicity).
            if need_bt and not _skip_top:
                clips.draw_fused(sx1, bt1, sx2, bt2, 'top')
            elif (not need_bt) and bch > ch:
                if not (_AP_SKIP_ENABLE and
                        clips.line_above_spans(sx1, ft1, sx2, ft2)):
                    clips.draw_fused(sx1, ft1, sx2, ft2, 'top')
            if need_bb and not _skip_bot:
                clips.draw_fused(sx1, bb1, sx2, bb2, 'bot')
            elif (not need_bb) and bfh < fh:
                if not (_AP_SKIP_ENABLE and
                        clips.line_below_spans(sx1, fb1, sx2, fb2)):
                    clips.draw_fused(sx1, fb1, sx2, fb2, 'bot')
            # --- seg end: merge pass / zero-touch dispatch ---
            tt1 = bt1 if need_bt else ft1
            tt2 = bt2 if need_bt else ft2
            tb1 = bb1 if need_bb else fb1
            tb2 = bb2 if need_bb else fb2
            yt1, yt2 = max(ft1, tt1), max(ft2, tt2)
            yb1, yb2 = min(fb1, tb1), min(fb2, tb2)
            clips.fused_finish(x_lo, x_hi, yt1, yt2, yb1, yb2)
            return

    # Seg number annotation (toggle with I key; drawn on upscaled display)
    if _show_seg_numbers:
        _seg_annotations.append((si, sx1, sx2, ft1, ft2))


def packed_render_subsector(idx, clips, ctx, vz, surface, ram):
    if _anim_ss_hook is not None:
        _anim_ss_hook(idx)
    """Render a subsector reading from packed ROM arrays."""
    layout = _p_layout
    rom = _p_rom_main
    ss_off = layout['off_ss']              # SoA pages: PG / SI
    cnt = rom[layout['off_ss_cnt'] + idx]  # cnt-1; $FF = empty (PG/CNT split)
    if cnt == 0xFF:
        return
    count = cnt + 1
    pg = rom[ss_off + idx]                 # page, PLAIN (no sentinel bias)
    plo = rom[ss_off + 256 + idx]          # plain in-page offset (slot*stride)
    first_seg = _seg_hdr_slot((pg << 8) | plo)

    # Deferral removed 2026-07-16: packed_render_seg's deferred=None
    # branches call clips.mark_solid / clips.tighten at seg end with the
    # records LIVE in $0700/$0800 — the snapshot machinery is gone.
    for si in range(first_seg, first_seg + count):
        packed_render_seg(si, clips, ctx, vz, surface, ram, None)


def packed_render_bsp(nid, clips, ctx, vz,
                      wx_full, wy_full, cos_f, sin_f, surface, ram):
    if nid == len(nodes) - 1:
        vspan_frame_reset()                 # root call = new frame
    """BSP traversal reading nodes from packed ROM.

    Node children are read from rom_main.  point_on_side and bbox visibility
    use the original un-prescaled Python node data (same as render_bsp_fp)
    to avoid rounding differences from prescaled partition lines.
    """
    if clips.is_full():
        return
    if nid & NF_SUBSECTOR:
        ssid = 0 if nid == 0xFFFF else nid & 0x7FFF
        packed_render_subsector(ssid, clips, ctx, vz, surface, ram)
        return

    # Read children from packed ROM
    layout = _p_layout
    rom = _p_rom_main
    nb = layout['off_nodes']               # SoA pages; child ids at pg 6/7 (u8)
    # Leaf-ness is the parent's property (TYPE byte bit 7 = right child
    # is a subsector, bit 6 = left) — re-synthesize the WAD-style tag
    # bit for the recursive dispatch above.
    typ = rom[nb + 8*256 + nid]
    child_r = rom[nb + 6*256 + nid] | ((typ & 0x80) << 8)
    child_l = rom[nb + 7*256 + nid] | ((typ & 0x40) << 9)

    # point_on_side uses un-prescaled node data (matches render_bsp_fp exactly)
    node = nodes[nid]
    side = point_on_side(wx_full, wy_full, node)

    ch = (child_r, child_l)
    # DOOM'S SHAPE (2026-09-04, Eben): the NEAR child is descended
    # UNCONDITIONALLY, exactly as r_bsp.c does -- it tests only the
    # far box.  The near child is the half-space the viewer stands
    # in, so the test rejected 5% of the time (1.9 subtrees/frame)
    # while costing ~37.7 calls/frame; measured -2.8% over the
    # 3,960-pose grid.  Over-descent draws NOTHING extra (the segs
    # are clipped individually), so no pixel moves -- see
    # project_overtraversal_robustness.
    packed_render_bsp(ch[side], clips, ctx, vz,
                      wx_full, wy_full, cos_f, sin_f, surface, ram)
    if clips.is_full():
        return
    far = side ^ 1
    if (nid, far) in ADESC:
        packed_render_bsp(ch[far], clips, ctx, vz,
                          wx_full, wy_full, cos_f, sin_f, surface, ram)
        return
    br = fp_bbox_visible_fixed(node, far, ctx)
    if br is not None:
        if clips.has_gap(br[0], br[1]):
            packed_render_bsp(ch[far], clips, ctx, vz,
                              wx_full, wy_full, cos_f, sin_f, surface, ram)


# ── Verification: compare classic FP vs packed paths ─────────────────────────



# ── Top-down map visualisation ────────────────────────────────────────────────

def _fit_scale():
    """Compute scale to fit the whole map on screen."""
    xs = [v[0] for v in vertexes]
    ys = [v[1] for v in vertexes]
    margin = 40
    dw = (max(xs) - min(xs)) or 1
    dh = (max(ys) - min(ys)) or 1
    return min((SCREEN_W - 2 * margin) / dw, (SCREEN_H - 2 * margin) / dh)

_map_scale = _fit_scale()
_map_cx = 0.0    # world center X (updated per frame when map shown)
_map_cy = 0.0    # world center Y



# Precompute exact subsector polygons by clipping against BSP partition lines.
# Start with the map bounding box, then clip at each node on the root-to-leaf path.

def _clip_polygon_by_line(poly, lx, ly, ldx, ldy, keep_side):
    """Sutherland-Hodgman clip: keep vertices on keep_side of the partition line."""
    if not poly:
        return []
    def side(px, py):
        # Must match point_on_side: node[3]*dx - node[2]*dy > 0 → side 0
        cross = ldy * (px - lx) - ldx * (py - ly)
        return 0 if cross > 0 else 1
    out = []
    n = len(poly)
    for i in range(n):
        cx, cy = poly[i]
        nx, ny = poly[(i + 1) % n]
        c_side = side(cx, cy)
        n_side = side(nx, ny)
        if c_side == keep_side:
            out.append((cx, cy))
        if c_side != n_side:
            # Edge crosses the line — find intersection
            dx, dy = nx - cx, ny - cy
            denom = ldy * dx - ldx * dy
            if abs(denom) > 1e-10:
                t = (ldx * (cy - ly) - ldy * (cx - lx)) / denom
                out.append((cx + t * dx, cy + t * dy))
    return out

def _clip_polygon_to_bbox(poly, left, right, bot, top):
    """Clip polygon to axis-aligned bounding box."""
    # Clip by 4 half-planes: x >= left, x <= right, y >= bot, y <= top
    # Express each as a partition line (lx, ly, ldx, ldy) with appropriate side
    poly = _clip_polygon_by_line(poly, left, 0, 0, 1, 0)   # x >= left (keep side 0)
    poly = _clip_polygon_by_line(poly, right, 0, 0, -1, 0)  # x <= right
    poly = _clip_polygon_by_line(poly, 0, bot, -1, 0, 0)    # y >= bot
    poly = _clip_polygon_by_line(poly, 0, top, 1, 0, 0)     # y <= top
    return poly

_ss_polys = {}
def _build_ss_polys(nid, poly):
    if nid & NF_SUBSECTOR:
        ssid = 0 if nid == 0xFFFF else nid & 0x7FFF
        _ss_polys[ssid] = poly
        return
    node = nodes[nid]
    lx, ly, ldx, ldy = node[0], node[1], node[2], node[3]
    for side in (0, 1):
        clipped = _clip_polygon_by_line(poly, lx, ly, ldx, ldy, side)
        _build_ss_polys(node[12 + side], clipped)

# Start with a large bounding box around the entire map
_all_x = [v[0] for v in vertexes]
_all_y = [v[1] for v in vertexes]
_margin = 200
_map_poly = [
    (min(_all_x) - _margin, min(_all_y) - _margin),
    (max(_all_x) + _margin, min(_all_y) - _margin),
    (max(_all_x) + _margin, max(_all_y) + _margin),
    (min(_all_x) - _margin, max(_all_y) + _margin),
]
_build_ss_polys(len(nodes) - 1, _map_poly)

# Trim to map bbox, then verify each polygon contains its subsector's geometry.
# Degenerate subsectors on BSP partition lines get the wrong polygon — replace
# with convex hull of seg vertices.
def _point_in_poly(px, py, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj-xi)*(py-yi)/(yj-yi)+xi):
            inside = not inside
        j = i
    return inside

for _ssid in list(_ss_polys):
    _ss_polys[_ssid] = _clip_polygon_to_bbox(
        _ss_polys[_ssid],
        min(_all_x) - 16, max(_all_x) + 16,
        min(_all_y) - 16, max(_all_y) + 16)
    # Verify: does the polygon contain the subsector's first vertex?
    _ssec = ssectors[_ssid]
    if _ssec[0] > 0 and len(_ss_polys[_ssid]) >= 3:
        _sv = vertexes[segs[_ssec[1]][0]]
        if not _point_in_poly(_sv[0], _sv[1], _ss_polys[_ssid]):
            _ss_polys[_ssid] = None  # mark for convex hull fallback




# ── Angle conversion helpers ─────────────────────────────────────────────────


def byte_to_radians(b):
    """Convert 0..255 (8-bit angle) to radians."""
    return math.radians((b & 0xFF) * 360.0 / 256.0)


# [total, unclipped, clipped, trivial_reject, clip_reject]
draw_stats = [0, 0, 0, 0, 0]

sys.setrecursionlimit(10000)
