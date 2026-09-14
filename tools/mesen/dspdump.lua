-- dump per-voice DSP registers + globals every 40 frames
local out = "C:\\Users\\Sean\\AppData\\Local\\Temp\\claude\\C--Users-Sean-Downloads-super-waverace\\4cb30ab5-3928-4d8b-8c61-a2ad969e5cf1\\scratchpad\\dspdump.txt"
local lines = {}
local function d(a) return emu.read(a, emu.memType.spcDspRegisters) end

emu.addEventCallback(function()
  local f = emu.getState()["frameCount"]
  if f % 40 == 0 and f >= 120 and f <= 400 then
    lines[#lines + 1] = string.format(
      "frame %d MVOL=%d/%d EVOL=%d/%d KON=%02X KOF=%02X FLG=%02X DIR=%02X",
      f, d(0x0C), d(0x1C), d(0x2C), d(0x3C), d(0x4C), d(0x5C), d(0x6C), d(0x5D))
    for v = 0, 5 do
      local b = v * 16
      lines[#lines + 1] = string.format(
        "  v%d VOL=%d/%d PITCH=%04X SRCN=%d ADSR=%02X/%02X GAIN=%02X ENVX=%d OUTX=%d",
        v, d(b), d(b + 1), d(b + 2) + d(b + 3) * 256, d(b + 4),
        d(b + 5), d(b + 6), d(b + 7), d(b + 8), d(b + 9))
    end
  end
  if f == 400 then
    -- DIR table first 8 entries (source directory at DIR*0x100)
    local dir = d(0x5D) * 0x100
    for i = 0, 7 do
      local a = dir + i * 4
      lines[#lines + 1] = string.format(
        "  dir[%d] start=%04X loop=%04X", i,
        emu.read16(a, emu.memType.spcRam), emu.read16(a + 2, emu.memType.spcRam))
    end
    local fh = io.open(out, "w")
    fh:write(table.concat(lines, "\n"))
    fh:close()
  end
end, emu.eventType.endFrame)
