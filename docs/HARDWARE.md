# Hardware snag log

Real-console findings that no emulator reproduces. Kept so a CRT pass has a
target list and so the "pending" notes in CLAUDE.md have evidence behind them.
Nothing here is a committed fix — triage and severity only.

## 2026-10-29 — NTSC Sony Trinitron, flashcart, release build (e771ecc)

First proper CRT pass of the six authored courses + the full game flow.
User verdict: **all P3/P4 — not worth a major change before the jam.** Logged
for a later pass. Mesen and the web player are clean on every item below.

### Family A — top-of-sky / text-backing cells read blue-teal, not the zenith

The dominant finding, seen on every course and in every mode:

- Sunny Island: the blue behind PAUSED is slightly pale; **clouds above a
  certain height went blue** (first time ever seen — possibly triggered by
  pausing); **FINISH text at the end went blue instead of white**, same two
  colours as the cloud shift (so: the same bug).
- Sunset Cove (orange sky): the band **behind the top text and the very
  bottom of the sky are blue instead of orange**. User's own prior: "common
  on real hardware, not on emulators."
- Grey Lake (teal sky): same but only at the top; the horizon reads a touch
  oversaturated, a darker teal/green sits behind the text.
- Deep Blue: top blue is about right but a little off once moving; **three
  subtly different shades of blue behind the top text** on the end screen.
- Champion page (`CALLISTA IS CHAMPION`, full-screen mode 1): the whole
  backdrop reads **dark teal instead of sky** — user wondered if it's the
  water colour leaking in. Likely the worst case of this family because the
  entire frame is mode 1.

**What those pixels are** (see `bake_tables.py::sky_palette` / `phase_tables`
and the CGRAM map in the README): the band's top dither anchors (CGRAM
31 / 33..47, zenith → horizon), the transparent index-0 cells that show the
backdrop (CGRAM 0 = horizon − a15), and glyph-background index 15 → `csZen`
(CGRAM 79/95/111 = the course zenith). On a chromatic-sky course these should
all be the authored zenith hue; on hardware they skew blue-teal.

**Leading suspects — all already flagged "pending a CRT pass," now with
evidence. Hypotheses, unverified:**

1. **The sand-fade CGRAM HDMA** (`$2121` mode 3, repaints CGRAM 8 down the
   frame — explicitly never hardware-verified). If its CGADD latch or the
   write-twice CGADD/CGDATA pairing behaves differently on a real PPU it can
   disturb neighbouring entries / land a frame's worth of writes early.
2. **COLDATA / colour-math tint on the band.** "Three subtly different shades
   of blue" is a *gradient rendered in-hue but uniformly tinted*, which points
   at an additive/subtractive colour-math or COLDATA state the band inherits
   on hardware (the band is authored add-zero; the safe strip below it adds
   `0xE0|a15`). Emulators only apply EXTBG/colour-math in the exact modes, so
   they stay clean — the same class as the known BG2/EXTBG mangle.
3. **CGRAM writes slipping outside true hblank/vblank** during the per-frame
   palette/HDMA churn (hardware shows it as wrong/placed-late entries;
   emulators are lenient about write timing).

The Sunset "orange→blue at the very top" looks like a *specific* corrupted
entry; the Deep-Blue "three shades" looks like a *uniform tint*. There may be
two mechanisms inside this family — don't assume one fix clears all of it.

### Family B — horizon seam saturation / paleness

- Grey Lake horizon slightly oversaturated; Deep Blue horizon slightly pale.

The seam between the band's bottom anchor and the mode-7 safe strip is
`backdrop 0 + COLDATA a15`; the bake already WARNs when a `sky_horizon`
channel is below `a15` (today 14/31 → needs ≥112/255), because then the strip
can't reproduce the band's bottom colour. These two courses are probably near
that threshold, so the match that's exact in-emulator drifts on hardware.
**Cheapest future tweak (pure data, re-bake only):** nudge those courses'
`sky_horizon` channels up a notch. Not done — deferred with the rest.

### Possibly distinct (user-flagged)

- "Darker blue behind the copy at the top" — user: "might be something
  different" from Family A. Noted, not yet separated.

## If/when a CRT pass happens

Order of attack, cheapest first:
1. Family B horizon nudges — data-only, zero risk, might also calm Family A's
   "slightly pale" edge.
2. Instrument the sand-fade CGRAM HDMA on hardware (is CGRAM 31/zenith intact
   mid-frame?) to confirm or clear suspect 1.
3. Only then weigh a COLDATA/colour-math change — that one *is* a "major
   change" and the user has explicitly deferred it.
