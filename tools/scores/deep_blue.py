"""deep_blue - composed 16-bar score for Deep Blue (draft 1).

Brief (the user's Suno prompt): bright coastal harbour, upbeat
jazz-fusion / light funk-pop, 120-130 BPM, tight syncopated slap-ish
bass, crisp drums, EP stabs, bright synth brass, marimba/bell accents,
flute phrases, colourful jazzy harmony WITH CHROMATIC PASSING MOVEMENT,
clean digital reverb (a light touch of DSP echo - song.json).

Key: C major, 124 BPM. Form:
  A (1-8): Cmaj9 Cmaj9 Fmaj9 Fmaj9 Em7 A9 Dm9 G13   (A9 = V/ii colour)
  B (9-16): Fmaj9 Em7 EBMAJ9 Dm9 - the chromatic descent (the bass
  walks F E Eb D; Em7 and Ebmaj9 share shell voicings so the EP glides
  while the bass and melody plane down in semitones) - then
  Fmaj9 Em7 Dm9 G13 turns it around.
BRASS is the call-and-response partner (answers bars 4/8/16); bells
sprinkle. Flute slides ride touching notes as usual.

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/deep_blue.py
"""
import os

import mido

BPM = 124
TPB = 480
TPR = TPB // 4
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "deep_blue")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    return 12 * (int(s[-1]) + 1) + NAMES[s[:-1]]


CHORDS = {
    "Cmaj9":  ("C2", ("E4", "B4")),
    "Am9":    ("A2", ("C4", "G4")),
    "Fmaj9":  ("F2", ("E4", "A4")),
    "Em7":    ("E2", ("G4", "D5")),
    "Ebmaj9": ("Eb2", ("G4", "D5")),   # same shell: the BASS is the move
    "A9":     ("A2", ("C#4", "G4")),
    "Dm9":    ("D2", ("F4", "C5")),
    "G13":    ("G2", ("F4", "B4")),
}
PROG = ["Cmaj9", "Cmaj9", "Fmaj9", "Fmaj9", "Em7", "A9", "Dm9", "G13",
        "Fmaj9", "Em7", "Ebmaj9", "Dm9", "Fmaj9", "Em7", "Dm9", "G13"]
BARS = len(PROG)

drums, bass, keys, brass, flute, bells = [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: crisp and forward ------------------------------------------
for b in range(BARS):
    fill = b in (7, 15)
    for r, v in ((0, 104), (6, 84), (10, 96)):
        drums.append((at(b, r), 1, 36, v))
    drums.append((at(b, 4), 1, 38, 104))
    if not fill:
        drums.append((at(b, 12), 1, 38, 104))
        if b % 4 == 3:
            drums.append((at(b, 15), 1, 38, 32))
    else:
        for i, r in enumerate((12, 13, 14, 15)):
            drums.append((at(b, r), 1, 38, 66 + i * 12))
    for r in range(16):
        if fill and r >= 12:
            continue
        drums.append((at(b, r), 1, 42, 80 if r % 4 == 0 else
                      (60 if r % 2 == 0 else 42)))

# ---- bass: tight slap-flavoured riff, octave pops ----------------------
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    bass += [(at(b, 0), 2, root, 98),
             (at(b, 3), 1, root + 12, 86),         # octave pop
             (at(b, 6), 2, root + 7, 82),
             (at(b, 8), 2, root, 92),
             (at(b, 10), 1, root + 12, 84),
             (at(b, 12), 1, root + 10, 76)]        # b7 grit
    if nxt != root:
        bass.append((at(b, 14), 2, nxt - 1 if nxt > root else nxt + 1, 78))
    else:
        bass.append((at(b, 14), 2, root + 7, 72))

# ---- keys: rhythmic EP stabs -------------------------------------------
for b in range(BARS):
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    nlo, nhi = (n(p) for p in CHORDS[PROG[(b + 1) % BARS]][1])
    for r, ln, v in ((3, 2, 68), (6, 2, 72), (10, 2, 64)):
        keys += [(at(b, r), ln, lo, v), (at(b, r), ln, hi, v)]
    if b % 2 == 1:
        keys += [(at(b, 15), 1, nlo, 52), (at(b, 15), 1, nhi, 52)]

# ---- brass: the answer voice -------------------------------------------
brass += [(at(3, 8), 1, n("F4"), 92), (at(3, 10), 1, n("G4"), 92),
          (at(3, 11), 3, n("A4"), 100)]
brass += [(at(7, 8), 1, n("B4"), 92), (at(7, 9), 1, n("B4"), 84),
          (at(7, 11), 2, n("C5"), 96), (at(7, 14), 2, n("D5"), 88)]
for i, (r, p) in enumerate(((8, "D4"), (10, "E4"), (12, "F4"),
                            (14, "G4"))):
    brass.append((at(15, r), 2, n(p), 84 + i * 5))

# ---- flute: catchy hook; B-half planes down with the harmony ----------
F = [
    (0, 0, 2, "E5", 88), (0, 3, 2, "G5", 86), (0, 6, 2, "A5", 90),
    (0, 8, 6, "G5", 90),
    (1, 4, 2, "E5", 82), (1, 6, 2, "D5", 80), (1, 8, 6, "C5", 84),
    (2, 2, 2, "C5", 80), (2, 4, 2, "D5", 82), (2, 6, 2, "E5", 84),
    (2, 8, 6, "E5", 86),
    (3, 2, 4, "D5", 80),                    # ...brass answers
    (4, 0, 2, "B4", 82), (4, 3, 2, "D5", 82), (4, 6, 2, "E5", 84),
    (4, 8, 4, "D5", 84),
    (5, 2, 2, "C#5", 84), (5, 4, 4, "E5", 86), (5, 10, 4, "G5", 88),
    (6, 0, 2, "F5", 86), (6, 3, 2, "E5", 82), (6, 6, 2, "D5", 80),
    (6, 8, 6, "C5", 82),
    (7, 2, 2, "B4", 78),                    # ...brass + bells answer
    (8, 0, 4, "A5", 92), (8, 6, 2, "G5", 86), (8, 8, 6, "F5", 88),
    (9, 2, 4, "G5", 86), (9, 8, 4, "E5", 84),
    (10, 2, 4, "F5", 84), (10, 8, 4, "D5", 82),   # Ebmaj9: planing down
    (11, 2, 4, "E5", 82), (11, 8, 6, "C5", 80),
    (12, 0, 2, "C5", 82), (12, 3, 2, "E5", 84), (12, 6, 2, "F5", 86),
    (12, 8, 4, "A5", 90),
    (13, 2, 4, "D5", 80), (13, 8, 4, "B4", 76),
    (14, 0, 2, "D5", 82), (14, 3, 2, "E5", 84), (14, 6, 2, "F5", 84),
    (14, 8, 4, "E5", 86),
    # bar 16: brass walk-up + bells own the turnaround
]
for bar, r, ln, p, v in F:
    flute.append((at(bar, r), ln, n(p), v))

# ---- bells: sprinkle in the gaps ---------------------------------------
B = [
    (7, 6, 2, "G5", 58),
    (11, 14, 2, "G5", 56),
    (15, 0, 2, "G5", 58), (15, 2, 2, "A5", 60), (15, 4, 2, "G5", 58),
    (15, 6, 2, "E5", 56),
]
for bar, r, ln, p, v in B:
    bells.append((at(bar, r), ln, n(p), v))

# --------------------------------------------------------------- emit
mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
meta = mido.MidiTrack()
meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM)))
meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
mid.tracks.append(meta)


def emit(name, events, program, channel):
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=name))
    if program is not None:
        tr.append(mido.Message("program_change", program=program,
                               channel=channel))
    msgs = []
    for row, ln, note, vel in events:
        msgs.append((row * TPR, 1, mido.Message(
            "note_on", note=note, velocity=vel, channel=channel)))
        msgs.append(((row + ln) * TPR - 4, 0, mido.Message(
            "note_off", note=note, velocity=0, channel=channel)))
    msgs.sort(key=lambda m: (m[0], m[1]))
    t = 0
    for att, _, m in msgs:
        m.time = att - t
        t = att
        tr.append(m)
    mid.tracks.append(tr)
    print("  %-6s %3d notes" % (name, len(events)))


emit("Bass", bass, 36, 1)    # slap bass
emit("Keys", keys, 4, 2)     # EP 1
emit("Brass", brass, 62, 3)  # synth brass
emit("Flute", flute, 73, 4)
emit("Bells", bells, 12, 5)  # marimba
emit("Drums", drums, None, 9)

os.makedirs(os.path.normpath(OUT), exist_ok=True)
out = os.path.join(os.path.normpath(OUT), "deep_blue.mid")
mid.save(out)
print("wrote", out,
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
