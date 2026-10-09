#!/usr/bin/env python3
"""Gate: the Master engine disc boots and runs on jsbeeb's Master 128
(step 1 of docs/master_textured_spec.md).

Builds build/master/doom_master.ssd (tools/build_master_ssd.py), then
tools/master_rig.mjs boots it headless and checks the driver flips frames,
the cursor keys turn and walk, and only palette colours reach the screen.
Prints MASTERDISC: PASS / FAIL, or MASTERDISC: SKIP when no jsbeeb clone is
available ($JSBEEB, default /home/user/jsbeeb) -- a skip is not a pass.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
JSBEEB = os.environ.get('JSBEEB', '/home/user/jsbeeb')
if not os.path.exists(os.path.join(JSBEEB, 'src', 'machine-session.js')):
    print(f'no jsbeeb at {JSBEEB}')
    print('MASTERDISC: SKIP')
    sys.exit(2)

subprocess.run([sys.executable, 'tools/build_master_ssd.py'], check=True,
               stdout=subprocess.DEVNULL)
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
           'panel_bin': os.path.join(out, 'panel.bin')}, open(addrs, 'w'))
r = subprocess.run(['node', 'tools/master_rig.mjs', 'engine',
                    os.path.join(out, 'doom_master.ssd'), addrs,
                    os.path.join(out, 'engine')], capture_output=True, text=True)
lines = [l for l in r.stdout.splitlines()
         if not l.startswith(('Running until', 'Loading OS')) and 'loaded as 80' not in l]
print('\n'.join(lines))
if r.returncode or not any('MASTERDISC: PASS' in l for l in lines):
    print(r.stderr[-2000:])
    sys.exit(1)

# the music on the machine: every byte the SN76489 received, tick by tick,
# against master_music.Player (whose ring never runs dry here: the frames
# are far shorter than its 8.6 s)
import master_music
mus = json.load(open(os.path.join(out, 'engine', 'music.json')))
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
for t in range(n):
    p.refill()
    want = p.tick()
    if got.get(t, []) != want:
        bad.append(f'tick {t}: chip got {got.get(t, [])} model {want}')
        break
print(f'music: {n} ticks ({n / 50:.1f} s), {sum(len(got.get(t, [])) for t in range(n))} chip bytes, '
      + ('as master_music.Player' if not bad else 'DIFFER: ' + '; '.join(bad)))
if n < 500 or bad:
    print('MASTERDISC: FAIL (music)')
    sys.exit(1)
