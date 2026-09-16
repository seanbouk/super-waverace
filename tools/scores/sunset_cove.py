"""sunset_cove - composed 16-bar score for Sunset Cove (draft 1).

Brief (the user's Suno prompt): warm sunset-over-the-ocean, smooth
jazz-pop/light fusion, 110-120 BPM, maj7/add9/sus harmony, mellow EP,
rounded melodic bass, crisp LIGHT drums, soft synth brass, airy
flute-like lead with long flowing phrases, mallet/bell accents,
wistful not hyperactive.

Key: F major, 112 BPM. Form:
  A (1-8): Fmaj9 Fmaj9 Bbmaj9 Bbmaj9 Am7 Dm9 Gm9 C13sus
  B (9-16): same shape, melody lifts to its high point then settles.
The FLUTE owns the melody channel; bells answer at every phrase end
(bars 4/8/12 and all of 16 - the flute rests there, they timeshare).
Touching flute notes = slide (gentle scoops); holds get DELAYED vibrato.

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/sunset_cove.py
"""
import os

import mido

BPM = 112
TPB = 480
TPR = TPB // 4
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "sunset_cove")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    return 12 * (int(s[-1]) + 1) + NAMES[s[:-1]]


# per-bar: (bass root, EP shell dyad below the flute's register)
CHORDS = {
    "Fmaj9":  ("F2", ("A3", "E4")),
    "Bbmaj9": ("Bb2", ("D4", "A4")),
    "Am7":    ("A2", ("C4", "G4")),
    "Dm9":    ("D2", ("C4", "F4")),
    "Gm9":    ("G2", ("Bb3", "F4")),
    "C13sus": ("C3", ("Bb3", "F4")),
}
PROG = ["Fmaj9", "Fmaj9", "Bbmaj9", "Bbmaj9",
        "Am7", "Dm9", "Gm9", "C13sus"] * 2
BARS = len(PROG)

drums, bass, keys, brass, flute, bells = [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: light and unhurried; soft snare, 8th hats ------------------
for b in range(BARS):
    drums.append((at(b, 0), 1, 36, 96))
    drums.append((at(b, 8), 1, 36, 88))
    if b % 2 == 1:
        drums.append((at(b, 11), 1, 36, 72))       # gentle pickup
    drums.append((at(b, 4), 1, 38, 64))            # soft, sidestick-ish
    drums.append((at(b, 12), 1, 38, 60))
    for r in range(0, 16, 2):
        drums.append((at(b, r), 1, 42, 52 if r % 4 == 0 else 40))
    if b in (7, 15):                               # tiny 16th lift
        for i, r in enumerate((13, 14, 15)):
            drums.append((at(b, r), 1, 42, 44 + i * 8))

# ---- bass: rounded and melodic, no slap --------------------------------
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    approach = nxt - 1 if nxt > root else nxt + 1
    bass += [(at(b, 0), 5, root, 84),
             (at(b, 6), 2, root + 7, 72),
             (at(b, 8), 4, root, 78),
             (at(b, 12), 2, root + 9 if PROG[b].startswith(("F", "Bb"))
              else root + 7, 68),
             (at(b, 14), 2, approach, 64)]

# ---- keys: EP shells, long and even (half-note feel) -------------------
for b in range(BARS):
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    nlo, nhi = (n(p) for p in CHORDS[PROG[(b + 1) % BARS]][1])
    keys += [(at(b, 0), 6, lo, 58), (at(b, 0), 6, hi, 58),
             (at(b, 8), 6, lo, 54), (at(b, 8), 6, hi, 54)]
    if b % 2 == 1:
        keys += [(at(b, 15), 1, nlo, 44), (at(b, 15), 1, nhi, 44)]

# ---- brass: two soft swells only ---------------------------------------
brass += [(at(7, 8), 8, n("G3"), 56)]
brass += [(at(15, 4), 6, n("Bb3"), 56), (at(15, 10), 6, n("C4"), 62)]

# ---- flute: long flowing phrases; touching notes = gentle scoops -------
F = [
    (0, 2, 4, "C5", 84), (0, 8, 8, "F5", 88),
    (1, 6, 2, "E5", 78), (1, 8, 6, "D5", 84),
    (2, 2, 2, "D5", 78), (2, 4, 8, "F5", 86),
    (3, 2, 4, "D5", 80),                       # ...bells answer bar 4
    (4, 2, 4, "E5", 84), (4, 8, 4, "C5", 80),
    (5, 2, 4, "D5", 82), (5, 8, 6, "A4", 78),
    (6, 2, 3, "Bb4", 78), (6, 6, 2, "C5", 76), (6, 8, 8, "D5", 86),
    (7, 2, 4, "C5", 82),                       # ...bells answer bar 8
    (8, 2, 2, "C5", 82), (8, 4, 4, "F5", 86), (8, 8, 8, "A5", 92),
    (9, 6, 2, "G5", 82), (9, 8, 6, "F5", 86),
    (10, 2, 2, "F5", 80), (10, 4, 8, "G5", 88),
    (11, 2, 4, "D5", 80),                      # ...bells answer bar 12
    (12, 0, 4, "E5", 84), (12, 4, 4, "D5", 80), (12, 8, 8, "A4", 78),
    (13, 4, 4, "C5", 80), (13, 8, 6, "E5", 84),
    (14, 0, 4, "D5", 82), (14, 4, 2, "C5", 78), (14, 6, 2, "Bb4", 76),
    (14, 8, 7, "C5", 84),                      # bar 16 is the bells'
]
for bar, r, ln, p, v in F:
    flute.append((at(bar, r), ln, n(p), v))

# ---- bells: mallet answers where the flute breathes --------------------
B = [
    (3, 8, 2, "F5", 62), (3, 10, 2, "A5", 62), (3, 12, 3, "F5", 58),
    (7, 8, 2, "G5", 62), (7, 10, 2, "E5", 60), (7, 12, 3, "C5", 56),
    (11, 8, 2, "Bb5", 62), (11, 10, 2, "A5", 60), (11, 12, 3, "F5", 58),
    (15, 0, 2, "G5", 60), (15, 2, 2, "A5", 62), (15, 4, 2, "Bb5", 64),
    (15, 6, 2, "A5", 60), (15, 8, 4, "F5", 58),
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


emit("Bass", bass, 33, 1)    # finger bass
emit("Keys", keys, 4, 2)     # EP 1
emit("Brass", brass, 61, 3)  # brass section (soft)
emit("Flute", flute, 73, 4)  # flute
emit("Bells", bells, 11, 5)  # vibraphone
emit("Drums", drums, None, 9)

os.makedirs(os.path.normpath(OUT), exist_ok=True)
out = os.path.join(os.path.normpath(OUT), "sunset_cove.mid")
mid.save(out)
print("wrote", out,
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
