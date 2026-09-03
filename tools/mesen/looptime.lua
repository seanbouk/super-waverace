-- per-tick CPU time split: cycles from WaitForVBlank's RETURN (the loop
-- top) to buildCamTables' entry ("pre": flow, physics, CPU racers), and
-- from there to WaitForVBlank's ENTRY ("render": tables, sprites, spray,
-- HUD). Averaged per tick over FRAMES frames. env: OUTDIR, TAG, FRAMES,
-- WAIT=hexaddr (WaitForVBlank), WAITEND=hexaddr (the next symbol),
-- BUILD=hexaddr, TICKADDR. Testrunner: rides --timeout out.
local out = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "x"
local limit = tonumber(os.getenv("FRAMES") or "3000")
local W = tonumber(os.getenv("WAIT"), 16)
local WE = tonumber(os.getenv("WAITEND"), 16)
local B = tonumber(os.getenv("BUILD"), 16)
local tickAddr = tonumber(os.getenv("TICKADDR"), 16)
local lastW, lastB, pre, render, nB = 0, 0, 0, 0, 0
local function cyc() return emu.getState().cpu.cycleCount end
emu.addMemoryCallback(function() lastW = cyc() end, emu.callbackType.exec, W, WE - 1, emu.cpuType.snes, emu.memType.snesMemory)
emu.addMemoryCallback(function()
  local c = cyc()
  if lastW > 0 then pre = pre + (c - lastW) end
  lastB = c; nB = nB + 1
end, emu.callbackType.exec, B, B, emu.cpuType.snes, emu.memType.snesMemory)
emu.addMemoryCallback(function()
  if lastB > 0 then render = render + (cyc() - lastB); lastB = 0 end
end, emu.callbackType.exec, W, W, emu.cpuType.snes, emu.memType.snesMemory)
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if n == limit then
    local ticks = emu.read16(tickAddr, emu.memType.snesMemory)
    local f = io.open(out .. "loop_" .. tag .. ".txt", "w")
    f:write(string.format("frames %d ticks %d builds %d\npre/tick %.0f  render/tick %.0f  busy/tick %.0f cycles\n",
      limit, ticks, nB, pre / nB, render / nB, (pre + render) / nB))
    f:close()
  end
end, emu.eventType.endFrame)
