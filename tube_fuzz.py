#!/usr/bin/env python3
"""Random poses for the Tube Master's fill server (docs/tube_master.md H4c).

The regression's poses are hand-picked; the server's edge cases (its
mark, run and plane tables, its list's cap, the sweep's joins) want many
more. random_poses gives poses the player can stand at: a point in a
reachable sector (colmap's flood from the spawn) with the player's box
(16 units each way) inside reachable sectors too, any angle byte.
check serves one pose on the 6502 (tube_server, py65) and the model
(tube_req.FillServer) and compares the lists.

    python3 tube_fuzz.py N [seed]     (prints each failing pose, then a count)
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import colmap
import e1m1
import tube_dl
import tube_req
import tube_server

RADIUS = 16


def _sector(x, y):
    nid = e1m1.find_subsector(x, y)
    return e1m1.seg_sectors(e1m1.segs[e1m1.ssectors[nid & 0x7FFF][1]])[0]


def random_poses(n, seed):
    """n standable poses (x, y, angle byte), x and y to 1/8 unit."""
    reach = colmap.build()['reach']
    xs = [v[0] for v in e1m1.vertexes]
    ys = [v[1] for v in e1m1.vertexes]
    rnd = random.Random(seed)
    out = []
    while len(out) < n:
        x = round(rnd.uniform(min(xs), max(xs)) * 8) / 8
        y = round(rnd.uniform(min(ys), max(ys)) * 8) / 8
        try:
            if all(_sector(x + dx, y + dy) in reach
                   for dx in (-RADIUS, 0, RADIUS) for dy in (-RADIUS, 0, RADIUS)):
                out.append((x, y, rnd.randrange(256)))
        except Exception:
            continue
    return out


class Fuzz:
    def __init__(self):
        self.H = tube_req.ReqRef()
        self.F = tube_req.FillServer()
        self.S = tube_server.Server(self.H)

    def check(self, p):
        """(ok, why, request bytes, list bytes, server cycles) at pose p."""
        H, F, S = self.H, self.F, self.S
        H.render(*p)
        req = tube_req.encode(H.frame())
        F.serve(tube_req.decode(req))
        want = tube_dl.encode(F.dl, F.fid)
        got, cyc = S.serve(req, drop=True)
        drop = S.mem[S.L['fs_drop']]
        if got == want and not drop:
            return True, '', len(req), len(got), cyc
        k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
        why = (f'dropped {drop}; ' if drop else '') + \
            ('' if got == want else f'list {len(got)} vs {len(want)} B, first at #{k}')
        return False, why, len(req), len(got), cyc


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    fz = Fuzz()
    bad = 0
    for p in random_poses(n, seed):
        ok, why, *_ = fz.check(p)
        if not ok:
            bad += 1
            print(f'  {p}  {why}', flush=True)
    print(f'{n} poses (seed {seed}): {bad} differ or drop')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
