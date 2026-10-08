#!/usr/bin/env python3
"""Preview the flats' far tones (step 7n) while choosing them.

    python3 tools/flat_far_preview.py                 # -> build/master/far_preview.png
    python3 tools/flat_far_preview.py --t 6           # try another FAR_T
    python3 tools/flat_far_preview.py --poses 0,3,7   # poses.POSITIONS indices

Rebuilds the assets from art/ first (so an edit to art/flats/far.txt
shows at once), then draws every flat beside its far tone, and the poses
through plane_ref: without the far tone, and with it. --t only previews:
the build keeps master_assets.FAR_T (change it there, then run
master_assets.py).
"""
import argparse, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import master_assets as M


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--t', type=float, default=None)
    ap.add_argument('--poses', default='0,3,7,12')
    ap.add_argument('--out', default=os.path.join(ROOT, 'build', 'master', 'far_preview.png'))
    a = ap.parse_args()
    out = os.path.join(ROOT, 'build', 'master')
    M.build(out)
    import pygame, numpy as np
    import plane_ref as P, poses as C, textured_ref as T
    pygame.init()
    font = pygame.font.SysFont('dejavusans', 13)
    pal = M.palette16()
    R = P.PlaneRef()
    dm = M.far_dm(t=a.t)
    flats = R.T.A.man['flats']

    Z = 6                                     # a full texel: Z*2 x Z (pixels 2:1)
    def put(s, x, y, b, w, h):                # a floor byte on its two lines:
        for k, v in enumerate((int(b), M.FLIP[int(b)])):      # FLIP on the odd
            l, r = M.mode2_pixels(v)
            s.fill(pal[l], (x, y + k * h // 2, w // 2, h // 2))
            s.fill(pal[r], (x + w // 2, y + k * h // 2, w - w // 2, h // 2))
    cols, fw = 4, 16 * Z * 2 * 2 + 40
    fh = 16 * Z + 24
    rows = (len(flats) + cols - 1) // cols
    views = [C.POSITIONS[int(i)] for i in a.poses.split(',')]
    VW, VH = 512, 272
    W = max(cols * fw, 2 * VW + 30) + 20
    H = rows * fh + len(views) * (VH + 24) + 40
    s = pygame.Surface((W, H))
    s.fill((40, 40, 48))
    for n, f in enumerate(flats):
        x0, y0 = 10 + (n % cols) * fw, 10 + (n // cols) * fh
        s.blit(font.render(f['name'], True, (230, 230, 230)), (x0, y0))
        full, far = R.flat[f['name']], R.far[f['name']]
        for r in range(16):
            for c in range(16):
                put(s, x0 + c * 2 * Z, y0 + 18 + r * Z, full[r, c], 2 * Z, Z)
                put(s, x0 + 16 * 2 * Z + 12 + c * 2 * Z, y0 + 18 + r * Z,
                    far, 2 * Z, Z)
    y = 10 + rows * fh + 10
    s.blit(font.render(f'without the far tone  |  with it (FAR_T = '
                       f'{a.t if a.t is not None else M.FAR_T}: D >= {dm} by k >> 1)',
                       True, (230, 230, 230)), (10, y))
    y += 20
    tmp = os.path.join(out, '_far_view.png')
    for pose in views:
        for k, d in enumerate(([255] * len(dm), dm)):
            R.dm = d
            T.to_png(R.render(*pose), tmp, M.PALETTE)
            s.blit(pygame.image.load(tmp), (10 + k * (VW + 10), y), (0, 0, VW, VH))
        y += VH + 24
    os.remove(tmp)
    pygame.image.save(s, a.out)
    print('wrote', os.path.relpath(a.out, ROOT))


if __name__ == '__main__':
    main()
