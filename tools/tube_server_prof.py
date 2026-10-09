#!/usr/bin/env python3
"""Profile the Tube fill server (tube_server.py) by routine: cycles per
top-level label (cheap locals folded in) over the regression poses.

    python3 tools/tube_server_prof.py [n_poses]
"""
import bisect
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import poses
import tube_req
import tube_server


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    H = tube_req.ReqRef()
    S = tube_server.Server(H)
    lst = open(os.path.join(tube_server.OUT, 'fserve.map')).read()
    # top-level code labels: names without '@', not data, inside the code areas
    names = {}
    for nm, a in S.L.items():
        if nm.startswith(('@', 'LOCAL')) or not (0x4000 <= a < 0x8000 or 0x0F00 <= a < 0x1800):
            continue
        names.setdefault(a, nm)
    ks = sorted(names)
    tot = collections.Counter()
    mpu, m = S.mpu, S.mem
    allc = 0
    for p in (list(poses.POSITIONS) + list(poses.VERIFY))[:n]:
        H.render(*p)
        req = tube_req.encode(H.frame())
        q = S.L['fs_ring']
        m[q:q + len(req)] = req
        m[S.L['fs_rp']], m[S.L['fs_rp'] + 1] = q & 0xFF, q >> 8
        w = q + len(req)
        m[S.L['fs_rw']], m[S.L['fs_rw'] + 1] = w & 0xFF, w >> 8
        ob = S.L['fs_lista']
        m[S.L['fs_ob']], m[S.L['fs_ob'] + 1] = ob & 0xFF, ob >> 8
        mpu.pc, mpu.sp = tube_server.TRAMP, 0xFF
        while mpu.pc != tube_server.TRAMP + 3:
            pc = mpu.pc
            c0 = mpu.processorCycles
            mpu.step()
            j = bisect.bisect_right(ks, pc) - 1
            tot[names[ks[j]] if j >= 0 else '?'] += mpu.processorCycles - c0
    allc = sum(tot.values())
    print(f'{n} poses, mean {allc // n:,} cycles a frame')
    for nm, c in tot.most_common(40):
        print(f'  {c // n:9,d}  {100 * c / allc:5.1f}%  {nm}')


if __name__ == '__main__':
    main()
