"""BspRender6502 — the engine rig's base: the span clipper rig (SpanClip6502)
plus the per-frame view setup and the render_frame entry. banked_bsp's
MasterBspRender builds the Master's memory images on top of it.
"""
import os
from span_clip_6502 import SpanClip6502
from symmap import sym as _sym


# ZP slots used by the engine — resolved from the linked symbol map.
ZP_PX           = _sym('zp_br_px')
ZP_PXH          = _sym('zp_br_px_h')
ZP_PY           = _sym('zp_br_py')
ZP_PYH          = _sym('zp_br_py_h')
ZP_VZ           = _sym('zp_br_vz')
ZP_SMAG         = _sym('zp_br_smag')
ZP_SNEG         = _sym('zp_br_sneg')
ZP_SONE         = _sym('zp_br_sone')
ZP_CMAG         = _sym('zp_br_cmag')
ZP_CNEG         = _sym('zp_br_cneg')
ZP_CONE         = _sym('zp_br_cone')
# Table base pointer slots (absolute RAM — the ZP scavenge moved most of
# them out of ZP; the angle module owns the freed slots).
ZP_PXRAW_LO     = _sym('zp_br_pxraw_l')
ZP_PXRAW_HI     = _sym('zp_br_pxraw_h')
ZP_PYRAW_LO     = _sym('zp_br_pyraw_l')
ZP_PYRAW_HI     = _sym('zp_br_pyraw_h')

ENTRY_BR_VIEW_SETUP   = _sym('view_setup')
ENTRY_BR_RENDER_FRAME = _sym('render_frame')



ADESC_NODES = 194                          # MAX_NODES (walk.s gate bound)


def adesc_reset_mem(sc):
    """Clear the walk's DYNAMIC always-descend bits (NODE_DSGN b3/b2).

    The engine keeps those bits across frames and has no wipe of its own:
    after a teleport the stale bits cost one frame of over-descent and the
    judge clears them.  A bench that steps between unrelated poses in one
    engine is not a motion the predictor is meant to survive, so the bench
    clears them and a cold frame descends exactly as the reference does.
    """
    from symmap import sym as _sym
    mem = sc.mpu.memory
    banked = hasattr(mem, 'select')        # banked_bsp swaps in BankedMemory
    base = _sym('NODE_DSGN')
    if banked:
        saved = mem[0xFE30]
        mem.select(7)                      # abi.BANK_WALK: the node SoA
    for i in range(ADESC_NODES):
        mem[base + i] &= 0xF3
    if banked:
        mem.select(saved)






class BspRender6502:
    """Persistent BSP-render 6502 instance for interactive use."""

    def __init__(self, packed_layout, packed_rom_main, packed_rom_detail,
                 packed_bbox_table, map_center_x=1200, map_center_y=-3250,
                 prescale=8):
        self.layout = packed_layout
        self.rom_main = packed_rom_main
        self.rom_detail = packed_rom_detail
        self.bbox_table = packed_bbox_table
        self.map_center_x = map_center_x
        self.map_center_y = map_center_y
        self.prescale = prescale
        self.last_cycles = 0

        self.sc = SpanClip6502()
        self._load_wad()

    def _load_wad(self):
        # The images themselves are built by banked_bsp.build_banked from
        # the packed layout; here only the angle tables' contract is checked.
        from engine_load import angle_table_contract
        angle_table_contract()

    def render_frame(self, player_x, player_y, angle_byte, floor_z=0):
        import fp
        sc = self.sc
        mem = sc.mpu.memory

        px_88 = int((player_x - self.map_center_x) * 256 / self.prescale)
        py_88 = int((player_y - self.map_center_y) * 256 / self.prescale)
        mem[ZP_PX]     = px_88 & 0xFF
        mem[ZP_PXH] = (px_88 >> 8) & 0xFF
        mem[ZP_PY]     = py_88 & 0xFF
        mem[ZP_PYH] = (py_88 >> 8) & 0xFF
        # s16 integer position: high bytes (whole-map support, not just
        # +/-127 prescaled units around MAP_CENTER)
        mem[_sym('zp_br_px_x')] = (px_88 >> 16) & 0xFF
        mem[_sym('zp_br_py_x')] = (py_88 >> 16) & 0xFF

        # Eye height (pre-scaled, s8). e1m1 normally does
        # vz = prescale_height(player_floor + 41); we get player_floor in.
        # Inline a minimal prescale_height.
        ASPECT_NUM = 6; ASPECT_DEN = 5
        vz = ((floor_z + 41) * ASPECT_NUM + (self.prescale * ASPECT_DEN) // 2) \
             // (self.prescale * ASPECT_DEN)
        mem[ZP_VZ] = vz & 0xFF

        # raws mirror pmf_cand EXACTLY (2026-08-26): position quantized
        # to 8.8 prescaled, raw = FLOOR (the old int() truncation and
        # trace_compare's round() were unfaithful at fractional poses),
        # plus the world-frac bytes and the tie-broken doubled pairs the
        # exact node point-on-side consumes.
        _px88 = int((player_x - self.map_center_x) * 256 / self.prescale)
        _py88 = int((player_y - self.map_center_y) * 256 / self.prescale)
        raw_px, raw_py = _px88 >> 5, _py88 >> 5
        _fx, _fy = (_px88 << 3) & 0xFF, (_py88 << 3) & 0xFF
        mem[ZP_PXRAW_LO]     = raw_px & 0xFF
        mem[ZP_PXRAW_HI] = (raw_px >> 8) & 0xFF
        mem[ZP_PYRAW_LO]     = raw_py & 0xFF
        mem[ZP_PYRAW_HI] = (raw_py >> 8) & 0xFF
        from symmap import sym as _sy
        mem[_sy('PM_FXW')], mem[_sy('PM_FXW') + 2] = _fx, _fy
        _px2 = (raw_px << 1) | (1 if _fx else 0)
        _py2 = (raw_py << 1) | (1 if _fy else 0)
        mem[_sy('zp_br_px2_l')] = _px2 & 0xFF
        mem[_sy('zp_br_px2_h')] = (_px2 >> 8) & 0xFF
        mem[_sy('zp_br_py2_l')] = _py2 & 0xFF
        mem[_sy('zp_br_py2_h')] = (_py2 >> 8) & 0xFF

        s_mag, s_neg, s_one, c_mag, c_neg, c_one = fp.fp_sincos(angle_byte)
        mem[ZP_SMAG] = s_mag
        mem[ZP_SNEG] = 1 if s_neg else 0
        mem[ZP_SONE] = 1 if s_one else 0
        mem[ZP_CMAG] = c_mag
        mem[ZP_CNEG] = 1 if c_neg else 0
        mem[ZP_CONE] = 1 if c_one else 0
        mem[_sym('bca_ab')] = angle_byte & 0xFF  # angle-space bbox view angle

        # --- Dynamic always-descend: the harness owns the discontinuity.
        # The engine keeps its productivity bits (NODE_DSGN b3/b2) across
        # frames and has NO wipe — after a teleport the stale bits cost one
        # frame of over-descent and the judge clears them.  A bench that
        # steps between unrelated poses in one engine is not a motion the
        # predictor is meant to survive, so clear the bits on a jump here,
        # with the same windows the walk's kinematics can never reach:
        # 128 world units, or 24 angle bytes (a max-rate turn frame is 14).
        _prev = getattr(self, '_adesc_pose', None)
        if _prev is None or abs(player_x - _prev[0]) > 128 \
                or abs(player_y - _prev[1]) > 128 \
                or min((angle_byte - _prev[2]) % 256,
                       (_prev[2] - angle_byte) % 256) > 24:
            self.adesc_reset()
        self._adesc_pose = (player_x, player_y, angle_byte)

        sc._run(ENTRY_BR_VIEW_SETUP)
        sc.init()
        sc.clear_screen()
        cyc = sc._run(ENTRY_BR_RENDER_FRAME, max_cycles=10000000)
        self.last_cycles = cyc
        return cyc

    def adesc_reset(self):
        adesc_reset_mem(self.sc)
