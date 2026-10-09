#!/usr/bin/env python3
"""Tube Master step 4a: does the second processor's half fit its 64K?

    python3 tools/tube_fit.py

A census of everything the Master build holds, region by region, from
the linker map (build/engine_m.map, via asmbuild) and the disc images
(tools/build_master_ssd.engine_images), each item given to the HOST
(screen, textures, flats, the pixel loops, music, gun, panel), the
SECOND PROCESSOR (the BSP, clipping, movement, the fill's set-up and the
list emitter) or both, and split into code, static data and run-time
workspace. Run-time caches ship as zeros in the images, so they are
named here rather than measured.

Sizes are the linker's for linked segments and measured for the images
(bytes in use = the region less its runs of free bytes, which are listed
and checked against the layout notes). Estimates (new code still to be
written) are marked.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
os.chdir(ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')


def segments():
    import asmbuild
    asmbuild.build()
    out, on = {}, False
    for line in open(os.path.join(ROOT, 'build', 'engine_m.map')):
        if line.startswith('Segment list'):
            on = True
            continue
        if on:
            f = line.split()
            if len(f) == 5 and f[0].isupper() and len(f[1]) == 6:
                out[f[0]] = int(f[3], 16)
            elif on and line.startswith('Exports'):
                break
    return out


def zero_runs(img, base, lo, hi, minlen=48):
    """[(start, length)] runs of $00 >= minlen in [lo, hi)."""
    out, i = [], lo
    while i < hi:
        if img[i - base] == 0:
            j = i
            while j < hi and img[j - base] == 0:
                j += 1
            if j - i >= minlen:
                out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def main():
    S = segments()
    import build_master_ssd as B
    b4, b7, b5, b6, andy, main_, cbits, hz = B.engine_images()

    # ---- bank 4 (SEG): map data + three per-frame caches -------------
    caches4 = [('VXCACHE (vertex recip + screen x, 4 planes)', 0x9800, 0x800),
               ('VRCACHE (rotated vertices, 4 planes)', 0xA000, 0x800),
               ('VYCACHE (y projection memo, 4 pages)', 0xB300, 0x400)]
    free4 = [(0xB8F8, 0xB9FF - 0xB8F8 + 1), (0xBDDA, 0xBFFF - 0xBDDA + 1)]
    cache_bytes = sum(n for _, _, n in caches4)
    b4_static = 0x4000 - cache_bytes - sum(n for _, n in free4)
    # ---- bank 7 (WALK): nodes, bbox, collision, anim ------------------
    runs7 = zero_runs(b7, 0x8000, 0x8000, 0xC000)
    freed7 = [(a, n) for a, n in runs7 if a == 0xA5C2 or a == 0xAF21]
    tails7 = [(a, n) for a, n in runs7 if n < 100 and (a & 0xFF) >= 0xB0]
    other7 = [(a, n) for a, n in runs7 if (a, n) not in freed7 + tails7]
    b7_static = 0x4000 - sum(n for _, n in runs7)

    # The second processor's half. (name, kind, bytes, note)
    P = [
        # code
        ('engine CODE (BSP walk, bbox classes, transform, project, seg emit,'
         ' anim, objects)', 'code', S['CODE'], 'map'),
        ('movement PMOVE + PMH + PMCOR', 'code', S['PMOVE'] + S['PMH'] + S['PMCOR'], 'map'),
        ('clipper BANKC (code + its data)', 'code', S['BANKC'], 'map'),
        ('fill cold set-up MFILLC + MFMAIN + MFILLV', 'code', S['MFILLC'] + S['MFMAIN'] + S['MFILLV'], 'map'),
        ('fill set-up in bank 6 MB6C, less gun / hz_run / far loops (1,534)', 'code',
         S['MB6C'] - 1534, 'map'),
        ('fill set-up in HAZEL MFILL: tr_screen tb_end sh_lim tr_fetch'
         ' mf_frame + hi16 lo16 skymap bitmask', 'code', 214 + 47 + 165 + 29 + 152 + 512 + 40, 'labels'),
        ('arithmetic MARITH', 'code', S['MARITH'], 'map'),
        ('row / view tables RWC + RWCARD + SHTAB', 'data', S['RWC'] + S['RWCARD'] + S['SHTAB'], 'map'),
        ('driver logic (frame loop, field clock, glue) of DRV', 'code', 400, 'estimate'),
        ('list emitter + Tube send + protocol + start-up', 'code', 1500, 'estimate'),
        # static data
        ('bank 4 map data (seg headers + DIRs, vertices, recips, TABL0, BPAL)', 'data', b4_static, 'measured'),
        ('bank 7 map data (nodes, bbox, collision, anim cfg, use / walk lines)', 'data', b7_static, 'measured'),
        ('clipper data and workspace (CBITS $7000-$78FF: VDESC, VEXPL,'
         ' sincos, FW_TOUCH...; VPTAB)', 'data', 0x900 + S['VPTAB'], 'region'),
        ('ANDY wall tables MANDY', 'data', S['MANDY'], 'map'),
        ('bank 6 step tables MB6R + part records MB6T', 'data', S['MB6R'] + S['MB6T'], 'map'),
        ('quarter squares MSQR', 'data', S['MSQR'], 'map'),
        # run-time workspace
        ('per-frame caches in bank 4: ' + ', '.join(n.split(' (')[0] for n, _, _ in caches4), 'work',
         cache_bytes, 'layout'),
        ('plane spans + row cache MPLANE', 'work', S['MPLANE'], 'map'),
        ('fill BSS MFILLBSS', 'work', S['MFILLBSS'], 'map'),
        ('zero page, stack, WORK', 'work', 0x100 + 0x100 + S['WORK'], 'map'),
        ('low RAM workspace $0800-$0EFF (plot queue page, VRCACHE state, span'
         ' pool, PM_FXW, driver vars)', 'work', 0x700, 'layout'),
    ]
    tot = {'code': 0, 'data': 0, 'work': 0}
    print('THE SECOND PROCESSOR\'S HALF')
    for name, kind, n, how in P:
        tot[kind] += n
        print(f'  {kind:4s} {n:6,d}  {name}  [{how}]')
    need = sum(tot.values())
    print(f'  code {tot["code"]:,}  static data {tot["data"]:,}  workspace {tot["work"]:,}'
          f'  = {need:,} B ({need / 1024:.1f}K)')
    have_safe = 0xF800 - 0x0000             # all but the Tube client ($F800-$FFFF)
    have_max = 0xFEF8 + 6                   # the client overwritten once loaded
    print(f'  usable: {have_safe:,} B keeping the Tube client ($0000-$F7FF), '
          f'{have_max:,} B taking all but its registers')
    print(f'  margin: {have_safe - need:,} B / {have_max - need:,} B')
    print('\nRECOVERABLE (not counted as free above)')
    print(f'  bank 7 node-plane page tails: {sum(n for _, n in tails7):,} B in {len(tails7)} runs')
    print(f'  bank 7 other empty runs (unverified): ' +
          ', '.join(f'${a:04X}+{n}' for a, n in other7))
    print(f'  bank 7 freed planes (the Master\'s music stream): ' +
          ', '.join(f'${a:04X}+{n}' for a, n in freed7))
    print(f'  bank 4 free: ' + ', '.join(f'${a:04X}+{n}' for a, n in free4))
    print('\nWAYS TO CLOSE THE GAP (bytes off the second processor)')
    opts = [
        ('A', 'movement + collision + use / walk lines run on the host (it keeps'
         ' the banks): PMOVE/PMH/PMCOR code and bank 7 $AF8A-$BFFF', S['PMOVE'] + S['PMH'] + S['PMCOR'] + 0xC000 - 0xAF8A),
        ('B', 'billboard objects off the second processor (off on the Master;'
         ' sprites come back as a host + emitter design): objects.s code, ROM_OBJ, art', 1529 + 604 + 366 + 768),
        ('C', 'small tables packed into bank 7\'s node-plane page tails and empty'
         ' runs, and bank 4\'s free top', sum(n for _, n in tails7) + sum(n for _, n in other7)
         + sum(n for _, n in free4)),
        ('D', 'the Model B low-RAM legacy ($0800 plot queue page; to verify)', 0x100),
        ('E', 'the Tube client $F800-$FEF7, overwritten once loaded', have_max - have_safe),
        ('F', 'fallback: the wall step from a division, not MB6R\'s tables', S['MB6R']),
        ('G', 'fallback: the plane set-up (sweep, row maths, span set-up) on the'
         ' host -- it also balances the load', 5000),
    ]
    for k, what, n in opts:
        print(f'  {k} {n:6,d}  {what}')
    abcde = sum(n for k, _, n in opts if k in 'ABCDE')
    print(f'  A-E together: {abcde:,} B against a gap of {need - have_safe:,} B: '
          f'margin {abcde - (need - have_safe):,} B')
    print('\nTHE HOST KEEPS: textures + flats (bank 5, 14K), the step 3 drawer and its tables,'
          ' music, gun, panel, HUD, raster split, keyboard')


if __name__ == '__main__':
    main()
