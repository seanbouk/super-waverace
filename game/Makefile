ifeq ($(strip $(PVSNESLIB_HOME)),)
$(error "Please set the PVSNESLIB_HOME environment variable: https://github.com/alekmaul/pvsneslib/wiki/Installation")
endif

# ROMNAME and variables are used in the snes_rules file
export ROMNAME := superwaverace
export ROMTITLE := SUPER WAVERACE

include ${PVSNESLIB_HOME}/devkitsnes/snes_rules

PYTHON := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)

.PHONY: all cleanLogs gen

# gen must run before buildWithSummary: the inner `make buildActual` re-globs
# sources, so files generated here are picked up on a clean build.
all: gen buildWithSummary

gen: bake.stamp sea.pc7

bake.stamp: ../tools/bake_tables.py
	@echo baking wave tables + sea texture ...
	$(PYTHON) ../tools/bake_tables.py
	@touch bake.stamp

sea.png: bake.stamp

sea.pc7: sea.png
	@echo convert bitmap ... $(notdir $@)
	$(GFXCONV) -p -m -M 7 -i $<

data.obj: sea.pc7

buildActual: $(OFILES) $(ROMNAME).sfc

clean: cleanBuildRes cleanRom cleanGfx cleanLogs
	@rm -f bake.stamp sea.png sea.pc7 sea.mp7 sea.pal wavetables.asm wavedata.c wavedata.h
