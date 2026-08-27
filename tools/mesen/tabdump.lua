-- at the first frame where tick==300, dump camTabs (7200 bytes) and, if
-- DUMP7F=1, the $7F0000..$7F6FFF wave arrays
local outdir = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "run"
local tickAddr = tonumber(os.getenv("TICKADDR"), 16)
local tabAddr = tonumber(os.getenv("TABADDR"), 16)
local dump7f = os.getenv("DUMP7F") == "1"
local armed = false
local frames = 0
emu.addEventCallback(function()
  frames = frames + 1
  if frames > 20000 then emu.exit(1) end
  local t = emu.read16(tickAddr, emu.memType.snesMemory)
  if not armed then
    if t < 5 then armed = true end
    return
  end
  if t == 300 then
    local b = {}
    for i = 0, 7199 do
      b[#b+1] = string.char(emu.read(tabAddr + i, emu.memType.snesMemory))
    end
    local f = io.open(outdir .. tag .. "_tabs.bin", "wb")
    f:write(table.concat(b)) f:close()
    if dump7f then
      local c = {}
      for i = 0, 0x6FFF do
        c[#c+1] = string.char(emu.read(0x7F0000 + i, emu.memType.snesMemory))
      end
      local g = io.open(outdir .. tag .. "_7f.bin", "wb")
      g:write(table.concat(c)) g:close()
    end
    emu.exit(0)
  end
end, emu.eventType.endFrame)
