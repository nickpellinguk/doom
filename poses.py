"""The test poses (x, y, angle byte): the frame-cycle suite every Master gate
renders, and the engine's extra line-list poses."""

POSITIONS = [
    (1056, -3616, 0),
    (1056, -3616, 32),
    (1056, -3616, 64),
    (1056, -3616, 128),
    (1056, -3616, 192),
    (1056, -3616, 224),
    (1024, -3500, 64),
    (1500, -3700, 0),
    (800, -3400, 96),
    (1200, -3000, 128),
    # far-from-spawn but in-spec (player pos is s16 8.8: integer part must
    # fit s8 after prescale, i.e. within +/-1023 world units of MAP_CENTER)
    (2112, -2368, 35),
    (192, -2368, 99),
    (1984, -2496, 67),
    (1856, -2368, 3),
    # beyond the old +/-1023-unit box (s16 player int, 2026-07-06)
    (3648, -2368, 35),
    (2500, -2600, 67),
    (3648, -4800, 131),
    # FRACTIONAL walker-grid pose (engine 8.8: 004A.0B FFF3.54 ang 6C) —
    # the zp_ys_v1ok cull-leak reproducer (6509a23). Walker positions
    # live on this grid; integer poses never sample it.
    (1792.34375, -3351.375, 108),
    # close angled walls whose lower parts combed on the Master (step 7g,
    # 2026-10-07): the right strip's extrapolated step, times the distance
    # from the wall's top line, put the right pixel a row off the left
    (1046.7, -3090.4, 157),
    # a random start-area pose where the Master's 6502 left 20 bytes that
    # differ from tex_ref with no reference-gap seg (found 2026-10-07)
    (1144.6, -3342.5, 153),
    # right in front of the thin STARTAN3 wall before the slime pool: B - T
    # in the thousands, a step of a few units, so (ys - T) * step put the
    # rows a row or more off, differently per column (step 7s: the first
    # v of such a run is exact)
    (1300, -3232, 0),
    (1316, -3232, 0),
    # the same wall's face at an angle, its runs clipped at the top of the
    # view: the right strip's 5.3 delta, rounded at every character row,
    # drifted up to a texel by the bottom (step 7t: ddh keeps 8 fraction bits)
    (1337.5, -3193.0, 229),
    (1300, -3240, 208),     # close by the pillar: the south window's sill (2026-10-08 ledge fix)
]

# the engine line-list gate's extra poses (test_master_engine): the old
# ground-truth verify positions
VERIFY = [(1792.34375, -3351.375, 108), (1056, -3616, 64), (1500, -3700, 0),
          (800, -3400, 96), (1056, -3328, 14), (1200, -3000, 129),
          (2112, -2368, 35), (1984, -2496, 67), (3648, -4800, 131),
          (-486, -3307, 243), (1301.5, -2586.09375, 0xB0)]

# the Tube Master's largest (H4b, found by random poses): requests longer
# than the server's 4K ring, and a 5.5K display list
TUBE_BIG = [(-192.9, -4266.6, 24), (1073.7, -3380.6, 85)]

# the Tube Master's SLOWEST views (H4f, tools/tube_slow.py scan 500 1: the
# 20 worst of 528 by the slower side's time), slowest first; profiling and
# speed work are judged on these, not the mean
TUBE_SLOW = [
    (167.125, -4676.0, 40),
    (819.875, -4605.375, 52),
    (86.125, -4819.25, 22),
    (357.0, -4579.75, 47),
    (1191.0, -4737.5, 46),
    (792.625, -4718.125, 22),
    (263.25, -3745.125, 18),
    (1426.625, -4765.5, 23),
    (1690.625, -4836.75, 67),
    (1256.75, -4401.75, 82),
    (886.625, -2210.5, 189),
    (1826.625, -4727.875, 45),
    (1141.375, -4476.875, 66),
    (1571.875, -4498.75, 24),
    (216.0, -3640.125, 10),
    (902.625, -4040.75, 79),
    (133.875, -3851.0, 33),
    (2263.125, -4649.875, 82),
    (1046.7, -3090.4, 157),
    (1164.25, -4852.125, 10),
]
