"""sf2extract - pull single looped samples out of a SoundFont (.sf2) for
the SNESMod instrument kit. SoundFonts carry loop points in their sample
headers, so this skips the fiddly loop-finding a raw WAV would need.

We bypass the preset/instrument routing and grab samples BY NAME from the
sample-header (shdr) chunk - FluidR3_GM's names are descriptive ("Rhodes",
"Slap Bass", "Marimba", ...). Each extracted sample keeps its own loop
region and root note; midi2it resamples/pitches from there.

Standalone dump:
  toolchain/py311-audio/Scripts/python.exe tools/sf2extract.py <file.sf2>
As a library: sf2 = SF2(path); names = sf2.list(); s = sf2.sample("Rhodes")
"""
import struct
import sys

import numpy as np


class SF2:
    def __init__(self, path):
        self.buf = open(path, "rb").read()
        self.smpl = None          # int16 PCM sample pool
        self.shdr = []            # list of sample-header dicts
        self._parse()

    def _chunks(self, data, off, end):
        """yield (tag, start, size) for RIFF chunks in [off, end)."""
        while off + 8 <= end:
            tag = data[off:off + 4]
            size = struct.unpack_from("<I", data, off + 4)[0]
            yield tag, off + 8, size
            off += 8 + size + (size & 1)   # chunks are word-aligned

    def _parse(self):
        b = self.buf
        assert b[:4] == b"RIFF" and b[8:12] == b"sfbk"
        for tag, s, sz in self._chunks(b, 12, len(b)):
            if tag != b"LIST":
                continue
            listtype = b[s:s + 4]
            for t2, s2, sz2 in self._chunks(b, s + 4, s + sz):
                if listtype == b"sdta" and t2 == b"smpl":
                    self.smpl = np.frombuffer(b, "<i2", sz2 // 2, s2)
                elif listtype == b"pdta" and t2 == b"shdr":
                    self._read_shdr(b, s2, sz2)

    def _read_shdr(self, b, off, size):
        for i in range(size // 46):
            o = off + i * 46
            name = b[o:o + 20].split(b"\0")[0].decode("latin1")
            (start, end, ls, le, rate) = struct.unpack_from("<5I", b, o + 20)
            pitch, corr, link, stype = struct.unpack_from("<BbHH", b, o + 40)
            if name == "EOS" or stype not in (1, 2, 4):  # mono/L/R only
                continue
            self.shdr.append(dict(name=name, start=start, end=end,
                                  loopstart=ls, loopend=le, rate=rate,
                                  pitch=pitch, corr=corr))

    def list(self):
        return [(h["name"], h["end"] - h["start"], h["pitch"],
                 h["loopend"] - h["loopstart"]) for h in self.shdr]

    def sample(self, name, exact=False):
        """(float32 data, loopstart, loopend, rate, root_midi) for the
        first sample whose name matches (case-insensitive substring)."""
        key = name.lower()
        for h in self.shdr:
            if (h["name"].lower() == key if exact else key in h["name"].lower()):
                d = self.smpl[h["start"]:h["end"]].astype(np.float32) / 32768.0
                root = h["pitch"] + h["corr"] / 100.0
                return (d, h["loopstart"] - h["start"],
                        h["loopend"] - h["start"], h["rate"], root)
        raise KeyError(name)


_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_from_name(name):
    """FluidR3 shdr pitch is a useless constant 60; the real root is in
    the NAME ('Rhodes C4', 'Slap Bass D3', 'Marimba C 4', 'Slap Bass GO'
    with an O for 0). Return MIDI note or None."""
    import re
    m = re.search(r"([A-G])\s*(#?)\s*([0-9O])(?:\(|$|\s)", name)
    if not m:
        return None
    semi = _NOTE[m.group(1)] + (1 if m.group(2) else 0)
    octv = 0 if m.group(3) == "O" else int(m.group(3))
    return 12 * (octv + 1) + semi   # MIDI: C4 = 60


def _resample(x, out_rate, in_rate):
    """band-limited resample in_rate -> out_rate (scipy poly)."""
    import math
    from scipy import signal
    g = math.gcd(int(out_rate), int(in_rate))
    return signal.resample_poly(x, int(out_rate) // g, int(in_rate) // g)


def _period(x, fmin=60, fmax=1200, rate=44100):
    """autocorrelation pitch period (samples) of a steady segment."""
    x = x - x.mean()
    lo, hi = int(rate / fmax), int(rate / fmin)
    best, bl = 0.0, lo
    for lag in range(lo, min(hi, len(x) // 2)):
        c = float(np.dot(x[:-lag], x[lag:]))
        if c > best:
            best, bl = c, lag
    return bl


# struck/decaying instruments: one-shots that ring out (no sustain loop -
# a single-period loop just machine-guns the strike). Substring match.
DECAY_KINDS = ("marimba", "glocken", "vibra", "bell", "xylo", "kalimba",
               "music box", "pizz", "harp", "koto", "steel drum")


def make_sample(sf2, name, out_rate=None, attack_ms=55, mode="auto",
                decay_ms=320):
    """Build a small SNES kit sample.
    Sustained: the WHOLE natural sample from 0..le (attack + sustain),
      looping the soundfont's own [ls:le] - seamless by the author's
      design, and untouched (no resample, no crossfade) so nothing can
      reintroduce a seam. out_rate=None keeps the native rate.
    Struck (marimba/vibes/...): attack + natural decay, one-shot.
    Returns (float32 data, loopstart|None, c5speed)."""
    data, ls, le, rate, _ = sf2.sample(name)
    root = note_from_name(name) or 60
    if out_rate and out_rate != rate:
        ratio = out_rate / rate
        data = _resample(data, out_rate, rate)
        ls, le = int(round(ls * ratio)), int(round(le * ratio))
    else:
        out_rate = rate
    le = min(le, len(data))

    struck = mode == "decay" or (
        mode == "auto" and any(k in name.lower() for k in DECAY_KINDS))
    if struck or le - ls < 32:
        atk = int(attack_ms / 1000 * out_rate)
        out = data[:atk + int(decay_ms / 1000 * out_rate)].copy()
        out[-64:] *= np.linspace(1, 0, min(64, len(out)))
        loop = None
    else:
        # short ATTACK + the author's UNTOUCHED loop [ls:le]. The loop
        # wrap (le->ls) is the soundfont author's seamless design, so it
        # is left exactly alone - nothing here can make it pulse. Only
        # the attack->loop JOIN is crossfaded, and that join plays ONCE
        # per note, not every loop. Native rate keeps the wrap seamless.
        atk = int(attack_ms / 1000 * out_rate)
        atk = min(atk, ls)                       # don't overrun into loop
        looplen = le - ls
        pad = (-looplen) % 16                    # x16 align by trimming
        loopreg = data[ls:le - (looplen % 16 if False else 0)].copy()
        if looplen % 16:
            loopreg = data[ls:le - (looplen % 16)].copy()
        head = data[:atk].copy()
        hx = min(48, atk // 3, len(loopreg))
        if hx > 0:
            head[-hx:] = (head[-hx:] * np.linspace(1, 0, hx)
                          + loopreg[:hx] * np.linspace(0, 1, hx))
        out = np.concatenate([head, loopreg])
        loop = len(head)
    m = np.abs(out).max()
    if m > 0:
        out = out * (0.9 / m)
    c5 = out_rate * 2 ** ((60 - root) / 12)
    return out.astype(np.float32), loop, c5


# the committed kit: role -> (sf2 sample name, make_sample kwargs). The
# 148MB .sf2 is local-only, so `bake` writes these to small committed
# WAVs + kit.json that midi2it loads (CI never sees the soundfont).
KIT = {
    "keys": ("Rhodes C5", dict(mode="decay", decay_ms=650, out_rate=22050)),
    "bass": ("Slap Bass D3", dict(out_rate=22050)),
    "pbass": ("Picked Bass E2", dict(out_rate=22050)),
    "flute": ("Flute G4", dict(out_rate=22050)),
    "sax": ("Tenor C5", dict(out_rate=22050)),
    "bell": ("Glocken C8", dict(out_rate=22050)),
    "marimba": ("Marimba C 5", dict(out_rate=22050)),
}


if __name__ == "__main__":
    sf2 = SF2(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == "bake":
        import json
        import os
        import soundfile as sf
        out = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "assets", "music", "kit"))
        os.makedirs(out, exist_ok=True)
        manifest = {}
        for role, (sfname, kw) in KIT.items():
            data, loop, c5 = make_sample(sf2, sfname, **kw)
            pcm = np.clip(data * 32767, -32768, 32767).astype("<i2")
            sf.write(os.path.join(out, role + ".wav"), pcm, 22050,
                     subtype="PCM_16")
            manifest[role] = {"loop": loop, "c5": round(c5, 2),
                              "src": sfname, "n": len(data)}
            print("%-8s <- %-16s %5d smp  loop=%s  c5=%.0f"
                  % (role, sfname, len(data),
                     "none" if loop is None else loop, c5))
        json.dump(manifest, open(os.path.join(out, "kit.json"), "w"),
                  indent=2)
        print("wrote", out, "+ kit.json (FluidR3_GM, Frank Wen, MIT)")
    elif len(sys.argv) > 2 and sys.argv[2] == "preview":
        # render each named candidate as a 3-note phrase for auditioning
        import os
        import soundfile as sf
        out = os.path.join(os.path.dirname(__file__), "..", "assets",
                           "music", "sf2preview")
        os.makedirs(os.path.normpath(out), exist_ok=True)
        # (sfname, kwargs) - EP is struck/decaying like a mallet, so a
        # longer one-shot at a reduced rate (it's used a lot; keep it small)
        CANDS = {
            "keys": ("Rhodes C5", dict(mode="decay", decay_ms=650,
                                       out_rate=22050)),
            "bass": ("Slap Bass D3", dict(out_rate=22050)),
            "pbass": ("Picked Bass E2", dict(out_rate=22050)),
            "flute": ("Flute G4", dict(out_rate=22050)),
            "sax": ("Tenor C5", dict(out_rate=22050)),
            "soprano": ("Soprano C5", dict(out_rate=22050)),
            "marimba": ("Marimba C 5", dict(out_rate=22050)),
            "vibes": ("Glocken C8", dict(out_rate=22050)),
        }
        SR = 22050
        for label, (sfname, kw) in CANDS.items():
            data, loop, c5 = make_sample(sf2, sfname, **kw)
            # play MIDI 55/60/64 for ~0.6s each
            buf = []
            for note in (55, 60, 64):
                rate = c5 * 2 ** ((note - 60) / 12)
                dur = int(0.6 * SR)
                idx = np.arange(dur) * rate / SR
                if loop is not None:
                    lw = len(data) - loop
                    idx = np.where(idx < len(data), idx,
                                   loop + (idx - loop) % lw)
                seg = data[np.minimum(idx.astype(int), len(data) - 1)]
                seg[-400:] *= np.linspace(1, 0, 400)
                buf.append(seg)
            sf.write(os.path.join(os.path.normpath(out),
                                  "%s.wav" % label),
                     np.concatenate(buf), SR)
            kb = len(data) * 9 / 16 / 1024      # approx BRR size
            print("%-8s <- %-18s %5d smp (~%.1fK BRR)  loop=%s  c5=%.0f"
                  % (label, sfname, len(data), kb,
                     "one-shot" if loop is None else len(data) - loop, c5))
    else:
        rows = sf2.list()
        print("%d samples\n" % len(rows))
        for name, ln, pitch, loop in sorted(rows):
            print("%-24s %7d smp  root=%3d  loop=%d"
                  % (name, ln, pitch, loop))
