#!/usr/bin/env python3
"""One-shot regression + metrics for the Master build.

Rebuilds the engine, runs every gate, and compares the frame cycles of the
Master rig over poses.POSITIONS against a recorded baseline. Prints a
compact PASS/FAIL summary; exit 0 iff all green.

    python3 run_regression.py                # gate against baseline.json
    python3 run_regression.py --rebaseline   # accept the current cycles as
                                             # the new baseline
    python3 run_regression.py --disc         # also boot the disc on jsbeeb
                                             # (test_master_disc; slow)

Gates:
  - every correctness script must pass;
  - cycles: the suite total must not regress more than CYCLE_TOL against
    the baseline. Improvements are reported and accepted silently; run
    --rebaseline after a deliberate optimisation to tighten the gate.
"""
import os, sys, subprocess, json
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
BASELINE_PATH = os.path.join(ROOT, 'baseline.json')
CYCLE_TOL = 0.0025          # 0.25% suite-total regression turns the board red
REBASELINE = '--rebaseline' in sys.argv

fails = []

# ---- build (fail-loud) -----------------------------------------------------
import asmbuild
try:
    asmbuild.build('engine')
except RuntimeError as e:
    fails.append('build: ' + str(e).strip().splitlines()[-1])


def run(label, argv, want):
    try:
        r = subprocess.run([sys.executable] + argv, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        fails.append(f'{label}: TIMEOUT'); print(f'  {label}: TIMEOUT'); return ''
    out = r.stdout + r.stderr
    ok = want(out)
    print(f'  {label}: {"OK" if ok else "FAIL"}')
    if not ok:
        fails.append(label)
        print('    ' + '\n    '.join(out.strip().splitlines()[-6:]))
    return out


print('== correctness ==')
# texture / flat packing read back through the assembled HAZEL tables, byte
# formats, bank/page invariants, determinism
run('master_assets', ['test_master_assets.py'], lambda o: 'MASTERASSETS: PASS' in o)
# the engine (BSP walk, transform, seg emission, span clipper) emits the
# recorded line list at every pose
run('master_engine', ['test_master_engine.py'], lambda o: 'MASTERENGINE: PASS' in o)
# the textured float reference's frames obey the byte rules and draw every
# on-map cell
run('textured_ref', ['test_textured_ref.py'], lambda o: 'TEXTUREDREF: PASS' in o)
# the span-diff fill model covers every on-map pixel and agrees with the
# textured reference's surfaces
run('fill_ref', ['test_fill_ref.py'], lambda o: 'FILLREF: PASS' in o)
# the integer textured-wall model agrees with the float reference
run('tex_ref', ['test_tex_ref.py'], lambda o: 'TEXREF: PASS' in o)
# the floor / ceiling model's 4.4 maths
run('plane_ref', ['test_plane_ref.py'], lambda o: 'PLANEREF: PASS' in o)
# the 6502 filler and texturers (src/master/mfill.s) draw exactly what
# plane_ref (on tex_ref) draws
run('master_tex', ['test_master_tex.py'], lambda o: 'MASTERTEX: PASS' in o)
# the gun overlay (master_gun.py the spec, gun_draw in bank 6)
run('master_gun', ['test_master_gun.py'], lambda o: 'MASTERGUN: PASS' in o)
# the divides against Python
run('master_div', ['test_master_div.py'], lambda o: 'MASTERDIV: PASS' in o)
# movement: pmove_try and the walk / strafe displacement against colmap
run('master_pmove', ['test_master_pmove.py'], lambda o: 'MASTERPMOVE: PASS' in o)
# tube-master step 1: the display list draws the whole frame (tube_dl.py)
run('tube_dl', ['test_tube_dl.py'], lambda o: 'TUBEDL: PASS' in o)
# tube-master step 3: the 6502 host drawer draws the list (src/tube/hdraw.s)
run('tube_host', ['test_tube_host.py'], lambda o: 'TUBEHOST: PASS' in o)
# host-led H1: the fill served from requests alone (tube_req.py)
run('tube_req', ['test_tube_req.py'], lambda o: 'TUBEREQ: PASS' in o)
# the music player (master_music.py the spec, src/master/mmusic.s)
run('master_music', ['test_master_music.py'], lambda o: 'MASTERMUSIC: PASS' in o)
# BAKED ADDRESSES: a literal address is a copy of a fact. This ratchets: the
# count may fall, never rise.
run('bakedscan', ['tools/bakedscan.py', '--gate'],
    lambda o: 'BAKEDSCAN: PASS' in o or 'baseline written' in o)
if '--disc' in sys.argv:
    run('master_disc', ['test_master_disc.py'], lambda o: 'MASTERDISC: PASS' in o)

baseline = None
if os.path.exists(BASELINE_PATH):
    with open(BASELINE_PATH) as f:
        baseline = json.load(f)
new_baseline = {'cycles': {}, 'total_cycles': 0}

# ---- frame cycles (gated) --------------------------------------------------
print('== frame cycles ==')
try:
    import pygame; pygame.init()
    import e1m1 as dw
    from banked_bsp import MasterBspRender
    import poses
    r = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                        dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    tot = 0
    for (px, py, ab) in poses.POSITIONS:
        cyc = r.render_frame(px, py, ab, dw.player_floor(px, py))
        tot += cyc
        new_baseline['cycles'][f'{px},{py},{ab}'] = cyc
    new_baseline['total_cycles'] = tot
    line = f'  TOTAL {tot:,}  MEAN {tot//len(poses.POSITIONS):,}'
    if baseline and not REBASELINE and baseline.get('total_cycles'):
        old_tot = baseline['total_cycles']
        delta = (tot - old_tot) / old_tot
        line += f'  ({delta:+.2%} vs baseline {old_tot:,})'
        if delta > CYCLE_TOL:
            fails.append(f'cycles: total {tot:,} regressed {delta:+.2%} vs {old_tot:,}')
            for k, v in new_baseline['cycles'].items():
                ov = baseline.get('cycles', {}).get(k)
                if ov and v > ov:
                    print(f'    {k}: {ov:,} -> {v:,} ({(v-ov)/ov:+.2%})')
    print(line)
except Exception as e:
    fails.append(f'cycles: {e}')
    print(f'  frame-cycle measure error: {e}')

if REBASELINE and not fails:
    with open(BASELINE_PATH, 'w') as f:
        json.dump(new_baseline, f, indent=1, sort_keys=True)
    print(f'\nbaseline written to {BASELINE_PATH}')
elif baseline is None:
    print(f'\nNOTE: no {BASELINE_PATH}; cycles not gated. '
          f'Run with --rebaseline to record one.')

print('\n' + ('ALL GREEN' if not fails else 'FAILURES: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
