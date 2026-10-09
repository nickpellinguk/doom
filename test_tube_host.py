#!/usr/bin/env python3
"""Gate (tube-master step 3): the 6502 host drawer (src/tube/hdraw.s, py65)
draws every regression pose's display list -- read off a modelled Tube
register 1 -- into exactly the view tube_dl.draw (and so PlaneRef) makes,
byte for byte. Prints the host's cycles a frame against the Master's
whole frame (baseline.json) and TUBEHOST: PASS / FAIL."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import poses
import tube_dl
import tube_host

base = json.load(open('baseline.json'))['cycles']
R = tube_dl.DLRef()
H = None
bad, rows = 0, []
for pose in list(poses.POSITIONS) + list(poses.VERIFY):
    fb = R.render(*pose)
    if H is None:
        H = tube_host.Host(R)
    enc = tube_dl.encode(R.dl, R.fid)
    got, cyc = H.draw(enc)
    diff = sum(a != b for a, b in zip(got, fb[:tube_host.VIEW_BYTES]))
    bad += bool(diff)
    key = ','.join(str(v) for v in pose)
    rows.append((key, cyc, base.get(key)))
    print(f'  {key:26s} {len(enc):5d} B  host {cyc:9,d}' +
          (f'  Master {base[key]:9,d}' if key in base else '') + ('  ok' if not diff else f'  DIFFER: {diff} bytes'))
seen = {k: (c, b) for k, c, b in rows if b}
hm = sum(c for c, _ in seen.values()) / len(seen)
mm = sum(b for _, b in seen.values()) / len(seen)
print(f'  {len(seen)} baseline poses: host mean {hm:,.0f} cycles a frame, the Master\'s whole frame {mm:,.0f}')
print('TUBEHOST: FAIL' if bad else 'TUBEHOST: PASS')
sys.exit(1 if bad else 0)
