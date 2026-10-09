#!/usr/bin/env python3
"""Gate (host-led tube-master, H1): the fill request is the whole fill.

At every regression pose: the host (tube_req.ReqRef, rendering as
PlaneRef does) records the frame's fill requests; they are encoded,
decoded, and served by a separate FillServer that holds only the static
tables. Its frame must equal PlaneRef's byte for byte, and its display
list encode to the same bytes as the host-side list. Prints each pose's
request and list sizes and TUBEREQ: PASS / FAIL."""
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
import tube_req

H = tube_req.ReqRef()
S = tube_req.FillServer()
P = plane_ref.PlaneRef()
bad, rq, ls = 0, [], []
for pose in list(poses.POSITIONS) + list(poses.VERIFY):
    fb = H.render(*pose)
    ref = P.render(*pose)
    req = tube_req.encode(H.frame())
    got = S.serve(tube_req.decode(req))
    dl_host, dl_srv = tube_dl.encode(H.dl, H.fid), tube_dl.encode(S.dl, S.fid)
    diff = sum(a != b for a, b in zip(got, ref))
    ok = fb == ref and not diff and dl_host == dl_srv
    bad += not ok
    rq.append(len(req))
    ls.append(len(dl_srv))
    print(f'  {str(pose):30s} {len(H.reqs):3d} segs {len(req):5d} B requests {len(dl_srv):5d} B list  '
          + ('ok' if ok else f'DIFFER: frame {diff} bytes, list ' + ('same' if dl_host == dl_srv else 'differs')))
print(f'  {len(rq)} poses: requests mean {sum(rq) // len(rq)} B (max {max(rq)}), '
      f'list mean {sum(ls) // len(ls)} B (max {max(ls)})')
print('TUBEREQ: FAIL' if bad else 'TUBEREQ: PASS')
sys.exit(1 if bad else 0)
