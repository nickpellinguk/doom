# beebtune

Turns a MIDI file, a Doom MUS lump, or a tune inside a Doom WAD into a
BBC BASIC music player for the BBC Micro (three square-wave voices plus noise).

    python3 beebtune.py DOOM1.WAD --list                 # what tunes are in the WAD
    python3 beebtune.py DOOM1.WAD --lump D_E1M1 -o e1m1  # "At Doom's Gate"
    python3 beebtune.py song.mid -o song                 # any MIDI file

Needs Python 3 and nothing else. Each run writes two files:

| File | What it is | How to use it |
| --- | --- | --- |
| `NAME.ssd` | DFS disc image holding the player | Load it in an emulator such as jsbeeb or BeebEm (or on a real machine) and press SHIFT+BREAK, or type `CHAIN "TUNE"` |
| `NAME.bas` | The same program as a plain-text listing | Paste it into an emulator, then type `RUN` |

ESCAPE stops the tune and silences the chip.

## What the converter does

1. Reads the notes, and finds the rhythmic grid they sit on.
2. Shifts everything by whole octaves so it fits the chip, whose lowest note
   is B2 (about 124 Hz), folding any strays by an octave.
3. Arranges for three voices. Parts that double each other in unison are
   merged; when more than three notes sound together it keeps the lowest, the
   highest and the most recently struck.
4. Maps General MIDI drums to the noise voice: kick, snare, toms, hi-hat and
   cymbals each get a noise pitch and envelope.
5. Splits the tune into blocks, stores each distinct block once in a DATA
   line, and writes the BASIC player around them.

## Options

| Option | Effect |
| --- | --- |
| `--info` | Show the tune's channels and stop |
| `--transpose N` | Shift by N semitones instead of the automatic octave |
| `--channels 0,1,15` | Use only these channels |
| `--no-drums` | Leave out percussion |
| `--double` | Keep doubled parts on two voices when there is room (chorus effect) |
| `--tempo 1.1` | Play 10% faster |
| `--no-loop` | Play once instead of looping |
| `--title "..."` | Title shown on screen |
| `--grid-ms`, `--grid-div` | Override the detected timing grid |

## Changing the sound

The listing is meant to be edited:

- Line 50, `S%`: tempo. Bigger is slower.
- Lines 130 to 160: the four `ENVELOPE`s. 1 shapes every note; 2 to 4 are the
  drums.
- Line 510: the sixteen note lengths, in twentieths of a second.

## Limits

- Pitch bends are ignored.
- It is BASIC, so notes that should sound together start a few hundredths of a
  second apart.
- The chip's pitch gets coarse in its top octave, so very high notes can be
  slightly out of tune.

`mountain-king.ssd` and `mountain-king.bas` are a short public-domain example
(Grieg, "In the Hall of the Mountain King", 1875) made with this tool.

`e1m1.bas` / `e1m1.ssd` are DOOM's E1M1 tune, "At Doom's Gate", made with
`python3 beebtune.py DOOM1.WAD --lump D_E1M1 -o e1m1`. The Master engine disc
plays `e1m1.bas` through its own interrupt-driven player
(`master_music.py`, `src/master/mmusic.s`), which reads the listing's DATA
and ENVELOPE lines, so an edited or re-made `e1m1.bas` is what the disc
plays next time it is built.
