#!/usr/bin/env python3
"""The Master's music player (docs/master_textured_spec.md step 7ac): the
spec the 6502 (src/master/mmusic.s) reproduces chip byte for chip byte.

The tune is a beebtune BBC BASIC listing (music/e1m1.bas, made by
music/beebtune.py from DOOM1.WAD's D_E1M1): its DATA lines hold the note
lengths, the play order and the blocks of 3-character events (wait,
voice + length, note), and its ENVELOPE lines the amplitude shapes. parse()
reads all of that out of the listing, so a re-made or hand-edited .bas
plays without touching the code.

Timing is the BASIC player's, exactly: a block starts at Z (Z += K% * S%
per block, Z -= R% when the order wraps), each event's wait adds D * S% to
W (1/256 cs), and an event sounds once TIME >= W DIV 256 -- and never
before the event ahead of it. Here TIME is the 50 Hz raster IRQ: tick n is
at 2n cs, so an event is due at tick ceil((W DIV 256) / 2).

The packed tune (pack()):
  stream, bank 7 MUS_STREAM: the play order (Q bytes), the blocks' address
    lo / hi bytes (G each), then the blocks as token strings, each ending
    $FF: token < NP is an event (a dictionary entry), NP <= token < $FF
    adds that wait's D * S% to W
  dictionary + tables, in the resident player at MUS_ORG:
    mus_da[t] = len << 4 | (env - 1) << 2 | voice, mus_db[t] = a tone's
    period index or the noise control; per-wait W steps (3 bytes); tone
    periods; durations in cs; the envelopes.

The player (Player, the 6502's twin):
  refill(): the main loop, once a frame: copy tokens into the 256-byte ring
    (HAZEL MUS_RING) while it has room, a block's $FF then the next
    block's tokens in play order. 255 tokens are 8.6 s of the tune at its
    densest.
  tick(): the IRQ. Decode the ring: $FF starts a block (W = Z; Z += K% *
    S%; Z -= R% first when the order has wrapped), a wait adds to W, an
    event becomes a note (event()) and starts once due -- an event not yet
    due stays in the ring for a later tick. A silent note zeroes its
    channel. Then per channel 3, 2, 1, 0 two 1-cs envelope steps and the
    attenuation 15 - level / 8, written when it changes.

Envelope steps (MOS-like, amplitude section only; parse() asserts the
subset): the duration counts down first (reaching 0 -> release); then
attack adds AA up to ALA, decay adds AD (<= 0) down to ALD, sustain adds
AS (<= 0) to no lower than 0, release adds AR (< 0) to 0, then off.
"""
import os, re, subprocess

import abi

ROOT = os.path.dirname(os.path.abspath(__file__))
TUNE = os.path.join(ROOT, 'music', 'e1m1.bas')
SRC = os.path.join(ROOT, 'src', 'master')
OUT = os.path.join(ROOT, 'build', 'master')

STREAM_END = abi.COLIDX_BASE            # bank 7: the free planes end here
RES_SIZE = 0x500                        # $0300-$07FF
CHIP_BASE = (0xE0, 0xC0, 0xA0, 0x80)    # SOUND 0 (noise) .. 3 -> chip regs
OFF, ATTACK, DECAY, SUSTAIN, RELEASE = 4, 0, 1, 2, 3


class Tune:
    pass


def parse(path=TUNE):
    """The listing's numbers: S K G Q R, the alphabet, envelopes, lengths,
    order and block texts (each up to its line end)."""
    lines = {}
    for raw in open(path):
        m = re.match(r'\s*(\d+)\s?(.*)', raw.rstrip('\r\n'))
        if m:
            lines[int(m.group(1))] = m.group(2)
    t = Tune()
    text = '\n'.join(lines[k] for k in sorted(lines))
    t.S = int(re.search(r'\bS%=(\d+)', text).group(1))
    m = re.search(r'\bK%=(\d+):G%=(\d+):Q%=(\d+):R%=(\d+)\*S%', text)
    t.K, t.G, t.Q, t.R = (int(v) for v in m.groups())
    t.R *= t.S
    t.alpha = re.search(r'A\$="([^"]*)"', text).group(1)
    assert len(t.alpha) == 64
    t.loop = re.search(r'IF J%=Q% THEN J%=0:Z%=Z%-R%', text) is not None
    assert t.loop, 'a --no-loop tune: the player loops'
    t.env = {}
    for m in re.finditer(r'ENVELOPE\s*(\d+),([-\d,]+)', text):
        p = [int(v) for v in m.group(2).split(',')]
        assert len(p) == 13
        T, pitch, (AA, AD, AS, AR, ALA, ALD) = p[0], p[1:7], p[7:]
        assert T == 1 and not any(pitch), 'amplitude-only envelopes of 1 cs steps'
        assert 0 < AA and AD <= 0 and AS <= 0 and AR < 0, 'envelope shape'
        assert 0 <= ALD <= ALA <= 126
        t.env[int(m.group(1))] = (AA, AD, AS, AR, ALA, ALD)
    nums, t.blocks = [], []
    for k in sorted(lines):
        m = re.match(r'DATA\s*(.*)', lines[k])
        if not m:
            continue
        if k > 999:
            t.blocks.append(m.group(1))
        else:
            nums += [int(v) for v in m.group(1).split(',')]
    t.lengths, t.order = nums[:16], nums[16:16 + t.Q]
    assert len(t.order) == t.Q and len(t.blocks) == t.G
    return t


def _T(t, c):
    i = t.alpha.find(c)
    return i if i >= 0 else 64


def basic_events(t, n):
    """The BASIC player, line for line: the first n SOUND commands as
    (U cs, channel 16..19, amplitude, pitch, duration)."""
    out, J, Z = [], 0, 0
    W = blk = pos = None

    def nxt():
        nonlocal J, Z, W, blk, pos
        if J == t.Q:
            J, Z = 0, Z - t.R
        blk, pos = t.blocks[t.order[J]], 0
        J += 1
        W, Z = Z, Z + t.K * t.S

    nxt()
    while len(out) < n:
        ch = lambda i: blk[i] if i < len(blk) else '\r'
        D = _T(t, ch(pos))
        if D > 62:
            if D == 63:
                W += t.S * _T(t, ch(pos + 1))
                pos += 2
            else:
                nxt()
            continue
        W += t.S * D
        a, b = _T(t, ch(pos + 1)), _T(t, ch(pos + 2))
        pos += 3
        if a & 3 == 0:
            out.append((W >> 8, 16, b // 8 + 2, b & 7, t.lengths[a // 4]))
        else:
            out.append((W >> 8, 16 + (a & 3), int(a > 3), b * 4, t.lengths[a // 4]))
    return out


def period(i):
    """Tone period for note i (beebtune: MIDI 47 + i, B2 up), 4 MHz / 32."""
    f = 440.0 * 2 ** ((47 + i - 69) / 12)
    n = int(125000 / f + 0.5)
    assert 1 <= n <= 1023
    return n


def pack(t):
    """(stream bytes, tables dict): see the module docstring."""
    pairs, waits, toks = {}, {}, []
    for text in t.blocks:
        pos, out = 0, []
        while True:
            D = _T(t, text[pos]) if pos < len(text) else 64
            if D == 64:
                break
            if D == 63:
                d = _T(t, text[pos + 1])
                assert d < 64
                pos += 2
                if d:
                    out.append(('w', d))
                continue
            if D:
                out.append(('w', D))
            out.append(('e', (_T(t, text[pos + 1]), _T(t, text[pos + 2]))))
            pos += 3
        toks.append(out)
        for k, v in out:
            (pairs if k == 'e' else waits)[v] = (pairs if k == 'e' else waits).get(v, 0) + 1
    plist = sorted(pairs, key=lambda p: (-pairs[p], p))
    wlist = sorted(waits)
    NP, ND = len(plist), len(wlist)
    assert NP + ND < 0xFF, 'too many distinct events + waits for byte tokens'
    tones = sorted({b for a, b in plist if a & 3})
    da, db = [], []
    for a, b in plist:
        c, ln = a & 3, a >> 2
        e = b // 8 + 2 if c == 0 else 1
        assert e in t.env, f'envelope {e} is not defined'
        da.append(ln << 4 | (e - 1) << 2 | c)
        db.append(b & 7 if c == 0 else tones.index(b))
    body = []
    head = len(t.order) + 2 * t.G
    addrs = []
    for out in toks:
        addrs.append(abi.MUS_STREAM + head + len(body))
        for k, v in out:
            body.append(plist.index(v) if k == 'e' else NP + wlist.index(v))
        body.append(0xFF)
    stream = bytes(t.order + [a & 0xFF for a in addrs] + [a >> 8 for a in addrs] + body)
    assert abi.MUS_STREAM + len(stream) <= STREAM_END, \
        f'tune stream {len(stream)} B overruns bank 7 ${STREAM_END:04X}'
    wd = [t.S * d for d in wlist]
    assert max(wd) < 1 << 24
    durs = [0 if v == 255 else 5 * v for v in t.lengths]
    assert max(durs) < 256
    tab = dict(NP=NP, ND=ND, Q=t.Q, G=t.G, KS=t.K * t.S, R=t.R, da=da, db=db, wd=wd,
               per=[period(i) for i in tones], dur=durs,
               env={e: t.env.get(e, (1, 0, 0, -1, 0, 0)) for e in range(5)},
               order_at=abi.MUS_STREAM, offlo_at=abi.MUS_STREAM + t.Q,
               offhi_at=abi.MUS_STREAM + t.Q + t.G, nbody=len(body))
    return stream, tab


def event(tab, tok, w):
    """Dictionary entry tok at W: the note [due lo, hi, voice | env << 2,
    dur cs, b1, b2] (env 0 silent, dur 0 held; a noise note's b2 is 0)."""
    due = (((w >> 8) + 1) >> 1) & 0xFFFF
    a, b = tab['da'][tok], tab['db'][tok]
    c, ln = a & 3, a >> 4
    ctl = c if c and not ln else (a & 0x0F) + 4
    if c:
        n = tab['per'][b]
        b1, b2 = CHIP_BASE[c] | (n & 15), n >> 4
    else:
        b1, b2 = CHIP_BASE[0] | b, 0
    return [due & 0xFF, due >> 8, ctl, tab['dur'][ln], b1, b2]


class Player:
    """The 6502's state machine: refill (main loop) copies tokens into the
    ring, following the play order; tick (the IRQ) decodes them."""

    def __init__(self, stream, tab):
        self.s, self.t = stream, tab
        self.ring = [0] * 256
        self.rd = self.wr = 0
        self.jr = 0                         # refill: the order position
        self.p = None                       # refill: None = at a block end
        self.tick_n = 0
        self.j = 0                          # tick: blocks since the wrap
        self.w = self.z = 0
        self.lvl = [0] * 4
        self.ph = [OFF] * 4
        self.env = [0] * 4
        self.dur = [0] * 4
        self.att = [15] * 4

    def _byte(self, addr):
        return self.s[addr - abi.MUS_STREAM]

    def refill(self):
        t = self.t
        while (self.wr + 1) & 0xFF != self.rd:
            if self.p is None:
                tok = 0xFF
            else:
                tok = self._byte(self.p)
                self.p += 1
            self.ring[self.wr] = tok
            self.wr = (self.wr + 1) & 0xFF
            if tok == 0xFF:
                if self.jr == t['Q']:
                    self.jr = 0
                blk = self._byte(t['order_at'] + self.jr)
                self.jr += 1
                self.p = self._byte(t['offlo_at'] + blk) | self._byte(t['offhi_at'] + blk) << 8

    def _step(self, c):
        if self.dur[c]:
            self.dur[c] -= 1
            if not self.dur[c] and self.ph[c] < RELEASE:
                self.ph[c] = RELEASE
        AA, AD, AS, AR, ALA, ALD = self.t['env'][self.env[c]]
        ph, lv = self.ph[c], self.lvl[c]
        if ph == ATTACK:
            lv += AA
            if lv >= ALA:
                lv, self.ph[c] = ALA, DECAY
        elif ph == DECAY:
            if not AD:
                self.ph[c] = SUSTAIN
            else:
                lv += AD
                if lv <= ALD:
                    lv, self.ph[c] = ALD, SUSTAIN
        elif ph == SUSTAIN:
            if AS:
                lv = max(0, lv + AS)
        elif ph == RELEASE:
            lv += AR
            if lv <= 0:
                lv, self.ph[c] = 0, OFF
        self.lvl[c] = lv

    def tick(self):
        """One IRQ: the chip bytes written, in order."""
        t, out = self.t, []
        while self.rd != self.wr:
            tok = self.ring[self.rd]
            if tok == 0xFF:                 # a block starts (PROCnext)
                if self.j == t['Q']:
                    self.j, self.z = 0, (self.z - t['R']) & 0xFFFFFFFF
                self.j += 1
                self.w, self.z = self.z, (self.z + t['KS']) & 0xFFFFFFFF
            elif tok >= t['NP']:            # a wait
                self.w = (self.w + t['wd'][tok - t['NP']]) & 0xFFFFFFFF
            else:
                r = event(t, tok, self.w)
                if (self.tick_n - (r[0] | r[1] << 8)) & 0x8000:
                    break                   # not due: wait for it
                c, e = r[2] & 3, r[2] >> 2
                self.env[c] = e
                self.lvl[c] = 0
                if e:
                    self.ph[c], self.dur[c] = ATTACK, r[3]
                    out.append(r[4])
                    if c:
                        out.append(r[5])
                else:
                    self.ph[c] = OFF
            self.rd = (self.rd + 1) & 0xFF
        for c in (3, 2, 1, 0):
            if self.ph[c] != OFF:
                self._step(c)
                self._step(c)
            a = 15 - (self.lvl[c] >> 3)
            if a != self.att[c]:
                self.att[c] = a
                out.append(CHIP_BASE[c] | 0x10 | a)
        self.tick_n = (self.tick_n + 1) & 0xFFFF
        return out


def notes(stream, tab, n):
    """The first n notes the stream decodes to, as event() gives them."""
    p, out = Player(stream, tab), []
    while len(out) < n:
        p.refill()
        while p.rd != p.wr:
            tok = p.ring[p.rd]
            p.rd = (p.rd + 1) & 0xFF
            if tok == 0xFF:
                if p.j == tab['Q']:
                    p.j, p.z = 0, p.z - tab['R']
                p.j += 1
                p.w, p.z = p.z, p.z + tab['KS']
            elif tok >= tab['NP']:
                p.w += tab['wd'][tok - tab['NP']]
            else:
                out.append(event(tab, tok, p.w))
    return out[:n]


def source(tab):
    """The generated include for mmusic.s."""
    def rows(name, data):
        return [f'{name}:'] + ['\t.byte ' + ','.join(f'${v & 0xFF:02X}' for v in data[i:i + 16])
                               for i in range(0, len(data), 16)]
    s = ['; GENERATED by master_music.py from music/e1m1.bas -- do not edit',
         f'MUS_NP = {tab["NP"]}', f'MUS_Q = {tab["Q"]}',
         f'MUS_KS = {tab["KS"]}', f'MUS_R = {tab["R"]}',
         f'MUS_ORDER = ${tab["order_at"]:04X}', f'MUS_OFFLO = ${tab["offlo_at"]:04X}',
         f'MUS_OFFHI = ${tab["offhi_at"]:04X}', '.macro MUS_TABLES']
    s += rows('mus_da', tab['da']) + rows('mus_db', tab['db'])
    for k in range(3):
        s += rows(f'mus_wd{k}', [v >> 8 * k for v in tab['wd']])
    s += rows('mus_plo', [n & 15 for n in tab['per']]) + rows('mus_phi', [n >> 4 for n in tab['per']])
    s += rows('mus_dur', tab['dur'])
    for k, nm in enumerate(('aa', 'ad', 'as', 'ar', 'ala', 'ald')):
        s += rows(f'e_{nm}', [tab['env'][e][k] for e in range(5)])
    s.append('.endmacro')
    return '\n'.join(s) + '\n'


def build(path=TUNE):
    """Assemble the resident player: (resident bytes, stream bytes, tables,
    labels {name: address})."""
    os.makedirs(OUT, exist_ok=True)
    stream, tab = pack(parse(path))
    open(os.path.join(OUT, 'mmusic_tab.inc'), 'w').write(source(tab))
    obj, binp, lab = (os.path.join(OUT, 'mmusic' + x) for x in ('.o', '.bin', '.lab'))
    subprocess.run(['ca65', '-g', '--cpu', '65C02', '-I', OUT, '-I', SRC, '-I', os.path.join(ROOT, 'src'),
                    os.path.join(SRC, 'mmusic.s'), '-o', obj], check=True)
    subprocess.run(['ld65', '-C', os.path.join(SRC, 'mmusic.cfg'), obj, '-o', binp, '-Ln', lab],
                   check=True)
    res = open(binp, 'rb').read()
    assert len(res) <= RES_SIZE, f'resident player {len(res)} B overruns $0300-$07FF'
    labels = {}
    for l in open(lab):
        m = re.match(r'al ([0-9A-F]+) \.(\w+)', l)
        if m:
            labels[m.group(2)] = int(m.group(1), 16)
    return res, stream, tab, labels


if __name__ == '__main__':
    res, stream, tab, _ = build()
    print(f'resident {len(res)} B at ${abi.MUS_ORG:04X} (staged bank 5 ${abi.MUS_STAGE:04X}); '
          f'stream {len(stream)} B at bank 7 ${abi.MUS_STREAM:04X}-${abi.MUS_STREAM + len(stream) - 1:04X}; '
          f'{tab["NP"]} events, {tab["ND"]} waits, {len(tab["per"])} tones')
