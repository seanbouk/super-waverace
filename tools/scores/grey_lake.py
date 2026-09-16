"""grey_lake - composed 16-bar score for Grey Lake (draft 1).

Brief (the user's Suno prompt): cool misty lake, restrained ambient
fusion, 105-115 BPM, minor/modal with occasional warm maj7, soft EP /
PADS, rounded fretless-ish bass, light drums, sparse elegant flute
melody with SPACE between phrases, bell accents, occasional soft brass,
"clean digital reverb" -> this song turns the SNES DSP ECHO ON (song
message: EDL/EVOL/EFB/EON in song.json) for pads + melody, drums and
bass stay dry.

Key: A dorian, 108 BPM. Half-time feel (snare on beat 3 only) with a
syncopated kick keeps it a racing tune under the calm. Form:
  A (1-8): Am9 Am9 D9 D9 Am9 Am9 Fmaj9 Esus7   (D9 = the dorian IV)
  B (9-16): Cmaj9 G6 Fmaj9 Esus7 x2, melody peaks at B5 in bar 13.
PADS (not EP) hold the harmony in 2-bar swells. Flute rests bars 4/8/
12/16 - bells answer there.

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/grey_lake.py
"""
import os

import mido

BPM = 108
TPB = 480
TPR = TPB // 4
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "grey_lake")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    return 12 * (int(s[-1]) + 1) + NAMES[s[:-1]]


CHORDS = {
    "Am9":   ("A2", ("C4", "G4")),
    "D9":    ("D2", ("C4", "F#4")),
    "Fmaj9": ("F2", ("A3", "E4")),
    "Esus7": ("E2", ("A3", "D4")),
    "Cmaj9": ("C3", ("E4", "B4")),
    "G6":    ("G2", ("B3", "E4")),
}
PROG = ["Am9", "Am9", "D9", "D9", "Am9", "Am9", "Fmaj9", "Esus7",
        "Cmaj9", "G6", "Fmaj9", "Esus7", "Cmaj9", "G6", "Fmaj9", "Esus7"]
BARS = len(PROG)

drums, bass, pad, brass, flute, bells = [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: half-time and hushed; the kick carries the syncopation ----
for b in range(BARS):
    drums.append((at(b, 0), 1, 36, 88))
    drums.append((at(b, 10), 1, 36, 76))          # the push
    if b % 2 == 1:
        drums.append((at(b, 14), 1, 36, 58))
    drums.append((at(b, 8), 1, 38, 56))           # soft, beat 3 only
    for r in range(0, 16, 2):
        drums.append((at(b, r), 1, 42, 44 if r % 8 == 0 else 32))
    if b in (7, 15):
        for i, r in enumerate((12, 13, 14, 15)):
            drums.append((at(b, r), 1, 42, 30 + i * 8))

# ---- bass: rounded, syncopated but soft ------------------------------
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    bass += [(at(b, 0), 3, root, 78),
             (at(b, 6), 2, root, 66),             # re-attack push
             (at(b, 10), 1, root + 7, 62),
             (at(b, 12), 3, root, 70)]
    if nxt != root:
        bass.append((at(b, 15), 1, nxt - 1 if nxt > root else nxt + 1, 56))

# ---- pads: 2-bar swells (the swell is the pad's envelope) ------------
for b in range(0, BARS, 2):
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    pad += [(at(b, 0), 30, lo, 60), (at(b, 0), 30, hi, 60)]
    if PROG[b + 1] != PROG[b]:                    # mid-pair chord change
        lo2, hi2 = (n(p) for p in CHORDS[PROG[b + 1]][1])
        pad += [(at(b + 1, 0), 14, lo2, 56), (at(b + 1, 0), 14, hi2, 56)]

# ---- brass: two soft low accents only --------------------------------
brass += [(at(7, 12), 4, n("B3"), 50)]
brass += [(at(15, 8), 8, n("E4"), 54)]

# ---- flute: sparse, wistful, lots of air; slides where notes touch ----
F = [
    (0, 4, 6, "E5", 80), (0, 12, 4, "G5", 84),     # crosses into bar 2
    (1, 2, 4, "F#5", 78), (1, 8, 6, "E5", 82),     # dorian colour
    (2, 4, 2, "E5", 76), (2, 6, 2, "F#5", 78), (2, 8, 6, "D5", 82),
    # bar 4 rests - bells answer
    (4, 4, 4, "C5", 78), (4, 10, 2, "B4", 74), (4, 12, 6, "A4", 78),
    (5, 6, 8, "E5", 82),
    (6, 2, 4, "E5", 80), (6, 8, 6, "C5", 78),
    # bar 8 rests - bells answer
    (8, 4, 4, "G5", 84), (8, 8, 6, "E5", 82),
    (9, 2, 4, "D5", 78), (9, 8, 4, "B4", 74), (9, 12, 4, "D5", 78),
    (10, 2, 6, "C5", 80), (10, 10, 4, "A4", 76),
    # bar 12 rests - bells answer
    (12, 4, 2, "E5", 80), (12, 6, 2, "G5", 84), (12, 8, 8, "B5", 88),
    (13, 4, 4, "A5", 84), (13, 8, 6, "G5", 82),
    (14, 2, 4, "E5", 80), (14, 8, 4, "D5", 78), (14, 12, 4, "C5", 76),
    # bar 16 rests - bells + brass close the loop
]
for bar, r, ln, p, v in F:
    flute.append((at(bar, r), ln, n(p), v))

# ---- bells: quiet answers in the flute's silences ---------------------
B = [
    (3, 8, 2, "E5", 56), (3, 10, 2, "F#5", 56), (3, 12, 3, "A5", 58),
    (7, 8, 2, "B4", 54), (7, 10, 2, "A4", 52), (7, 12, 3, "E5", 56),
    (11, 8, 2, "D5", 56), (11, 10, 2, "E5", 56), (11, 12, 3, "A4", 54),
    (15, 0, 2, "B4", 54), (15, 2, 2, "D5", 56), (15, 4, 2, "E5", 58),
    (15, 8, 4, "A4", 52),
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


emit("Bass", bass, 35, 1)    # fretless bass
emit("Pad", pad, 89, 2)      # warm pad
emit("Brass", brass, 61, 3)
emit("Flute", flute, 73, 4)
emit("Bells", bells, 11, 5)  # vibraphone
emit("Drums", drums, None, 9)

os.makedirs(os.path.normpath(OUT), exist_ok=True)
out = os.path.join(os.path.normpath(OUT), "grey_lake.mid")
mid.save(out)
print("wrote", out,
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
