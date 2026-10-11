#!/usr/bin/env python3
"""Gate: the Master engine disc boots and runs on jsbeeb's Master 128
(step 1 of docs/master_textured_spec.md).

Builds build/master/doom_master.ssd (tools/build_master_ssd.py), then
tools/master_rig.mjs boots it headless and checks the driver flips frames,
the cursor keys turn and walk, and only palette colours reach the screen.
Prints MASTERDISC: PASS / FAIL, or MASTERDISC: SKIP when no jsbeeb clone is
available ($JSBEEB, default /home/user/jsbeeb) -- a skip is not a pass.

    python3 test_master_disc.py --tube [mhz]

runs the Tube Master disc (docs/tube_master.md H4; build_master_ssd.py
--tube) the same way on a Master with its 65C102 second processor at mhz
(default 3): the host engine sends the fill requests, the second
processor serves them, the host draws the lists.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
JSBEEB = os.environ.get('JSBEEB', '/home/user/jsbeeb')
if not os.path.exists(os.path.join(JSBEEB, 'src', 'machine-session.js')):
    print(f'no jsbeeb at {JSBEEB}')
    print('MASTERDISC: SKIP')
    sys.exit(2)

TUBE = '--tube' in sys.argv
MHZ = float(sys.argv[sys.argv.index('--tube') + 1]) if TUBE and len(sys.argv) > sys.argv.index('--tube') + 1 else 3
os.environ['DOOM_ASMDEFS'] = 'TUBE=1' if TUBE else ''
subprocess.run([sys.executable, 'tools/build_master_ssd.py'] + (['--tube'] if TUBE else []),
               check=True, stdout=subprocess.DEVNULL)
import abi, symmap
out = os.path.join(ROOT, 'build', 'master')
addrs = os.path.join(out, 'addrs.json')
import master_panel
import master_assets as M
open(os.path.join(out, 'panel.bin'), 'wb').write(master_panel.panel_bytes())
json.dump({'flip_sched': symmap.sym('flip_sched'),
           'render_frame': symmap.sym('render_frame'),
           'hole_marker': M.mode2_byte((13, 0)),   # (13, 0): no art makes it
           'DV_ANGIDX': abi.DV_ANGIDX,
           'mus_init': abi.MUS_INIT, 'mus_tick': abi.MUS_TICK,
           'panel': [abi.MPANEL],                  # the one, shared panel
           'bufs': [abi.MSCREEN0, abi.MSCREEN1],
           'panel_bin': os.path.join(out, 'panel.bin'),
           'tube_mhz': MHZ if TUBE else 0,
           # H6: the Tube build's objects are ON -- their bitmap must be
           # filled at boot (the py65 rigs seed it themselves)
           'obj_anyb': symmap.sym('OBJ_ANYB') if TUBE else None,
           'panel_live': TUBE,
           'sfx_start': symmap.sym('sfx_start') if TUBE else None}, open(addrs, 'w'))
r = subprocess.run(['node', 'tools/master_rig.mjs', 'engine',
                    os.path.join(out, 'doom_tube.ssd' if TUBE else 'doom_master.ssd'), addrs,
                    os.path.join(out, 'tube' if TUBE else 'engine')], capture_output=True, text=True)
lines = [l for l in r.stdout.splitlines()
         if not l.startswith(('Running until', 'Loading OS')) and 'loaded as 80' not in l]
print('\n'.join(lines))
if r.returncode or not any('MASTERDISC: PASS' in l for l in lines):
    print(r.stderr[-2000:])
    sys.exit(1)

# the Tube build's panel is live (H8): it must be the panel showing SOME
# health and armour (the rig's walk may take a pickup) -- glyphs, cells
# and the rest of the panel byte for byte master_panel.panel_with's
if TUBE:
    import master_panel as MP
    pg = open(os.path.join(out, 'tube', 'panel_got.bin'), 'rb').read()
    shown = next(((h, a) for h in range(201) for a in range(201)
                  if MP.panel_with((50, h, a)) == pg), None)
    print(f'panel: ' + (f'ammo 50, health {shown[0]}, armour {shown[1]}' if shown
                        else 'NOT a panel_with panel'))
    if shown is None:
        print('MASTERDISC: FAIL (panel)')
        sys.exit(1)

# the music on the machine: every byte the SN76489 received, tick by tick,
# against master_music.Player (whose ring never runs dry here: the frames
# are far shorter than its 8.6 s)
import master_music
mus = json.load(open(os.path.join(out, 'tube' if TUBE else 'engine', 'music.json')))
stream, tab = master_music.pack(master_music.parse())
p = master_music.Player(stream, tab)
p.refill()
got = {}
for t, v in mus['pokes']:
    got.setdefault(t, []).append(v)
bad = []
if got.get(-1) != [0x9F, 0xBF, 0xDF, 0xFF]:
    bad.append(f'MUS_INIT sent {got.get(-1)}')
n = mus['ticks'] - 1                    # the last tick may be cut short
import master_sfx
fx = master_sfx.Sfx()                   # (H9: the Tube build's effects --
asks = {}                               #  each asked for between ticks t and
for t, e in mus.get('sfx', []):         #  t + 1, so heard from t + 1)
    asks.setdefault(t, []).append(e)
for t in range(n):
    p.refill()
    for e in asks.get(t - 1, []):
        fx.request(e)
    want = p.tick() + fx.tick(p)
    if got.get(t, []) != want:
        bad.append(f'tick {t}: chip got {got.get(t, [])} model {want}')
        break
print(f'music: {n} ticks ({n / 50:.1f} s), {len(mus.get("sfx", []))} effects, '
      f'{sum(len(got.get(t, [])) for t in range(n))} chip bytes, '
      + ('as master_music.Player' if not bad else 'DIFFER: ' + '; '.join(bad)))
if n < 500 or bad:
    print('MASTERDISC: FAIL (music)')
    sys.exit(1)
