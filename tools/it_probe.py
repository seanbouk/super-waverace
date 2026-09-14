"""it_probe - generate cut-down .it variants (via midi2it's writer) to
bisect what the SNESMod SPC driver chokes on. Usage:
  python tools/it_probe.py <variant> -o assets/music/sunny_island.it
Variants: mini (1ch/8 notes), cuts (adds note-cuts), sixch (6 sparse
channels), dense (hats every row + busy bass), song1/song4 (the real
sunny island data truncated to 1/4 patterns).
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import midi2it as M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variant")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    KIT = ["kick", "snare", "hat", "bass", "keys", "lead", "brass"]
    I = {n: KIT.index(n) + 1 for n in KIT}
    rng = np.random.default_rng(0x5EA)
    kit = M.synth_kit(rng)
    ch = {c: [] for c in range(6)}
    total = 64
    v = args.variant

    if v in ("mini", "cuts"):
        for i in range(8):
            r = i * 8
            cut = (r + 4) if v == "cuts" else None
            ch[2].append((r, 45 + (i % 4) * 3, I["bass"], 48, cut))
    elif v == "sixch":
        for i in range(8):
            r = i * 8
            ch[0].append((r, 60, I["kick" if i % 2 == 0 else "snare"], 52, None))
            ch[1].append((r + 4, 60, I["hat"], 40, None))
            ch[2].append((r, 45, I["bass"], 48, r + 6))
            ch[3].append((r, 69, I["keys"], 36, r + 6))
            ch[4].append((r, 64, I["keys"], 32, r + 6))
            ch[5].append((r + 2, 76, I["lead"], 48, r + 7))
    elif v == "dense":
        for r in range(64):
            ch[1].append((r, 60, I["hat"], 20 + (r % 3) * 8, None))
            if r % 2 == 0:
                ch[0].append((r, 60, I["kick" if r % 4 == 0 else "snare"],
                              40 + r % 20, None))
            ch[2].append((r, 40 + r % 12, I["bass"], 30 + r % 30, r + 1))
            if r % 2:
                ch[3].append((r, 65 + r % 7, I["keys"], 30 + r % 20, r + 2))
                ch[4].append((r, 60 + r % 5, I["keys"], 30 + r % 15, r + 2))
            ch[5].append((r, 70 + r % 10, I["lead"], 30 + r % 25, r + 1))
        total = 64
    elif v.startswith("song"):
        # the real pipeline, truncated to N patterns
        import json
        bars = int(v[4:]) * 4
        sys.argv = ["midi2it", "assets/music/sunny_island", "-o", args.out,
                    "--bpm", "130.8", "--bars", str(bars)]
        M.main()
        return
    else:
        raise SystemExit("unknown variant")

    M.write_it(args.out, "PROBE_" + v.upper(), 131, kit, KIT, ch, total)


if __name__ == "__main__":
    main()
