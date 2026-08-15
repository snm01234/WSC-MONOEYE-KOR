-- Capture the second intermission atlas used after confirming a focused item.
--
-- Environment:
--   MONOEYE_ROOT  repository root
--   MONOEYE_OUT   output directory


local root = assert(os.getenv("MONOEYE_ROOT"), "MONOEYE_ROOT required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local state_home = root .. "\\BizHawk-2.11.1-win-x64\\WonderSwan\\State\\monoeye ko expanded.Cygne"
local cases = {
  {
    "operation",
    root .. "\\out\\patch\\intermission_all_focus_clean\\states\\mission_status_clean.State",
  },
  { "organization", state_home .. "\\Mednafen.QuickSave3.State" },
  { "development", state_home .. "\\Mednafen.QuickSave2.State" },
  { "system", state_home .. "\\Mednafen.QuickSave1.State" },
}
local checkpoints = { 0, 20, 40, 60, 76, 80, 84, 88, 92, 100, 120, 160, 200 }
local state_points = { [76] = true, [84] = true, [92] = true, [120] = true }

os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\confirm_timeline.log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end
local function press_a()
  for _ = 1, 8 do
    joypad.set({ ["P1 A"] = true })
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({ ["P1 A"] = false })
    emu.frameadvance()
  end
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
for _, case in ipairs(cases) do
  local name, state = case[1], case[2]
  local loaded, load_err = pcall(savestate.load, state)
  write(string.format("CASE %s LOAD=%s ERROR=%s", name, tostring(loaded), tostring(load_err)))
  if loaded then
    advance(2)
    pcall(client.screenshot, output .. "\\" .. name .. "_before.png")
    press_a()
    local elapsed = 0
    for _, target in ipairs(checkpoints) do
      advance(target - elapsed)
      elapsed = target
      local shot = output .. "\\" .. name .. string.format("_t%03d.png", target)
      local shot_ok, shot_err = pcall(client.screenshot, shot)
      local state_ok, state_err = true, nil
      if state_points[target] then
        local saved = output .. "\\" .. name .. string.format("_t%03d.State", target)
        state_ok, state_err = pcall(savestate.save, saved)
      end
      write(string.format(
        "POINT %s t=%d frame=%d SHOT=%s STATE=%s ERROR=%s|%s",
        name, target, emu.framecount(), tostring(shot_ok), tostring(state_ok),
        tostring(shot_err), tostring(state_err)))
    end
  end
end
write("DONE")
log:close()
client.exit()
