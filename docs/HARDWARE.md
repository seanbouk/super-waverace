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

**Investigation Oct 29 (what the emulator CAN prove):**

- **The ROM palette is correct.** Live CGRAM dump during attract (Sunny
  Island) via `scratchpad/cgdump.lua` on the release ROM:
  `C0`(backdrop)=(2,7,17), `C29`(cloud white)=(31,31,31),
  `C30`(cloud shade)=(25,27,30), `C31`(zenith/HUD backing)=(2,7,18),
  `C40/C47`(sky anchors)=(9,14,24)/(16,21,31), `C79/95/111`(csZen)=(2,7,18).
  Every entry the blue appears on is the authored colour. **So Family A is a
  hardware-side rendering effect, not a baked bug** — nothing to fix in the
  palette/bake.
- **It is not additive colour math and not the sub-screen.** `CGADSUB=0x21`
  (add, BG1+backdrop), `CGWSEL=0x00` (fixed colour, sub-screen ignored). An
  *additive* COLDATA can't turn white (31,31,31) into blue, and the clouds
  are **BG3**, which isn't in colour math at all — so "white clouds / white
  FINISH text → blue" cannot be colour-math or sub-screen bleed. That kills
  suspect 2 for the white-goes-blue symptom and suspect 3's sub-screen angle.
- Most likely residue: a true analog / PPU-readback skew of the lightest sky
  colours on this console+CRT, which no emulator models. Not confidently
  ROM-fixable. The one part with a ROM-side hook is the "slightly **pale**"
  top (suspect 2's COLDATA half) — see the experiment below.

**Experiment 1 — COLDATA=0xE0 in vblTop — TRIED AND REVERTED (Oct 29).**
Added `REG_COLDATA = 0xE0` to the per-frame frame-top restore, on the theory
a stale crest-glow add bled onto the top band. Shipped in `209f35d`, flashed
to the NTSC Trinitron. **Result: no change** — the top was still blue on the
results screen. Reverted in the following commit. (It only ever targeted the
"pale top during live racing/pause", which that arcade run didn't exercise,
so the pale-top theory is neither confirmed nor refuted — but the results
blue is a *different* symptom, see below.)

**Sharpened by the second hardware run (Oct 29, arcade on Sunset Cove):**
the whole **race rendered with correct colours**; the blue appeared **only
when the results screen came up**, and only **at the very top and just above
the horizon** — the two scanline-boundary regions, not the middle of the
gradient. That is decisive:

- It is **results-state-specific**, not a global palette/analog skew (the
  same course's live race was clean).
- "Just above the horizon" is the **mode-1→mode-7 switch / safe-strip seam**;
  "very top" is the band start. Blue at the seam = a line or two of **sea**
  showing where sky should, i.e. the switch/backdrop landing a hair off on
  hardware — the **same class as the documented web-player bug** ("the
  mid-frame mode switch lands a few lines late and drew sea over the HUD").
- The only sky-region deltas at `raceState==2` vs a live race are
  `skyUp=1` + `cloudHofs=0` (BG3 holds the results table and stops scrolling)
  and the static, stopped camera. So the suspect is the **scroll / mode-switch
  timing at the seam when the scene is static and BG3 is the table**, not any
  colour register.

**Status: NOT a quick ROM tweak.** Palette is proven correct, colour math is
proven innocent, and the remaining cause is a hardware mode-switch/seam timing
effect with no emulator to verify a fix against. It shows up only on the
*static results screen* — the actual racing (where players spend ~all their
time) is clean on hardware. **Recommendation: accept + document for the jam.**
If revisited post-jam, the targeted move is to give the results/finish screen
its own clean full-mode-1 sky instead of leaving the live mode-7 seam on
screen under the table — that is a real change, not a one-liner, and still
needs CRT iteration.

### Family B — horizon seam saturation / paleness  *(ADDRESSED Oct 29)*

- Grey Lake horizon slightly oversaturated; Deep Blue horizon slightly pale.

Root cause: both courses shipped `sky_horizon: None`, so the bake auto-derived
the horizon as `zenith + a15` in **every** channel (the seam rule forces each
horizon channel ≥ `a15` = 14/31 = 112/255, or the mode-7 safe strip can't
reproduce the band's bottom colour). That forced-brightening is exactly the
symptom: Deep Blue's deep navy had red/green shoved up to ~120 → washed pale;
Grey Lake's muted teal-grey got a cool blue bias → oversaturated.

**Fix (data only, no code):** authored explicit `sky_horizon` per course,
kept seam-legal (every channel ≥ 112 → no bake warning):
- Grey Lake `#d8d8e0` (5-bit 27,27,28) — neutral grey, kills the cool-teal
  bias; backdrop-behind-text goes neutral too (bd5 13,13,14).
- Deep Blue `#7078f0` (5-bit 14,15,30) — green dropped to cut the cyan
  paleness, blue kept deep (bd5 0,1,16, a deeper blue behind text).

Verified: validate_course clean, rebake clean (no seam warning, ROM at cap),
and the emulator baselines now read overcast-grey / deep-blue as intended
(race shots, autopilot courses 2 & 3). The **hardware** improvement still
needs the user's CRT to confirm — this tunes the baseline toward the safe
region, it doesn't prove the hardware delta is gone.

### Possibly distinct (user-flagged)

- "Darker blue behind the copy at the top" — user: "might be something
  different" from Family A. Noted, not yet separated.

## If/when a CRT pass happens

Order of attack, cheapest first:
1. ~~Family B horizon nudges~~ — DONE Oct 29 (authored `sky_horizon` for Grey
   Lake + Deep Blue). Confirm on the CRT next time the flashcart is out.
2. Instrument the sand-fade CGRAM HDMA on hardware (is CGRAM 31/zenith intact
   mid-frame?) to confirm or clear suspect 1.
3. Only then weigh a COLDATA/colour-math change — that one *is* a "major
   change" and the user has explicitly deferred it.
