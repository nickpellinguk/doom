#!/usr/bin/env python3
"""Gate (host-led tube-master, H3): the fill server in 6502.

At every regression pose the Python host (tube_req.ReqRef) makes the
frame's requests; the 6502 fill server (src/tube/fserve.s with
src/master/mfill.s, SERVER; tube_server.py's py65 rig) serves them, and
its display list must be byte for byte tube_dl.encode of the Python
server's (tube_req.FillServer). Prints each pose's list size and the
server's cycles, then TUBESERVER: PASS / FAIL.

    python3 test_tube_server.py [px py ab]    # one pose
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import poses
import tube_dl
import tube_req
import tube_server


def first_diff(got, want, fid):
    """Where two encoded lists part: the records either side decode to."""
    k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    names = {v: n for n, v in fid.items()}
    out = f'{len(got)} vs {len(want)} B, first at #{k}: {got[k:k + 12].hex()} vs {want[k:k + 12].hex()}'
    try:
        g, w = tube_dl.decode(want, names), None
        w = g
        g = tube_dl.decode(got, names)
        i = next((i for i, (a, b) in enumerate(zip(g, w)) if a != b), min(len(g), len(w)))
        out += f'\n      record #{i}: 6502 {g[i] if i < len(g) else None}\n      {"":10s}model {w[i] if i < len(w) else None}'
    except Exception as e:                      # (the 6502's bytes may not decode)
        out += f' (6502 list does not decode: {e!r})'
    return out


def main():
    pl = list(poses.POSITIONS) + list(poses.VERIFY)
    if len(sys.argv) == 4:
        pl = [tuple(float(a) if '.' in a else int(a) for a in sys.argv[1:])]
    H = tube_req.ReqRef()
    S = tube_req.FillServer()
    srv = tube_server.Server(H)
    bad, sizes, cycles = 0, [], []
    for p in pl:
        H.render(*p)
        req = tube_req.encode(H.frame())
        S.serve(tube_req.decode(req))
        want = tube_dl.encode(S.dl, S.fid)
        got, cyc = srv.serve(req)
        ok = got == want
        bad += not ok
        sizes.append(len(got))
        cycles.append(cyc)
        print(f'  {str(p):30s} {len(req):5d} B in {len(got):5d} B out {cyc:9,d} cycles  '
              + ('ok' if ok else 'DIFFER: ' + first_diff(got, want, S.fid)))
    n = len(sizes)
    print(f'  {n} poses: list mean {sum(sizes) // n} B (max {max(sizes)}), '
          f'server mean {sum(cycles) // n:,} cycles (max {max(cycles):,})')
    print('TUBESERVER: FAIL' if bad else 'TUBESERVER: PASS')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
