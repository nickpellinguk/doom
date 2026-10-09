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
LOADS = ((0x0F00, 0x0900), (0x4000, 0x4000))     # fserve.cfg's MID, CODE (the file)
TRAMP = 0xFF00                                  # JSR fs_frame; (stop): above the link


def build():
    """(the binary, {label: address})."""
    os.makedirs(OUT, exist_ok=True)
    objs = []
    for u in UNITS:
        n = os.path.splitext(os.path.basename(u))[0]
        o = os.path.join(OUT, n + '.o')
        subprocess.run(['ca65', '-g'] + DEFS + ['-I', 'src', '-I', 'src/master', u, '-o', o,
                        '-l', os.path.join(OUT, n + '.lst')], check=True, cwd=ROOT)
        objs.append(o)
    b, dbg = os.path.join(OUT, 'fserve.bin'), os.path.join(OUT, 'fserve.dbg')
    subprocess.run(['ld65', '-C', 'src/tube/fserve.cfg'] + objs +
                   ['-o', b, '-m', os.path.join(OUT, 'fserve.map'), '--dbgfile', dbg],
                   check=True, cwd=ROOT)
    return open(b, 'rb').read(), dict(_parse_dbg(dbg))


class Server:
    """The fill server on py65. R: a tube_req.ReqRef (its walls, assets and
    map, for the tables)."""

    def __init__(self, R):
        from py65.devices.mpu65c02 import MPU
        code, self.L = build()
        L = self.L
        mem = [0] * 0x10000
        p = 0
        for a, n in LOADS:
            mem[a:a + n] = code[p:p + n]
            p += n
        assert p == len(code), 'fserve.bin is not MID + CODE'
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

    def serve(self, req, limit=20_000_000):
        """Run fs_frame over a frame's request bytes: (the list, cycles)."""
        L, m, mpu = self.L, self.mem, self.mpu
        q = L['fs_req']
        assert len(req) <= L['fs_req'] + 0xC00 - q
        m[q:q + len(req)] = req
        m[L['fs_rp']], m[L['fs_rp'] + 1] = q & 0xFF, q >> 8
        mpu.pc, mpu.sp = TRAMP, 0xFF
        c0 = mpu.processorCycles
        while mpu.pc != TRAMP + 3:
            mpu.step()
            if mpu.processorCycles - c0 > limit:
                raise RuntimeError(f'runaway at ${mpu.pc:04X}')
        rp = m[L['fs_rp']] | m[L['fs_rp'] + 1] << 8
        assert rp - q == len(req), f'read {rp - q} of {len(req)} request bytes'
        op = m[L['fs_op']] | m[L['fs_op'] + 1] << 8
        return bytes(m[L['fs_dl']:op]), mpu.processorCycles - c0
