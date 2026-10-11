#!/usr/bin/env python3
"""Gate (H6 step 3): the sprite model's display list draws its frame.

sprite_ref.SpriteDL renders the Tube frame with the billboards drawn last;
tube_dl.draw must make the same frame from the list alone (the SPRITE
records with the rest), and every row a record reads must be inside its art. Regression and
random poses.
Prints SPRITEDL: PASS / FAIL.
"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
pygame.init()
import poses, tube_fuzz, tube_dl, sprite_ref, sprite_art as A


def main():
    R = sprite_ref.SpriteDL()
    pl = list(poses.POSITIONS) + list(poses.VERIFY) + tube_fuzz.random_poses(60, 5)
    bad = n = 0
    for p in pl:
        fb = R.render(*p)
        n += len(R.sprites)
        ok = tube_dl.draw(R.dl, R.tex, R.W.tparams, R.flat) == fb
        for r in R.sprites:
            w, h = A.size(r['spr'])
            last = r['v0'] + ((r['y1'] & ~1) - (r['y0'] & ~1)) * r['step']
            ok &= 0 <= r['col'] < w and 0 <= r['v0'] and last >> 8 < h
        if not ok:
            bad += 1
            print('  mismatch at', p)
    print(f'  {len(pl)} poses, {n} sprite columns')
    print('SPRITEDL: FAIL' if bad else 'SPRITEDL: PASS')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
