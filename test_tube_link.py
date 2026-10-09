#!/usr/bin/env python3
"""Gate (host-led tube-master, H4a): the fill server over a real Tube.

jsbeeb's Master 128 and 65C102 second processor (tools/tube_link_rig.mjs):
the fill server's image (tube_server.py) runs on the second processor as
its program (fs_main); a host pump sends each regression pose's requests
(tube_req.encode of the Python host's frame) through register 1 (taken by IRQ) and reads
the frame before's display list back through register 1, pipelined as
the host will. Every list must be byte for byte tube_dl.encode of
tube_req.FillServer's. Prints the host's cycles a frame (send + wait +
read: with nothing drawn, the server's pace) and TUBELINK: PASS / FAIL.

    python3 test_tube_link.py [mhz]     (the second processor, default 3)
"""
import json
import os
import subprocess
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


def pump():
    """src/tube/lpump.s at $1900: {hex, pump_n, done}."""
    o, b, lab = (os.path.join(tube_server.OUT, 'lpump' + x) for x in ('.o', '.bin', '.lab'))
    subprocess.run(['ca65', 'src/tube/lpump.s', '-o', o], check=True)
    subprocess.run(['ld65', '-t', 'none', '-S', '0x1900', o, '-o', b, '-Ln', lab], check=True)
    L = {l.split()[2].lstrip('.'): int(l.split()[1], 16) for l in open(lab)}
    return dict(hex=open(b, 'rb').read().hex(), pump_n=L['pump_n'], done=L['done'])


def main():
    mhz = float(sys.argv[1]) if len(sys.argv) > 1 else 3
    H = tube_req.ReqRef()
    F = tube_req.FillServer()
    S = tube_server.Server(H)
    load, img, entry = S.image()
    pl = list(poses.POSITIONS) + list(poses.VERIFY)
    reqs, want = [], [b'\0']                    # frame -1's list: empty
    for p in pl:
        H.render(*p)
        r = tube_req.encode(H.frame())
        F.serve(tube_req.decode(r))
        reqs.append(r.hex())
        want.append(tube_dl.encode(F.dl, F.fid))
    os.makedirs(tube_server.OUT, exist_ok=True)
    fin, fout = (os.path.join(tube_server.OUT, f) for f in ('link_in.json', 'link_out.json'))
    json.dump(dict(mhz=mhz, image=dict(load=load, hex=img.hex(), entry=entry), pump=pump(),
                   frames=reqs, lists=[len(w) for w in want]), open(fin, 'w'))
    subprocess.run(['node', os.path.join(ROOT, 'tools', 'tube_link_rig.mjs'), fin, fout], check=True)
    out = json.load(open(fout))
    bad = 0
    if 'error' in out:
        print('  ' + out['error'])
        bad += 1
    for n, (got, w) in enumerate(zip(out['lists'], want)):
        ok = bytes.fromhex(got) == w
        bad += not ok
        tag = 'frame -1' if n == 0 else str(pl[n - 1])
        g = bytes.fromhex(got)
        k = next((i for i, (a, b) in enumerate(zip(g, w)) if a != b), None)
        print(f'  {tag:30s} list {len(w):5d} B  host {out["cycles"][n]:9,d} cycles  '
              + ('ok' if ok else f'DIFFER at #{k}: {g[k:k+12].hex() if k is not None else ""} vs '
                                 f'{w[k:k+12].hex() if k is not None else ""}'))
    c = out['cycles'][1:-1] or [0]
    print(f'  {len(pl)} frames at {mhz:g}MHz: host mean {sum(c) // len(c):,} cycles a frame '
          f'({sum(c) / len(c) / 2000:.0f} ms), max {max(c):,}')
    print('TUBELINK: FAIL' if bad else 'TUBELINK: PASS')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
