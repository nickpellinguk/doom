#!/usr/bin/env python3
"""
beebtune.py - turn a MIDI file, a Doom MUS lump, or a tune inside a Doom WAD
into a BBC BASIC music player for the BBC Micro.

The BBC Micro's sound chip (SN76489) has three square-wave voices and one
noise voice, so the tune is arranged down to three notes at a time plus a
drum line.  The result is written twice:

    NAME.bas   plain-text BBC BASIC listing (paste into an emulator)
    NAME.ssd   DFS disc image, boots with SHIFT+BREAK

Examples:

    python3 beebtune.py DOOM1.WAD --list
    python3 beebtune.py DOOM1.WAD --lump D_E1M1 -o e1m1
    python3 beebtune.py song.mid -o song --transpose 12

Needs only Python 3; no third-party packages.
"""

import argparse
import json
import math
import os
import struct
import sys

__version__ = "1.0"

# ---------------------------------------------------------------------------
# BBC Micro constants
# ---------------------------------------------------------------------------

# SOUND pitch is in quarter semitones.  The manual gives 53 as middle C, but
# the machine really plays about a third of a semitone sharp of that, so the
# player uses pitch = 4 * (midi - 47), which lands closest to concert pitch.
# MIDI 47 (B2) -> pitch 0 and MIDI 110 -> pitch 252.
LOWEST_MIDI = 47
HIGHEST_MIDI = 110

# One printable character carries six bits.  The player looks characters up
# in a table, so the alphabet is chosen only for being safe to type or paste.
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+-"
assert len(ALPHABET) == 64 and len(set(ALPHABET)) == 64

WAIT_CODE = 63          # first character of a "wait only" record
MAX_DELTA = 62          # biggest gap an ordinary event can carry

# Duration codes 0..15 -> SOUND duration in twentieths of a second.
# Code 0 silences the voice; code 15 holds until the next event on the voice.
DUR_TABLE = [1, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 255]
HOLD_CODE = 15
OFF_CODE = 0

MAX_BLOCK_CHARS = 225   # keeps every DATA line short enough to type in
FIRST_DATA_LINE = 1000  # block lines start here; the player finds them by number

# Memory on a Model B with DFS in MODE 7: PAGE=&1900, HIMEM=&7C00
MODEL_B_FREE = 0x7C00 - 0x1900

# ENVELOPE definitions used by the player (number, then the 13 parameters).
ENVELOPES = [
    (1, "1,0,0,0,0,0,0,126,-6,0,-12,126,96", "tone: sharp attack, sustain, quick release"),
    (2, "1,0,0,0,0,0,0,126,-16,-16,-16,126,0", "noise: short tick (kick, hi-hat)"),
    (3, "1,0,0,0,0,0,0,126,-7,-7,-7,126,0", "noise: medium (snare, toms)"),
    (4, "1,0,0,0,0,0,0,126,-3,-3,-3,126,0", "noise: long (cymbals)"),
]

# General MIDI percussion -> (priority, noise type, envelope index 0-2, duration code)
# Noise type: 4 = high white noise, 5 = medium, 6 = low.
DRUM_DEFAULT = (1, 4, 0, 1)
DRUM_MAP = {}
for _n in (35, 36):
    DRUM_MAP[_n] = (5, 6, 0, 2)          # kick
for _n in (37, 38, 39, 40):
    DRUM_MAP[_n] = (6, 5, 1, 3)          # snare, rimshot, clap
for _n in (41, 43, 45):
    DRUM_MAP[_n] = (4, 6, 1, 3)          # low toms
for _n in (47, 48, 50):
    DRUM_MAP[_n] = (4, 5, 1, 3)          # high toms
for _n in (49, 52, 55, 57):
    DRUM_MAP[_n] = (3, 4, 2, 9)          # crash, china, splash
DRUM_MAP[46] = (2, 4, 1, 3)              # open hi-hat
for _n in (42, 44, 51, 53, 59):
    DRUM_MAP[_n] = (1, 4, 0, 1)          # closed hat, ride


class ConvertError(Exception):
    pass


class Note:
    __slots__ = ("start", "end", "chan", "pitch", "vel", "drum",
                 "ks", "ke", "voice", "dead", "cut")

    def __init__(self, start, end, chan, pitch, vel, drum):
        self.start = start      # seconds
        self.end = end          # seconds
        self.chan = chan
        self.pitch = pitch      # MIDI note number
        self.vel = vel
        self.drum = drum
        self.ks = 0             # start, in grid steps
        self.ke = 0             # end, in grid steps
        self.voice = None
        self.dead = False
        self.cut = None


class Song:
    def __init__(self):
        self.notes = []
        self.length = 0.0       # seconds, loop length
        self.title = ""
        self.programs = {}      # channel -> program number
        self.source = ""
        self.bends = 0


# ---------------------------------------------------------------------------
# Input: WAD, MUS, MIDI
# ---------------------------------------------------------------------------

def wad_directory(data):
    if data[:4] not in (b"IWAD", b"PWAD"):
        raise ConvertError("not a WAD file")
    count, offset = struct.unpack_from("<ii", data, 4)
    lumps = []
    for i in range(count):
        pos, size, name = struct.unpack_from("<ii8s", data, offset + 16 * i)
        lumps.append((name.split(b"\0")[0].decode("ascii", "replace"), pos, size))
    return lumps


def wad_music_lumps(data):
    out = []
    for name, pos, size in wad_directory(data):
        head = data[pos:pos + 4]
        if head == b"MUS\x1a":
            out.append((name, pos, size, "MUS"))
        elif head == b"MThd":
            out.append((name, pos, size, "MIDI"))
    return out


def parse_mus(data, rate=140.0):
    """Doom MUS: events at a fixed tick rate (140 per second in Doom)."""
    if data[:4] != b"MUS\x1a":
        raise ConvertError("not a MUS lump")
    score_len, score_start = struct.unpack_from("<HH", data, 4)
    song = Song()
    pos = score_start
    tick = 0
    last_vol = [127] * 16
    chan_vol = [100] * 16
    open_notes = {}

    def close(chan, pitch, when):
        stack = open_notes.get((chan, pitch))
        if stack:
            start, vel = stack.pop(0)
            song.notes.append(Note(start / rate, when / rate, chan, pitch, vel, chan == 15))

    def close_channel(chan, when):
        for (c, p), stack in list(open_notes.items()):
            if c == chan:
                while stack:
                    close(c, p, when)

    while pos < len(data):
        head = data[pos]
        pos += 1
        last = head & 0x80
        kind = (head >> 4) & 7
        chan = head & 15
        if kind == 0:                                   # release note
            close(chan, data[pos] & 127, tick)
            pos += 1
        elif kind == 1:                                 # play note
            b = data[pos]
            pos += 1
            if b & 0x80:
                last_vol[chan] = data[pos] & 127
                pos += 1
            vel = last_vol[chan] * chan_vol[chan] // 127
            open_notes.setdefault((chan, b & 127), []).append((tick, vel))
        elif kind == 2:                                 # pitch wheel (ignored)
            song.bends += 1
            pos += 1
        elif kind == 3:                                 # system event
            if data[pos] in (10, 11):
                close_channel(chan, tick)
            pos += 1
        elif kind == 4:                                 # controller
            ctrl, value = data[pos], data[pos + 1]
            pos += 2
            if ctrl == 0:
                song.programs[chan] = value
            elif ctrl == 3:
                chan_vol[chan] = value & 127
        elif kind == 5:                                 # end of measure
            pass
        elif kind == 6:                                 # score end
            break
        else:
            pos += 1
        if last:
            delay = 0
            while True:
                b = data[pos]
                pos += 1
                delay = delay * 128 + (b & 127)
                if not b & 0x80:
                    break
            tick += delay
    for (c, p) in list(open_notes):
        while open_notes[(c, p)]:
            close(c, p, tick)
    song.length = tick / rate
    return song


def _varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def parse_midi(data):
    """Standard MIDI file, format 0 or 1."""
    if data[:4] == b"RIFF":                             # RMID wrapper
        i = data.find(b"MThd")
        if i < 0:
            raise ConvertError("RIFF file without MIDI data")
        data = data[i:]
    if data[:4] != b"MThd":
        raise ConvertError("not a MIDI file")
    hlen, fmt, ntracks, division = struct.unpack_from(">IHHH", data, 4)
    if fmt == 2:
        raise ConvertError("MIDI format 2 files are not supported")
    pos = 8 + hlen
    raw = []            # (tick, sequence, kind, chan, a, b)
    tempos = []         # (tick, microseconds per quarter note)
    seq = 0
    end_tick = 0
    song = Song()
    for track in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            break
        (tlen,) = struct.unpack_from(">I", data, pos + 4)
        p = pos + 8
        end = p + tlen
        pos = end
        tick = 0
        status = 0
        while p < end:
            delta, p = _varlen(data, p)
            tick += delta
            b = data[p]
            if b & 0x80:
                status = b
                p += 1
            if status == 0xFF:                          # meta event
                mtype = data[p]
                mlen, p = _varlen(data, p + 1)
                body = data[p:p + mlen]
                p += mlen
                if mtype == 0x51 and mlen == 3:
                    tempos.append((tick, int.from_bytes(body, "big")))
                elif mtype == 0x03 and not song.title:
                    song.title = body.decode("latin-1").strip()
                elif mtype == 0x2F:
                    break
            elif status in (0xF0, 0xF7):                # sysex
                slen, p = _varlen(data, p)
                p += slen
            else:
                kind = status & 0xF0
                chan = status & 0x0F
                if kind in (0xC0, 0xD0):
                    a, bb = data[p], 0
                    p += 1
                else:
                    a, bb = data[p], data[p + 1]
                    p += 2
                raw.append((tick, seq, kind, chan, a, bb))
                seq += 1
        end_tick = max(end_tick, tick)
    raw.sort()

    # tick -> seconds
    if division & 0x8000:
        fps = 256 - (division >> 8)
        per_second = fps * (division & 0xFF)

        def seconds(tick):
            return tick / per_second
    else:
        tempos.sort()
        if not tempos or tempos[0][0] != 0:
            tempos.insert(0, (0, 500000))
        marks = []                                       # (tick, seconds, usec per quarter)
        t = 0.0
        for i, (tk, us) in enumerate(tempos):
            if i:
                ptk, _, pus = marks[-1]
                t += (tk - ptk) * pus / 1e6 / division
            marks.append((tk, t, us))

        def seconds(tick):
            lo, hi = 0, len(marks) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if marks[mid][0] <= tick:
                    lo = mid
                else:
                    hi = mid - 1
            tk, t0, us = marks[lo]
            return t0 + (tick - tk) * us / 1e6 / division

    volume = [100] * 16
    expression = [127] * 16
    open_notes = {}

    def close(chan, pitch, tick):
        stack = open_notes.get((chan, pitch))
        if stack:
            start, vel = stack.pop(0)
            song.notes.append(Note(seconds(start), seconds(tick), chan, pitch, vel, chan == 9))

    for tick, _, kind, chan, a, b in raw:
        if kind == 0x90 and b > 0:
            vel = b * volume[chan] * expression[chan] // (127 * 127)
            open_notes.setdefault((chan, a), []).append((tick, vel))
        elif kind == 0x80 or kind == 0x90:
            close(chan, a, tick)
        elif kind == 0xB0:
            if a == 7:
                volume[chan] = b
            elif a == 11:
                expression[chan] = b
            elif a in (120, 123):
                for (c, p) in list(open_notes):
                    if c == chan:
                        while open_notes[(c, p)]:
                            close(c, p, tick)
        elif kind == 0xC0:
            song.programs.setdefault(chan, a)
        elif kind == 0xE0:
            song.bends += 1
    for (c, p) in list(open_notes):
        while open_notes[(c, p)]:
            close(c, p, end_tick)
    song.length = seconds(end_tick)
    return song


def load_song(path, lump=None, mus_rate=140.0):
    with open(path, "rb") as f:
        data = f.read()
    head = data[:4]
    base = os.path.basename(path)
    if head in (b"IWAD", b"PWAD"):
        music = wad_music_lumps(data)
        if not music:
            raise ConvertError("no music lumps found in this WAD")
        if lump is None:
            names = ", ".join(m[0] for m in music)
            raise ConvertError("this is a WAD: pick a tune with --lump NAME.\n"
                               "Music lumps: " + names)
        for name, pos, size, kind in music:
            if name.upper() == lump.upper():
                body = data[pos:pos + size]
                song = parse_mus(body, mus_rate) if kind == "MUS" else parse_midi(body)
                song.source = "%s lump %s" % (base, name)
                song.title = song.title or name
                return song
        raise ConvertError("lump %s not found (try --list)" % lump)
    if head == b"MUS\x1a":
        song = parse_mus(data, mus_rate)
    elif head in (b"MThd", b"RIFF"):
        song = parse_midi(data)
    else:
        raise ConvertError("unrecognised file type (expected MIDI, MUS or WAD)")
    song.source = base
    song.title = song.title or os.path.splitext(base)[0]
    return song


# ---------------------------------------------------------------------------
# Timing grid
# ---------------------------------------------------------------------------

def _cluster(times, window=0.025):
    """Merge onsets that are almost simultaneous (strums, conversion jitter)."""
    out = []
    for t in sorted(times):
        if not out or t - out[-1] > window:
            out.append(t)
    return out


def detect_grid(onsets):
    """Find the rhythmic step the tune is played on.

    Returns (step_seconds, phase_seconds, fit) or None if the notes do not sit
    on a regular grid.  fit is the fraction of onsets that land on the grid.
    """
    ts = _cluster(onsets)
    if len(ts) < 12:
        return None
    iois = [b - a for a, b in zip(ts, ts[1:])]

    def score(g):
        tol = min(0.016, 0.25 * g)
        hit = 0
        for d in iois:
            n = round(d / g)
            if n >= 1 and abs(d - n * g) <= tol:
                hit += 1
        return hit / len(iois)

    best = None
    g = 0.6
    while g > 0.03:
        if score(g) >= 0.95:
            best = g
            break
        g *= 0.998
    if best is None:
        return None
    # sharpen the estimate from the gaps themselves
    g = best
    for _ in range(4):
        num = den = 0.0
        tol = min(0.016, 0.25 * g)
        for d in iois:
            n = round(d / g)
            if n >= 1 and abs(d - n * g) <= tol and n <= 8:
                num += d * n
                den += n * n
        if den:
            g = num / den
    # then fit absolute positions, widening the window as the estimate firms up
    phase = ts[0]
    window = 16 * g
    span = ts[-1] - ts[0]
    while True:
        tol = min(0.016, 0.25 * g)
        pts = []
        for t in ts:
            if t - ts[0] > window:
                break
            k = round((t - phase) / g)
            if abs(t - (phase + k * g)) <= tol:
                pts.append((k, t))
        if len(pts) >= 4:
            n = len(pts)
            sk = sum(p[0] for p in pts)
            st = sum(p[1] for p in pts)
            skk = sum(p[0] * p[0] for p in pts)
            skt = sum(p[0] * p[1] for p in pts)
            den = n * skk - sk * sk
            if den:
                g = (n * skt - sk * st) / den
                phase = (st - g * sk) / n
        if window > span:
            break
        window *= 1.5
    tol = min(0.020, 0.3 * g)
    fit = sum(1 for t in ts if abs(t - (phase + round((t - phase) / g) * g)) <= tol) / len(ts)
    if fit < 0.93:
        return None
    # keep the phase within one step of zero so step numbers start near 0
    phase -= math.floor(phase / g + 0.5) * g
    return g, phase, fit


def refine_grid(notes, step, phase):
    """Split the grid step if fast notes (fills, runs) would land on top of each other."""
    by_chan = {}
    for n in notes:
        by_chan.setdefault(n.chan, []).append(n.start)
    onsets = {c: _cluster(ts) for c, ts in by_chan.items()}
    total = sum(len(ts) for ts in onsets.values())
    best = None
    for div in (1, 2, 3, 4, 6, 8):
        st = step / div
        if st < 0.015 and best is not None:
            break
        bad = 0
        for ts in onsets.values():
            prev = None
            for t in ts:
                k = round((t - phase) / st)
                if k == prev or abs(t - phase - k * st) > 0.022:
                    bad += 1
                prev = k
        if bad <= 0.002 * total:
            return div
        if best is None or bad < best[0]:
            best = (bad, div)
    return best[1]


# ---------------------------------------------------------------------------
# Arranging for three voices and noise
# ---------------------------------------------------------------------------

def choose_transpose(notes):
    """Whole-octave shift that puts the most notes inside the chip's range."""
    best = (None, 0)
    for shift in sorted(range(-48, 49, 12), key=abs):
        inside = sum(1 for n in notes if LOWEST_MIDI <= n.pitch + shift <= HIGHEST_MIDI)
        if best[0] is None or inside > best[0]:
            best = (inside, shift)
    return best[1]


def fold(pitch):
    while pitch < LOWEST_MIDI:
        pitch += 12
    while pitch > HIGHEST_MIDI:
        pitch -= 12
    return pitch


def merge_unisons(notes, keep_doubles=False):
    """Notes of the same pitch that overlap become one voice's worth of sound.

    Two instruments doubling a line would otherwise take a voice each.  A
    later onset of the same pitch still re-strikes the note.  With
    keep_doubles, only overlaps within one channel are merged, so a doubled
    part stays on two voices while there is room for it.
    """
    by_pitch = {}
    for n in sorted(notes, key=lambda n: (n.ks, -n.ke)):
        by_pitch.setdefault((n.pitch, n.chan) if keep_doubles else n.pitch, []).append(n)
    out = []
    for pitch, group in by_pitch.items():
        current = None
        for n in group:
            if current is not None and n.ks < current.ke:
                if n.ks == current.ks or n.start - current.start < 0.03:   # one strike, doubled
                    if n.ke > current.ke:
                        current.ke, current.end = n.ke, max(current.end, n.end)
                    current.vel = max(current.vel, n.vel)
                    continue
                reach_ke, reach_end = current.ke, current.end
                current.ke, current.end = n.ks, min(current.end, n.start)
                if reach_ke > n.ke:
                    n.ke, n.end = reach_ke, max(n.end, reach_end)
            out.append(n)
            current = n
    out.sort(key=lambda n: (n.ks, n.pitch))
    return out


def allocate_voices(notes):
    """Give each note one of three voices, dropping what will not fit.

    When more than three notes sound at once the lowest (bass) and highest
    (melody) are kept, then the most recently struck.
    """
    starts = {}
    for n in notes:
        starts.setdefault(n.ks, []).append(n)
    voices = [None, None, None]
    affinity = {}                                   # channel -> voice last used
    dropped = 0
    for k in sorted(starts):
        for v in range(3):
            if voices[v] is not None and voices[v].ke <= k:
                voices[v] = None
        playing = [n for n in voices if n is not None]
        fresh = starts[k]
        pool = playing + fresh
        if len(pool) > 3:
            # a second copy of a note already sounding is the first thing to go
            seen = {}
            for n in sorted(pool, key=lambda n: (n.voice is None, -n.vel)):
                if n.pitch in seen and len(pool) > 3:
                    pool.remove(n)
                    n.dead = True
                    dropped += 1
                    if n.voice is not None:
                        n.cut = k
                        voices[n.voice] = None
                else:
                    seen[n.pitch] = n
        if len(pool) > 3:
            keep = []
            low = min(pool, key=lambda n: n.pitch)
            high = max(pool, key=lambda n: n.pitch)
            keep.append(low)
            if high is not low:
                keep.append(high)
            rest = [n for n in pool if n is not low and n is not high]
            rest.sort(key=lambda n: (-n.ks, -n.vel, -n.pitch))
            keep.extend(rest[:3 - len(keep)])
            for n in pool:
                if n not in keep:
                    n.dead = True
                    dropped += 1
                    if n.voice is not None:
                        n.cut = k
                        voices[n.voice] = None
        for n in sorted(fresh, key=lambda n: n.pitch):
            if n.dead:
                continue
            free = [v for v in range(3) if voices[v] is None]
            want = affinity.get(n.chan)
            v = want if want in free else free[0]
            n.voice = v
            voices[v] = n
            affinity[n.chan] = v
    return dropped


def tone_events(notes, step_seconds, total_steps, loop):
    """Turn allocated notes into (step, order, voice, duration code, pitch index).

    step_seconds is the grid step as it will be played (after any tempo change).
    """
    events = []
    per_voice = {0: [], 1: [], 2: []}
    for n in notes:
        if n.voice is not None:
            per_voice[n.voice].append(n)
    for v, group in per_voice.items():
        group.sort(key=lambda n: n.ks)
        for i, n in enumerate(group):
            nxt = group[i + 1].ks if i + 1 < len(group) else None
            end_step = n.cut if n.cut is not None else n.ke
            if nxt is not None:
                end_step = min(end_step, nxt)
            # Lengths are taken from the grid, not the raw file, so that a bar
            # played twice comes out identical and is stored once.  Rounding
            # down leaves a small gap that lets repeated notes re-strike.
            length = (end_step - n.ks) * step_seconds
            twentieths = max(1, int(length * 20 + 0.1))
            if twentieths > DUR_TABLE[HOLD_CODE - 1]:
                code = HOLD_CODE
            else:
                code = min(range(1, HOLD_CODE), key=lambda c: (abs(DUR_TABLE[c] - twentieths), c))
            events.append((n.ks, 1 + v, v + 1, code, n.pitch - LOWEST_MIDI))
            if code == HOLD_CODE and (nxt is None or nxt > end_step):
                if end_step < total_steps:
                    events.append((end_step, 1 + v, v + 1, OFF_CODE, 0))
                elif loop and end_step - total_steps < group[0].ks:
                    # the note rings to the end of the tune: stop it as the loop restarts
                    events.append((end_step - total_steps, 1 + v, v + 1, OFF_CODE, 0))
    return events


def drum_events(notes, step_seconds):
    hits = {}
    for n in notes:
        prio, ntype, env, code = DRUM_MAP.get(n.pitch, DRUM_DEFAULT)
        best = hits.get(n.ks)
        if best is None or prio > best[0]:
            hits[n.ks] = (prio, ntype, env, code)
    events = []
    ring_prio, ring_until = 0, -1.0
    for k in sorted(hits):
        prio, ntype, env, code = hits[k]
        now = k * step_seconds
        if prio <= 2 and prio < ring_prio and now < ring_until:
            continue                                    # a hi-hat must not chop a snare or cymbal
        ring_prio, ring_until = prio, now + DUR_TABLE[code] * 0.05
        events.append((k, 0, 0, code, ntype + 8 * env))
    return events


# ---------------------------------------------------------------------------
# Packing into DATA lines
# ---------------------------------------------------------------------------

def encode_block(events, start):
    out = []
    prev = start
    for step, _, voice, code, param in events:
        delta = step - prev
        while delta > MAX_DELTA:
            n = min(delta, 63)
            out.append(ALPHABET[WAIT_CODE] + ALPHABET[n])
            delta -= n
        out.append(ALPHABET[delta] + ALPHABET[voice + 4 * code] + ALPHABET[param])
        prev = step
    if not out:
        out.append(ALPHABET[WAIT_CODE] + ALPHABET[0])
    return "".join(out)


def build_blocks(events, total_steps, block_steps):
    count = max(1, -(-total_steps // block_steps))
    buckets = [[] for _ in range(count)]
    for ev in events:
        buckets[ev[0] // block_steps].append(ev)
    unique = {}
    texts = []
    order = []
    for i, bucket in enumerate(buckets):
        text = encode_block(bucket, i * block_steps)
        if len(text) > MAX_BLOCK_CHARS:
            return None
        if text not in unique:
            unique[text] = len(texts)
            texts.append(text)
        order.append(unique[text])
    return texts, order


def best_blocks(events, total_steps):
    """Try every block length and keep whichever gives the smallest listing.

    Repeated bars only need storing once, so a block length that lines up
    with the music's own repeats wins.
    """
    best = None
    for block_steps in range(1, min(total_steps, 512) + 1):
        built = build_blocks(events, total_steps, block_steps)
        if built is None:
            continue
        texts, order = built
        cost = sum(len(t) + 10 for t in texts) + 8 * len(order)
        if best is None or cost < best[0]:
            best = (cost, block_steps, texts, order)
    if best is None:
        raise ConvertError("could not pack the tune into DATA lines")
    return best[1], best[2], best[3]


# ---------------------------------------------------------------------------
# BBC BASIC listing
# ---------------------------------------------------------------------------

def basic_listing(title, source, texts, order, block_steps, step_units, pad_steps, loop):
    title = "".join(c if 32 <= ord(c) < 127 and c != '"' else " " for c in title).strip()[:36]
    L = []
    add = L.append
    add('10 REM ' + (title or "Tune"))
    add('20 REM BBC Micro 3 voices + noise. Made by beebtune.py from ' + source[:40])
    add('30 MODE 7')
    add('40 ON ERROR PROCquiet:REPORT:PRINT " at line ";ERL:END')
    add('50 S%%=%d:REM grid step in 1/256 centiseconds. Bigger = slower' % step_units)
    add('60 K%%=%d:G%%=%d:Q%%=%d:R%%=%d*S%%' % (block_steps, len(texts), len(order), pad_steps))
    add('70 DIM T% 127,F% 127,M% 127,L% 127,N% 127,E% 127,Y% 127,V% 15,H%(G%),O%(Q%)')
    add('80 FOR I%=0 TO 127:T%?I%=64:NEXT')
    add('90 FOR I%=0 TO 15:READ C%:V%?I%=C%:NEXT')
    add('100 A$="' + ALPHABET + '"')
    add('110 FOR I%=0 TO 63:C%=ASC(MID$(A$,I%+1,1)):T%?C%=I%:F%?C%=16+(I% AND 3):M%?C%=-(I%>3)'
        ':L%?C%=V%?(I% DIV 4):N%?C%=I%*4:E%?C%=I% DIV 8+2:Y%?C%=I% AND 7:NEXT')
    add('120 FOR I%=0 TO Q%-1:READ O%(I%):NEXT')
    num = 130
    for env, params, what in ENVELOPES:
        add('%d ENVELOPE %d,%s:REM %s' % (num, env, params, what))
        num += 10
    add('170 P%=PAGE:I%=0')
    add('180 REPEAT:IF P%?1*256+P%?2>' + str(FIRST_DATA_LINE - 1) + ' THEN PROCline')
    add('190 P%=P%+P%?3:UNTIL P%?1>127')
    add('200 IF I%<>G% THEN PRINT "Tune data is incomplete":END')
    add('210 PRINT \'"' + (title or "Tune") + '"\'\'"Press ESCAPE to stop"')
    add('220 PROCquiet:J%=0:Z%=TIME*256+12800:PROCnext:U%=W% DIV 256:REPEAT UNTIL TIME>=U%')
    add('230 REPEAT')
    add('240 D%=T%?(?P%):IF D%>62 THEN PROCodd:UNTIL FALSE')
    add('250 IF D% THEN W%=W%+S%*D%:U%=W% DIV 256:REPEAT UNTIL TIME>=U%')
    add('260 A%=P%?1:B%=P%?2:P%=P%+3')
    add('270 IF F%?A%=16 THEN SOUND 16,E%?B%,Y%?B%,L%?A% ELSE SOUND F%?A%,M%?A%,N%?B%,L%?A%')
    add('280 UNTIL FALSE')
    add('290 END')
    add('300 DEF PROCline')
    add('310 C%=P%+3:REPEAT C%=C%+1:UNTIL ?C%>127')
    add('320 REPEAT C%=C%+1:UNTIL ?C%<>32:H%(I%)=C%:I%=I%+1')
    add('330 ENDPROC')
    add('340 DEF PROCodd')
    add('350 IF D%=63 THEN W%=W%+S%*T%?(P%?1):P%=P%+2 ELSE PROCnext')
    add('360 U%=W% DIV 256:REPEAT UNTIL TIME>=U%')
    add('370 ENDPROC')
    add('380 DEF PROCnext')
    if loop:
        add('390 IF J%=Q% THEN J%=0:Z%=Z%-R%')
    else:
        add('390 IF J%=Q% THEN U%=(Z%-R%) DIV 256:REPEAT UNTIL TIME>=U%:PROCquiet:END')
    add('400 P%=H%(O%(J%)):J%=J%+1:W%=Z%:Z%=Z%+K%*S%')
    add('410 ENDPROC')
    add('420 DEF PROCquiet')
    add('430 FOR I%=0 TO 3:SOUND 16+I%,0,4,1:NEXT')
    add('440 ENDPROC')
    add('500 REM Note lengths, in 1/20 second, for length codes 0 to 15')
    add('510 DATA ' + ",".join(str(d) for d in DUR_TABLE))
    add('520 REM Play order of the blocks below')
    num = 530
    for i in range(0, len(order), 40):
        add('%d DATA %s' % (num, ",".join(str(o) for o in order[i:i + 40])))
        num += 1
    if num > FIRST_DATA_LINE - 10:
        raise ConvertError("tune is too long: the play order does not fit")
    add('%d REM Tune blocks: 3 characters per event (wait, voice+length, note)' % (FIRST_DATA_LINE - 10))
    for i, text in enumerate(texts):
        add('%d DATA %s' % (FIRST_DATA_LINE + i, text))
    return L


# ---------------------------------------------------------------------------
# BBC BASIC tokeniser (enough of BASIC II for the listing above and more)
# ---------------------------------------------------------------------------

_KEYWORDS = [
    ("AND", 0x80, 0x00), ("ABS", 0x94, 0x00), ("ACS", 0x95, 0x00), ("ADVAL", 0x96, 0x00),
    ("ASC", 0x97, 0x00), ("ASN", 0x98, 0x00), ("ATN", 0x99, 0x00), ("AUTO", 0xC6, 0x10),
    ("BGET", 0x9A, 0x01), ("BPUT", 0xD5, 0x03), ("COLOUR", 0xFB, 0x02), ("CALL", 0xD6, 0x02),
    ("CHAIN", 0xD7, 0x02), ("CHR$", 0xBD, 0x00), ("CLEAR", 0xD8, 0x01), ("CLOSE", 0xD9, 0x03),
    ("CLG", 0xDA, 0x01), ("CLS", 0xDB, 0x01), ("COS", 0x9B, 0x00), ("COUNT", 0x9C, 0x01),
    ("DATA", 0xDC, 0x20), ("DEG", 0x9D, 0x00), ("DEF", 0xDD, 0x00), ("DELETE", 0xC7, 0x10),
    ("DIV", 0x81, 0x00), ("DIM", 0xDE, 0x02), ("DRAW", 0xDF, 0x02), ("ENDPROC", 0xE1, 0x01),
    ("END", 0xE0, 0x01), ("ENVELOPE", 0xE2, 0x02), ("ELSE", 0x8B, 0x14), ("EVAL", 0xA0, 0x00),
    ("ERL", 0x9E, 0x01), ("ERROR", 0x85, 0x04), ("EOF", 0xC5, 0x01), ("EOR", 0x82, 0x00),
    ("ERR", 0x9F, 0x01), ("EXP", 0xA1, 0x00), ("EXT", 0xA2, 0x01), ("FOR", 0xE3, 0x02),
    ("FALSE", 0xA3, 0x01), ("FN", 0xA4, 0x08), ("GOTO", 0xE5, 0x12), ("GET$", 0xBE, 0x00),
    ("GET", 0xA5, 0x00), ("GOSUB", 0xE4, 0x12), ("GCOL", 0xE6, 0x02), ("HIMEM", 0x93, 0x43),
    ("INPUT", 0xE8, 0x02), ("IF", 0xE7, 0x02), ("INKEY$", 0xBF, 0x00), ("INKEY", 0xA6, 0x00),
    ("INT", 0xA8, 0x00), ("INSTR(", 0xA7, 0x00), ("LIST", 0xC9, 0x10), ("LINE", 0x86, 0x00),
    ("LOAD", 0xC8, 0x02), ("LOMEM", 0x92, 0x43), ("LOCAL", 0xEA, 0x02), ("LEFT$(", 0xC0, 0x00),
    ("LEN", 0xA9, 0x00), ("LET", 0xE9, 0x04), ("LOG", 0xAB, 0x00), ("LN", 0xAA, 0x00),
    ("MID$(", 0xC1, 0x00), ("MODE", 0xEB, 0x02), ("MOD", 0x83, 0x00), ("MOVE", 0xEC, 0x02),
    ("NEXT", 0xED, 0x02), ("NEW", 0xCA, 0x01), ("NOT", 0xAC, 0x00), ("OLD", 0xCB, 0x01),
    ("ON", 0xEE, 0x02), ("OFF", 0x87, 0x00), ("OR", 0x84, 0x00), ("OPENIN", 0x8E, 0x00),
    ("OPENOUT", 0xAE, 0x00), ("OPENUP", 0xAD, 0x00), ("OSCLI", 0xFF, 0x02), ("PRINT", 0xF1, 0x02),
    ("PAGE", 0x90, 0x43), ("PTR", 0x8F, 0x43), ("PI", 0xAF, 0x01), ("PLOT", 0xF0, 0x02),
    ("POINT(", 0xB0, 0x00), ("PROC", 0xF2, 0x0A), ("POS", 0xB1, 0x01), ("RETURN", 0xF8, 0x01),
    ("REPEAT", 0xF5, 0x00), ("REPORT", 0xF6, 0x01), ("READ", 0xF3, 0x02), ("REM", 0xF4, 0x20),
    ("RUN", 0xF9, 0x01), ("RAD", 0xB2, 0x00), ("RESTORE", 0xF7, 0x12), ("RIGHT$(", 0xC2, 0x00),
    ("RND", 0xB3, 0x01), ("RENUMBER", 0xCC, 0x10), ("STEP", 0x88, 0x00), ("SAVE", 0xCD, 0x02),
    ("SGN", 0xB4, 0x00), ("SIN", 0xB5, 0x00), ("SQR", 0xB6, 0x00), ("SPC", 0x89, 0x00),
    ("STR$", 0xC3, 0x00), ("STRING$(", 0xC4, 0x00), ("SOUND", 0xD4, 0x02), ("STOP", 0xFA, 0x01),
    ("TAN", 0xB7, 0x00), ("THEN", 0x8C, 0x14), ("TO", 0xB8, 0x00), ("TAB(", 0x8A, 0x00),
    ("TRACE", 0xFC, 0x12), ("TIME", 0x91, 0x43), ("TRUE", 0xB9, 0x01), ("UNTIL", 0xFD, 0x02),
    ("USR", 0xBA, 0x00), ("VDU", 0xEF, 0x02), ("VAL", 0xBB, 0x00), ("VPOS", 0xBC, 0x01),
    ("WIDTH", 0xFE, 0x02),
]


def _is_alnum(c):
    return c.isalnum() and ord(c) < 128


def _encode_line_number(n):
    return bytes([0x8D,
                  (((n & 0xC0) >> 2) | ((n & 0xC000) >> 12)) ^ 0x54,
                  (n & 0x3F) | 0x40,
                  ((n >> 8) & 0x3F) | 0x40])


def tokenise_line(text):
    """Tokenise one line (without its line number), as BASIC does on entry."""
    out = bytearray()
    i = 0
    n = len(text)
    start = True            # at the start of a statement
    want_line = False       # a line number may follow (after GOTO, THEN, ...)
    while i < n:
        c = text[i]
        if c == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j + 1
            out += text[i:j].encode("latin-1")
            i = j
            start = want_line = False
            continue
        if c == " ":
            out.append(32)
            i += 1
            continue
        if c == ":":
            out.append(58)
            i += 1
            start, want_line = True, False
            continue
        if c == "," and want_line:
            out.append(44)
            i += 1
            continue
        if c == "*" and start:
            out += text[i:].encode("latin-1")
            break
        if c == "&":
            j = i + 1
            while j < n and text[j] in "0123456789ABCDEF":
                j += 1
            out += text[i:j].encode("latin-1")
            i = j
            start = want_line = False
            continue
        if c.isdigit() or c == ".":
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            if want_line and "." not in text[i:j] and int(text[i:j]) < 32768:
                out += _encode_line_number(int(text[i:j]))
            else:
                out += text[i:j].encode("latin-1")
                want_line = False
            i = j
            start = False
            continue
        if "A" <= c <= "Z":
            match = None
            for word, token, flags in _KEYWORDS:
                if text.startswith(word, i):
                    match = (word, token, flags)
                    break
            if match:
                word, token, flags = match
                j = i + len(word)
                if flags & 0x01 and j < n and _is_alnum(text[j]):
                    match = None
            if match:
                if flags & 0x40 and start:
                    token += 0x40
                out.append(token)
                i = j
                want_line = bool(flags & 0x10)
                if flags & 0x02:
                    start = False
                if flags & 0x04:
                    start = True
                if flags & 0x08:
                    while i < n and (_is_alnum(text[i]) or text[i] == "_"):
                        out.append(ord(text[i]))
                        i += 1
                if flags & 0x20:
                    out += text[i:].encode("latin-1")
                    break
                continue
        if _is_alnum(c) or c == "_":
            j = i
            while j < n and (_is_alnum(text[j]) or text[j] == "_"):
                j += 1
            out += text[i:j].encode("latin-1")
            i = j
            start = want_line = False
            continue
        out.append(ord(c) & 0xFF)
        i += 1
        start = want_line = False
    return bytes(out)


def tokenise_program(lines):
    out = bytearray()
    for line in lines:
        line = line.rstrip("\r\n")
        j = 0
        while j < len(line) and line[j].isdigit():
            j += 1
        number = int(line[:j])
        body = tokenise_line(line[j:].rstrip(" "))    # BASIC keeps the space after the number
        if len(body) > 251:
            raise ConvertError("line %d is too long for BASIC" % number)
        out += bytes([0x0D, number >> 8, number & 0xFF, len(body) + 4]) + body
    out += b"\x0D\xFF"
    return bytes(out)


# ---------------------------------------------------------------------------
# DFS disc image
# ---------------------------------------------------------------------------

def make_ssd(files, title="TUNE", boot_option=3):
    """files: list of (name, load address, exec address, data).  40-track SSD."""
    sectors = 400
    disc = bytearray(sectors * 256)
    title = (title.upper() + " " * 12)[:12].encode("ascii", "replace")
    disc[0:8] = title[:8]
    disc[0x100:0x104] = title[8:12]
    disc[0x104] = 0                                     # cycle count
    disc[0x105] = len(files) * 8
    disc[0x106] = (boot_option << 4) | (sectors >> 8)
    disc[0x107] = sectors & 0xFF
    sector = 2
    placed = []
    for name, load, exe, data in files:
        need = max(1, -(-len(data) // 256))
        if sector + need > sectors:
            raise ConvertError("tune is too big for the disc image")
        disc[sector * 256:sector * 256 + len(data)] = data
        placed.append((name, load, exe, len(data), sector))
        sector += need
    # the catalogue lists files from the highest start sector down
    for slot, (name, load, exe, length, start) in enumerate(reversed(placed)):
        base = 8 + slot * 8
        disc[base:base + 7] = (name + " " * 7)[:7].encode("ascii")
        disc[base + 7] = ord("$")
        info = 0x100 + base
        disc[info + 0] = load & 0xFF
        disc[info + 1] = (load >> 8) & 0xFF
        disc[info + 2] = exe & 0xFF
        disc[info + 3] = (exe >> 8) & 0xFF
        disc[info + 4] = length & 0xFF
        disc[info + 5] = (length >> 8) & 0xFF
        disc[info + 6] = (((exe >> 16) & 3) << 6) | (((length >> 16) & 3) << 4) | \
                         (((load >> 16) & 3) << 2) | ((start >> 8) & 3)
        disc[info + 7] = start & 0xFF
    return bytes(disc)


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

GM_FAMILIES = ["piano", "chromatic percussion", "organ", "guitar", "bass", "strings",
               "ensemble", "brass", "reed", "pipe", "synth lead", "synth pad",
               "synth effects", "ethnic", "percussive", "sound effects"]


def describe(song, out=sys.stdout):
    print("Source : %s" % song.source, file=out)
    print("Length : %.1f seconds, %d notes" % (song.length, len(song.notes)), file=out)
    chans = sorted(set(n.chan for n in song.notes))
    for c in chans:
        group = [n for n in song.notes if n.chan == c]
        if group[0].drum:
            what = "percussion"
        else:
            prog = song.programs.get(c)
            what = "program %d (%s)" % (prog, GM_FAMILIES[prog // 8]) if prog is not None and prog < 128 \
                else "melodic"
        print("  channel %2d: %4d notes, range %d-%d, %s" %
              (c, len(group), min(n.pitch for n in group), max(n.pitch for n in group), what), file=out)
    if song.bends:
        print("  (%d pitch-bend events will be ignored)" % song.bends, file=out)


def convert(song, args, log=print):
    notes = [n for n in song.notes if n.end > n.start or n.drum]
    if args.channels:
        wanted = set(args.channels)
        notes = [n for n in notes if n.chan in wanted]
    if args.no_drums:
        notes = [n for n in notes if not n.drum]
    if not notes:
        raise ConvertError("no notes to play")
    melodic = [n for n in notes if not n.drum]
    drums = [n for n in notes if n.drum]

    # ---- timing grid
    onsets = [n.start for n in notes]
    grid = None
    if args.grid_ms is None:
        grid = detect_grid(onsets)
        if grid:
            step, phase, fit = grid
            div = args.grid_div or refine_grid(notes, step, phase)
            step /= div
            log("Rhythm : %.1f ms grid (%.0f%% of notes land on it)" % (step * 1000, fit * 100))
        else:
            step, phase = 0.02, 0.0
            log("Rhythm : no regular grid found, using 20 ms steps (no bar compression)")
    else:
        if not 5 <= args.grid_ms <= 2000:
            raise ConvertError("--grid-ms should be between 5 and 2000")
        step, phase = args.grid_ms / 1000.0, 0.0
        log("Rhythm : %.1f ms grid (forced)" % (step * 1000))

    first = min(onsets)
    if args.keep_lead_in:
        origin = phase
    else:
        origin = phase + round((first - phase) / step) * step
    shift_error = 0.0
    for n in notes:
        n.ks = max(0, round((n.start - origin) / step))
        n.ke = n.ks + max(1, round((n.end - n.start) / step))
        shift_error = max(shift_error, abs(n.start - origin - n.ks * step))
    total_steps = max(1, round((song.length - origin) / step))
    total_steps = max(total_steps, max(n.ks for n in notes) + 1)
    log("         largest timing nudge %.0f ms" % (shift_error * 1000))

    # ---- pitch
    if melodic:
        shift = choose_transpose(melodic) if args.transpose is None else args.transpose
        folded = 0
        for n in melodic:
            p = n.pitch + shift
            q = fold(p)
            folded += p != q
            n.pitch = q
        log("Pitch  : transposed %+d semitones%s" %
            (shift, ", %d notes folded by an octave to fit" % folded if folded else ""))

    # ---- voices
    before = len(melodic)
    melodic = merge_unisons(melodic, args.double)
    allocate_voices(melodic)
    played = sum(1 for n in melodic if n.voice is not None)
    cut = sum(1 for n in melodic if n.cut is not None)
    log("Voices : %d melodic notes -> %d played (%d doubled unisons merged, %d left out and %d cut "
        "short for lack of voices)" % (before, played, before - len(melodic), len(melodic) - played, cut))

    speed = args.tempo
    if not 0.1 <= speed <= 10:
        raise ConvertError("--tempo should be between 0.1 and 10")
    events = tone_events(melodic, step / speed, total_steps, not args.no_loop)
    devents = drum_events(drums, step / speed)
    if drums:
        log("Drums  : %d hits -> %d played on the noise voice" % (len(drums), len(devents)))
    events += devents
    events.sort()

    # ---- pack
    block_steps, texts, order = best_blocks(events, total_steps)
    pad = len(order) * block_steps - total_steps
    step_units = max(1, round(step * 100 * 256 / speed))
    log("Blocks : %d events in %d blocks of %d steps, %d unique" %
        (len(events), len(order), block_steps, len(texts)))

    title = args.title or song.title
    lines = basic_listing(title, song.source, texts, order, block_steps, step_units, pad,
                          not args.no_loop)
    program = tokenise_program(lines)
    workspace = 7 * 128 + 16 + 4 * (len(texts) + 1) + 4 * (len(order) + 1) + 400
    need = len(program) + workspace
    log("Size   : %d bytes of BASIC + about %d bytes of workspace (%d free on a Model B)" %
        (len(program), workspace, MODEL_B_FREE))
    if need > MODEL_B_FREE - 512:
        log("WARNING: this will not fit on a 32K Model B. Try --no-drums or --channels.")

    expected = []
    for step_no, _, voice, code, param in events:
        expected.append({"cs": step_no * step_units / 256.0, "voice": voice,
                         "off": voice != 0 and code == OFF_CODE,
                         "pitch": (param * 4) if voice else None,
                         "noise": (param & 7) if not voice else None,
                         "env": (param // 8 + 2) if not voice else (0 if code == OFF_CODE else 1),
                         "dur": DUR_TABLE[code]})
    info = {"step_units": step_units, "block_steps": block_steps, "total_steps": total_steps,
            "loop_cs": total_steps * step_units / 256.0, "events": expected}
    return lines, program, info


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Turn a MIDI file or Doom MUS/WAD music into a BBC BASIC player for the BBC Micro.")
    ap.add_argument("input", help="a .mid file, a .mus file, or a Doom .wad")
    ap.add_argument("-o", "--output", help="output name without extension (default: from the input)")
    ap.add_argument("--lump", help="music lump to take from a WAD, e.g. D_E1M1")
    ap.add_argument("--list", action="store_true", help="list the music lumps in a WAD and stop")
    ap.add_argument("--info", action="store_true", help="describe the tune's channels and stop")
    ap.add_argument("--title", help="title shown on screen")
    ap.add_argument("--transpose", type=int, help="semitones to shift (default: best whole octave)")
    ap.add_argument("--channels", type=lambda s: [int(x) for x in s.split(",")],
                    help="only use these channels, e.g. 0,1,15")
    ap.add_argument("--no-drums", action="store_true", help="leave out percussion")
    ap.add_argument("--double", action="store_true",
                    help="keep doubled parts on two voices when there is room (chorus effect)")
    ap.add_argument("--tempo", type=float, default=1.0, help="speed multiplier (1.1 = 10%% faster)")
    ap.add_argument("--grid-ms", type=float, help="force the timing grid, in milliseconds")
    ap.add_argument("--grid-div", type=int, help="split the detected grid step by this (default: automatic)")
    ap.add_argument("--keep-lead-in", action="store_true", help="keep silence before the first note")
    ap.add_argument("--no-loop", action="store_true", help="play once instead of looping")
    ap.add_argument("--mus-rate", type=float, default=140.0, help="MUS ticks per second (Doom: 140)")
    ap.add_argument("--json", help="also write the event list here (for testing)")
    args = ap.parse_args(argv)

    try:
        if args.list:
            with open(args.input, "rb") as f:
                data = f.read()
            for name, pos, size, kind in wad_music_lumps(data):
                print("%-8s %-4s %6d bytes" % (name, kind, size))
            return 0
        song = load_song(args.input, args.lump, args.mus_rate)
        describe(song)
        if args.info:
            return 0
        lines, program, info = convert(song, args)
        stem = args.output
        if not stem:
            stem = (args.lump or os.path.splitext(os.path.basename(args.input))[0]).lower()
        if stem.lower().endswith((".bas", ".ssd")):
            stem = stem[:-4]
        with open(stem + ".bas", "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        boot = b'CHAIN "TUNE"\r'
        ssd = make_ssd([("!BOOT", 0, 0, boot), ("TUNE", 0xFF1900, 0xFF8023, program)],
                       title=(args.title or song.title or "TUNE"))
        with open(stem + ".ssd", "wb") as f:
            f.write(ssd)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(info, f)
        print("Wrote  : %s.bas (%d lines) and %s.ssd" % (stem, len(lines), stem))
        return 0
    except ConvertError as e:
        print("beebtune: %s" % e, file=sys.stderr)
        return 1
    except (OSError, struct.error, IndexError) as e:
        print("beebtune: could not read the input (%s)" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
