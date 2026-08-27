-- two-race flow harness: menu -> race1 -> results -> menu -> race2.
-- Needs an AUTOPILOT build (auto-confirms menu + results) and AllZeros
-- power-on RAM (see tickshot.lua). Screenshots each state; logs tick-keyed
-- position traces per race (flow_trace1/2.csv) and compares nothing itself.
-- ENV (bare hex): TICKADDR WXADDR WYADDR THADDR RSADDR, OUTDIR, all from
-- superwaverace.sym (they MOVE between builds).
local outdir = os.getenv("OUTDIR") or "./"
local tickA = tonumber(os.getenv("TICKADDR"), 16)
local wxA = tonumber(os.getenv("WXADDR"), 16)
local wyA = tonumber(os.getenv("WYADDR"), 16)
local thA = tonumber(os.getenv("THADDR"), 16)
local rsA = tonumber(os.getenv("RSADDR"), 16)
local function r16(a) return emu.read16(a, emu.memType.snesMemory) end

local n, race, lastTick, prevTick = 0, 0, -1, 99999
local lines = { {}, {} }
local shots = {}

local function shoot(name)
  if shots[name] then return end
  shots[name] = true
  local f = io.open(outdir .. name .. ".png", "wb")
  f:write(emu.takeScreenshot())
  f:close()
end

local function finish(code)
  for ri = 1, 2 do
    local f = io.open(outdir .. "flow_trace" .. ri .. ".csv", "w")
    f:write(table.concat(lines[ri], "\n"))
    f:close()
  end
  emu.exit(code)
end

emu.addEventCallback(function()
  n = n + 1
  if n == 200 then shoot("flow_menu1") end
  local t = r16(tickA)
  if t < 5 and prevTick > 1000 then race = race + 1 end
  if race == 0 and n > 300 and t > 100 and t < 5000 then race = 1 end
  prevTick = t
  if race >= 1 and race <= 2 then
    if t % 25 == 0 and t ~= lastTick and t > 0 then
      lastTick = t
      lines[race][#lines[race] + 1] = string.format("%d,%d,%d,%d",
        t, r16(wxA), r16(wyA), r16(thA))
    end
    if race == 1 and t >= 500 then shoot("flow_race1") end
    if race == 1 and emu.read(rsA, emu.memType.snesMemory) == 2 then
      shoot("flow_results1")
    end
    if race == 2 and t >= 500 then shoot("flow_race2") end
    if race == 2 and t >= 2000 then finish(0) end
  end
  if n > 80000 then finish(1) end
end, emu.eventType.endFrame)
