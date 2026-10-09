#!/usr/bin/env python3
"""Gate: the 6502 movement (pmove_try, py65 rig) agrees with colmap.try_move
-- verdict and eye height -- on random candidate moves: half around room
24's narrow stair flight, half over the playable area; then the same
around the nukage room's lift at several live lift heights. pmove.s is the
6502 expression of colmap's rules (incl. the 2026-10-09 half-box probes,
pm_corners). Prints MASTERPMOVE: PASS / FAIL."""
import os, sys, random
os.environ['SDL_VIDEODRIVER']='dummy'
ROOT=os.path.dirname(os.path.abspath(__file__)); os.chdir(ROOT)
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT,'tools'))
import pygame; pygame.init()
import e1m1 as dw, colmap as C, abi
from banked_bsp import MasterBspRender
from symmap import sym
from e1m1 import _prescale_height as ph
R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                    dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
bm=R.bm; mpu=R.sc.mpu
movers=sorted(dw.ANIM_SECTORS)
mp=[(ph(dw.sectors[s][1]) if dw.ANIM_SECTORS[s]=='ceil' else ph(dw.sectors[s][0]))&0xFF for s in movers]
for i,v in enumerate(mp): bm[sym('ANIM_WS')+3*i+1]=v
CX,CY=dw.MAP_CENTER_X,dw.MAP_CENTER_Y
S={n:sym(n) for n in ('pmove_try','pm_vz','pmt_ok','pm_lmv','zp_br_pxraw_l','zp_br_pxraw_h','zp_br_pyraw_l','zp_br_pyraw_h','zp_br_px2_l','zp_br_px2_h','zp_br_py2_l','zp_br_py2_h','pm_oldx','pm_oldy')}
def w16(a,v): bm[a]=v&0xFF; bm[a+1]=(v>>8)&0xFF
def run(ox,oy,nx,ny,z):
    rx,ry=nx-CX,ny-CY
    for n,v in (('zp_br_pxraw_l',rx),('zp_br_pyraw_l',ry)):
        bm[S[n]]=v&0xFF; bm[S[n.replace('_l','_h')]]=(v>>8)&0xFF
    bm[S['zp_br_px2_l']]=(rx<<1)&0xFF; bm[S['zp_br_px2_h']]=((rx<<1)>>8)&0xFF
    bm[S['zp_br_py2_l']]=(ry<<1)&0xFF; bm[S['zp_br_py2_h']]=((ry<<1)>>8)&0xFF
    for k in range(4): bm[abi.PM_FXW+k]=0
    w16(S['pm_oldx'],ox-CX); w16(S['pm_oldy'],oy-CY)
    bm[S['pm_vz']]=z&0xFF; bm[S['pmt_ok']]=0; bm[S['pm_lmv']]=0
    R.sc._run(S['pmove_try'])
    ok=bool(mpu.p & 1); vz=bm[S['pm_vz']]; vz=vz-256 if vz>=128 else vz
    return ok, (vz if ok else z)

import math
def batch(seed, n, lift):
    rnd=random.Random(seed); pts=[]
    li=movers.index(59)
    if lift is not None:
        mp[li]=lift & 0xFF; bm[sym('ANIM_WS')+3*li+1]=mp[li]
    for _ in range(n):
        if lift is not None: x,y=rnd.randint(3250,3520),rnd.randint(-3620,-3380)
        elif rnd.random()<0.5: x,y=rnd.randint(200,420),rnd.randint(-3320,-3140)
        else: x,y=rnd.randint(-100,2400),rnd.randint(-3800,-2200)
        a=rnd.uniform(0,6.283); d=rnd.choice((4,8,16,22))
        pts.append((x,y,x+int(d*math.cos(a)),y+int(d*math.sin(a)),rnd.randint(-6,22)))
    bad=0
    for x,y,nx,ny,z in pts:
        want=C.try_move(x-CX,y-CY,nx-CX,ny-CY,z,mp); want=(want[0], want[1] if want[0] else z)
        got=run(x,y,nx,ny,z)
        if want!=got:
            bad+=1
            if bad<=4: print('  mismatch',(x,y,nx,ny,z),'model',want,'6502',got)
    print(f'  seed {seed} lift {lift}: {len(pts)} moves, {bad} mismatches')
    return bad
bad = batch(9, 400, None)
for k, h in enumerate((-7, 0, 7, 14)):
    bad += batch(20 + k, 100, h)
if 59 in movers:
    mp[movers.index(59)] = (ph(dw.sectors[59][0])) & 0xFF

# the frame's displacement (pm_frame_i, up to pf_move) against
# colmap.walk_disp: every key combination of forward / back / strafe left
# (Z) / strafe right (X), every angle, every field count; cold (cache
# miss) then again (cache hit)
class _AtMove(Exception):
    pass
def _stop(mpu):
    raise _AtMove
R.sc.pc_hooks = {sym('pf_move'): _stop, sym('pf_nomove'): _stop}
PMS = abi.DRV_ORG                         # PM_SCRATCH (pmove.s)
def s16(a): v = bm[a] | bm[a + 1] << 8; return v - 65536 if v & 0x8000 else v
dbad = 0
for bits in range(16):
    inp = (bits & 3) | (bits & 12) << 2   # b0 b1 fwd/back, b4 b5 strafes
    for ang in range(64):
        for f in range(1, 11):
            want = C.walk_disp(f, bits & 1, bits & 2, ang, bits & 4, bits & 8)
            bm[PMS + 0x8D] = 0xFF         # pmc_ang: cold
            for rep in range(2):
                bm[abi.DV_ANGIDX] = ang
                bm[PMS + 0x4D:PMS + 0x51] = [0, 0, 0, 0]
                mpu.a, mpu.x = f, inp
                try:
                    R.sc._run(sym('pm_frame_i'))
                    got = (0, 0)          # returned: pf_none
                except _AtMove:
                    got = (s16(PMS + 0x4D), s16(PMS + 0x4F))
                if got != want:
                    dbad += 1
                    if dbad <= 4:
                        print('  displacement', dict(bits=bits, ang=ang, f=f, rep=rep), 'model', want, '6502', got)
R.sc.pc_hooks = {}
print(f'  walk displacement: 16 key sets x 64 angles x 10 field counts x (miss, hit): {dbad} mismatches')
bad += dbad
print('MASTERPMOVE: FAIL' if bad else 'MASTERPMOVE: PASS')
sys.exit(1 if bad else 0)
