#!/usr/bin/env bash
# One-time toolchain setup for building the ROM on Windows under Git Bash.
#
# Installs PVSnesLib 4.6.0 to ~/pvsneslib and applies two local patches to
# devkitsnes/snes_rules that make it work with Git Bash + native GNU make
# (the stock rules assume a full MSYS2 environment):
#
#   1. The Windows linkfile path mangling is bypassed — wlalink accepts
#      C:/forward/slash paths, so LIBDIRSOBJSW can just equal LIBDIRSOBJS.
#   2. -I$(CURDIR) becomes -I. so project paths containing spaces survive.
#
# GNU make itself: winget install ezwinports.make
set -euo pipefail

PVS_HOME="$HOME/pvsneslib"
ZIP_URL="https://github.com/alekmaul/pvsneslib/releases/download/4.6.0/pvsneslib_460_64b_windows_release.zip"

if [ -d "$PVS_HOME" ]; then
  echo "~/pvsneslib already exists - skipping download"
else
  echo "Downloading PVSnesLib 4.6.0..."
  curl -sSL -o /tmp/pvsneslib.zip "$ZIP_URL"
  unzip -q /tmp/pvsneslib.zip -d "$HOME"
  rm /tmp/pvsneslib.zip
fi

echo "Patching devkitsnes/snes_rules for Git Bash..."
python - "$PVS_HOME/devkitsnes/snes_rules" <<'EOF'
import sys, re
path = sys.argv[1]
text = open(path, encoding="utf-8").read()

# Patch 1: bypass Windows linkfile path mangling
block = re.compile(
    r"ifeq \(\$\(OS\),Windows_NT\)\n"
    r"DRIDIROBJW.*?\n"
    r"REPDIROBJSW.*?\n"
    r"REPDIROBJSW1.*?\n"
    r"LIBDIRSOBJSW.*?\n"
    r"else\n",
    re.S,
)
text, n1 = block.subn(
    "ifeq ($(OS),Windows_NT)\nLIBDIRSOBJSW := ${LIBDIRSOBJS}\nelse\n", text
)

# Patch 2: relative include path (survives spaces in the project path)
text, n2 = text.replace("-I$(CURDIR)", "-I."), text.count("-I$(CURDIR)")

open(path, "w", encoding="utf-8", newline="\n").write(text)
print(f"  patch 1 (linkfile paths): {'applied' if n1 else 'already applied / not found'}")
print(f"  patch 2 (-I. include):    {'applied' if n2 else 'already applied / not found'}")
EOF

echo
echo "Done. Build with:"
echo "  export PVSNESLIB_HOME=\$HOME/pvsneslib"
echo "  make -C game"
