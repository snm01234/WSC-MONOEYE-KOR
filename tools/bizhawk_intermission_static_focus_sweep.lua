-- Capture all twelve intermission focus states for the static-BG candidate.
--
-- Environment:
--   MONOEYE_ROOT       repository root
--   MONOEYE_OUT        screenshot/log directory
--   MONOEYE_STATE_OUT  savestate output directory

local root = assert(os.getenv("MONOEYE_ROOT"), "MONOEYE_ROOT required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local state_output = assert(os.getenv("MONOEYE_STATE_OUT"), "MONOEYE_STATE_OUT required")
local state_home = root .. "\\BizHawk-2.11.1-win-x64\\WonderSwan\\State\\monoeye ko expanded.Cygne"
local q1 = state_home .. "\\Mednafen.QuickSave1.State"
local q2 = state_home .. "\\Mednafen.QuickSave2.State"
local q3 = state_home .. "\\Mednafen.QuickSave3.State"
local cases = {
  { "mission_status", q3, "X1" },
  { "scouting", q3, "X1", "X2" },
  { "advance", q3, "X1", "X4" },
  { "supply", q3, nil },
  { "list", q3, "X2" },
  { "assignment", q3, "X4" },
  { "development_plan", q2, nil },
  { "remodel", q2, "X2" },
  { "disassemble", q2, "X4" },
  { "save", q1, nil },
  { "load", q1, "X2" },
  { "library", q1, "X4" },
}

os.execute('mkdir "' .. output .. '" 2>nul')
os.execute('mkdir "' .. state_output .. '" 2>nul')
local log = assert(io.open(output .. "\\focus_sweep.log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end
local function press(button)
  for _ = 1, 6 do
    joypad.set({ ["P1 " .. button] = true })
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({ ["P1 " .. button] = false })
    emu.frameadvance()
  end
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
for _, case in ipairs(cases) do
  local name, state = case[1], case[2]
  local loaded, load_err = pcall(savestate.load, state)
  local shot, shot_err = false, nil
  local saved, save_err = false, nil
  if loaded then
    advance(4)
    -- Savestates restore their old VRAM. Enter and leave the selected submenu so
    -- this candidate reloads both the static atlas and guarded private payload.
    press("A")
    advance(120)
    press("B")
    advance(120)
    for index = 3, #case do
      press(case[index])
      advance(2)
    end
    shot, shot_err = pcall(client.screenshot, output .. "\\" .. name .. ".png")
    saved, save_err = pcall(
      savestate.save, state_output .. "\\" .. name .. ".State")
  end
  write(string.format(
    "%s BUTTONS=%d LOAD=%s SHOT=%s SAVE=%s ERROR=%s|%s|%s",
    name, #case - 2, tostring(loaded), tostring(shot), tostring(saved),
    tostring(load_err), tostring(shot_err), tostring(save_err)))
end
write("DONE")
log:close()
client.exit()
