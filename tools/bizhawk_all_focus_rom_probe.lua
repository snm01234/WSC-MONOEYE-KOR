-- Prove that cursor movement fetches the localized focus sprites from the ROM.
-- Unlike *_clean.State, every source state below still contains Japanese VRAM.
--
-- Environment
--   MONOEYE_ROOT  repository root
--   MONOEYE_OUT   screenshot/log output directory


local root = assert(os.getenv("MONOEYE_ROOT"), "MONOEYE_ROOT required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local state_home = root .. "\\BizHawk-2.11.1-win-x64\\WonderSwan\\State\\monoeye ko expanded.Cygne"
local q1 = state_home .. "\\Mednafen.QuickSave1.State"
local q2 = state_home .. "\\Mednafen.QuickSave2.State"
local q3 = state_home .. "\\Mednafen.QuickSave3.State"
local top = root .. "\\out\\patch\\intermission_focus_sweep\\input_probe\\button_X1.State"
local cases = {
  { "mission_status", q3, "X1" },
  { "scouting", top, "X2" },
  { "advance", top, "X4" },
  { "list", q3, "X2" },
  { "assignment", q3, "X4" },
  { "remodel", q2, "X2" },
  { "disassemble", q2, "X4" },
  { "load", q1, "X2" },
  { "library", q1, "X4" },
}

os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\rom_probe.log", "w"))
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
  local name, state, button = case[1], case[2], case[3]
  local loaded, load_err = pcall(savestate.load, state)
  local shot, shot_err = false, nil
  if loaded then
    advance(2)
    press(button)
    shot, shot_err = pcall(client.screenshot, output .. "\\" .. name .. ".png")
  end
  write(string.format(
    "%s BUTTON=%s LOAD=%s SHOT=%s ERROR=%s|%s",
    name, button, tostring(loaded), tostring(shot),
    tostring(load_err), tostring(shot_err)))
end
write("DONE")
log:close()
client.exit()
