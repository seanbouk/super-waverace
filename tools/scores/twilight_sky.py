"""twilight_sky - composed 16-bar score for Twilight Sky (draft 2).

Draft 1 leaned on Sunny Island's funk skeleton and read as "tune 1 but
moodier" (user). Draft 2 gives the finale its own engine: 138 BPM,
OCTAVE-PUMPING 8th-note bass (night-racer drive, nothing like the slap
riff), four-on-the-floor kick under busy 16th hats, a tight
repeated-note sax hook that develops across the form, and fast-rising
B-half changes (Em9-A13-Dmaj9-Gmaj9). The dark dip is ONE bar (15) with
the bass still pumping under a pad breath, then the F#7alt brass
walk-up slams the loop shut.

Key: B minor, 138 BPM. Form:
  A (1-8): Bm9 Bm9 Gmaj9 A13 | Bm9 Bm9 Gmaj9 F#7alt
  B (9-16): Em9 A13 DMAJ9 Gmaj9 | Em9 F#7alt Bm9(pad dip) F#7alt

Regenerate:
  toolchain/py311-audio/Scripts/python.exe tools/scores/twilight_sky.py
"""
import os

import mido

BPM = 138
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
PROG = ["Bm9", "Bm9", "Gmaj9", "A13", "Bm9", "Bm9", "Gmaj9", "F#7alt",
        "Em9", "A13", "Dmaj9", "Gmaj9", "Em9", "F#7alt", "Bm9", "F#7alt"]
BARS = len(PROG)
DIP = 14  # one-bar pad breath; the bass keeps pumping through it

drums, bass, keys, pad, brass, sax, bells = [], [], [], [], [], [], []


def at(bar, row):
    return bar * 16 + row


# ---- drums: four-on-the-floor drive + busy accented 16th hats ----------
for b in range(BARS):
    fill = b in (7, 15)
    for r in (0, 4, 8, 12):
        drums.append((at(b, r), 1, 36, 104 if r % 8 == 0 else 96))
    if not fill:
        drums.append((at(b, 4), 1, 38, 106))       # layered with the kick
        drums.append((at(b, 12), 1, 38, 106))
        drums.append((at(b, 15), 1, 38, 30))       # ghost
    else:
        drums.append((at(b, 4), 1, 38, 106))
        for i, r in enumerate((10, 12, 13, 14, 15)):
            drums.append((at(b, r), 1, 38, 60 + i * 11))
    for r in range(16):
        if fill and r >= 10:
            continue
        drums.append((at(b, r), 1, 42, 86 if r % 4 == 2 else
                      (58 if r % 2 == 0 else 44)))  # accent the offbeats

# ---- bass: octave-pumping 8ths - the night-drive engine ----------------
for b in range(BARS):
    root = n(CHORDS[PROG[b]][0])
    nxt = n(CHORDS[PROG[(b + 1) % BARS]][0])
    for i, r in enumerate(range(0, 16, 2)):
        if r == 14 and nxt != root:
            bass.append((at(b, r), 2,
                         nxt - 1 if nxt > root else nxt + 1, 84))
        else:
            hi = i % 2 == 1
            bass.append((at(b, r), 2, root + (12 if hi else 0),
                         80 if hi else 98))

# ---- keys: sharp stabs on the pushes (rest in the dip bar) -------------
for b in range(BARS):
    if b == DIP:
        continue
    lo, hi = (n(p) for p in CHORDS[PROG[b]][1])
    for r, v in ((2, 72), (7, 78), (10, 68)):
        keys += [(at(b, r), 1, lo, v), (at(b, r), 1, hi, v)]

# ---- pad: one dark breath under bar 15, engine still running -----------
lo, hi = (n(p) for p in CHORDS["Bm9"][1])
pad += [(at(DIP, 0), 14, lo, 60), (at(DIP, 0), 14, hi, 60)]

# ---- brass: alt-chord stings + the walk-up that slams the loop shut ----
brass += [(at(3, 12), 2, n("C#5"), 88), (at(3, 14), 2, n("E5"), 92)]
brass += [(at(7, 8), 1, n("F#4"), 96), (at(7, 9), 1, n("F#4"), 86),
          (at(7, 11), 3, n("A#4"), 104)]
brass += [(at(13, 8), 1, n("C#5"), 92), (at(13, 10), 2, n("A#4"), 96)]
for i, (r, p) in enumerate(((8, "E4"), (10, "F#4"), (12, "A#4"),
                            (14, "C#5"))):
    brass.append((at(15, r), 2, n(p), 88 + i * 5))

# ---- sax: the repeated-note hook, developed across the form ------------
def hook(bar, p1, p2, p3, p4):
    """rhythmic motif: da-da-daa ... DA(push) da-daa"""
    return [(bar, 0, 1, p1, 92), (bar, 2, 1, p1, 88), (bar, 4, 2, p1, 94),
            (bar, 7, 3, p2, 98), (bar, 11, 1, p3, 88), (bar, 12, 3, p4, 92)]

S = []
S += hook(0, "F#5", "A5", "F#5", "E5")
S += [(1, 0, 2, "D5", 88), (1, 3, 2, "E5", 88), (1, 6, 2, "F#5", 90),
      (1, 8, 5, "D5", 88)]
S += hook(2, "D5", "G5", "F#5", "E5")
S += [(3, 0, 2, "E5", 88), (3, 3, 2, "F#5", 90), (3, 6, 2, "G5", 92),
      (3, 8, 3, "E5", 88)]                      # brass takes r12
S += hook(4, "F#5", "A5", "F#5", "E5")
S += [(5, 0, 2, "D5", 88), (5, 3, 2, "E5", 88), (5, 6, 2, "F#5", 90),
      (5, 8, 5, "D5", 88)]
S += [(6, 0, 2, "G5", 92), (6, 3, 2, "A5", 94), (6, 6, 6, "B5", 98),
      (6, 13, 3, "A5", 90)]                     # the climb
S += [(7, 2, 2, "A#5", 94), (7, 4, 3, "F#5", 90)]  # alt sting answer
S += hook(8, "E5", "G5", "F#5", "E5")
S += [(9, 0, 2, "F#5", 90), (9, 3, 2, "G5", 92), (9, 6, 2, "E5", 88),
      (9, 8, 4, "C#5", 86)]
S += [(10, 0, 4, "A5", 98), (10, 6, 2, "F#5", 90), (10, 8, 4, "D5", 88),
      (10, 13, 3, "E5", 90)]                    # the bright arrival
S += [(11, 0, 2, "F#5", 90), (11, 3, 2, "G5", 92), (11, 6, 6, "A5", 94)]
S += hook(12, "E5", "A5", "G5", "F#5")
S += [(13, 0, 2, "C#5", 88), (13, 3, 2, "A#4", 86), (13, 6, 2, "C#5", 88)]
# bar 15 = the dip: sax out, pad + bells breathe, bass pumps on
S += [(15, 0, 2, "E5", 88), (15, 3, 2, "C#5", 86)]  # then brass walks
for bar, r, ln, p, v in S:
    sax.append((at(bar, r), ln, n(p), v))

# ---- bells: glints; they own the dip bar -------------------------------
B = [
    (7, 13, 3, "C#5", 58),
    (11, 13, 3, "D5", 58),
    (14, 2, 2, "D5", 60), (14, 4, 2, "F#5", 62), (14, 6, 2, "B4", 56),
    (14, 8, 2, "D5", 60), (14, 10, 2, "F#5", 62), (14, 12, 3, "A5", 64),
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


emit("Bass", bass, 38, 1)    # synth bass 1 (pumping)
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
