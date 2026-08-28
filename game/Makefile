ifeq ($(strip $(PVSNESLIB_HOME)),)
$(error "Please set the PVSNESLIB_HOME environment variable: https://github.com/alekmaul/pvsneslib/wiki/Installation")
endif

# ROMNAME and variables are used in the snes_rules file
export ROMNAME := superwaverace
export ROMTITLE := SUPER WAVERACE

# hand-maintained hdr.asm: the IRQ vector points at irqStub (camera.asm)
AUTOHDR := 0

include ${PVSNESLIB_HOME}/devkitsnes/snes_rules

# prefer python3 only if it RUNS: on Windows the Store's App Execution Alias
# stub is named python3 (and comes back after being deleted) and exits 49
PYTHON := $(shell (python3 -c "pass" >/dev/null 2>&1 && echo python3) || (python -c "pass" >/dev/null 2>&1 && echo python) || echo python3)

.PHONY: all cleanLogs gen

# gen must run before buildWithSummary: the inner `make buildActual` re-globs
# sources, so files generated here are picked up on a clean build.
all: gen buildWithSummary

gen: bake.stamp

# nested wildcards: courses live in assets/courses/<nn>_<name>/ and wave
# profiles in assets/waves/ - edits there must retrigger the bake
bake.stamp: ../tools/bake_tables.py ../tools/wave_params.json $(wildcard ../assets/*) $(wildcard ../assets/*/*) $(wildcard ../assets/*/*/*)
	@echo baking wave tables + sea texture ...
	$(PYTHON) ../tools/bake_tables.py
	@touch bake.stamp

buildActual: $(OFILES) $(ROMNAME).sfc

clean: cleanBuildRes cleanRom cleanGfx cleanLogs
	@rm -f bake.stamp sea.png sea*.png ski.png wavetables.asm wavedata.c wavedata.h
