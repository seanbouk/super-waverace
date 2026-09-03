-- exec-callback call counter: how often given ROM routines run over a fixed
-- frame window, normalised per loop tick - the way to find WHERE a loop got
-- slower between two builds (tick counts say that it did, not why).
-- env: OUTDIR, TAG, FRAMES (default 3000), TICKADDR (hex WRAM addr of tick),
-- FUNCS = "name=hexaddr,name=hexaddr,..." (CPU addresses from the .sym:
-- bank:addr as printed, e.g. 04baa3). Testrunner: rides --timeout out.
local out = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "x"
local limit = tonumber(os.getenv("FRAMES") or "3000")
local tickAddr = tonumber(os.getenv("TICKADDR"), 16)
local names, counts = {}, {}
for kv in string.gmatch(os.getenv("FUNCS") or "", "[^,]+") do
  local k, v = kv:match("(%w+)=(%x+)")
  local a = tonumber(v, 16)
  names[#names + 1] = k
  counts[k] = 0
  emu.addMemoryCallback(function() counts[k] = counts[k] + 1 end,
    emu.callbackType.exec, a, a, emu.cpuType.snes, emu.memType.snesMemory)
end
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if n == limit then
    local ticks = emu.read16(tickAddr, emu.memType.snesMemory)
    local f = io.open(out .. "calls_" .. tag .. ".txt", "w")
    f:write(string.format("frames %d ticks %d\n", limit, ticks))
    for _, k in ipairs(names) do
      f:write(string.format("%-16s %8d  %7.2f/tick\n", k, counts[k],
        counts[k] / math.max(1, ticks)))
    end
    f:close()
  end
end, emu.eventType.endFrame)
