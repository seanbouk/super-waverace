"""stems2midi - merge a song folder's Basic Pitch stem MIDIs (plus a
drum track transcribed from the drum stem WAV) into ONE multi-track
MIDI for auditioning/editing in a DAW, BEFORE the 6-channel cut-down.

Tracks carry the raw transcription (full polyphony, no quantise); the
file tempo is the fitted BPM and note times are shifted by the fitted
grid offset so bars line up in the editor. Drums land on MIDI channel
10 as GM kick/snare/closed-hat.

Run under toolchain/py311-audio:
  toolchain/py311-audio/Scripts/python.exe tools/stems2midi.py \
      assets/music/sunny_island [-o out.mid] [--bpm 130.8]
"""
import argparse
import glob
import os
import sys

import mido

sys.path.insert(0, os.path.dirname(__file__))
import midi2it as M

# stem-name fragment -> (track name, GM program, MIDI channel)
ROLES = [
    ("Bass",     ("Bass",     36, 1)),   # slap bass
    ("Guitar",   ("Guitar",   27, 2)),   # clean electric
    ("Keyboard", ("Keyboard",  4, 3)),   # electric piano
    ("Synth",    ("Synth",    81, 4)),   # square lead
    ("Brass",    ("Brass",    61, 5)),   # brass section
    ("Other",    ("Other",    48, 6)),   # strings
]
GM_DRUM = {"kick": 36, "snare": 38, "hat": 42}
TPB = 480


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("songdir")
    ap.add_argument("-o", "--out")
    ap.add_argument("--bpm", type=float, default=128)
    args = ap.parse_args()
    d = args.songdir
    out = args.out or os.path.join(
        d, os.path.basename(d.rstrip("/\\")) + "_all_stems.mid")

    drum_wav = glob.glob(os.path.join(d, "*Drums.wav"))[0]
    bpm, off, on, dx, dsr = M.fit_grid(drum_wav, args.bpm - 2, args.bpm + 2)
    spb = 60.0 / bpm  # seconds per beat

    def ticks(t):
        return max(0, int(round((t - off) / spb * TPB)))

    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    meta.append(mido.MetaMessage("time_signature", numerator=4,
                                 denominator=4))
    mid.tracks.append(meta)

    def add_track(name, events, program=None, channel=0):
        """events: (start_s, dur_s, pitch, vel), absolute seconds."""
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name))
        if program is not None:
            tr.append(mido.Message("program_change", program=program,
                                   channel=channel))
        msgs = []
        for s, dur, p, v in events:
            msgs.append((ticks(s), 1, mido.Message(
                "note_on", note=p, velocity=v, channel=channel)))
            msgs.append((ticks(s + max(dur, 0.03)), 0, mido.Message(
                "note_off", note=p, velocity=0, channel=channel)))
        msgs.sort(key=lambda m: (m[0], m[1]))
        t = 0
        for at, _, m in msgs:
            m.time = at - t
            t = at
            tr.append(m)
        mid.tracks.append(tr)
        print("  %-9s %4d notes" % (name, len(events)))

    for frag, (name, prog, chan) in ROLES:
        files = glob.glob(os.path.join(d, "midi", "*%s*.mid" % frag))
        if not files:
            continue
        notes = M.midi_notes(files[0])
        add_track(name, [(s, dur, p, v) for s, dur, p, v in notes],
                  prog, chan)

    drums = [(t, 0.1, GM_DRUM[c], int(min(40 + amp * 180, 127)))
             for t, c, amp in M.drum_events(on, dx, dsr)]
    add_track("Drums", drums, None, 9)

    mid.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
