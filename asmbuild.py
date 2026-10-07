"""Engine build helper — the ONE way tests and tools rebuild the Master engine
link (src/engine_master.cfg, the 65C12). ca65 + ld65 (real objects, real
linker); fail-loud (raises on any assembler/linker error instead of silently
loading a stale .bin) and memoized against a fingerprint of src/.

Output binaries (engine_*_m.bin) land in the repo root; the map and debug
file in build/ (engine_m.map, engine_m.dbg).

DOOM_ASMDEFS="SYM=val,SYM2=val" adds ca65 defines for experiments; it is part
of the build key, so a variant never masquerades as the plain build.
"""
import os
import subprocess

_ROOT = os.path.dirname(os.path.abspath(__file__)) or '.'
_built = set()

# The engine is ONE link: the objects below resolved together, so cross-module
# calls are linker symbols. Link order = CODE layout: bsp_render FIRST (it
# carries the one .align $100 in CODE, so placed first it aligns for free at
# the region head and the others abut behind it).
_SOURCES = ['src/bsp_render.s', 'src/slope_div.s', 'src/span_clip.s',
            'src/drv/walk_drv.s', 'src/master/mfill.s']
CFG = 'src/engine_master.cfg'


def _run(argv):
    r = subprocess.run(argv, capture_output=True, text=True, cwd=_ROOT)
    if r.returncode != 0:
        raise RuntimeError(f'{argv[0]} failed:\n{r.stdout}{r.stderr}')
    return r.stdout + r.stderr


def _srcstamp():
    """Fingerprint of everything the engine link reads.

    Every src/ .s/.inc/.cfg file: an edited source always relinks.
    """
    import hashlib
    h = hashlib.sha1()
    roots = [os.path.join(_ROOT, 'src')]
    files = []
    for r in roots:
        for dp, _dn, fn in os.walk(r):
            files += [os.path.join(dp, f) for f in fn
                      if f.endswith(('.s', '.inc', '.cfg', '.asm'))]
    for f in sorted(files):
        try:
            st = os.stat(f)
        except OSError:
            continue
        h.update(f'{f}:{st.st_mtime_ns}:{st.st_size}|'.encode())
    return h.hexdigest()[:16]


def build(asm='engine', force=False, **_ignored):
    """Build the engine link. Raises RuntimeError on any tool error.
    (`asm` and any keyword arguments are accepted for older callers.)"""
    defs = os.environ.get('DOOM_ASMDEFS', '')
    dflags = []
    for d in filter(None, defs.split(',')):
        dflags += ['-D', d]
    key = ('engine', defs)
    _marker = os.path.join(_ROOT, 'build', 'engine_on_disk')
    _stamp = _srcstamp()
    try:
        _disk = open(_marker).read()
    except OSError:
        _disk = ''
    if key in _built and _disk == f'{defs},{_stamp}' and not force:
        return ''
    # refuse to build with unallocated ZP declarations (name = ?) pending —
    # run tools/zpcheck.py --alloc to assign them
    zp = open(os.path.join(_ROOT, 'src', 'zp.inc')).read()
    import re as _re
    m = _re.search(r'^\s*([A-Za-z_]\w*)\s*=\s*\?', zp, _re.M)
    if m:
        raise RuntimeError(f'unallocated ZP declaration {m.group(1)!r} in src/zp.inc '
                           f'— run: python3 tools/zpcheck.py --alloc')
    objdir = os.path.join(_ROOT, 'build')
    os.makedirs(objdir, exist_ok=True)
    # BUILD LOCK: parallel processes each relinked the shared outputs and
    # read each other's half-written files. One builder at a time; the
    # marker check repeats under the lock so the waiters skip the rebuild.
    import fcntl
    with open(os.path.join(objdir, '.build.lock'), 'w') as _lk:
        fcntl.flock(_lk, fcntl.LOCK_EX)
        try:
            _disk = open(_marker).read()
        except OSError:
            _disk = ''
        if _disk == f'{defs},{_stamp}' and not force:
            _built.add(key)
            return ''
        return _build_locked(dflags, key, objdir, _marker, defs, _stamp)


def _build_locked(dflags, key, objdir, _marker, defs, _stamp):
    text = ''
    objs = []
    for src in _SOURCES:
        name = os.path.basename(src).replace('.s', '')
        obj = os.path.join(objdir, f'{name}_m.o')
        text += _run(['ca65', '-g', '-D', 'ENGINE=1', '-D', 'C02=1',
                      '-D', 'BANKED=1', '-D', 'MASTER=1']
                     + dflags + ['-l', os.path.join(objdir, f'{name}_m.lst'),
                      os.path.join(_ROOT, src), '-o', obj])
        objs.append(obj)
    text += _run(['ld65', '-C', os.path.join(_ROOT, CFG)] + objs +
                 ['-m', os.path.join(objdir, 'engine_m.map'),
                  '--dbgfile', os.path.join(objdir, 'engine_m.dbg')])
    _built.add(key)
    with open(_marker, 'w') as _mf:
        _mf.write(f'{defs},{_stamp}')
    return text


