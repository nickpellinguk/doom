#!/usr/bin/env python3
"""Where each side of the Tube pipeline waits (docs/tube_master.md H4d).

Builds the Tube disc, finds the server's wait loops in its image, runs
tools/tube_waits.mjs on jsbeeb (the disc walking a fixed key plan from
the spawn for 30 s) and prints each processor's time a frame by class.

    python3 tools/tube_waits.py [mhz ...]      (default 3 4)
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ['DOOM_ASMDEFS'] = 'TUBE=1'
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
OUT = os.path.join(ROOT, 'build', 'master')


def labels():
    """The host's and the server's addresses the classes need."""
    import abi
    import symmap
    import tube_server
    code, L = tube_server.build()
    mem = bytearray(0x10000)
    p = 0
    for a, n in tube_server.LOADS:
        mem[a:a + n] = code[p:p + n]
        p += n

    def find(start, pat):
        for a in range(start, start + 400):
            if mem[a:a + len(pat)] == bytes(pat):
                return a
        raise SystemExit(f'tube_waits: {bytes(pat).hex()} not found after ${start:04X}')
    f = L['fs_frame']
    return dict(fs_frame=f,
                fs_wait=find(L['fs_main'], [0x20, f & 0xFF, f >> 8, 0x20]) + 3,  # JSR fs_frame; @wait
                fs_getcli=find(L['fs_get'], [0x58]),        # fs_get's CLI: the ring empty
                fs_getg=find(L['fs_get'], [0xB2]),          # @g: LDA (fs_rp)
                fs_irq=L['fs_irq'], fs_irq_end=L['fs_drop1'], fs_get=L['fs_get'],
                fs_poll=L['fs_poll'], render_frame=symmap.sym('render_frame'),
                hd_frame=symmap.sym('hd_frame'), flip_sched=symmap.sym('flip_sched'),
                DV_ANGIDX=abi.DV_ANGIDX)


def table(d):
    n, H, P, T, m = d['frames'], d['host'], d['para'], d['host_total'], d['mhz']
    print(f"{m:g}MHz: {n} frames in {d['fields'] / 50:.0f} s, {n / (d['fields'] / 50):.2f} fps, "
          f"{T / n / 2000:.0f} ms a frame; lists {P['bytes'] / n:.0f} B a frame")
    rows = (('engine + sending', H['render'] - H['sendwait']), ('  waiting to send', H['sendwait']),
            ('drawing', H['hd'] - H['listwait']), ('  waiting for list bytes', H['listwait']),
            ('flip', H['flip']), ('rest (driver, gun, HUD, IRQs)', T - H['render'] - H['hd'] - H['flip']))
    for k, v in rows:
        print(f'  host    {k:30s} {v / n / 1000:6.0f}K {v / n / 2000:5.0f} ms {v / T * 100:4.0f}%')
    pt = sum(v for k, v in P.items() if k != 'bytes')
    for k, name in (('work', 'the fill'), ('send', 'sending the list'), ('irq', 'taking requests (IRQ)'),
                    ('getwait', 'waiting for requests'), ('listwait', 'waiting for the host to take the list')):
        v = P[k]
        print(f'  second  {name:30s} {v / n / 1000:6.0f}K {v / n / (m * 1000):5.0f} ms {v / pt * 100:4.0f}%')


def main():
    mhzs = [float(a) for a in sys.argv[1:]] or [3, 4]
    subprocess.run([sys.executable, 'tools/build_master_ssd.py', '--tube'], check=True,
                   stdout=subprocess.DEVNULL)
    lab = os.path.join(OUT, 'tube_waits_labels.json')
    json.dump(labels(), open(lab, 'w'))
    for m in mhzs:
        out = os.path.join(OUT, f'tube_waits_{m:g}.json')
        subprocess.run(['node', 'tools/tube_waits.mjs', lab, os.path.join(OUT, 'doom_tube.ssd'),
                        str(m), out], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        table(json.load(open(out)))


if __name__ == '__main__':
    main()
