#!/usr/bin/env python3
"""The Tube Master's SLOW views: find them, then profile them
(docs/tube_master.md H4f).

A frame overlapped across the two processors takes as long as its slower
side: the host's engine and sending plus its drawer at 2MHz, or the fill
server at 3MHz. Mean figures over the regression poses hide the frames
that set how the game feels; this measures each pose's both sides on
py65 and works on the worst.

    python3 tools/tube_slow.py scan [n [seed]]
        n random standable poses (tube_fuzz.random_poses, default 400)
        and the regression's, each side timed; prints the worst 20 and
        writes build/tube/slow_scan.json
    python3 tools/tube_slow.py slow
        poses.TUBE_SLOW timed (no profiling): each side's mean and worst,
        the yardstick for speed work
    python3 tools/tube_slow.py prof [k [engine,draw,server]]
        the k slowest poses of poses.TUBE_SLOW (0, the default: all), the
        sides named (default all) profiled by routine: exclusive cycles to
        the nearest code label; the engine also by source file, the
        server also by job

The host is the TUBE engine link (test_tube_hreq's rig): render_frame
with register 1's bytes captured (the requests), then hd_frame drawing
the model's encoded list. The server is tube_server's py65 rig.
"""
import bisect
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

HOST_HZ, SERVER_HZ = 2_000_000, 3_000_000
OUT = os.path.join(ROOT, 'build', 'tube', 'slow_scan.json')


class Every(dict):
    """A pc_hooks / trace dict that calls f at every instruction."""
    def __init__(self, f):
        super().__init__()
        self.f = f

    def __bool__(self):
        return True

    def __contains__(self, k):
        return True

    def __getitem__(self, k):
        return self.f


class Rig:
    def __init__(self):
        import test_tube_hreq as T
        import tube_req
        import tube_server
        from banked_mem import BankedMemory
        from symmap import sym
        self.T, self.tube_req = T, tube_req

        class TubeMem(BankedMemory):            # register 1's writes captured
            def __setitem__(self, i, v):
                if i == T.R1_DATA:
                    return
                super().__setitem__(i, v)

        class ListMem(TubeMem):                  # ... and the list to read
            def __getitem__(self, i):
                if i == T.R1_STATUS:
                    return 0x40 | (0x80 if self.lp < len(self.dl) else 0)
                if i == T.R1_DATA:
                    self.lp += 1
                    return self.dl[self.lp - 1]
                return list.__getitem__(self, i)
        self.TubeMem, self.ListMem = TubeMem, ListMem
        self.dw, self.r = T.renderer()
        self.r.bm.__class__ = TubeMem
        list.__setitem__(self.r.bm, T.R1_STATUS, 0x40)
        self.hd = sym('hd_frame')
        self.H, self.F = tube_req.ReqRef(), tube_req.FillServer()
        self.S = tube_server.Server(self.H)

    def pose(self, p, host_hook=None, server_trace=None):
        """{engine, draw, server} cycles at pose p (hooks: profiling)."""
        import tube_dl
        r, tr = self.r, self.tube_req
        r.sc.pc_hooks = host_hook
        r.render_frame(*p, self.dw.player_floor(*p[:2]))
        engine = r.sc.total_cycles
        self.H.render(*p)
        req = tr.encode(self.H.frame())
        self.F.serve(tr.decode(req))
        r.bm.__class__ = self.ListMem
        r.bm.dl, r.bm.lp = tube_dl.encode(self.F.dl, self.F.fid), 0
        r.sc._run(self.hd)                       # (_run counts from 0)
        draw = r.sc.mpu.processorCycles
        r.bm.__class__ = self.TubeMem
        r.sc.pc_hooks = None
        _, server = self.S.serve(req, drop=True, trace=server_trace)
        return dict(engine=engine, draw=draw, server=server, req=len(req),
                    list=len(r.bm.dl))


def ms(d):
    host = (d['engine'] + d['draw']) / HOST_HZ * 1000
    return host, d['server'] / SERVER_HZ * 1000


def scan(n, seed):
    import poses
    import tube_fuzz
    rig = Rig()
    pl = list(dict.fromkeys(list(poses.POSITIONS) + list(poses.VERIFY) + tube_fuzz.random_poses(n, seed)))
    rows = []
    for i, p in enumerate(pl):
        d = rig.pose(p)
        h, s = ms(d)
        rows.append(dict(pose=p, host_ms=round(h, 1), server_ms=round(s, 1), **d))
        if i % 50 == 49:
            print(f'  {i + 1} poses', flush=True)
    rows.sort(key=lambda r: -max(r['host_ms'], r['server_ms']))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, 'w'))
    frame = sorted(max(r['host_ms'], r['server_ms']) for r in rows)
    pct = lambda q: frame[min(len(frame) - 1, int(q * len(frame)))]
    print(f'{len(rows)} poses: frame (the slower side) median {pct(0.5):.0f} ms, '
          f'90% {pct(0.9):.0f} ms, 99% {pct(0.99):.0f} ms, worst {frame[-1]:.0f} ms')
    hb = sum(r['host_ms'] > r['server_ms'] for r in rows[:20])
    print(f'the worst 20 ({hb} host-bound, {20 - hb} server-bound at 3MHz):')
    for r in rows[:20]:
        print(f"  {str(tuple(r['pose'])):34s} host {r['host_ms']:5.0f} ms (engine {r['engine'] // 1000}K "
              f"draw {r['draw'] // 1000}K)  server {r['server_ms']:5.0f} ms  "
              f"req {r['req']} B list {r['list']} B")


ARITH = {'mul16', 'm16_a1z', 'm16_done', 'dq_core', 'divq16', 'div32', 'dv8f', 'd8_fast',
         'd8_byte', 'dv_slow', 'dv_w16', 'dv_slj', 'mul8x32', 'm8_run', 'm8_lp', 'm8_nx', 'm8_p1',
         'm8_p2', 'm8_m1', 'm8_m2', 'pl_prod', 'mul8lo'}
OWNER_JOBS = {
    'walls: texture set-up (u, v, step)': (
        'tx_seg', 'at', 'ld_dress', 'set_cur', 'tx_getd', 'tcol', 'tv_v0', 'tv_divm', 'tvstep',
        'tv_fine', 'trun', 'tr_lines', 'tr_screen', 'sh_lim', 'l16t', 'pl_seg', 'wall_run', 'tx_dat',
        'tx_ent'),
    'walls: edge steppers': ('st_init', 'st_init8', 'st_q', 'st_peek', 'st_body'),
    'the column walk': ('mf_fill', 'col', 'adv', 'next_col', 'band', 'mf_range', 'mf_snap',
                        'hz_run', 'prun', 'mf_planes'),
    'floors / ceilings: row maths': ('pl_row', 'pl_rowc', 'pl_zrow', 'pl_hq', 'pl_dh', 'pl_dhn',
                                     'fs_key'),
    'frame-end pass: runs and spans': (
        'fs_end', 'fs_sweep', 'fs_change', 'fs_spans', 'fs_runs', 'fs_pairs', 'fs_singles',
        'fs_span', 'fs_ghead', 'fs_gclose', 'fs_fills'),
    'reading requests': ('fs_get', 'fs_frame'),
    'marks and WALL records': ('fs_mark', 'fs_trim', 'fs_wall', 'fs_wclose', 'fs_planes', 'fs_pid',
                               'fs_drop1'),
    'the Tube (sending, IRQ)': ('fs_poll', 'fs_irq'),
}

SERVER_JOBS = {
    'reading requests': ('fs_get', 'fs_frame'),
    'marks and WALL records': ('fs_mark', 'fs_trim', 'fs_wall', 'fs_wclose', 'fs_planes', 'fs_pid',
                               'fs_drop1'),
    'the frame-end pass (runs, spans, fills)': (
        'fs_end', 'fs_sweep', 'fs_change', 'fs_spans', 'fs_runs', 'fs_key', 'fs_pairs',
        'fs_singles', 'fs_span', 'fs_ghead', 'fs_gclose', 'mul8lo', 'fs_fills'),
    'sending the list': ('fs_poll',),
}


def server_jobs(c, L):
    """The server's profile by job: its own routines by name, the rest the
    Master's fill (mfill.s) by segment: its arithmetic, and the rest."""
    import tube_server
    segs = []
    for line in open(os.path.join(tube_server.OUT, 'fserve.map')):
        f = line.split()
        if len(f) == 5 and f[1].startswith('00') and f[0].isupper():
            segs.append((int(f[1], 16), int(f[2], 16), f[0]))
    job = {nm: j for j, nms in SERVER_JOBS.items() for nm in nms}
    out = collections.Counter()
    for name, v in c.items():
        if name in job:
            out[job[name]] += v
            continue
        a = L.get(name, -1)
        seg = next((sg for lo, hi, sg in segs if lo <= a <= hi), '?')
        out['the fill: arithmetic' if seg == 'MARITH' or name in (
            'mul16', 'dq_core', 'div32', 'divq16', 'dv8f', 'd8_fast', 'm8_m2', 'm16_a1z',
            'mul8x32', 'dq_set', 'tx_dat') else
            'server, other' if seg == 'FSCODE' else 'the fill: the rest'] += v
    return out


def server_labels():
    """The server link's true labels (not its equates: SCREEN0 and the like
    sit among the code) as (name, address)."""
    import tube_server
    out = []
    for line in open(os.path.join(tube_server.OUT, 'fserve.dbg')):
        if not line.startswith('sym'):
            continue
        f = dict(x.split('=', 1) for x in line.split('\t')[1].strip().split(','))
        if f.get('type') == 'lab' and 'val' in f:
            out.append((f['name'].strip('"'), int(f['val'], 16)))
    return out


def labeller(pairs, lo=0, hi=0x10000):
    pairs = list(pairs)
    seen = collections.Counter(n for n, _ in pairs)  # (a name defined more
    ks = sorted((a, n) for n, a in pairs             #  than once is a scoped
                if lo <= a < hi and seen[n] == 1     #  local of an unrolled
                and not n.startswith(('@', 'LOCAL', '__')))   # loop: its routine)
    addrs = [a for a, _ in ks]

    def lab(pc):
        i = bisect.bisect_right(addrs, pc) - 1
        return ks[i][1] if i >= 0 else f'${pc:04X}'
    return lab


def prof(k, sides=('engine', 'draw', 'server')):
    import poses
    from symmap import _parse_dbg
    rig = Rig()
    pl = poses.TUBE_SLOW[:k] if k else poses.TUBE_SLOW
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import master_profile as MP
    hl = MP.code_labels('build/engine_m.dbg')    # code labels only (equates
    hlab = labeller([(n, a) for a, n, _, _ in hl])     #  sit among them)
    hfile = {n: f for _, n, f, _ in hl}
    slab = labeller(server_labels(), 0x0C00, 0x8000)
    parts = {'engine': collections.Counter(), 'draw': collections.Counter(),
             'server': collections.Counter()}
    st = {'h': None, 'hc': 0, 's': None, 'sc': 0, 'part': 'engine'}

    def htick(mpu):
        c = mpu.processorCycles
        if st['h'] is not None and c >= st['hc']:
            parts[st['part']][st['h']] += c - st['hc']
        st['h'], st['hc'] = hlab(mpu.pc), c

    owners = collections.Counter()                  # arithmetic charged to
    stack = []                                      #  its nearest caller

    def stick(mpu):
        c = mpu.processorCycles
        if st['s'] is not None:
            parts['server'][st['s']] += c - st['sc']
            own = st['s']
            if own in ARITH:
                own = next((f for f in reversed(stack) if f not in ARITH), own)
            owners[own] += c - st['sc']
        op = rig.S.mem[mpu.pc]
        if op == 0x20:                              # JSR: its target's frame
            stack.append(slab(rig.S.mem[mpu.pc + 1] | rig.S.mem[mpu.pc + 2] << 8))
        elif op == 0x60 and stack:                  # RTS
            stack.pop()
        st['s'], st['sc'] = slab(mpu.pc), c
    tot = collections.Counter()
    hd = rig.hd
    for p in pl:
        # the drawer is entered by _run(hd): the hook knows it by the pc
        def h2(mpu, _h=htick):
            if mpu.pc == hd:
                st['part'] = 'draw'
            _h(mpu)
        st.update(h=None, s=None, part='engine')
        d = rig.pose(tuple(p), host_hook=Every(h2) if {'engine', 'draw'} & set(sides) else None,
                     server_trace=Every(stick) if 'server' in sides else None)
        for x in ('engine', 'draw', 'server'):
            tot[x] += d[x]
    n = len(pl)
    for x, hz in (('engine', HOST_HZ), ('draw', HOST_HZ), ('server', SERVER_HZ)):
        if x not in sides:
            continue
        c = parts[x]
        t = sum(c.values()) or 1
        print(f'== {x}: mean {tot[x] // n:,} cycles ({tot[x] / n / hz * 1000:.0f} ms) over {n} slow poses')
        if x == 'server':                        # by job, arithmetic to its caller
            job = {nm: j for j, nms in OWNER_JOBS.items() for nm in nms}
            byj, loose = collections.Counter(), collections.Counter()
            for nm, v in owners.items():
                byj[job.get(nm, 'other')] += v
                if nm not in job:
                    loose[nm] += v
            for j, v in byj.most_common():
                print(f'  {v // n:9,d} {v / t * 100:5.1f}%  [{j}]')
            print('  (other: ' + ', '.join(f'{nm} {v // n:,}' for nm, v in loose.most_common(8)) + ')')
            print('  -- by routine, arithmetic charged to its caller:')
            for nm, v in owners.most_common(20):
                print(f'  {v // n:9,d} {v / t * 100:5.1f}%  {nm}')
            print('  -- by routine, exclusive:')
        if x == 'engine':                        # by source file first
            byf = collections.Counter()
            for name, v in c.items():
                byf[hfile.get(name, '?')] += v
            for f, v in byf.most_common(12):
                print(f'  {v // n:9,d} {v / t * 100:5.1f}%  [{f}]')
        for name, v in c.most_common(25):
            print(f'  {v // n:9,d} {v / t * 100:5.1f}%  {name}')


def slow():
    import poses
    rig = Rig()
    rows = [rig.pose(tuple(p)) for p in poses.TUBE_SLOW]
    n = len(rows)
    for x, hz in (('engine', HOST_HZ), ('draw', HOST_HZ), ('server', SERVER_HZ)):
        v = [r[x] for r in rows]
        print(f'  {x:7s} mean {sum(v) // n:9,d} cycles ({sum(v) / n / hz * 1000:4.0f} ms), '
              f'worst {max(v):9,d}')
    fr = [max(ms(r)) for r in rows]
    f4 = [max((r['engine'] + r['draw']) / HOST_HZ, r['server'] / 4_000_000) * 1000 for r in rows]
    print(f'  frame (the slower side): mean {sum(fr) / n:.0f} ms at 3MHz, {sum(f4) / n:.0f} ms at 4MHz; '
          f'worst {max(fr):.0f} / {max(f4):.0f} ms')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if cmd == 'slow':
        slow()
    elif cmd == 'scan':
        scan(int(sys.argv[2]) if len(sys.argv) > 2 else 400, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    else:
        prof(int(sys.argv[2]) if len(sys.argv) > 2 else 0,
             sys.argv[3].split(',') if len(sys.argv) > 3 else ('engine', 'draw', 'server'))
