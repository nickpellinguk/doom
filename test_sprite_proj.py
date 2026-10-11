#!/usr/bin/env python3
"""Gate (H6 step 1): the engine's object projection against the spec.

The Master engine on py65 (banked_bsp.MasterBspRender) with its objects on
but not drawn: obj_draw_slot is an RTS, so nothing is drawn and no clip
span is tightened (the walk is the plain one the model walks). At each
call it is handed a staged object (slot X): its subsector, depth, centre
x, top and bottom y and kind must be sprite_ref's, at the regression
poses and random ones. Prints SPRITEPROJ: PASS / FAIL.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.init()
import e1m1 as dw
import poses
import tube_fuzz
from banked_bsp import MasterBspRender
from symmap import sym
import sprite_ref


def s16(lo, hi):
    v = lo | hi << 8
    return v - 0x10000 if v & 0x8000 else v


def engine_staged():
    r = MasterBspRender(dw.packed_layout, dw.packed_rom_main,
                        dw.packed_rom_detail, dw.packed_bbox_table,
                        dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    m = r.bm
    L = dw.packed_layout                         # objects on: the shipped
    bits = L['off_obj'] + 7 * L['n_obj']         #  OBJ_BITS into OBJ_ANYB
    for i in range(L['obj_bits_len']):           #  (the rig zeroes it: off)
        m[sym('OBJ_ANYB') + i] = dw.packed_rom_main[bits + i]
    S = {n: sym(n) for n in ('obj_draw_slot', 'obj_ss', 'obj_sd_l', 'obj_sd_h', 'obj_scx_l',
                             'obj_scx_h', 'obj_syt_l', 'obj_syt_h', 'obj_syb_l', 'obj_syb_h',
                             'obj_sasp')}
    got = []

    def at_draw(mpu):
        x = mpu.x
        g = lambda n: m[S[n] + x]
        got.append((m[S['obj_ss']], (g('obj_sd_l') | g('obj_sd_h') << 8,
                                     s16(g('obj_scx_l'), g('obj_scx_h')),
                                     s16(g('obj_syt_l'), g('obj_syt_h')),
                                     s16(g('obj_syb_l'), g('obj_syb_h')), g('obj_sasp'))))
    r.sc.pc_hooks = {S['obj_draw_slot']: at_draw}
    if 'TUBE' in os.environ.get('DOOM_ASMDEFS', ''):
        # (H8: the TUBE link stages a subsector's slots and hands them all
        #  to rq_objs -- no pass 2, no obj_draw_slot)
        class _X:
            pass

        def at_rq(mpu):
            for x in range(m[sym('obj_n')]):
                fake = _X()
                fake.x = x
                at_draw(fake)
        r.sc.pc_hooks = {sym('rq_objs'): at_rq}

    gone_at = sym('OBJ_GONE') if 'TUBE' in os.environ.get('DOOM_ASMDEFS', '') else None

    def run(p, gone=()):
        got.clear()
        if gone_at is not None:                 # (H8: the TUBE link's picked-up)
            for i in range(7):
                m[gone_at + i] = sum(1 << b for b in range(8) if 8 * i + b in gone)
        a = S['obj_draw_slot']
        save = list.__getitem__(m, a)
        list.__setitem__(m, a, 0x60)                # RTS: stage only
        try:
            r.render_frame(*p, dw.player_floor(*p[:2]))
        finally:
            list.__setitem__(m, a, save)
        return sorted(got)
    return run


def main():
    pl = list(poses.POSITIONS) + list(poses.VERIFY) + tube_fuzz.random_poses(150, 3)
    eng = engine_staged()
    R = sprite_ref.SpriteRef()
    bad = n = 0
    for p in pl:
        want = eng(p)
        R.render(*p)
        mine = sorted((ss, v) for ss, v, _ in R.staged)
        n += len(want)
        if want != mine:
            bad += 1
            if bad <= 8:
                print(f'  {p}: engine {want[:4]} ... model {mine[:4]} ...')
    print(f'  {len(pl)} poses, {n} objects staged')
    print('SPRITEPROJ: FAIL' if bad else 'SPRITEPROJ: PASS')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
