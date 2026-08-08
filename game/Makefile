ifeq ($(strip $(PVSNESLIB_HOME)),)
$(error "Please set the PVSNESLIB_HOME environment variable: https://github.com/alekmaul/pvsneslib/wiki/Installation")
endif

# ROMNAME and variables are used in the snes_rules file
export ROMNAME := superwaverace
export ROMTITLE := SUPER WAVERACE

include ${PVSNESLIB_HOME}/devkitsnes/snes_rules

.PHONY: all cleanLogs

all: buildWithSummary
buildActual: $(OBJS) $(ROMNAME).sfc

clean: cleanBuildRes cleanRom cleanGfx cleanLogs
