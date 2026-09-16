"""dawn_coast - composed 16-bar score for Dawn Coast (draft 1).

Brief (the user's Suno prompt): serene tropical beach at dawn, pink
sky, breezy jazz-pop, 112-120 BPM, warm EP + soft pads, rounded bass,
light drums with gentle syncopation, airy flute/whistle lead with
SMOOTH RISING PHRASES and space, marimba/bells, occasional mellow
brass, maj7/add9/sus harmony, dreamy-optimistic, modest reverb.

Key: G major, 116 BPM. The dawn is in the structure: the A-half
harmony RISES STEPWISE (Gmaj9-Am9-Bm7-Cmaj9 - the bass and the EP
shells climb together), and the melody keeps lifting until its B5 peak
in bar 11. Chord channels alternate by section: EP stabs (bars 1-8,
13-16), PADS take over for the dreamy stretch (bars 9-12).

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/dawn_coast.py
"""
import os

import mido

BPM = 116
TPB = 480
TPR = TPB // 4
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "dawn_coast")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    return 12 * (int(s[-1]) + 1) + NAMES[s[:-1]]


CHORDS = {
    "Gmaj9": ("G2", ("B3", "F#4")),
    "Am9":   ("A2", ("C4", "G4")),
    "Bm7":   ("B2", ("D4", "A4")),
    "Cmaj9": ("C3", ("E4", "B4")),
    "D9sus": ("D2", ("C4", "G4")),
    "Em9":   ("E2", ("D4", "G4")),
}
PAD_V = {  # pad voicings sit a shade lower than the EP shells
    "Em9": ("G3", "D4"), "Cmaj9": ("B3", "E4"),
    "Gmaj9": ("B3", "F#4"), "D9sus": ("C4", "G4"),
}
PROG = ["Gmaj9", "Am9", "Bm7", "Cmaj9", "Gmaj9", "Am9", "Cmaj9", "D9sus",
        "Em9", "Cmaj9", "Gmaj9", "D9sus", "Em9", "Cmaj9", "Am9", "D9sus"]
BARS = len(PROG)
PAD_BARS = range(8, 12)

drums, bass, keys, pad, brass, flute, bells = [], [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: light, gently syncopated (the r7 kick = the swell push) ----
for b in range(BARS):
    drums.append((at(b, 0), 1, 36, 92))
    drums.append((at(b, 7), 1, 36, 74))
    drums.append((at(b, 10), 1, 36, 84))
    drums.append((at(b, 4), 1, 38, 78))
    drums.append((at(b, 12), 1, 38, 74))
    for r in range(0, 16, 2):
        drums.append((at(b, r), 1, 42, 56 if r % 4 == 0 else 42))
    if b % 4 == 3:
        drums.append((at(b, 15), 1, 42, 48))       # small 16th flick
    if b in (7, 15):
        for i, r in enumerate((13, 14, 15)):
            drums.append((at(b, r), 1, 38, 48 + i * 10))

# ---- bass: rounded, undulating; the A-half walks UP with the dawn -----
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    bass += [(at(b, 0), 4, root, 82),
             (at(b, 7), 1, root, 66),              # syncopated push
             (at(b, 8), 3, root + 7, 74),
             (at(b, 12), 2, root + 9, 66)]         # sixth colour
    if nxt != root:
        bass.append((at(b, 14), 2, nxt - 1 if nxt > root else nxt + 1, 62))

# ---- keys: gentle offbeat EP (rests while the pads dream, bars 9-12) --
for b in range(BARS):
    if b in PAD_BARS:
        continue
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    keys += [(at(b, 3), 2, lo, 60), (at(b, 3), 2, hi, 60),
             (at(b, 10), 2, lo, 56), (at(b, 10), 2, hi, 56)]
    if b % 2 == 1 and (b + 1) % BARS not in PAD_BARS:
        nlo, nhi = (n(p) for p in CHORDS[PROG[(b + 1) % BARS]][1])
        keys += [(at(b, 15), 1, nlo, 46), (at(b, 15), 1, nhi, 46)]

# ---- pads: the dreamy stretch ------------------------------------------
for b in PAD_BARS:
    lo, hi = (n(p) for p in PAD_V[PROG[b]])
    pad += [(at(b, 0), 14, lo, 58), (at(b, 0), 14, hi, 58)]

# ---- brass: warm and rare ----------------------------------------------
brass += [(at(11, 0), 4, n("G3"), 48)]             # under the melody peak
brass += [(at(15, 8), 4, n("A3"), 52), (at(15, 12), 4, n("C4"), 56)]

# ---- flute: rising, hopeful, with air ----------------------------------
F = [
    (0, 4, 2, "D5", 80), (0, 6, 2, "E5", 82), (0, 8, 6, "G5", 86),
    (1, 4, 2, "E5", 80), (1, 8, 6, "A5", 88),
    (2, 2, 4, "F#5", 84), (2, 8, 4, "D5", 80),
    # bar 4 rests - bells
    (4, 4, 2, "B4", 78), (4, 6, 2, "D5", 80), (4, 8, 6, "E5", 84),
    (5, 4, 4, "G5", 86), (5, 10, 4, "E5", 82),
    (6, 2, 2, "E5", 80), (6, 4, 2, "G5", 84), (6, 8, 6, "A5", 88),
    # bar 8 rests - bells
    (8, 4, 4, "E5", 80), (8, 8, 6, "G5", 84),
    (9, 2, 4, "A5", 86), (9, 8, 4, "G5", 84),
    (10, 2, 2, "F#5", 84), (10, 4, 2, "G5", 86), (10, 8, 8, "B5", 92),
    (11, 6, 2, "A5", 84), (11, 8, 4, "G5", 82),
    (12, 4, 4, "D5", 78), (12, 8, 4, "B4", 74),
    (13, 2, 4, "C5", 78), (13, 8, 6, "E5", 82),
    (14, 2, 4, "E5", 80), (14, 8, 4, "D5", 78), (14, 12, 4, "C5", 74),
    # bar 16 rests - bells + brass close the loop
]
for bar, r, ln, p, v in F:
    flute.append((at(bar, r), ln, n(p), v))

# ---- bells: dawn sparkle in the gaps -----------------------------------
B = [
    (3, 8, 2, "E5", 58), (3, 10, 2, "G5", 60), (3, 12, 3, "B4", 54),
    (7, 8, 2, "A4", 56), (7, 10, 2, "C5", 58), (7, 12, 3, "E5", 60),
    (11, 12, 2, "F#5", 58), (11, 14, 2, "A5", 60),
    (15, 0, 2, "A4", 54), (15, 2, 2, "C5", 56), (15, 4, 2, "E5", 58),
    (15, 6, 2, "G5", 60),
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
emit("Pad", pad, 89, 6)      # warm pad
emit("Brass", brass, 61, 3)
emit("Flute", flute, 73, 4)
emit("Bells", bells, 11, 5)  # vibraphone
emit("Drums", drums, None, 9)

os.makedirs(os.path.normpath(OUT), exist_ok=True)
out = os.path.join(os.path.normpath(OUT), "dawn_coast.mid")
mid.save(out)
print("wrote", out,
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
