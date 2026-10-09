#!/usr/bin/env python3
"""Tube Master step 2: the split frame's cost, per regression pose.

    python3 tools/tube_estimate.py

For each pose in baseline.json (the Master's measured cycles a frame, py65)
the display list (tube_dl.DLRef) is priced on each side:

  host      the drawing loops at the Master's own documented costs
            (docs/master_textured_spec.md 7c-7d, 5b): a wall line pair 69
            cycles with its own right row (+8 a character row), 52 shared;
            a plane byte 58 for both lines of a pair (the pair's two
            lines are one span when they match, as the Master draws them),
            35 for a line alone; a far-tone byte 8 a line; a fill line 8.
            Plus each record's set-up (WALL 60, SPAN 50, FILL 30), 10
            cycles a byte to read the list off the Tube (measured:
            tools/tube_bench.mjs), and 10K a frame for the rest (flip,
            gun, input).
  parasite  today's frame less the same drawing loops, plus emitting the
            list (12 of its cycles a byte), at 3 or 4 MHz.

Pipelined, a frame takes the longer side. Every figure but the Tube's is
an ESTIMATE until step 3 builds the host drawer.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import tube_dl

PAIR_OWN, PAIR_SHARED, ROW_DH = 69, 52, 8
SPAN_PAIR, SPAN_LINE, FAR_LINE, FILL_LINE = 58, 35, 8, 8
SET_WALL, SET_SPAN, SET_FILL, READ, FRAME = 60, 50, 30, 10, 10000
EMIT = 12


def price(dl):
    """(drawing loops, record set-up, pair-merged SPAN count)."""
    loops = setup = 0
    for r in dl:
        if r['kind'] == 'WALL':
            n = r['y1'] - r['y0'] + 1
            per = PAIR_SHARED if r['share'] else PAIR_OWN
            loops += n * per // 2
            if not r['share']:
                loops += ROW_DH * ((r['y1'] >> 3) - (r['y0'] >> 3))
            setup += SET_WALL
        elif r['kind'] == 'FILL':
            loops += FILL_LINE * (r['y1'] - r['y0'] + 1)
            setup += SET_FILL
    spans = {(r['y'], r['k0'], r['k1'], r.get('far'), r.get('flat'), r.get('u0'),
              r.get('v0'), r.get('du'), r.get('dv')): r for r in dl if r['kind'] == 'SPAN'}
    merged = 0
    for key, r in spans.items():
        n = r['k1'] - r['k0'] + 1
        if 'far' in r:
            loops += FAR_LINE * n
            setup += SET_SPAN
            continue
        mate = (key[0] ^ 1,) + key[1:]
        if mate in spans:                       # the pair's two lines match
            if key[0] & 1 == 0:
                loops += SPAN_PAIR * n
                setup += SET_SPAN
                merged += 1
        else:
            loops += SPAN_LINE * n
            setup += SET_SPAN
    return loops, setup, merged


def main():
    base = json.load(open('baseline.json'))['cycles']
    R = tube_dl.DLRef()
    rows = []
    for key, today in base.items():
        pose = tuple(float(v) if '.' in v else int(v) for v in key.split(','))
        R.render(*pose)
        nb = len(tube_dl.encode(R.dl, R.fid))
        loops, setup, merged = price(R.dl)
        host = loops + setup + READ * nb + FRAME
        para = today - loops + EMIT * nb
        rows.append((key, today, nb, loops, host, para))
    print(f'{"pose":24s} {"today":>9s} {"list B":>7s} {"loops":>8s} {"host":>8s} {"parasite":>9s}'
          f' {"ms 3MHz":>8s} {"ms 4MHz":>8s} {"x3":>5s} {"x4":>5s}')
    tot = [0] * 6
    for key, today, nb, loops, host, para in rows:
        t0 = today / 2000                       # ms at 2MHz
        t3 = max(host / 2000, para / 3000)
        t4 = max(host / 2000, para / 4000)
        print(f'{key:24s} {today:9,d} {nb:7d} {loops:8,d} {host:8,d} {para:9,d}'
              f' {t3:8.0f} {t4:8.0f} {t0 / t3:5.2f} {t0 / t4:5.2f}')
        for i, v in enumerate((today, nb, loops, host, para)):
            tot[i] += v
    n = len(rows)
    today, nb, loops, host, para = (v / n for v in tot[:5])
    t0, t3, t4 = today / 2000, max(host / 2000, para / 3000), max(host / 2000, para / 4000)
    print(f'{"mean":24s} {today:9,.0f} {nb:7.0f} {loops:8,.0f} {host:8,.0f} {para:9,.0f}'
          f' {t3:8.0f} {t4:8.0f} {t0 / t3:5.2f} {t0 / t4:5.2f}')
    print(f'today {t0:.0f} ms a frame ({1000 / t0:.1f} fps); split: {t3:.0f} ms '
          f'({1000 / t3:.1f} fps) with a 3MHz second processor, {t4:.0f} ms ({1000 / t4:.1f} fps) at 4MHz')


if __name__ == '__main__':
    main()
