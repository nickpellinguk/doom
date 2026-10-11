#!/usr/bin/env python3
"""Gate (H8): the Tube host's game rules -- pickups and nukage.

The Tube link (DOOM_ASMDEFS=TUBE=1) on py65. Scenarios of frames, each
the player at raws (rx, ry) in a subsector with some fields gone by:
walks past the map's pickups (inside and just outside the touch box) and
through nukage and dry subsectors, from starting states that reach every
rule (health under and over 100, every armour type, nearly no armour).
After every frame gm_tick's state -- health, armour, its type, the field
clock, the GONE objects and the panel's values -- must be game_ref.Game's,
and so must the sound effects it asks for (H9: sfx_start's calls, in
order), the movers setting off and arriving at random.

Then the engine: a frame rendered with objects GONE must stage (and so
request) exactly the rest -- the same objects as sprite_ref.Staging with
that gone set. Prints GAME: PASS / FAIL. Rebuilds the normal link after.
"""
import os, random, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
import pygame
pygame.init()
import e1m1 as dw
import game_ref as G
import poses
import sprite_ref
from banked_bsp import MasterBspRender
from symmap import sym


def main():
    r = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                        dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    m, mpu = r.bm, r.sc.mpu
    S = {n: sym(n) for n in ('gm_tick', 'gm_health', 'gm_armor', 'gm_atype', 'gm_clock',
                             'gm_ready', 'OBJ_GONE', 'ANIM_FIELDS', 'pn_val', 'zp_br_pxraw_l')}
    rnd = random.Random(11)
    asked = []                                  # H9: sfx_start's A, in order
    r.sc.pc_hooks = {sym('sfx_start'): lambda mpu: asked.append(mpu.a)}
    WS = 0x0D6B                                 # anim.s ANIM_WS (state at +2)
    table = G.pickups()
    nuke = sorted(G.damaging())
    dry = [s for s in range(len(dw.fp_ssectors)) if s not in G.damaging()]
    bad = frames = 0

    def state():
        gone = {i for i in range(len(dw.fp_objects))
                if m[S['OBJ_GONE'] + (i >> 3)] >> (i & 7) & 1}
        pv = tuple(m[S['pn_val'] + 2 * f] | m[S['pn_val'] + 2 * f + 1] << 8 for f in (1, 2))
        return (m[S['gm_health']], m[S['gm_armor']], m[S['gm_atype']], m[S['gm_clock']],
                gone, pv)

    for sc in range(40):
        g = G.Game()
        g.health = rnd.choice([100, 100, 1, 50, 95, 99, 150, 199, 200])
        g.armor, g.atype = rnd.choice([(0, 0), (1, 1), (2, 1), (1, 2), (99, 1), (100, 1),
                                       (150, 2), (200, 2), (199, 1)])
        m[0xFE30] = 6                           # bank 6: the rules' state
        m[S['gm_ready']] = 0
        m[S['gm_health']], m[S['gm_armor']], m[S['gm_atype']] = g.health, g.armor, g.atype
        m[S['gm_clock']] = 0
        for i in range(7):
            m[S['OBJ_GONE'] + i] = rnd.randint(0, 255)   # (the first frame clears it)
        mv = [rnd.choice([0x00, 0x40, 0x80, 0xC0]) | rnd.randrange(64) for _ in range(6)]
        for _ in range(60):
            for k in range(6):                      # movers setting off, arriving
                if rnd.random() < 0.1:
                    mv[k] = (mv[k] + 0x40) & 0xC0 | rnd.randrange(64)
            if rnd.random() < 0.6:
                _, x, y, _ = rnd.choice(table)
                rx, ry = x + rnd.randint(-40, 40), y + rnd.randint(-40, 40)
            else:
                rx, ry = rnd.randint(-3000, 3000), rnd.randint(-3000, 3000)
            ss = rnd.choice(nuke) if rnd.random() < 0.4 else rnd.choice(dry)
            fields = rnd.choice([1, 2, 3, 5, 8, 20, 46, 47, 100])
            g.tick(rx, ry, ss, fields, mv)
            for k in range(6):
                m[WS + 3 * k + 2] = mv[k]
            asked.clear()
            m[0xFE30] = 6
            p = S['zp_br_pxraw_l']
            for k, v in enumerate((rx & 0xFF, rx >> 8 & 0xFF, ry & 0xFF, ry >> 8 & 0xFF)):
                m[p + k] = v
            m[S['ANIM_FIELDS']] = fields
            mpu.a = ss
            r.sc._run(S['gm_tick'])
            want = (g.health, g.armor, g.atype, g.clock, g.gone, (g.health, g.armor), g.sounds)
            got = state() + (list(asked),)
            frames += 1
            if got != want:
                bad += 1
                if bad <= 5:
                    print(f'  scenario {sc} ({rx}, {ry}) ss {ss} +{fields}: 6502 {got[:4]} '
                          f'gone {sorted(got[4])} sounds {got[6]} / model {want[:4]} '
                          f'gone {sorted(want[4])} sounds {want[6]}')
    print(f'  rules: {frames} frames, {bad} differ')

    # the engine skips GONE objects: staged slots against Staging's
    import test_sprite_proj as T
    eng = T.engine_staged()                    # (this rig: the TUBE link)
    R = sprite_ref.SpriteRef()
    gb = 0
    for p in list(poses.POSITIONS)[:12]:
        gone = set(rnd.sample(range(len(dw.fp_objects)), 20))
        R.gone = gone
        R.render(*p)
        want = sorted((ss, v) for ss, v, _ in R.staged)
        got = eng(p, gone)
        if got != want:
            gb += 1
            print(f'  {p}: engine staged {len(got)}, model {len(want)} (gone {len(gone)})')
    print(f'  gone objects: 12 poses, {gb} differ')
    bad += gb
    print('GAME: FAIL' if bad else 'GAME: PASS')
    return bad


if __name__ == '__main__':
    bad = main()
    os.environ['DOOM_ASMDEFS'] = ''
    import asmbuild
    asmbuild.build()
    sys.exit(1 if bad else 0)
