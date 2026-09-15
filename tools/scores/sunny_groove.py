"""sunny_groove - composed 16-bar score for Sunny Island (draft 1).

The score-MIDI front door (replaces stem transcription): every note here
is intentional and already fits the 6-channel plan -
  Drums (ch10: 36 kick / 38 snare / 42 hat) -> channels 0+1
  Bass  (mono)                               -> channel 2
  Keys  (2-note dyads, EP shell voicings)    -> channels 3+4
  Brass (mono stabs; overlays keys B)        -> channel 4
  Lead + Bells (never simultaneous - they    -> channel 5
    timeshare: lead owns the A half, bells answer in the B half)

Brief (the user's Suno prompt): mid-90s console racing, breezy
jazz-pop/light fusion, ~128 BPM major key, maj7/add9 colour, syncopated
melodic bass, crisp drums, EP + synth brass + marimba/bell accents,
call-and-response hooks. Key: D major. Form: A (I-I-IV-V13sus x2),
B (vi9-II9-IV-V13sus x2) - the E9 is the fusion lift.

Rows are 16ths (4 per beat, 16 per bar). Edit freely - regenerating is
  toolchain/py311-audio/Scripts/python.exe tools/scores/sunny_groove.py

LEAD SEMANTICS (the guitar treatment, applied by midi2it --score):
touching/overlapping lead notes become a Gxx SLIDE (bend, no re-pick);
a gap between notes means a fresh pick; notes held 5+ rows get vibrato.
The grace-note pickups below exist purely to create bends.
"""
import os
import sys

import mido

BPM = 128
TPB = 480
TPR = TPB // 4  # ticks per 16th row
OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "assets", "music", "sunny_island", "sunny_groove.mid")

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
         "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
         "A#": 10, "Bb": 10, "B": 11}


def n(s):
    """'F#4' -> midi number (C4 = 60)."""
    name = s[:-1]
    return 12 * (int(s[-1]) + 1) + NAMES[name]


# ---------------------------------------------------------------- harmony
# per-bar: (bass root, EP dyad = 3rd+7th shell with the colour tone)
CHORDS = {
    "Dmaj9":  ("D2", ("F#4", "C#5")),
    "Gmaj9":  ("G2", ("B4", "F#5")),
    "A13sus": ("A2", ("G4", "D5")),
    "Bm9":    ("B2", ("D4", "A4")),
    "E9":     ("E2", ("G#4", "D5")),
}
PROG = ["Dmaj9", "Dmaj9", "Gmaj9", "A13sus",
        "Dmaj9", "Dmaj9", "Gmaj9", "A13sus",
        "Bm9", "E9", "Gmaj9", "A13sus",
        "Bm9", "E9", "Gmaj9", "A13sus"]
BARS = len(PROG)

# ------------------------------------------------------------ note events
# each: (absolute row, length in rows, midi note, velocity)
drums, bass, keys, brass, lead = [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: funk-pop; kick 1 & 2.5+ & 3.5, snare 2/4, 16th hats -------
for b in range(BARS):
    fill = b in (7, 15)
    for r, v in ((0, 105), (7, 92), (10, 98)):
        drums.append((at(b, r), 1, 36, v))
    drums.append((at(b, 4), 1, 38, 108))
    if not fill:
        drums.append((at(b, 12), 1, 38, 108))
        if b % 4 == 3:
            drums.append((at(b, 14), 1, 38, 34))     # ghost
    else:                                            # bar-8/16 fill
        for i, r in enumerate((12, 13, 14, 15)):
            drums.append((at(b, r), 1, 38, 70 + i * 12))
    for r in range(16):
        if fill and r >= 12:
            continue
        drums.append((at(b, r), 1, 42, 84 if r % 4 == 0 else
                      (66 if r % 2 == 0 else 48)))

# ---- bass: signature melodic riff, approach note into the next bar ----
for b in range(BARS):
    chord = PROG[b]
    root = n(CHORDS[chord][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    if chord == "A13sus" and b in (7, 15):
        # turnaround run up to the loop/section top
        bass += [(at(b, 0), 3, root, 100), (at(b, 3), 2, root, 88),
                 (at(b, 6), 2, root + 12, 96), (at(b, 10), 1, root - 2, 84),
                 (at(b, 12), 1, root - 2, 88), (at(b, 13), 1, root, 92),
                 (at(b, 14), 1, root + 2, 96), (at(b, 15), 1, root + 4, 100)]
        continue
    approach = nxt - 1 if nxt != root else root + 11   # chromatic below
    bass += [(at(b, 0), 3, root, 100),           # root
             (at(b, 3), 2, root + 7, 88),        # fifth pop
             (at(b, 6), 2, root + 12, 96),       # octave
             (at(b, 10), 1, root + 9, 84),       # sixth (the melodic bit)
             (at(b, 12), 2, root + 7, 88),
             (at(b, 15), 1, approach, 80)]

# ---- keys: EP dyad comps on the &2 / 4, pushed 16th into next bar -----
for b in range(BARS):
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    nlo, nhi = (n(p) for p in CHORDS[PROG[(b + 1) % BARS]][1])
    if b < 8:                                     # A: offbeat stabs
        hits = ((6, 2, 74), (12, 2, 70))
    else:                                         # B: bossa-ish earlier
        hits = ((3, 2, 72), (10, 2, 70))
    for r, ln, v in hits:
        keys += [(at(b, r), ln, lo, v), (at(b, r), ln, hi, v)]
    if b % 2 == 1:                                # push into the next bar
        keys += [(at(b, 15), 1, nlo, 58), (at(b, 15), 1, nhi, 58)]

# ---- brass: punctuation, answers the lead at phrase ends --------------
brass += [(at(3, 8), 1, n("A4"), 96), (at(3, 10), 1, n("B4"), 96),
          (at(3, 11), 3, n("C#5"), 104)]
brass += [(at(7, 8), 1, n("C#5"), 96), (at(7, 9), 1, n("C#5"), 88),
          (at(7, 11), 3, n("D5"), 104)]
for i, (r, p) in enumerate(((8, "E4"), (10, "F#4"), (12, "G4"),
                            (14, "A4"))):        # bar-16 walk-up
    brass.append((at(15, r), 2, n(p), 88 + i * 4))

# ---- lead: A-half hook (2-bar call, 2-bar answer) ---------------------
HOOK = [(0, 0, 2, "F#4", 100), (0, 3, 2, "A4", 92), (0, 6, 2, "B4", 94),
        (0, 8, 6, "A4", 96),
        (1, 6, 1, "E5", 88), (1, 8, 2, "D5", 94), (1, 10, 2, "B4", 90),
        (1, 12, 3, "A4", 92)]
for base in (0, 4):
    for bar, r, ln, p, v in HOOK:
        lead.append((at(base + bar, r), ln, n(p), v))
lead += [(at(2, 0), 2, n("B4"), 96), (at(2, 3), 2, n("D5"), 92),
         (at(2, 6), 2, n("E5"), 94), (at(2, 8), 5, n("D5"), 96)]
lead += [(at(6, 0), 2, n("B4"), 96), (at(6, 3), 2, n("D5"), 92),
         (at(6, 5), 1, n("E5"), 80),                  # slide pickup...
         (at(6, 6), 2, n("F#5"), 96), (at(6, 8), 4, n("E5"), 96)]
# bars 3 and 7 rest: brass answers there
lead += [(at(3, 14), 2, n("E4"), 72)]  # bends into the bar-5 hook F#4

# ---- bells: B-half offbeat 8th arps (timeshare the lead channel) ------
bells = []
ARPS = {"Bm9": ("D5", "F#5", "A5", "F#5"), "E9": ("E5", "G#5", "B5", "G#5"),
        "Gmaj9": ("D5", "G5", "B5", "A5"), "A13sus": ("C#5", "E5", "A5", "G5")}
for b in (8, 9, 12, 13):
    for i, p in enumerate(ARPS[PROG[b]]):
        bells.append((at(b, 2 + 4 * i), 2, n(p), 68))
# lead answers over bars 11-12 / 15 handled by lead below
lead += [(at(10, 0), 3, n("F#5"), 98), (at(10, 4), 2, n("E5"), 92),
         (at(10, 6), 2, n("D5"), 90), (at(10, 8), 4, n("E5"), 94),
         (at(11, 6), 1, n("E5"), 86), (at(11, 8), 2, n("D5"), 90),
         (at(11, 10), 4, n("B4"), 88)]
lead += [(at(14, 0), 2, n("D5"), 92), (at(14, 4), 2, n("E5"), 94),
         (at(14, 8), 2, n("F#5"), 96),
         (at(14, 11), 1, n("G5"), 84),                # bend up into...
         (at(14, 12), 3, n("A5"), 98)]

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


emit("Bass", bass, 36, 1)    # slap bass 1
emit("Keys", keys, 4, 2)     # EP 1
emit("Brass", brass, 62, 3)  # synth brass 1
emit("Lead", lead, 80, 4)    # square lead
emit("Bells", bells, 12, 5)  # marimba
emit("Drums", drums, None, 9)

mid.save(os.path.normpath(OUT))
print("wrote", os.path.normpath(OUT),
      "(%d bars at %d BPM = %.1fs loop)" % (BARS, BPM, BARS * 16 * 60 / BPM / 4))
