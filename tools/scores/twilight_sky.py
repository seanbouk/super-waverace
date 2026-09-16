"""twilight_sky - composed 16-bar score for Twilight Sky (draft 1).

Brief (the user's Suno prompt): the championship finale - twilight
harbour city after the lights come on. Energetic sophisticated funk-
fusion, 128-138 BPM, tight syncopated bass, punchy drums with BUSY
hats, sharp EP stabs, bright brass, subtle mallets, airy pads,
SAX-like lead; tense-but-confident harmony with altered chords and
chromatic movement, brief darker passages resolving bright, ascending
determined melody, dramatic harmonic lifts.

Key: B minor, 132 BPM. Form:
  A (1-8):  Bm9 Bm9 Em9 A13 | DMAJ9 Gmaj9 Em9 F#7alt
            (circle-of-fifths lift into the bright Dmaj9 arrival;
             F#7alt is the tension that snaps it back to dark)
  B (9-16): Bm9 Bm9 (THE DARK PASSAGE: pads, thinned drums, brooding
            low sax) | Gmaj9 F#7alt | Em9 A13 DMAJ9 F#7alt
            (the kit punches back in and climbs to the triumphant
             held A5 over Dmaj9 in bar 15)
SAX owns the melody channel (scoop slides on touching notes, deep
delayed vibrato); bells answer bars 4/8/16; brass stings the F#7alt
bars with the A# (the altered third).

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/twilight_sky.py
"""
import os

import mido

BPM = 132
TPB = 480
TPR = TPB // 4
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "twilight_sky")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    return 12 * (int(s[-1]) + 1) + NAMES[s[:-1]]


CHORDS = {
    "Bm9":    ("B1", ("D4", "A4")),
    "Em9":    ("E2", ("D4", "G4")),
    "A13":    ("A1", ("C#4", "G4")),
    "Dmaj9":  ("D2", ("F#4", "C#5")),
    "Gmaj9":  ("G2", ("B3", "F#4")),
    "F#7alt": ("F#2", ("A#3", "E4")),
}
PROG = ["Bm9", "Bm9", "Em9", "A13", "Dmaj9", "Gmaj9", "Em9", "F#7alt",
        "Bm9", "Bm9", "Gmaj9", "F#7alt", "Em9", "A13", "Dmaj9", "F#7alt"]
BARS = len(PROG)
DARK = (8, 9)  # the brooding bars: pads in, stabs out, drums thinned

drums, bass, keys, pad, brass, sax, bells = [], [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: punchy, busy hats; bars 9-10 thin out then build back ------
for b in range(BARS):
    if b in DARK:
        drums.append((at(b, 0), 1, 36, 88))
        for r in range(0, 16, 2):
            drums.append((at(b, r), 1, 42, 30))
        if b == DARK[-1]:                          # re-entry build
            for i, r in enumerate((10, 12, 14, 15)):
                drums.append((at(b, r), 1, 38, 46 + i * 14))
        continue
    fill = b in (7, 15)
    for r, v in ((0, 108), (7, 88), (10, 100), (13, 76)):
        drums.append((at(b, r), 1, 36, v))
    drums.append((at(b, 4), 1, 38, 108))
    if not fill:
        drums.append((at(b, 12), 1, 38, 108))
        drums.append((at(b, 15), 1, 38, 30))       # ghost
    else:
        for i, r in enumerate((12, 13, 14, 15)):
            drums.append((at(b, r), 1, 38, 70 + i * 12))
    for r in range(16):
        if fill and r >= 12:
            continue
        drums.append((at(b, r), 1, 42, 84 if r % 4 == 0 else
                      (64 if r % 2 == 0 else 48)))

# ---- bass: the tightest riff of the six --------------------------------
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    if b in DARK:
        bass += [(at(b, 0), 4, root, 76), (at(b, 8), 4, root, 68),
                 (at(b, 12), 2, root + 7, 60)]
        continue
    bass += [(at(b, 0), 2, root, 100),
             (at(b, 3), 1, root + 12, 88),
             (at(b, 6), 2, root, 84),
             (at(b, 8), 1, root, 92),
             (at(b, 10), 1, root + 12, 86),
             (at(b, 11), 1, root + 10, 74),
             (at(b, 12), 2, root + 7, 80)]
    if nxt != root:
        bass.append((at(b, 14), 2, nxt - 1 if nxt > root else nxt + 1, 78))
    else:
        bass.append((at(b, 14), 1, root + 12, 70))

# ---- keys: sharp short stabs (rest in the dark bars) --------------------
for b in range(BARS):
    if b in DARK:
        continue
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    for r, v in ((3, 74), (6, 78), (11, 68), (14, 60)):
        keys += [(at(b, r), 1, lo, v), (at(b, r), 1, hi, v)]

# ---- pads: only the dark passage breathes ------------------------------
lo, hi = (n(p) for p in CHORDS["Bm9"][1])
pad += [(at(DARK[0], 0), 30, lo, 60), (at(DARK[0], 0), 30, hi, 60)]

# ---- brass: F#7alt stings (the A# is the drama) + the loop turnaround --
brass += [(at(7, 8), 1, n("F#4"), 96), (at(7, 9), 1, n("F#4"), 86),
          (at(7, 11), 3, n("A#4"), 104)]
brass += [(at(11, 8), 1, n("C#5"), 92), (at(11, 10), 2, n("A#4"), 96)]
for i, (r, p) in enumerate(((8, "E4"), (10, "F#4"), (12, "A#4"),
                            (14, "C#5"))):
    brass.append((at(15, r), 2, n(p), 86 + i * 5))

# ---- sax: determined, ascending; scoops on touching notes ---------------
S = [
    (0, 0, 2, "B4", 88), (0, 3, 2, "D5", 88), (0, 6, 1, "E5", 84),
    (0, 7, 7, "F#5", 92),                       # scoop onto the & push
    (1, 6, 2, "E5", 82), (1, 8, 4, "D5", 84), (1, 12, 4, "B4", 80),
    (2, 2, 2, "D5", 84), (2, 4, 2, "E5", 86), (2, 6, 2, "G5", 88),
    (2, 8, 6, "F#5", 90),                       # 9 over Em: the tension
    (3, 2, 4, "E5", 84),                        # ...bells answer
    (4, 0, 4, "A5", 96),                        # the BRIGHT arrival
    (4, 6, 2, "F#5", 86), (4, 8, 6, "E5", 88),
    (5, 2, 2, "F#5", 86), (5, 4, 2, "G5", 88), (5, 8, 6, "D5", 84),
    (6, 0, 2, "B4", 82), (6, 3, 2, "D5", 84), (6, 6, 2, "E5", 86),
    (6, 8, 4, "G5", 90),
    (7, 2, 2, "A#4", 88), (7, 4, 3, "C#5", 86), # ...brass sting answers
    (8, 4, 4, "B4", 74), (8, 10, 4, "A4", 70),  # the dark passage:
    (9, 2, 4, "F#4", 68), (9, 8, 6, "B4", 74),  # low and brooding
    (10, 0, 2, "B4", 86), (10, 3, 2, "D5", 88), (10, 6, 2, "F#5", 90),
    (10, 8, 4, "G5", 92),                       # back with intent
    (11, 2, 2, "E5", 86), (11, 4, 4, "C#5", 84),
    (12, 0, 2, "E5", 86), (12, 3, 2, "G5", 90), (12, 8, 4, "A5", 92),
    (13, 2, 2, "F#5", 86), (13, 4, 4, "G5", 88), (13, 10, 2, "E5", 82),
    (14, 0, 8, "A5", 96),                       # the triumphant hold
    (14, 10, 4, "F#5", 88),
    (15, 2, 2, "E5", 84), (15, 4, 3, "C#5", 82),  # ...brass walk closes
]
for bar, r, ln, p, v in S:
    sax.append((at(bar, r), ln, n(p), v))

# ---- bells: harbour-light glints in the gaps ---------------------------
B = [
    (3, 8, 2, "C#5", 58), (3, 10, 2, "E5", 60), (3, 12, 2, "A4", 54),
    (7, 8, 2, "A#4", 58), (7, 10, 2, "C#5", 60), (7, 12, 2, "E5", 58),
    (15, 8, 2, "A#4", 56), (15, 10, 2, "C#5", 58), (15, 12, 2, "E5", 60),
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
emit("Pad", pad, 89, 6)      # warm pad
emit("Brass", brass, 62, 3)  # synth brass
emit("Sax", sax, 66, 4)      # tenor sax
emit("Bells", bells, 11, 5)  # vibraphone
emit("Drums", drums, None, 9)

os.makedirs(os.path.normpath(OUT), exist_ok=True)
out = os.path.join(os.path.normpath(OUT), "twilight_sky.mid")
mid.save(out)
print("wrote", out,
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
