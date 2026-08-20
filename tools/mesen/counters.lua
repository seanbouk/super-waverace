-- dual-run equivalence harness readout: two u16 WRAM counters (typically
-- calls/bad) logged every 500 frames. env: OUTDIR, ADDR_CALLS, ADDR_BAD
-- (hex, from the .sym - they move between builds)
local outdir = os.getenv("OUTDIR") or "./"
local aCalls = tonumber(os.getenv("ADDR_CALLS"), 16)
local aBad = tonumber(os.getenv("ADDR_BAD"), 16)
local log = io.open(outdir .. "counters.log", "w")
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if n % 500 == 0 then
    log:write(string.format("frame %-5d calls=%-6d bad=%d\n", n,
      emu.read16(aCalls, emu.memType.snesMemory),
      emu.read16(aBad, emu.memType.snesMemory)))
    log:flush()
  end
  if n >= 2000 then
    log:close()
    emu.exit(0)
  end
end, emu.eventType.endFrame)
