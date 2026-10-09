#!/usr/bin/env python3
"""Host-led Tube Master: the fill REQUEST (docs/tube_master.md, H1).

The host keeps the engine -- the BSP walk, transform, clipping, movement,
the map -- and the drawing; the second processor runs the fill's set-up
and answers with the display list (tube_dl). What crosses from the host
is exactly what the Master's fill is called with: per frame the view, per
wall seg the call's arguments, its near-clip terms and the clip spans
around it before and after the seg's update. Everything else the fill
reads is static and lives on the second processor: the wall parts,
dressings and lengths by seg slot (master_walls), the segs' sector
heights and flats, the flats' far tones.

  ReqRef       the host: DLRef (so PlaneRef) rendering as before, and
               recording each frame's requests
  encode()     the requests as bytes (below)
  FillServer   the second processor: the same fill (DLRef's), driven by
               decoded requests alone -- no engine, no map

A request is made for a seg whose fill can draw: its columns [lo, hi)
not empty and some clip span over them (as the 6502's mf_fill returns
early otherwise).

Bytes, one frame:
  view      px88 (s24), py88 (s24), vz (s8), ab (u8)            8 B
  seg       $01, then:
            si | solid << 10 | c1 << 11 | c2 << 12 (u16)      2 B
            ss: the seg's subsector (the 6502 fill's flats and
            sky are by subsector)                               1 B
            lo, hi: the columns [lo, hi) clamped to 0..255      2 B
            sx1 sx2 ft1 ft2 fb1 fb2 (s16)                      12 B
            m1 s1 m2 s2 (u8): the ends' reciprocal terms         4 B
            t (u16, 0..256) when exactly one end is clipped:
            the near-plane crossing from the clipped end, as
            the engine leaves it in mf_xt                       2 B
            nb, nb spans; na, na spans: the pool's spans over
            [lo, hi) before and after the seg's update; a span
            is the pool's own fields, XSTART XEND TXLO TDEN TL BL
            TR BR BXLO BDEN (u8: line ends as start + delta)  1 + 10 B each
  end       $00
"""
import struct

import fill_ref as Fm
import tube_dl
from span_clip_6502 import Span


class ReqRef(tube_dl.DLRef):
    """DLRef recording, per frame, the view and each wall seg's request."""

    def __init__(self):
        super().__init__()
        self.ss_of = {}                         # seg slot -> its subsector
        for ss, (cnt, first) in enumerate(self.dw.fp_ssectors):
            for j in range(first, first + cnt):
                self.ss_of[j] = ss

    def render(self, px, py, ab):
        self.reqs = []
        return super().render(px, py, ab)

    def _fill(self, si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid):
        lo, hi = max(0, x_lo), min(255, x_hi)
        over = lambda spans: [s for s in spans if s[0] < hi and s[1] > lo]
        b = over(before)
        if lo < hi and b:
            near = self.near[si]
            self.reqs.append(dict(si=si, ss=self.ss_of[si], lo=lo, hi=hi,
                                  line=(sx1, sx2, ft1, ft2, fb1, fb2), solid=bool(solid),
                                  c1=bool(near[0]), c2=bool(near[1]), m=tuple(near[6:10]),
                                  t=self._cross_t(si) if bool(near[0]) != bool(near[1]) else 0,
                                  before=b, after=over(after)))
        return super()._fill(si, before, after, x_lo, x_hi, sx1, sx2, ft1, ft2, fb1, fb2, solid)

    def frame(self):
        v = self.view
        return dict(view=(v['px88'], v['py88'], v['vz'], v['ab']), reqs=self.reqs)


def _span_bytes(s):
    """A span as the clip pool holds it: XSTART XEND TXLO TDEN TL BL TR BR
    BXLO BDEN (the line ends as start + delta, mod 256: read_spans)."""
    xs, xe, tx0, tx1, tl, bl, tr, br = s
    bx0, bx1 = (s.bxlo, s.bxhi) if hasattr(s, 'bxlo') else (tx0, tx1)
    return bytes((xs, xe, tx0, (tx1 - tx0) & 0xFF, tl, bl, tr, br, bx0, (bx1 - bx0) & 0xFF))


def encode(frame):
    """One frame's requests as bytes."""
    px, py, vz, ab = frame['view']
    out = bytearray(px.to_bytes(3, 'little', signed=True) + py.to_bytes(3, 'little', signed=True)
                    + bytes((vz & 0xFF, ab)))
    for r in frame['reqs']:
        assert r['si'] < 1024
        out.append(0x01)
        out += struct.pack('<H', r['si'] | r['solid'] << 10 | r['c1'] << 11 | r['c2'] << 12)
        out += bytes((r['ss'], r['lo'], r['hi']))
        out += struct.pack('<6h', *r['line'])
        out += bytes(r['m'])
        if r['c1'] != r['c2']:
            out += struct.pack('<H', r['t'])
        for spans in (r['before'], r['after']):
            out.append(len(spans))
            for s in spans:
                out += _span_bytes(s)
    return bytes(out + b'\0')


def decode(b):
    """A frame's requests from their bytes."""
    px = int.from_bytes(b[0:3], 'little', signed=True)
    py = int.from_bytes(b[3:6], 'little', signed=True)
    vz = b[6] - 256 if b[6] & 0x80 else b[6]
    frame = dict(view=(px, py, vz, b[7]), reqs=[])
    p = 8
    while b[p]:
        assert b[p] == 0x01, f'request tag ${b[p]:02X}'
        w, = struct.unpack_from('<H', b, p + 1)
        si, solid, c1, c2 = w & 0x3FF, w >> 10 & 1, w >> 11 & 1, w >> 12 & 1
        ss, lo, hi = b[p + 3:p + 6]
        line = struct.unpack_from('<6h', b, p + 6)
        m = tuple(b[p + 18:p + 22])
        p += 22
        t = 0
        if c1 != c2:
            t, = struct.unpack_from('<H', b, p)
            p += 2
        spans = []
        for _ in range(2):
            k, p = b[p], p + 1
            lst = []
            for _ in range(k):
                xs, xe, tx0, tden, tl, bl, tr, br, bx0, bden = b[p:p + 10]
                s = Span((xs, xe, tx0, (tx0 + tden) & 0xFF, tl, bl, tr, br))
                s.bxlo, s.bxhi = bx0, (bx0 + bden) & 0xFF
                lst.append(s)
                p += 10
            spans.append(lst)
        frame['reqs'].append(dict(si=si, ss=ss, lo=lo, hi=hi, line=line, solid=bool(solid),
                                  c1=bool(c1), c2=bool(c2), m=m, t=t,
                                  before=spans[0], after=spans[1]))
    assert p + 1 == len(b), 'request bytes left over'
    return frame


class FillServer(tube_dl.DLRef):
    """The second processor: DLRef's fill and display list, served from
    requests. Its tables (walls, parts, flats, sectors' heights) are the
    static ones DLRef builds; it never walks the map."""

    def render(self, *a):
        raise RuntimeError('the fill server does not render: serve() requests')

    def _cross_t(self, si):
        return self.xt[si]                      # the request's t

    def serve(self, frame):
        """The display list (self.dl) and frame for one frame's requests."""
        px, py, vz, ab = frame['view']
        self.view = dict(px88=px, py88=py, vz=vz, ab=ab)
        self.grid = [[None] * Fm.STRIPS for _ in range(Fm.LINES)]
        self.owner = [[None] * Fm.STRIPS for _ in range(Fm.LINES)]
        self.near, self.geom, self.bands, self.rows = {}, {}, {}, {}
        self.walls, self.pcells = [], {}
        self.xt = {}
        for r in frame['reqs']:
            self.near[r['si']] = (r['c1'], r['c2'], 0, 0, 0, 0) + r['m']
            self.xt[r['si']] = r['t']
            self._fill(r['si'], r['before'], r['after'], r['lo'], r['hi'], *r['line'], r['solid'])
        self.dl = self._display_list()
        return self._compose()
