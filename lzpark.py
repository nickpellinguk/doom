#!/usr/bin/env python3
"""The boot's parked blob (src/master/mboot.s): HAZEL, ANDY, the gun and
the panel, LZ-compressed so they fit in the half of the shadow screen the
loading picture leaves (shadow $5800-$7FFF).

The stream, a token at a time:
  $00-$7F   t + 1 literal bytes follow
  $80-$FE   a match: (t & $7F) + 3 bytes copied from dest - d, d the next
            two bytes (lo, hi; 1 <= d); the copy runs forwards, so d < the
            length repeats (a run)
  $FF       the stream's end
decode() is the spec the boot stub's lz routine (6502) follows.
"""
MAXLIT, MAXMATCH, MINMATCH = 128, 129, 3


def encode(data, window=0xFFFF):
    out, lit = bytearray(), bytearray()
    heads = {}
    i, n = 0, len(data)

    def flush():
        while lit:
            chunk = lit[:MAXLIT]
            out.append(len(chunk) - 1)
            out.extend(chunk)
            del lit[:MAXLIT]
    while i < n:
        best, bd = 0, 0
        if i + MINMATCH <= n:
            for j in reversed(heads.get(bytes(data[i:i + MINMATCH]), [])[-64:]):
                if i - j > window:
                    break
                k = 0
                while i + k < n and k < MAXMATCH and data[j + k] == data[i + k]:
                    k += 1
                if k > best:
                    best, bd = k, i - j
                    if k == MAXMATCH:
                        break
        if best >= MINMATCH:
            flush()
            out += bytes((0x80 | (best - MINMATCH), bd & 0xFF, bd >> 8))
            step = best
        else:
            lit.append(data[i])
            step = 1
        for p in range(i, min(i + step, n - MINMATCH + 1)):
            heads.setdefault(bytes(data[p:p + MINMATCH]), []).append(p)
        i += step
    flush()
    out.append(0xFF)
    return bytes(out)


def decode(stream):
    out, p = bytearray(), 0
    while True:
        t = stream[p]
        p += 1
        if t == 0xFF:
            return bytes(out)
        if t < 0x80:
            out += stream[p:p + t + 1]
            p += t + 1
        else:
            d = stream[p] | stream[p + 1] << 8
            p += 2
            for _ in range((t & 0x7F) + MINMATCH):
                out.append(out[-d])


def blob(parts, top=0x8000):
    """[(name, data, dest)] -> (blob bytes, its start page address, [(name,
    stream address, dest, length)]): the streams in order, the blob ending
    at top, its start page-aligned (the boot parks whole pages)."""
    streams = [(n, encode(d), dest, len(d)) for n, d, dest in parts]
    for n, s, _, ln in streams:
        assert decode(s)[:ln] == [d for nn, d, _ in parts if nn == n][0], n
    size = sum(len(s) for _, s, _, _ in streams)
    start = (top - size) & ~0xFF
    out, at, table = bytearray(), start + (top - start - size), []
    out += bytes(top - start - size)           # (page padding, in front)
    for n, s, dest, ln in streams:
        table.append((n, at, dest, ln))
        out += s
        at += len(s)
    return bytes(out), start, table
