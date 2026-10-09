#!/usr/bin/env python3
"""Gate (tube-master step 1): the display list is the whole frame.

At every regression pose: DLRef renders the frame PlaneRef renders (the
recording changes nothing), and the host's draw() makes the same 10K
buffer from the display list alone, byte for byte. Prints each pose's
list size (records and encoded bytes) and TUBEDL: PASS / FAIL."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import plane_ref
import poses
import tube_dl

R = tube_dl.DLRef()
P = plane_ref.PlaneRef()
bad, sizes = 0, []
for pose in list(poses.POSITIONS) + list(poses.VERIFY):
    fb = R.render(*pose)
    ref = P.render(*pose)
    host = tube_dl.draw(R.dl, R.tex, R.W.tparams, R.flat)
    nb, n = tube_dl.size(R.dl)
    sizes.append(nb)
    diff = sum(a != b for a, b in zip(host, ref))
    ok = fb == ref and not diff
    bad += not ok
    print(f'  {str(pose):22s} {n["WALL"]:4d} WALL {n["SPAN"]:4d} SPAN {n["FILL"]:4d} FILL'
          f' {nb:6d} B  ' + ('ok' if ok else f'DIFFER: {diff} bytes'
                              + ('' if fb == ref else ' (DLRef != PlaneRef)')))
print(f'  {len(sizes)} poses: display list mean {sum(sizes) // len(sizes)} B, max {max(sizes)} B')
print('TUBEDL: FAIL' if bad else 'TUBEDL: PASS')
sys.exit(1 if bad else 0)
