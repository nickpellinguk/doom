"""Shared loader for the Master engine binaries into a py65 memory.

One definition of "where things go": load addresses are parsed from the SAME
ld65 config the linker places code with (they cannot drift).
"""
import os
import re

import asmbuild
from symmap import sym

_ROOT = asmbuild._ROOT


def _regions():
    """Parse MEMORY areas from the engine ld65 config: [(start, file)]."""
    cfg = open(os.path.join(_ROOT, asmbuild.CFG)).read()
    # BRACE-MATCHED, not cfg.index('SEGMENTS') (2026-09-05): the word
    # SEGMENTS inside a MEMORY comment truncated the block and silently
    # dropped every area declared after it.
    i = cfg.index('MEMORY')
    j = cfg.index('{', i) + 1
    depth = 1
    while depth:
        depth += 1 if cfg[j] == '{' else -1 if cfg[j] == '}' else 0
        j += 1
    mem = cfg[i:j]
    out = []
    for m in re.finditer(r'start\s*=\s*\$([0-9A-Fa-f]+)[^;]*?file\s*=\s*"([^"]+)"', mem):
        out.append((int(m.group(1), 16), m.group(2)))
    return out


def load_engine(mem):
    """Build the engine and load every output region into py65 memory.
    Regions sharing an output file concatenate in declaration order."""
    asmbuild.build('engine')
    loaded = set()
    for start, fname in _regions():
        if fname in loaded or fname == 'engine_gun_m.bin':
            continue                     # later areas append to the same file;
                                         # the gun body is shadow RAM's
        loaded.add(fname)
        code = open(os.path.join(_ROOT, fname), 'rb').read()
        mem[start:start + len(code)] = code


def angle_table_contract():
    """The angle tables' seed-time contract (tools/atanexp_cert.py is the one
    source of the option-F tables; the engine bakes these facts)."""
    import angle_bbox as A
    assert A.EPSILON_F == 12, 'EPSILON drifted from the baked bca_tail bias (EPSILON_F equate, ang/header_div.s)'
    assert A._TA0 == 0, 'TA0 drifted from the baked num==0 arm'
    assert A._ATANEXP[0] == 512, \
        'AE[0] must be 512: lf_ns ties ride k=0 with no fallback compare'
    assert max(A._ATANEXP) <= 512, \
        'ta > 512 would overflow comb\'s unmasked add arm (hi > $0F)'
    # bca_tail bakes the VATOX ends as constants (clamp/==1024 arms skip
    # the lookup): VATOX[0] = 0, VATOX[1024] = 255
    v = lambda k: max(0, min(255, (A._vatox_lo[k + 512] + A._vatox_hi[k + 512]) // 2))
    assert v(0) == 0 and v(1024) == 255, \
        'VATOX ends drifted from bca_tail baked constants (0/255)'
