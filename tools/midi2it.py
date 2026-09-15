"""midi2it - Suno stems -> SNESMod-legal Impulse Tracker module.

Pipeline (PLAN.md "Sound" phase 2): Basic Pitch MIDIs (one per pitched
stem, in <songdir>/midi/) + the drum stem WAV -> quantise to a 16th-note
grid (tempo+offset FITTED to the drum onsets, never trusted from a
single estimate) -> reduce to 6 channels -> synthesise the house
instrument kit -> write .it. Channels 7-8 stay empty (the SFX pair;
SNESMod steals ch8). Also renders <songdir>/preview.wav - the same note
data through the same samples - so the arrangement can be judged by ear
before the SNES ever plays it.

Run under toolchain/py311-audio (needs numpy/soundfile/librosa/mido):
  toolchain/py311-audio/Scripts/python.exe tools/midi2it.py \
      assets/music/sunny_island -o assets/music/sunny_island.it

Channel plan (0-based IT channels):
  0 kick/snare   1 hats   2 bass(mono-low)   3 keys A(chord top)
  4 keys B(chord bottom, brass overlays and wins)   5 lead(mono-high)

SNESMod composition contract (pvsneslib_snesmod.txt): instrument mode,
linear slides, old effects OFF, no NNA/filters/envelopes-with-fancy
-features, loop widths multiples of 16, <=58K samples post-BRR.
"""
import argparse
import glob
import json
import os
import struct
import sys

import numpy as np
import soundfile as sf

C5 = 16744  # C5Speed for pitched samples: 64-sample loop = 261.6 Hz
SAMPLE_BITS = 8  # the WORKING SNESMod modules all ship 8-bit samples
NOTE_MAX = 95    # SNESMod: playback rate must stay under 128 kHz


# ---------------------------------------------------------------- tempo fit
def fit_grid(drum_wav, bpm_lo, bpm_hi):
    """Fit (bpm, offset) of the 16th grid to the drum stem's onsets by
    maximising the circular concentration of onset phases."""
    import librosa
    x, sr = sf.read(drum_wav, dtype='float32')
    if x.ndim > 1:
        x = x.mean(axis=1)
    on = librosa.onset.onset_detect(y=x, sr=sr, units='time',
                                    backtrack=False, delta=0.04, wait=2)
    best = (0.0, 120.0, 0.0)
    for bpm in np.arange(bpm_lo, bpm_hi, 0.01):
        step = 60.0 / bpm / 4
        z = np.exp(2j * np.pi * on / step)
        score = abs(z.mean())
        if score > best[0]:
            off = (np.angle(z.mean()) / (2 * np.pi)) * step
            best = (score, bpm, off % step)
    score, bpm, off = best
    print("grid fit: bpm=%.2f offset=%.1fms concentration=%.3f"
          % (bpm, off * 1000, score))
    return bpm, off, on, x, sr


# ------------------------------------------------------------------- drums
def drum_events(on, x, sr):
    """Classify each drum onset kick/snare/hat by band energy."""
    ev = []
    for t in on:
        i = int(t * sr)
        seg = x[i:i + int(0.06 * sr)]
        if len(seg) < 256:
            continue
        S = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1 / sr)
        lo = S[(f > 40) & (f < 150)].sum()
        mid = S[(f > 150) & (f < 1200)].sum()
        hi = S[(f > 4000) & (f < 12000)].sum()
        tot = lo + mid + hi + 1e-9
        amp = float(np.abs(seg).max())
        if lo / tot > 0.45:
            c = "kick"
        elif hi / tot > 0.55:
            c = "hat"
        else:
            c = "snare"
        ev.append((t, c, amp))
    return ev


# -------------------------------------------------------------------- midi
def midi_notes(path):
    """(start_s, dur_s, pitch, vel) list from a Basic Pitch MIDI."""
    import mido
    mf = mido.MidiFile(path)
    tempo = 500000
    for m in mf.tracks[0]:
        if m.type == "set_tempo":
            tempo = m.tempo
            break
    k = tempo / 1e6 / mf.ticks_per_beat
    notes = []
    for tr in mf.tracks:
        t = 0
        on = {}
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                on[m.note] = (t, m.velocity)
            elif m.type in ("note_off", "note_on") and m.note in on:
                s, v = on.pop(m.note)
                notes.append((s * k, (t - s) * k, m.note, v))
    notes.sort()
    return notes


def reduce_stream(notes, mode, step, off, vel_min=18):
    """Quantise and reduce to monophonic (row, pitch, vel, rows) events.
    mode: 'low' (bass), 'high' (lead), or 'chord2' -> two streams."""
    groups = {}
    for s, d, p, v in notes:
        if v < vel_min:
            continue
        r = int(round((s - off) / step))
        if r < 0:
            continue
        groups.setdefault(r, []).append((p, v, d))
    voices = ([], []) if mode == "chord2" else ([],)
    for r in sorted(groups):
        g = groups[r]
        vmax = max(v for _, v, _ in g)
        g = [n for n in g if n[1] >= 0.4 * vmax]
        g.sort()
        if mode == "low":
            p, v, d = g[0]
            voices[0].append((r, p, v, max(1, int(round(d / step)))))
        elif mode == "high":
            p, v, d = g[-1]
            voices[0].append((r, p, v, max(1, int(round(d / step)))))
        else:  # chord2: top to voice A, bottom to voice B
            p, v, d = g[-1]
            voices[0].append((r, p, v, max(1, int(round(d / step)))))
            if len(g) > 1 and g[0][0] != p:
                p2, v2, d2 = g[0]
                voices[1].append((r, p2, v2, max(1, int(round(d2 / step)))))
    return voices


# ------------------------------------------------------------- score mode
def score_events(path):
    """A COMPOSED score MIDI (tools/scores/*): tracks named Bass/Keys/
    Brass/Lead/Bells/Drums, notes already on the 16th grid. Returns
    (bpm, {trackname: [(row, len_rows, pitch, vel)]})."""
    import mido
    mf = mido.MidiFile(path)
    bpm = 125.0
    for m in mf.tracks[0]:
        if m.type == "set_tempo":
            bpm = mido.tempo2bpm(m.tempo)
    tpr = mf.ticks_per_beat / 4
    tracks = {}
    for tr in mf.tracks:
        t = 0
        on = {}
        notes = []
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                on[m.note] = (t, m.velocity)
            elif m.type in ("note_off", "note_on") and m.note in on:
                s, v = on.pop(m.note)
                notes.append((int(round(s / tpr)),
                              max(1, int(round((t - s) / tpr))), m.note, v))
        if notes and tr.name:
            tracks[tr.name] = sorted(notes)
    return bpm, tracks


GM_KICK, GM_SNARE, GM_HAT = 36, 38, 42
CMD_G, CMD_H = 7, 8  # IT effects: tone portamento, vibrato


def score_channels(tracks, I):
    """Map named score tracks onto the 6-channel plan. Lead gets the
    guitar treatment: a note starting at/before the previous note's end
    becomes a Gxx slide (bend) instead of a retrigger, and held notes
    get gentle Hxy vibrato rows."""
    ch = {i: [] for i in range(6)}

    def vol(v):
        return int(np.clip(v // 2, 1, 64))

    for r, ln, p, v in tracks.get("Drums", []):
        if p == GM_KICK:
            ch[0].append((r, 60, I["kick"], vol(v), None))
        elif p == GM_SNARE:
            ch[0].append((r, 60, I["snare"], vol(v), None))
        elif p == GM_HAT:
            ch[1].append((r, 60, I["hat"], vol(v), None))

    for r, ln, p, v in tracks.get("Bass", []):
        ch[2].append((r, p, I["bass"], vol(v), r + ln))

    groups = {}
    for r, ln, p, v in tracks.get("Keys", []):
        groups.setdefault(r, []).append((p, ln, v))
    for r in sorted(groups):
        g = sorted(groups[r])
        p, ln, v = g[-1]
        ch[3].append((r, p, I["keys"], vol(v), r + ln))
        if len(g) > 1:
            p, ln, v = g[0]
            ch[4].append((r, p, I["keys"], vol(v), r + ln))

    bre = [(r, p, I["brass"], vol(v), r + ln)
           for r, ln, p, v in tracks.get("Brass", [])]
    if bre:
        occupied = set()
        for r, note, ins, v, cut in bre:
            occupied.update(range(r, cut + 1))
        ch[4] = [e for e in ch[4] if e[0] not in occupied] + bre
        ch[4].sort()

    lead = [(r, ln, p, v, "lead") for r, ln, p, v in tracks.get("Lead", [])]
    lead += [(r, ln, p, v, "bell") for r, ln, p, v in tracks.get("Bells", [])]
    lead.sort()
    prev_end, prev_p = -99, 0
    for i, (r, ln, p, v, ins) in enumerate(lead):
        cmd = None
        if ins == "lead" and prev_end >= r and prev_p != p:
            # legato/overlap in the WRITTEN score = a bend: Gxx glides
            # from the still-held previous pitch, no re-pick
            cmd = (CMD_G, 0x20 if abs(p - prev_p) <= 2 else 0x40)
        prev_end, prev_p = r + ln, p
        cut = r + ln
        if ins == "lead":
            # sustain: ring to the next event across small written gaps
            # (guitar legato), and a touch into a real rest
            nxt = lead[i + 1][0] if i + 1 < len(lead) else None
            if nxt is not None and nxt - (r + ln) <= 4:
                cut = nxt
            else:
                cut = r + ln + 2
        ch[5].append((r, p, I[ins], vol(v), cut, cmd))
        if ins == "lead" and cut - r >= 5:
            for vr in range(r + 3, min(cut, r + 14)):
                ch[5].append((vr, None, None, None, None, (CMD_H, 0x23)))
    return ch


# ------------------------------------------------------- house instruments
def _norm(x, peak=0.85):
    m = np.abs(x).max()
    return x * (peak / m) if m > 0 else x


def synth_kit(rng):
    """name -> (data float32, loop_begin or None, C5Speed). Pitched
    samples end in a 64-sample loop (multiple of 16 - SNESMod unrolls or
    resamples anything else) designed for 261.6 Hz at C5Speed 16744."""
    kit = {}

    def cyc(harm, n=64):
        t = np.arange(n) / n
        w = np.zeros(n)
        for k, a in harm:
            w += a * np.sin(2 * np.pi * k * t)
        return w

    def pitched(harm, attack_cycles, attack_gain, bright):
        # attack = the loop cycle with extra brightness + noise, decaying
        # into the steady loop; total length = attack + one loop
        loop = cyc(harm)
        ab = cyc(harm + bright)
        att = []
        for i in range(attack_cycles):
            g = 1.0 - i / attack_cycles
            n = rng.standard_normal(64) * 0.12 * g
            att.append((loop + (ab - loop) * g + n) * (1 + attack_gain * g))
        data = np.concatenate(att + [loop])
        return _norm(data), len(data) - 64, C5

    # slap-ish bass: strong fundamental, snappy bright attack
    kit["bass"] = pitched([(1, 1.0), (2, 0.35), (3, 0.18)],
                          6, 0.8, [(5, 0.5), (7, 0.35)])
    # keys: soft square-ish organ pad
    kit["keys"] = pitched([(1, 1.0), (3, 0.33), (5, 0.2), (7, 0.14)],
                          3, 0.2, [(2, 0.2)])
    # lead: SUSTAINED electric guitar. Hard-clipped odd-heavy stack; the
    # loop is 16 cycles whose brightness swings through one full period
    # (seamless) - the harmonic "growl" that makes a distorted sustain
    # read as guitar instead of organ. Pick attack at FULL level (a
    # compressed guitar does not decay); vibrato/slides ride Gxx/Hxy.
    gbase = cyc([(1, 1.0), (2, 0.45), (3, 0.6), (4, 0.3), (5, 0.35),
                 (6, 0.18), (7, 0.2)])
    gbright = cyc([(8, 0.5), (9, 0.3), (11, 0.2)])
    gcyc = [np.tanh(3.2 * (gbase + 0.35 * np.cos(2 * np.pi * k / 16)
                           * gbright)) for k in range(16)]
    gatt = [np.tanh(3.2 * (gbase + 0.5 * gbright
                           + rng.standard_normal(64) * 0.35 * (1 - i / 5)))
            for i in range(5)]
    gdata = np.concatenate(gatt + gcyc)
    kit["lead"] = (_norm(gdata), 5 * 64, C5)
    # brass: saw stack, hard attack
    kit["brass"] = pitched([(1, 1.0), (2, 0.6), (3, 0.45), (4, 0.35),
                            (5, 0.28), (6, 0.22)], 5, 0.7, [(7, 0.3)])
    # bell/marimba: fundamental + strong 4th partial, loud strike
    # decaying into a QUIET loop (no envelopes - the tail fades by
    # construction and note cuts finish the job)
    strike = cyc([(1, 1.0), (4, 0.45), (10, 0.1)])
    tail = strike * 0.18
    cyl = [strike * (1.3 * 0.74 ** i + 0.18) for i in range(10)]
    bell = np.concatenate(cyl + [tail])
    kit["bell"] = (_norm(bell), len(bell) - 64, C5)

    sr = 16000
    t = np.arange(int(0.09 * sr)) / sr
    sweep = 95 * np.exp(-t * 22) + 40
    kick = np.sin(2 * np.pi * np.cumsum(sweep) / sr) * np.exp(-t * 18)
    kick[:40] += rng.standard_normal(40) * 0.4 * np.linspace(1, 0, 40)
    kit["kick"] = (_norm(kick), None, sr)

    t = np.arange(int(0.13 * sr)) / sr
    n = rng.standard_normal(len(t))
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30)
    snare = (n * np.exp(-t * 24) * 0.8 + body * 0.7)
    kit["snare"] = (_norm(snare), None, sr)

    t = np.arange(int(0.045 * sr)) / sr
    n = rng.standard_normal(len(t))
    n -= np.concatenate(([0], n[:-1]))  # crude hipass
    kit["hat"] = (_norm(n * np.exp(-t * 60), 0.7), None, sr)
    return kit


# --------------------------------------------------------------- IT writer
def pack_pattern(rows, nrows):
    """rows: {row: {chan: (note, ins, vol, cmdpair)}}; note 254 = cut,
    None = no note (a command-only row); cmdpair = (cmd 1-26, param)."""
    out = bytearray()
    for r in range(nrows):
        for ch in sorted(rows.get(r, {})):
            note, ins, vol, cmd = rows[r][ch]
            mask = 0
            if note is not None:
                mask |= 1
            if ins is not None:
                mask |= 2
            if vol is not None:
                mask |= 4
            if cmd is not None:
                mask |= 8
            if not mask:
                continue
            out.append((ch + 1) | 0x80)
            out.append(mask)
            if note is not None:
                out.append(note)
            if ins is not None:
                out.append(ins)
            if vol is not None:
                out.append(vol)
            if cmd is not None:
                out.append(cmd[0])
                out.append(cmd[1])
        out.append(0)
    return bytes(out)


def env_bytes():
    # 82 bytes: Flg=0(off) Num=2 LpB LpE SLB SLE + 25 nodes (s8 val,
    # u16 tick) + 1 trailing
    b = bytearray([0, 2, 0, 0, 0, 0])
    b += struct.pack("<bH", 64, 0) + struct.pack("<bH", 64, 1)
    b += bytes(23 * 3) + bytes(1)
    assert len(b) == 82
    return bytes(b)


def instrument_bytes(name, smp_1based):
    b = bytearray(b"IMPI")
    b += name[:12].ljust(12, "\0").encode()
    b += bytes([0, 0, 0, 0])              # 00h, NNA=cut, DCT, DCA
    b += struct.pack("<H", 128)           # fadeout
    b += bytes([0, 60, 128, 160, 0, 0])   # PPS PPC GbV DfP(off) RV RP
    b += struct.pack("<H", 0x0217) + bytes([1, 0])  # TrkVers NoS x
    b += name[:26].ljust(26, "\0").encode()
    b += bytes([0, 0, 0, 0]) + struct.pack("<H", 0)  # IFC IFR MCh MPr Bnk
    for i in range(120):
        b += bytes([i, smp_1based])       # every key -> this sample
    b += env_bytes() * 3                  # vol/pan/pitch envelopes: off
    b += bytes(554 - len(b))
    assert len(b) == 554
    return bytes(b)


def sample_bytes(name, data, loop_begin, c5, data_ofs):
    n = len(data)
    flg = 0x01 | (0x02 if SAMPLE_BITS == 16 else 0) \
        | (0x10 if loop_begin is not None else 0)
    b = bytearray(b"IMPS")
    b += name[:12].ljust(12, "\0").encode()
    b += bytes([0, 64, flg, 64])
    b += name[:26].ljust(26, "\0").encode()
    b += bytes([1, 32])                   # Cvt=signed, DfP off
    b += struct.pack("<IIII", n, loop_begin or 0,
                     n if loop_begin is not None else 0, c5)
    b += struct.pack("<III", 0, 0, data_ofs)
    b += bytes([0, 0, 0, 0])
    assert len(b) == 0x50
    return bytes(b)


def write_it(path, songname, bpm, kit, kit_order, channels, total_rows):
    """channels: {chan: [(row, note, ins_1based, vol, cut_row[, cmdpair])]}
    note None = command-only event (e.g. a vibrato row)."""
    ROWS = 64
    npat = (total_rows + ROWS - 1) // ROWS

    # scatter events into per-pattern row dicts: real notes win the row,
    # then command-only events, then cuts fill what remains
    pats = [dict() for _ in range(npat)]
    for rank in (0, 1, 2):
        for ch, evs in channels.items():
            for e in evs:
                r, note, ins, vol, cut = e[:5]
                cmd = e[5] if len(e) > 5 else None
                if rank == 0 and note is not None:
                    pats[r // ROWS].setdefault(r % ROWS, {})[ch] = \
                        (note, ins, vol, cmd)
                elif rank == 1 and note is None:
                    pats[r // ROWS].setdefault(r % ROWS, {}).setdefault(
                        ch, (None, None, None, cmd))
                elif rank == 2 and cut is not None and cut < total_rows:
                    pc, pr = divmod(cut, ROWS)
                    pats[pc].setdefault(pr, {}).setdefault(
                        ch, (254, None, None, None))

    packed = [pack_pattern(p, ROWS) for p in pats]
    # dedupe identical patterns (funk grooves repeat)
    uniq, order = [], []
    for pk in packed:
        if pk in uniq:
            order.append(uniq.index(pk))
        else:
            uniq.append(pk)
            order.append(len(uniq) - 1)
    print("patterns: %d orders -> %d unique, %d bytes packed"
          % (len(order), len(uniq), sum(len(u) for u in uniq)))

    orders = bytes(order) + b"\xff"
    n_ins = len(kit_order)

    hdr = bytearray(b"IMPM")
    hdr += songname[:26].ljust(26, "\0").encode()
    hdr += struct.pack("<HHHHH", 0x0410, len(orders), n_ins, n_ins,
                       len(uniq))
    hdr += struct.pack("<HHHH", 0x0217, 0x0214, 0x000D, 0)
    hdr += bytes([128, 48, 6, int(round(bpm)), 128, 0])  # GV MV IS IT Sep
    hdr += struct.pack("<HI", 0, 0) + bytes(4)
    hdr += bytes([32] * 8 + [128] * 56)   # channel pans
    hdr += bytes([64] * 64)               # channel vols
    assert len(hdr) == 0xC0

    pos = len(hdr) + len(orders) + 4 * n_ins * 2 + 4 * len(uniq)
    ins_ofs, smp_ofs, body = [], [], bytearray()
    for name in kit_order:
        ins_ofs.append(pos + len(body))
        body += instrument_bytes(name, kit_order.index(name) + 1)
    smp_hdr_pos = []
    for name in kit_order:
        smp_ofs.append(pos + len(body))
        smp_hdr_pos.append(len(body))
        body += bytes(0x50)               # patched once data offsets known
    pat_ofs = []
    for pk in uniq:
        pat_ofs.append(pos + len(body))
        body += struct.pack("<HH", len(pk), ROWS) + bytes(4) + pk
    for i, name in enumerate(kit_order):
        data, loop, c5 = kit[name]
        if SAMPLE_BITS == 16:
            pcm = np.clip(data * 32767, -32768, 32767).astype("<i2").tobytes()
        else:
            pcm = np.clip(data * 127, -128, 127).astype("i1").tobytes()
        sb = sample_bytes(name, data, loop, c5, pos + len(body))
        body[smp_hdr_pos[i]:smp_hdr_pos[i] + 0x50] = sb
        body += pcm

    with open(path, "wb") as f:
        f.write(hdr)
        f.write(orders)
        f.write(struct.pack("<%dI" % n_ins, *ins_ofs))
        f.write(struct.pack("<%dI" % n_ins, *smp_ofs))
        f.write(struct.pack("<%dI" % len(uniq), *pat_ofs))
        f.write(body)
    print("wrote %s (%d bytes)" % (path, len(hdr) + len(orders)
          + 8 * n_ins + 4 * len(uniq) + len(body)))


# ---------------------------------------------------------------- preview
def render_preview(path, bpm, kit, kit_order, channels, total_rows):
    sr = 32000
    step = 60.0 / bpm / 4
    out = np.zeros(int((total_rows + 8) * step * sr), dtype=np.float64)
    GAIN = {"kick": 0.9, "snare": 0.7, "hat": 0.35, "bass": 0.8,
            "keys": 0.4, "lead": 0.55, "brass": 0.6, "bell": 0.5}
    for ch, evs in channels.items():
        for i, e in enumerate(evs):
            r, note, ins, vol, cut = e[:5]
            if note is None or note > 200:
                continue
            name = kit_order[ins - 1]
            data, loop, c5 = kit[name]
            rate = c5 * 2 ** ((note - 60) / 12)
            end_r = cut if cut is not None else r + 16
            for j in range(i + 1, len(evs)):   # next NOTE on channel cuts
                if evs[j][0] > r and evs[j][1] is not None:
                    end_r = min(end_r, evs[j][0])
                    break
            dur = max(1, end_r - r) * step
            n_out = int(dur * sr)
            idx = (np.arange(n_out) * rate / sr)
            if loop is not None:
                lw = len(data) - loop
                idx = np.where(idx < len(data), idx,
                               loop + (idx - loop) % lw)
            else:
                n_out = min(n_out, int(len(data) * sr / rate))
                idx = idx[:n_out]
            seg = data[np.minimum(idx.astype(int), len(data) - 1)]
            fade = np.ones(len(seg))
            f = min(400, len(seg))
            fade[-f:] = np.linspace(1, 0, f)
            v = (vol if vol is not None else 48) / 64.0
            s0 = int((r * step) * sr)
            out[s0:s0 + len(seg)] += seg * fade * v * GAIN[name]
    out = _norm(out, 0.9)
    sf.write(path, out.astype(np.float32), sr)
    print("wrote", path, "(%.1fs)" % (len(out) / sr))


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("songdir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--bpm", type=float, help="centre of the bpm search")
    ap.add_argument("--bars", type=int, help="truncate to N bars")
    ap.add_argument("--score", help="composed score MIDI: skip the stem "
                    "transcription path and convert this directly")
    args = ap.parse_args()
    d = args.songdir

    cfgp = os.path.join(d, "song.json")
    cfg = json.load(open(cfgp)) if os.path.exists(cfgp) else {}
    bpm_hint = args.bpm or cfg.get("bpm", 128)

    KIT_ALL = ["kick", "snare", "hat", "bass", "keys", "lead", "brass",
               "bell"]
    if args.score:
        bpm, tracks = score_events(args.score)
        ch = score_channels(tracks, {n: KIT_ALL.index(n) + 1
                                     for n in KIT_ALL})
        total = max(e[0] for evs in ch.values() for e in evs) + 1
        total = ((total + 15) // 16) * 16  # whole bars, clean loop
        print("score: bpm=%.2f rows=%d events=%d"
              % (bpm, total, sum(len(v) for v in ch.values())))
        rng = np.random.default_rng(0x5EA)
        kit = synth_kit(rng)
        name = cfg.get("name",
                       os.path.basename(d.rstrip("/\\")).upper())
        write_it(args.out, name, bpm, kit, KIT_ALL, ch, total)
        render_preview(os.path.join(d, "preview.wav"), bpm, kit,
                       KIT_ALL, ch, total)
        return

    def stem(pat):
        m = glob.glob(os.path.join(d, pat))
        return m[0] if m else None

    bpm, off, on, dx, dsr = fit_grid(stem("*Drums.wav"),
                                     bpm_hint - 2, bpm_hint + 2)
    step = 60.0 / bpm / 4

    # drums -> channels 0 (kick/snare) and 1 (hats)
    ch = {0: [], 1: [], 2: [], 3: [], 4: [], 5: []}
    KIT = ["kick", "snare", "hat", "bass", "keys", "lead", "brass"]
    I = {n: KIT.index(n) + 1 for n in KIT}
    seen = {}
    for t, c, amp in drum_events(on, dx, dsr):
        r = int(round((t - off) / step))
        if r < 0:
            continue
        vol = int(np.clip(amp * 110, 20, 64))
        if c == "hat":
            if seen.get((1, r)) is None:
                ch[1].append((r, 60, I["hat"], min(vol, 44), None))
                seen[(1, r)] = True
        else:
            prev = seen.get((0, r))
            if prev is None or (c == "snare" and prev == "kick"):
                if prev is not None:
                    ch[0].pop()
                ch[0].append((r, 60, I[c], vol, None))
                seen[(0, r)] = c

    def load(pat):
        p = stem(os.path.join("midi", pat))
        return midi_notes(p) if p else []

    def events(voice, ins, base_vol=None):
        evs = []
        for r, p, v, rows in voice:
            vol = base_vol or int(np.clip(v // 2, 16, 64))
            while p > NOTE_MAX:  # SNESMod 128 kHz ceiling: fold an octave
                p -= 12
            evs.append((r, int(max(p, 0)), I[ins], vol, r + rows))
        return evs

    bass, = reduce_stream(load("*Bass*.mid"), "low", step, off)
    ch[2] = events(bass, "bass")
    keysA, keysB = reduce_stream(load("*Keyboard*.mid"), "chord2", step, off)
    ch[3] = events(keysA, "keys", 36)
    ch[4] = events(keysB, "keys", 32)
    lead, = reduce_stream(load("*Synth*.mid"), "high", step, off)
    ch[5] = events(lead, "lead", 48)
    brass, = reduce_stream(load("*Brass*.mid"), "high", step, off)
    bre = events(brass, "brass", 52)
    # brass overlays keys B and wins the channel while it plays
    if bre:
        occupied = set()
        for r, note, ins, vol, cut in bre:
            occupied.update(range(r, (cut or r + 1) + 1))
        ch[4] = [e for e in ch[4] if e[0] not in occupied] + bre
        ch[4].sort()

    total = max(e[0] for evs in ch.values() for e in evs) + 2
    if args.bars:
        total = min(total, args.bars * 16)
        ch = {c: [e for e in evs if e[0] < total - 1]
              for c, evs in ch.items()}
    print("rows:", total, " events:", sum(len(v) for v in ch.values()))

    rng = np.random.default_rng(0x5EA)
    kit = synth_kit(rng)
    name = cfg.get("name", os.path.basename(d.rstrip("/\\")).upper())
    write_it(args.out, name, bpm, kit, KIT, ch, total)
    render_preview(os.path.join(d, "preview.wav"), bpm, kit, KIT, ch, total)


if __name__ == "__main__":
    main()
