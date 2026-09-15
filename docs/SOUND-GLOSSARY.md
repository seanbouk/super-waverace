# Sound glossary — describing instrument changes

How to ask for changes to the music's instruments, what each one is
doing today, and where the hard walls are. Every sound is a small
synthesised sample (`tools/midi2it.py`, `synth_kit`) played by the SNES
DSP; there are no filters or amp models — timbre changes mean
re-authoring the wave, which is cheap.

## Shared vocabulary (applies to every pitched instrument)

| Term | What it turns |
|---|---|
| brighter / duller | more / fewer high harmonics in the wave |
| buzzier / hollower | all harmonics (saw-like) vs odd-only (square-like) |
| fatter / thinner | more / less fundamental and low harmonics |
| more / less drive, crunchier / cleaner | saturation (clip) amount |
| harder / softer attack | strength & noise of the onset transient |
| more pluck | louder attack relative to the sustain |
| longer / shorter release | the fade after a note ends (envelope) |
| decay while held | piano/EP-style: loud onset settling to quieter hold |
| swell | slow volume rise after the note starts (pads, brass) |
| louder / quieter | that instrument's slot in the shared MIX table |
| pan left / right | per-instrument stereo position (all centred today) |

Lead-only (the guitar treatment): **wider / faster / slower vibrato**,
**delay the vibrato** (start straight, then wobble), **slower / faster
bends** (the Gxx slides), **more / less growl** (the motion baked into
the sustain loop — static = organ, more = chewing distortion).

Reference-by-example always works: "more like a Rhodes", "like the
Top Gear lead", "like Dire Straits neck pickup" — I translate.

## The instruments today

- **Kick** — 90ms pitch-drop thump (95→40Hz) with a noise click on top.
  Soft-ish, 909-adjacent. Ask for: *punchier* (faster drop, more
  click), *boomier* (longer, deeper), *tighter* (shorter).
- **Snare** — 130ms noise burst over a 185Hz tone body. Ask for: *more
  snap/crack* (noise), *fatter body* (tone), *tighter/longer*.
- **Hat** — 45ms hi-passed noise tick. Ask for: *crisper*, *shorter*,
  *more sizzle*; an **open hat** would be a second sample (easy) but
  costs nothing musically — say the word.
- **Bass** — round fundamental-heavy wave with a short bright snap on
  the onset; sits between finger-bass and synth-bass, more finger than
  slap right now. Ask for: *more slap/snap*, *rounder/subbier*, *more
  mid growl*, *shorter/punchier notes* (that one's a score change).
- **Keys (EP)** — soft odd-harmonic wave, gentle attack; honestly more
  organ than electric piano. Ask for: *more EP/Rhodes* (bell-ish
  partial on the attack + decay-while-held), *warmer*, *glassier*.
- **Brass** — full saw stack with a hard attack; raspy synth-brass
  stab. Ask for: *rounder*, *a swell* (slow attack for held notes),
  *stabbier*, *brassier* (detuned-two-voice fatness is possible: it
  doubles the sample, not the channels).
- **Lead (guitar)** — hard-clipped odd-heavy wave; the sustain loop
  swings its brightness (the growl); full-level pick attack; automatic
  vibrato on holds, slides where score notes touch. Your read:
  "triangle with distortion" — i.e. the core wave is too hollow/pure.
  Ask for: *more bite* (upper-mid harmonics), *thicker/power-chord*,
  *smoother/neck-pickup*, plus the lead-only dials above.
- **Bell (marimba)** — strike (fundamental + strong 4th partial +
  shimmer) decaying into a quiet ring. Ask for: *woodier/marimba*
  (fast decay, mellow), *glassier/vibes* (higher partials, longer
  ring), *shorter/longer ring*.

## Hard limitations (the walls)

- **One timbre per instrument per song.** Velocity only changes volume
  — no soft/hard layers. A second articulation (open hat, palm-mute
  guitar) = another instrument slot: cheap in ARAM, but two
  articulations can't SOUND at once on one channel.
- **A channel is monophonic** and a new note cuts the previous one —
  release tails truncate under dense writing. 6 music channels total
  (7-8 are the SFX pair).
- **No filters, no wah, no filter sweeps.** Approximations exist
  (re-authored waves, volume tricks) but a moving filter is not a thing
  the DSP does.
- **Registers are baked in.** A sample is authored for the octave it
  plays in: the DSP's gaussian filter dulls everything as notes go
  lower, and very high notes thin out and hit the 128kHz ceiling (we
  fold anything above note 83). "Same sound but two octaves down"
  usually means re-voicing the sample.
- **Echo exists but is OFF.** The DSP has a genuine echo/delay
  (per-channel enable, delay, feedback) — very SNES, and cheap for us
  now (~2KB ARAM per 16ms of delay; we have ~38KB spare). "Add some
  echo to the lead/snare" is a fair request.
- **Pitch bends are stepped per tick** (24 ticks/beat) — fast wide
  bends are convincing, ultra-slow ones can zipper.
- I can't hear any of it: **preview_it.wav** (real tracker render of
  the actual module) is the shared reference, your ears on the ROM are
  the judge, and register metrics (dutycheck/dspdump) only prove notes
  key on and hold.
