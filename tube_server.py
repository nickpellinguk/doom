#!/usr/bin/env python3
"""The Tube Master fill server's build and py65 rig (docs/tube_master.md
H3). src/tube/fserve.s with src/master/mfill.s assembled for the second
processor (SERVER) turns a frame's fill requests (tube_req.encode) into
its display list (tube_dl.encode); this links it (src/tube/fserve.cfg)
and runs it on a flat 64K py65 memory, as the second processor's.

The image is the link's code and initialised tables plus what the Master
build seeds: the wall tables (master_walls.Walls.images, laid out by the
server's labels, its column index holding texture columns), the quarter
squares and the sky map.
"""
import os
import subprocess

import fill_ref
import span_clip_6502
from symmap import _parse_dbg

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'build', 'tube', 'srv')
DEFS = ['-D', 'ENGINE=1', '-D', 'C02=1', '-D', 'BANKED=1', '-D', 'MASTER=1', '-D', 'SERVER=1']
UNITS = ('src/master/mfill.s', 'src/tube/fserve.s')
LOADS = ((0x0C00, 0x1400), (0x2000, 0x1000), (0x3000, 0x1000),
         (0x4000, 0x3200))                      # fserve.cfg's file areas
IMAGE = (0x0C00, 0x7200)                        # the second processor's file
TRAMP = 0xFF00                                  # JSR fs_frame; (stop): above the link


def build(defs=None):
    """(the binary, {label: address}). defs: {name: value} for a gate's
    variant (a small RING or LISTCAP), built in its own directory."""
    out = OUT + ''.join(f'_{k}{v:x}' for k, v in sorted((defs or {}).items()))
    os.makedirs(out, exist_ok=True)
    extra = [a for k, v in sorted((defs or {}).items()) for a in ('-D', f'{k}=${v:X}')]
    objs = []
    for u in UNITS:
        n = os.path.splitext(os.path.basename(u))[0]
        o = os.path.join(out, n + '.o')
        subprocess.run(['ca65', '-g'] + DEFS + extra + ['-I', 'src', '-I', 'src/master', u, '-o', o,
                        '-l', os.path.join(out, n + '.lst')], check=True, cwd=ROOT)
        objs.append(o)
    b, dbg = os.path.join(out, 'fserve.bin'), os.path.join(out, 'fserve.dbg')
    subprocess.run(['ld65', '-C', 'src/tube/fserve.cfg'] + objs +
                   ['-o', b, '-m', os.path.join(out, 'fserve.map'), '--dbgfile', dbg],
                   check=True, cwd=ROOT)
    return open(b, 'rb').read(), dict(_parse_dbg(dbg))


class Server:
    """The fill server on py65. R: a tube_req.ReqRef (its walls, assets and
    map, for the tables)."""

    def __init__(self, R, defs=None):
        from py65.devices.mpu65c02 import MPU
        code, self.L = build(defs)
        L = self.L
        mem = [0] * 0x10000
        p = 0
        for a, n in LOADS:
            mem[a:a + n] = code[p:p + n]
            p += n
        assert p == len(code), 'fserve.bin is not the file areas'
        im = R.W.images(R.T.A.man, L=L.__getitem__, server=True)
        a = L['man_slot_d']
        mem[a:a + len(im['andy'])] = im['andy']
        a = im['b6t_base']
        mem[a:a + len(im['b6t'])] = im['b6t']
        a = L['mb6_rc']
        mem[a:a + len(im['b6r'])] = im['b6r']
        a = im['ix_base']
        mem[a:a + len(im['ix'])] = im['ix']
        sl, sh, s2l, s2h = span_clip_6502._gen_quarter_square()
        q = L['sqr_quad_m']                     # [mir lo][lo][2lo][mir hi][hi][2hi]
        mem[q + 0x100:q + 0x200], mem[q + 0x200:q + 0x300] = sl, s2l
        mem[q + 0x400:q + 0x500], mem[q + 0x500:q + 0x600] = sh, s2h
        for k in range(1, 256):
            mem[q + k], mem[q + 0x300 + k] = sl[256 - k], sh[256 - k]
        mem[q], mem[q + 0x300] = 0, 64
        sk = L['mf_skymap']
        for i, b in enumerate(fill_ref.sky_bitmap()):
            mem[sk + i] = b
        e = L['fs_frame']
        mem[TRAMP:TRAMP + 3] = [0x20, e & 0xFF, e >> 8]
        self.mem = mem
        self.mpu = MPU(memory=mem)

    def image(self):
        """The second processor's file: (load address, bytes), and fs_main."""
        a, b = IMAGE
        return a, bytes(self.mem[a:b]), self.L['fs_main']

    def serve(self, req, limit=20_000_000, drop=False, trace=None):
        """Run fs_frame over a frame's request bytes, put in its ring as
        fs_irq would: (the list, cycles). drop: the records the list's cap
        (LISTCAP) and the marks, runs and planes the server's tables could
        not take; asserted none unless drop. trace: {pc: f(mpu)}, called
        as each of those instructions is reached (tools)."""
        L, m, mpu = self.L, self.mem, self.mpu
        q, n = L['fs_ring'], L['fs_ringsz']
        rp_, rw_ = L['fs_rp'], L['fs_rw']
        m[L['fs_drop']] = 0
        m[rp_], m[rp_ + 1] = q & 0xFF, q >> 8
        m[rw_], m[rw_ + 1] = q & 0xFF, q >> 8
        fed = [0, 0]                            # bytes fed, and read before the feed

        def feed():
            # fs_irq's part: the host's bytes into the ring while the server
            # has left room (a page short of full, as fs_irq keeps it)
            rp = m[rp_] | m[rp_ + 1] << 8
            w = q + (fed[0] % n)
            room = (rp - w - 0x100) % n if fed[0] else n - 0x100
            k = min(room, len(req) - fed[0])
            for i in range(k):
                m[q + (fed[0] + i) % n] = req[fed[0] + i]
            fed[0] += k
            w = q + fed[0] % n
            m[rw_], m[rw_ + 1] = w & 0xFF, w >> 8

        feed()
        ob = L['fs_lista']
        m[L['fs_ob']], m[L['fs_ob'] + 1] = ob & 0xFF, ob >> 8
        mpu.pc, mpu.sp = TRAMP, 0xFF
        c0 = mpu.processorCycles
        while mpu.pc != TRAMP + 3:
            if trace and mpu.pc in trace:
                trace[mpu.pc](mpu)
            mpu.step()
            if fed[0] < len(req) and m[rp_ + 1] != fed[1]:
                fed[1] = m[rp_ + 1]             # a page read: room for more
                feed()
            if mpu.processorCycles - c0 > limit:
                raise RuntimeError(f'runaway at ${mpu.pc:04X}')
        rp = m[rp_] | m[rp_ + 1] << 8
        assert fed[0] == len(req) and rp - q == len(req) % n, \
            f'read {(rp - q) % n} of {len(req)} request bytes (mod the ring)'
        op = m[L['fs_op']] | m[L['fs_op'] + 1] << 8
        assert drop or m[L['fs_drop']] == 0, 'the server dropped marks, runs, planes or records'
        return bytes(m[ob:op]), mpu.processorCycles - c0
