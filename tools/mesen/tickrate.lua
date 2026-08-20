-- loop throughput: ticks completed in exactly 2000 frames.
-- env: OUTDIR, TICKADDR (hex WRAM addr of `tick` from the .sym - re-grep
-- every build, it moves), TICKTAG (output name suffix)
local outdir = os.getenv("OUTDIR") or "./"
local addr = tonumber(os.getenv("TICKADDR"), 16)
local tag = os.getenv("TICKTAG") or "x"
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if n == 2000 then
    local f = io.open(outdir .. "tick_" .. tag .. ".txt", "w")
    f:write(string.format("%d\n", emu.read16(addr, emu.memType.snesMemory)))
    f:close()
    emu.exit(0)
  end
end, emu.eventType.endFrame)
