#!/usr/bin/env python3
"""Whole-frame cycle profile of the MASTER build in py65 (docs/master_textured_spec.md).

Renders the regression poses (default: the 14 on-map ones; or one pose
given as  px py ab) and attributes every cycle:

  exclusive  to the code label at or below the PC (ld65's debug file:
             code segments only; in $8000-$BFFF only bank 6's fill code),
             cheap locals qualified by their routine (trr_rd@line), and
             to its source file
  inclusive  to every routine on the call stack -- a shadow stack keyed by
             S, so the BSP walk's unwind (TXS) is handled. Routines entered
             by JMP (trun, prun, pl_row, hz_run, mf_flush) are not
             frames: their inclusive figures read 0. tx_seg's includes the
             plane flushes its tail call (pe_init) can trigger.
  arithmetic to the nearest non-arithmetic caller on the stack
  loop heads executions of each label's first instruction (wall pairs =
             the 16 pair bodies' te_* labels, span pairs = the 4 span
             bodies' sf_r* texel reads, sl_lp = line-span bytes)

The fill's routines are also summed by job. Figures are means per frame.

    python3 tools/master_profile.py              # 14 on-map poses
    python3 tools/master_profile.py 1056 -3616 32
"""
import bisect
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

OFFMAP = {(192, -2368, 99), (3648, -2368, 35), (1500, -3700, 0), (3648, -4800, 131)}
BANK6_CODE = ('MB6C', 'MFILLV')
ARITH = {'mf_mul8', 'mul16', 'div32', 'divq16', 'dq_set', 'dq_core', 'd8_byte',
         'mul8x32', 'pl_prod', 'q_a_h', 'neg_ah', 'tx_dat'}
JOBS = [
    ('wall texel loop', ['tr_screen', 'tr_fetch', 'tr_vstep', 'tr_ent', 'tb_end']
                        + [f'tv_{v}' for v in range(4)]
                        + [f'te_{v}{j}' for v in range(4) for j in range(4)]
                        + ['ts_ent', 'ts_end'] + [f'sv_{v}' for v in range(4)]
                        + [f'ts_{v}{j}' for v in range(4) for j in range(4)]),
    ('span loops (pair, line)', ['sp_go2', 'sf_end', 'sf_ent', 'sl_go', 'sl_lp', 'sl_rd',
                                 'sl_fl', 'sl_mk']
                                + ['sf_lp'] + [f'sf_e{q}' for q in range(4)]
                                + [f'sf_r{q}' for q in range(4)]),
    ('solid fills', ['hz_run', 'hz_two', 'hz_done', 'run', 'pl_shade']
                    + [f'h{v}_{n}' for v in (1, 2)
                       for n in ('call', 'b0', 't')]),
    ('partial/second-run cells', ['pl_line', 'pl_pair', 'pl_cell', 'pc_go', 'pc_rd', 'pl_wr1']),
    ('plane spans set-up', ['mk_spans', 'mk_close', 'sp_setup', 'pp_slot', 'sl_draw', 'pe_init',
                            'pe_kind', 'pe_clr', 'pd_flush', 'mf_flush', 'prun', 'pl_part',
                            'ceil_run', 'floor_run', 'pl_seg', 'mf_planes', 'uvat']),
    ('plane rows', ['pl_rowc', 'pl_row', 'pl_zrow']),
    ('wall set-up per seg', ['tx_seg', 'at', 'l16t', 'ld_dress', 'set_cur']),
    ('wall set-up per byte/run', ['trun', 'tv_div', 'tv_v0', 'tcol', 'tx_getd', 'tr_lines',
                                  'wall_run', 'st_peek', 'sh_lim']),
    ('column walk + span edges', ['mf_fill', 'col', 'adv', 'next_col', 'st_init8', 'st_init',
                                  'st_step', 'st_val', 'band', 'clamp_ln', 'ln_ptr', 'ln_ptrk',
                                  'mf_range', 'mf_snap']),
    ('arithmetic', sorted(ARITH) + ['dv_slj', 'dv_w16', 'dv_slow', 'dq_lp', 'dq_ch', 'dq_cl',
                                    'dq_sub', 'dq_sl', 'dq_sh', 'dq_nx', 'd8_lp', 'd8_c',
                                    'd8_s', 'd8_n', 'dv_r0', 'dv_b1', 'dv_b0', 'dv_z0',
                                    'dv_l0', 'dv_end']),
]
INCLUSIVE = ['mf_fill', 'mf_snap', 'band', 'wall_run', 'tv_div', 'tv_v0', 'tcol', 'tx_getd',
             'tx_seg', 'at', 'pl_pair', 'mk_spans', 'sp_setup', 'pl_rowc', 'uvat', 'st_step',
             'st_val', 'st_peek', 'div32', 'divq16', 'mul16', 'mul8x32']
LOOP_HEADS = ['sl_lp', 'trun', 'sp_go2', 'sl_go', 'mf_fill', 'col', 'band', 'tx_getd']
WALL_PAIR_LABELS = ([f'te_{v}{j}' for v in range(4) for j in range(4)]
                    + [f'ts_{v}{j}' for v in range(4) for j in range(4)])
SPAN_PAIR_LABELS = [f'sf_r{q}' for q in range(4)]


def _kv(line):
    return dict(x.split('=', 1) for x in line.split('\t')[1].strip().split(','))


def code_labels(dbg):
    """[(addr, qualified name, source file, segment)] for the code labels."""
    files, lines, segs, raw = {}, {}, {}, []
    rows = open(dbg).read().splitlines()
    for line in rows:
        kind = line.split('\t')[0]
        if kind == 'file':
            f = _kv(line)
            files[f['id']] = os.path.relpath(f['name'].strip('"'), os.path.join(ROOT, 'src'))
        elif kind == 'line':
            f = _kv(line)
            lines[f['id']] = f['file']
        elif kind == 'seg':
            f = _kv(line)
            segs[f['id']] = (f['name'].strip('"'), f.get('type'))
    for line in rows:
        if not line.startswith('sym'):
            continue
        f = _kv(line)
        if f.get('type') != 'lab' or 'val' not in f or 'seg' not in f:
            continue
        seg, typ = segs[f['seg']]
        if typ != 'ro':
            continue
        v = int(f['val'], 16)
        if 0x8000 <= v < 0xC000 and seg not in BANK6_CODE:
            continue
        raw.append((v, f['name'].strip('"'), files[lines[f['def'].split('+')[0]]], seg))
    raw.sort()
    out, parent = [], '?'
    for v, n, fl, seg in raw:
        if n.startswith('@'):
            n = parent + n
        else:
            parent = n
        out.append((v, n, fl, seg))
    return out


class Profiler:
    def __init__(self, labs):
        self.labs = labs
        self.addrs = [x[0] for x in labs]
        self.at_addr = {x[0]: x[1] for x in labs}
        self.info = {x[1]: (x[2], x[3]) for x in labs}
        self.cache = {}
        self.excl, self.incl, self.calls = Counter(), Counter(), Counter()
        self.files, self.hits, self.arith = Counter(), Counter(), Counter()
        self.total = 0

    def label(self, pc):
        r = self.cache.get(pc)
        if r is None:
            i = bisect.bisect_right(self.addrs, pc) - 1
            r = self.cache[pc] = self.labs[i] if i >= 0 else (0, '?', '?', '?')
        return r

    def frame(self, R, pose, floor):
        stack, active, start = [], Counter(), {}
        st = {'pc': None, 'c': 0}
        excl, incl, calls = self.excl, self.incl, self.calls
        files, hits, arith = self.files, self.hits, self.arith

        def hook(m):
            pc, c = m.pc, m.processorCycles
            if st['pc'] is not None:
                d = c - st['c']
                x = self.label(st['pc'])
                excl[x[1]] += d
                files[x[2]] += d
                if x[1].split('@')[0] in ARITH or (stack and stack[-1][1] in ARITH):
                    arith[next((n for _, n in reversed(stack) if n not in ARITH), '?')] += d
            if pc in self.at_addr:
                hits[self.at_addr[pc]] += 1
            sp = m.sp
            while stack and sp >= stack[-1][0]:
                _, n = stack.pop()
                active[n] -= 1
                if active[n] == 0:
                    incl[n] += c - start.pop(n)
            if m.memory[pc] == 0x20:
                n = self.label(m.memory[pc + 1] | m.memory[pc + 2] << 8)[1]
                calls[n] += 1
                stack.append((sp, n))
                if active[n] == 0:
                    start[n] = c
                active[n] += 1
            st['pc'], st['c'] = pc, c

        class Every(dict):
            def __bool__(self):
                return True

            def __contains__(self, pc):
                return True

            def __getitem__(self, pc):
                return hook

        R.sc.pc_hooks = Every()
        cyc = R.render_frame(*pose, floor)
        R.sc.pc_hooks = None
        for n in list(active):
            if active[n] > 0:
                incl[n] += st['c'] - start[n]
        self.total += cyc
        return cyc

    def report(self, n):
        T = max(self.total, 1)
        avg = lambda v: v / n
        print(f'\n== by source file (exclusive, mean per frame) ==')
        for k, v in self.files.most_common(14):
            print(f'  {k:26s} {avg(v):9,.0f} {100 * v / T:5.1f}%')
        base = lambda k: k.split('@')[0]
        jobs, seen = Counter(), set()
        for name, ls in JOBS:
            for k, v in self.excl.items():
                if base(k) in ls:
                    jobs[name] += v
                    seen.add(k)
        fill = lambda k: self.info.get(k, ('', ''))[0] == 'master/mfill.s'
        other = sum(v for k, v in self.excl.items() if k not in seen and fill(k))
        engine = sum(v for k, v in self.excl.items() if not fill(k))
        print('\n== the fill by job (exclusive, mean per frame) ==')
        for name, _ in JOBS:
            print(f'  {name:28s} {avg(jobs[name]):9,.0f} {100 * jobs[name] / T:5.1f}%')
        print(f'  {"other fill":28s} {avg(other):9,.0f} {100 * other / T:5.1f}%')
        print(f'  {"ENGINE (BSP, clip, project)":28s} {avg(engine):9,.0f} {100 * engine / T:5.1f}%')
        print('\n== top routines, exclusive (mean per frame) ==')
        for k, v in self.excl.most_common(30):
            print(f'  {k:22s} {avg(v):9,.0f} {100 * v / T:5.1f}%  {self.info.get(k, ("?", "?"))[1]}')
        print('\n== arithmetic by caller (mean per frame) ==')
        for k, v in self.arith.most_common(12):
            print(f'  {k:14s} {avg(v):9,.0f} {100 * v / T:5.1f}%')
        print('\n== inclusive, selected (mean per frame) ==')
        for k in INCLUSIVE:
            c = self.calls[k]
            print(f'  {k:12s} {avg(self.incl[k]):9,.0f} {100 * self.incl[k] / T:5.1f}%  '
                  f'calls {c / n:7.1f}  each {self.incl[k] / max(c, 1):7.0f}')
        print('\n== loop heads (executions per frame) ==')
        pairs = sum(self.hits[k] for k in WALL_PAIR_LABELS)
        spans = sum(self.hits[k] for k in SPAN_PAIR_LABELS)
        print(f'  wall pairs {pairs / n:.0f}  span pairs {spans / n:.0f}  '
              + '  '.join(f'{k} {self.hits[k] / n:.0f}' for k in LOOP_HEADS))


def main():
    import pygame
    pygame.init()
    import asmbuild
    import poses as C
    import e1m1 as dw
    from banked_bsp import MasterBspRender
    asmbuild.build('engine')
    if len(sys.argv) == 4:
        poses = [tuple(float(a) if '.' in a else int(a) for a in sys.argv[1:])]
    else:
        poses = [p for p in C.POSITIONS if p not in OFFMAP]
    P = Profiler(code_labels(os.path.join(ROOT, 'build', 'engine_m.dbg')))
    R = MasterBspRender(dw.packed_layout, dw.packed_rom_main, dw.packed_rom_detail,
                        dw.packed_bbox_table, dw.MAP_CENTER_X, dw.MAP_CENTER_Y, dw.PRESCALE)
    cyc = [P.frame(R, pose, dw.player_floor(*pose[:2])) for pose in poses]
    for pose, c in zip(poses, cyc):
        print(f'  {pose}: {c:,} cycles')
    print(f'{len(poses)} poses, mean frame {sum(cyc) / len(cyc):,.0f} cycles '
          f'(min {min(cyc):,} max {max(cyc):,})')
    P.report(len(poses))


if __name__ == '__main__':
    main()
