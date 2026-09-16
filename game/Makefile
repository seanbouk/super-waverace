ifeq ($(strip $(PVSNESLIB_HOME)),)
$(error "Please set the PVSNESLIB_HOME environment variable: https://github.com/alekmaul/pvsneslib/wiki/Installation")
endif

# ROMNAME and variables are used in the snes_rules file
export ROMNAME := superwaverace
export ROMTITLE := SUPER WAVERACE

# SNESMod soundbank: .it modules in assets/music/ -> smconv -> soundbank
# .asm/.h/.bnk in game/ (all gitignored). The EFFECTS bank lists FIRST
# (MOD 0; -f sizes the effect set against it, per the lib's example).
# -b 12: high banks are empty, and the profiler's code-bank map survives.
AUDIOFILES := ../assets/music/spikesfx.it ../assets/music/sunny_island.it ../assets/music/sunset_cove.it ../assets/music/grey_lake.it ../assets/music/deep_blue.it
export SOUNDBANK := soundbank
SMCONVFLAGS := -s -o $(SOUNDBANK) -V -b 12 -f

# hand-maintained hdr.asm: the IRQ vector points at irqStub (camera.asm)
AUTOHDR := 0

include ${PVSNESLIB_HOME}/devkitsnes/snes_rules

# snes_rules adds $(SOUNDBANK).asm to SFILES explicitly AND its *.asm
# wildcard picks the generated file up once it exists on disk - the
# duplicate soundbank.obj in the linkfile made wlalink drop the section
# ("Unresolved reference to SOUNDBANK__"). sort dedupes; object order in
# the linkfile is irrelevant (placement is section/bank-driven).
export OFILES := $(sort $(OFILES))

# prefer python3 only if it RUNS: on Windows the Store's App Execution Alias
# stub is named python3 (and comes back after being deleted) and exits 49
PYTHON := $(shell (python3 -c "pass" >/dev/null 2>&1 && echo python3) || (python -c "pass" >/dev/null 2>&1 && echo python) || echo python3)

.PHONY: all cleanLogs gen

# gen must run before buildWithSummary: the inner `make buildActual` re-globs
# sources, so files generated here are picked up on a clean build.
all: gen buildWithSummary

# soundbank.h must exist before the inner build compiles main.c (this
# Makefile has no header deps - the bake gotcha applies to MOD_ ids too)
gen: bake.stamp $(SOUNDBANK).asm

# nested wildcards: courses live in assets/courses/<nn>_<name>/ and wave
# profiles in assets/waves/ - edits there must retrigger the bake.
# assets/music is EXCLUDED on purpose: the bake never reads it, and the
# local-only Suno stems have SPACES in their names, which break make's
# space-separated prerequisite list ("No rule to make target .../0")
bake.stamp: ../tools/bake_tables.py ../tools/wave_params.json $(wildcard ../assets/*.png) $(wildcard ../assets/*.act) $(wildcard ../assets/*.json) $(wildcard ../assets/courses/*/*) $(wildcard ../assets/waves/*)
	@echo baking wave tables + sea texture ...
	$(PYTHON) ../tools/bake_tables.py
	@touch bake.stamp

buildActual: $(OFILES) $(ROMNAME).sfc

clean: cleanBuildRes cleanRom cleanGfx cleanLogs
	@rm -f bake.stamp sea.png sea*.png ski.png wavetables.asm wavedata.c wavedata.h
