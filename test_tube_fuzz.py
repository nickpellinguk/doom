#!/usr/bin/env python3
"""Gate (host-led tube-master, H4c): the fill server at random poses.

The regression's poses are hand-picked; the server's edge cases are not.
Here the 6502 fill server (tube_server, py65) must give tube_req.
FillServer's display list byte for byte, dropping nothing, at:

  1. every pose random sampling has caught it out at (CORPUS, below),
     each fixed since: overlapping runs (a wall or a later run over an
     earlier mark: fs_trim) and full line run lists (16 runs a line);
  2. a fixed batch of random standable poses (tube_fuzz.random_poses).

Prints each failing pose and TUBEFUZZ: PASS / FAIL.

    python3 test_tube_fuzz.py [n [seed]]       (default 150 poses, seed 1)
"""
import sys

import tube_fuzz

CORPUS = [
    (3439.625, -2676.25, 90),
    (1426.375, -4368.0, 37),
    (2052.875, -4471.875, 4),
    (2187.0, -4586.125, 50),
    (1992.25, -4505.75, 86),
    (623.0, -3852.25, 78),
    (2217.875, -4484.375, 102),
    (1822.0, -4003.625, 103),
    (2382.125, -4809.5, 77),
    (2287.625, -4514.125, 48),
    (2886.875, -2624.5, 72),
    (1849.875, -3964.375, 111),
    (1321.375, -4480.875, 58),
    (432.625, -4412.625, 24),
    (58.625, -3435.75, 12),
    (2139.25, -4271.75, 34),
    (1509.0, -4634.75, 29),
    (926.5, -3795.0, 253),
    (3410.875, -2667.875, 84),
    (2114.125, -4551.0, 44),
    (115.375, -2106.25, 218),
    (3596.0, -4736.25, 94),
    (820.67, -3133.48, 102),
    (3049.03, -2449.15, 84),
    (2860.83, -3687.99, 90),
    (152.54, -3679.18, 10),
    (451.04, -2544.25, 172),
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    fz = tube_fuzz.Fuzz()
    bad, cyc = 0, []
    for tag, pl in (('corpus', CORPUS), (f'random (seed {seed})', tube_fuzz.random_poses(n, seed))):
        b = 0
        for p in pl:
            ok, why, _, _, c = fz.check(p)
            cyc.append(c)
            if not ok:
                b += 1
                print(f'  {p}  {why}', flush=True)
        print(f'  {tag}: {len(pl)} poses, {b} differ or drop')
        bad += b
    print(f'  server mean {sum(cyc) // len(cyc):,} cycles a frame, max {max(cyc):,}')
    print('TUBEFUZZ: FAIL' if bad else 'TUBEFUZZ: PASS')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
