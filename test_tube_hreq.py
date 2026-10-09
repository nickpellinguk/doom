#!/usr/bin/env python3
"""Gate (host-led tube-master, H2): the host's 6502 sends the requests.

The engine is linked with TUBE (DOOM_ASMDEFS=TUBE=1): walk.s calls
rq_frame in place of mf_frame and seg_emit calls rq_fill in place of
mf_fill (src/master/mfill.s). The rig's memory captures every write to
the Tube's register 1 data ($FEE5; the status $FEE4 reads room). At every
regression pose (the walk's end sends rq_end's $00 itself):

  1. the bytes sent, through rq_end, decode (tube_req.decode) to the
     Python host's requests (tube_req.ReqRef): the same segs in the same
     order, with the same view and trig, slot, flags, subsector, heights
     over the eye, columns, reciprocal terms and crossing t;
  2. served (tube_req.FillServer), they draw EXACTLY the Master's own
     frame -- the normal link's 6502 fill, rendered in a subprocess
     (the two links share bin names).

What 2 holds, not 1: a request's line ends and clip spans are the
ENGINE's, which the Python host need not equal byte for byte -- the
reference rounds off-screen sx where the engine truncates (the gap
test_master_tex.py names), and the 6502's span pool can hold as two
spans what the Python clipper holds as one, drawing the same. Those
requests are counted, not failed.

Prints each pose's request size and the host's cycles (engine plus send,
no drawing), then TUBEHREQ: PASS / FAIL. Rebuilds the normal link on the
way out, so later gates in the same tree see the usual bins.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
FRAMES = os.path.join(ROOT, 'build', 'tube', 'master_frames.bin')
R1_STATUS, R1_DATA = 0xFEE0, 0xFEE1   # requests out, the list in


def renderer():
    import pygame
    pygame.init()
    import e1m1 as dw
    from banked_bsp import MasterBspRender
    return dw, MasterBspRender(dw.packed_layout, dw.packed_rom_main,
                               dw.packed_rom_detail, dw.packed_bbox_table,
                               dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)


def all_poses():
    import poses
    return list(poses.POSITIONS) + list(poses.VERIFY)


def master_frames():
    """(subprocess, normal link) the Master's 6502 frames -> FRAMES."""
    dw, r = renderer()
    out = bytearray()
    for p in all_poses():
        r.render_frame(*p, dw.player_floor(*p[:2]))
        out += r.framebuffer()
    os.makedirs(os.path.dirname(FRAMES), exist_ok=True)
    open(FRAMES, 'wb').write(out)


def main():
    env = dict(os.environ, DOOM_ASMDEFS='')
    subprocess.run([sys.executable, __file__, '--frames'], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = open(FRAMES, 'rb').read()

    os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
    import tube_req
    from banked_mem import BankedMemory
    from symmap import sym

    class TubeMem(BankedMemory):
        """The banked memory with register 1's writes captured."""
        def __setitem__(self, i, v):
            if i == R1_DATA:
                self.sent.append(v & 0xFF)
                return
            super().__setitem__(i, v)

    class ListMem(TubeMem):
        """... and register 1 holding a display list (while hd_frame runs:
        reads cost, so only then)."""
        def __getitem__(self, i):
            if i == R1_STATUS:
                return 0x40 | (0x80 if self.lp < len(self.dl) else 0)
            if i == R1_DATA:
                self.lp += 1
                return self.dl[self.lp - 1]
            return list.__getitem__(self, i)

    dw, r = renderer()
    r.bm.__class__ = TubeMem
    r.bm.sent = []
    list.__setitem__(r.bm, R1_STATUS, 0x40)     # always room
    H, S = tube_req.ReqRef(), tube_req.FillServer()
    import tube_dl
    hd = sym('hd_frame')
    dcyc = []
    strict = ('si', 'ss', 'dz', 'lo', 'hi', 'solid', 'c1', 'c2', 'm', 't')
    bad, sizes, cycles, same, nreq, gaps = 0, [], [], 0, 0, {'line': 0, 'spans': 0}
    for n, p in enumerate(all_poses()):
        r.bm.sent = []
        r.render_frame(*p, dw.player_floor(*p[:2]))
        cyc = r.sc.total_cycles
        got = bytes(r.bm.sent)
        H.render(*p)
        want = H.frame()
        same += got == tube_req.encode(want)
        req = tube_req.decode(got)
        why = []
        if req['view'] != want['view'] or req['trig'] != tuple(map(int, want['trig'])):
            why.append('view')
        if len(req['reqs']) != len(want['reqs']):
            why.append(f'{len(req["reqs"])} requests, model {len(want["reqs"])}')
        for a, b in zip(req['reqs'], want['reqs']):
            k = [f for f in strict if a[f] != b[f]]
            if k:
                why.append(f'seg {a["si"]}: ' + ' '.join(f'{f} {a[f]} (model {b[f]})' for f in k))
            gaps['line'] += a['line'] != tuple(b['line'])
            gaps['spans'] += any(_spans(a[f]) != _spans(b[f]) for f in ('before', 'after'))
        nreq += len(req['reqs'])
        frame = S.serve(req)
        ref = frames[n * 10240:(n + 1) * 10240]
        diff = sum(x != y for x, y in zip(frame, ref))
        if diff:
            why.append(f'served frame: {diff} bytes differ from the Master\'s')
        # 3. the host drawer (src/tube/hdraw.s in this link) draws the
        # served list into the back buffer: the Master's frame again
        r.bm.__class__ = ListMem
        r.bm.dl, r.bm.lp = tube_dl.encode(S.dl, S.fid), 0
        c0 = r.sc.mpu.processorCycles
        r.sc._run(hd)
        dcyc.append(r.sc.mpu.processorCycles - c0)
        r.bm.__class__ = TubeMem
        if r.bm.lp != len(r.bm.dl):
            why.append(f'hd_frame read {r.bm.lp} of {len(r.bm.dl)} list bytes')
        drawn = r.framebuffer()
        diff = sum(x != y for x, y in zip(drawn, ref))
        if diff:
            k = next(i for i, (x, y) in enumerate(zip(drawn, ref)) if x != y)
            why.append(f'drawn frame: {diff} bytes differ from the Master\'s, first at {k}')
        bad += bool(why)
        sizes.append(len(got))
        cycles.append(cyc)
        print(f'  {str(p):30s} {len(req["reqs"]):3d} segs {len(got):5d} B {cyc:8,d} cycles  '
              + ('; '.join(why) if why else 'ok'))
    k = len(sizes)
    print(f'  {k} poses: requests mean {sum(sizes) // k} B (max {max(sizes)}), '
          f'host engine + send mean {sum(cycles) // k:,} cycles (max {max(cycles):,})')
    print(f'  host drawer: mean {sum(dcyc) // k:,} cycles a frame (max {max(dcyc):,}); '
          f'host engine + send + draw mean {(sum(cycles) + sum(dcyc)) // k:,}')
    print(f'  {same}/{k} poses byte-identical to the Python host; of {nreq} requests, '
          f'{gaps["line"]} carry engine line ends and {gaps["spans"]} engine span splits '
          f'the model does not (drawn the same)')
    print('TUBEHREQ: FAIL' if bad else 'TUBEHREQ: PASS')
    return bad


def _spans(lst):
    return [tuple(s) + ((s.bxlo, s.bxhi) if hasattr(s, 'bxlo') else (s[2], s[3])) for s in lst]


if __name__ == '__main__':
    if '--frames' in sys.argv:
        master_frames()
        sys.exit(0)
    bad = main()
    os.environ['DOOM_ASMDEFS'] = ''
    import asmbuild
    asmbuild.build()
    sys.exit(1 if bad else 0)
