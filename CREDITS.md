# Credits

**Super Waverace** — a Mode 7 SNES jet-ski racer.
For the [SNES DEV Game Jam 2026](https://itch.io/jam/snes-dev-game-jam-2026).

## Game

- **Design, code, art, courses** — Sean Thompson (seanbouk).
- **Rider & jet-ski art** — authored by Sean Thompson (Photoshop).
- **Course layouts** — authored by Sean Thompson in the in-repo course
  editor (`tools/course-painter`).
- **Boot epigraph** ("The wave that bears you is brother to the wave that
  breaks you. — Poseidon") — an original line written by Sean Thompson; the
  "Poseidon" attribution is a stylistic flourish, not a quotation.

## Music & sound

All music and sound effects in the ROM are **original and self-made**:
the tracks are original compositions, the melody/percussion are rendered to
Impulse Tracker modules by the in-repo tools (`tools/midi2it.py`,
`tools/mksfx.py`) and played back by SNESMod. **No ripped, licensed or
AI-generated audio ships in the game.** (Suno was used only as a disposable
compositional *reference* while prototyping the course themes — nothing it
produced is present in the ROM or this repository.)

- **Sampled melody instruments** (the keys / bass / flute / sax / bell
  voices) are extracted from the **FluidR3_GM** General MIDI SoundFont by
  **Frank Wen**, used under the MIT License — see `LICENSE` for the full
  notice. Drums, lead guitar, brass and pad are synthesised in-repo.

## Tools & libraries

- **PVSnesLib** by Alekmaul — SNES SDK (zlib license). Bundles:
  - **SNESMod** SPC700 music/SFX driver by **Mukunda Johnson**.
  - the **tcc-816** compiler and **WLA-DX** assembler/linker toolchain.
- **Mesen 2** — development emulator / debugger (not distributed).
- **EmulatorJS** (snes9x core) — powers the in-browser web player only; it
  is **not** part of the SNES ROM.

See `LICENSE` for the third-party license notices that accompany the
distributed ROM.
