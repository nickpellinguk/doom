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
]

# the engine line-list gate's extra poses (test_master_engine): the old
# ground-truth verify positions
VERIFY = [(1792.34375, -3351.375, 108), (1056, -3616, 64), (1500, -3700, 0),
          (800, -3400, 96), (1056, -3328, 14), (1200, -3000, 129),
          (2112, -2368, 35), (1984, -2496, 67), (3648, -4800, 131),
          (-486, -3307, 243), (1301.5, -2586.09375, 0xB0)]
