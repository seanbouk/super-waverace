"""mksfx - synthesise the menu/game SFX bank (Virtua Fighter feel:
tiny precise digital ticks against a huge reverberant confirm boom).

Writes assets/music/menusfx.it (the FIRST soundbank module, so its
samples are global sources 0..N-1 for spcLoadEffect) and a
preview_<name>.wav per effect in assets/music/menusfx/ for audition.

Reverb is BAKED INTO the samples (the DSP echo config belongs to
whichever music module is loaded, so effects carry their own space).

Playback contract (snesmodwla.asm): spcLoadEffect(source) assigns
slots in CALL ORDER after every spcLoad; spcEffect(pitch, slot,
vol*16+pan) with pitch = playbackrate/4000 (4 bits, so rate is a
multiple of 4kHz) and slot 0-15. Author rates here: ticks 32kHz
(pitch 8), boom 16kHz (pitch 4).

Run:
  toolchain/py311-audio/Scripts/python.exe tools/mksfx.py
"""
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(__file__))
import midi2it as M

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
OUTDIR = os.path.join(ROOT, "assets", "music", "menusfx")
rng = np.random.default_rng(0x5F0)


def lowpass(x, k):
    """one-pole smoothing, k in (0,1); smaller = darker."""
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += k * (x[i] - acc)
        y[i] = acc
    return y


def room(dry, sr, taps, decay, dark=0.25, tail_pad=0.0):
    """bake a reverb tail: `taps` diffuse delayed copies, exponentially
    decaying, lowpassed progressively darker."""
    n = len(dry) + int(tail_pad * sr)
    out = np.zeros(n)
    out[:len(dry)] += dry
    for i in range(taps):
        d = int(sr * (0.018 + 0.9 * (i / taps) ** 1.4 * tail_pad))
        g = decay ** (i + 1)
        wet = lowpass(dry, dark * (0.9 ** i)) * g
        j = min(d + len(wet), n)
        out[d:j] += wet[:j - d] * (1 if i % 2 == 0 else -1)
    return out


def norm(x, peak=0.9):
    m = np.abs(x).max()
    return x * (peak / m) if m > 0 else x


def env(n, k):
    return np.exp(-np.arange(n) / n * k)


FX = {}

# ---- tick: tiny precise digital blip (32kHz -> pitch 8) ----------------
sr = 32000
n = int(0.035 * sr)
t = np.arange(n) / sr
tick = np.sin(2 * np.pi * 2093 * t) * env(n, 7)          # C7-ish
tick += np.sin(2 * np.pi * 4186 * t) * env(n, 12) * 0.3
tick[:32] += rng.standard_normal(32) * 0.12
FX["tick"] = (norm(room(tick, sr, 4, 0.4, 0.5, 0.09)), sr)

# ---- back: the tick's falling cousin -----------------------------------
n = int(0.07 * sr)
t = np.arange(n) / sr
f = 1560 * np.exp(-t * 9) + 780
back = np.sin(2 * np.pi * np.cumsum(f) / sr) * env(n, 6)
FX["back"] = (norm(room(back, sr, 4, 0.4, 0.45, 0.11)), sr)

# ---- boom: the confirm figure - "biddly BUM": two quick bright rising
# bell notes, then the big hit (sub drop + gong ring + impact), long
# bright tail (user: bigger, brighter, longer)
sr = 16000


def bellnote(freq, dur, sparkle=0.5):
    m = int(dur * sr)
    tt = np.arange(m) / sr
    w = np.sin(2 * np.pi * freq * tt) * env(m, 6)
    w += np.sin(2 * np.pi * freq * 3.01 * tt) * env(m, 10) * sparkle
    w += np.sin(2 * np.pi * freq * 5.4 * tt) * env(m, 14) * sparkle * 0.4
    w[:24] += rng.standard_normal(24) * 0.1
    return w


total = int(0.72 * sr)
dry = np.zeros(total)
dry[0:0] = 0
b1 = bellnote(659.3, 0.30)                        # E5  "bid-"
b2 = bellnote(880.0, 0.34)                        # A5  "-dly"
dry[:len(b1)] += b1 * 0.62
o = int(0.095 * sr)
dry[o:o + len(b2)] += b2 * 0.72

o = int(0.19 * sr)                                # "BUM"
n = total - o
t = np.arange(n) / sr
f = 130 * np.exp(-t * 9) + 48
sub = np.sin(2 * np.pi * np.cumsum(f) / sr) * env(n, 4.5)
gong = np.zeros(n)
for ratio, g, dk in ((1.0, 0.9, 3.5), (2.756, 0.55, 5), (4.07, 0.4, 6.5),
                     (5.42, 0.28, 8), (6.79, 0.18, 9.5), (8.21, 0.1, 11)):
    gong += np.sin(2 * np.pi * 220 * ratio * t
                   + rng.uniform(0, 6.28)) * g * env(n, dk)
imp = np.zeros(n)
imp[:int(0.015 * sr)] = rng.standard_normal(int(0.015 * sr)) \
    * env(int(0.015 * sr), 4) * 0.9
dry[o:] += sub * 1.05 + gong * 0.55 + lowpass(imp, 0.6)
FX["boom"] = (norm(room(dry, sr, 12, 0.58, 0.38, 0.38), 0.95), sr)

# --------------------------------------------------------- emit the .it
os.makedirs(OUTDIR, exist_ok=True)
kit = {}
for name, (data, rate) in FX.items():
    # pad to a multiple of 16 samples (BRR block alignment)
    pad = (-len(data)) % 16
    data = np.concatenate([data, np.zeros(pad)])
    kit[name] = (data, None, rate)
    sf.write(os.path.join(OUTDIR, "preview_%s.wav" % name),
             data.astype(np.float32), rate)
    print("%-5s %5.0fms at %dHz (%d samples)"
          % (name, 1000 * len(data) / rate, rate, len(data)))

ORDER = ["tick", "back", "boom"]   # = source indexes 0/1/2
I = {nm: ORDER.index(nm) + 1 for nm in ORDER}
ch = {0: [(i, 60, I[nm], 1, None) for i, nm in enumerate(ORDER)]}
M.write_it(os.path.join(ROOT, "assets", "music", "menusfx.it"),
           "MENUSFX", 125, kit, ORDER, ch, 16)
