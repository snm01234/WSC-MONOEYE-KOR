-- Natural A/B reload for the static-BG candidate, then save a steady state.
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

os.execute('mkdir "' .. output .. '" 2>nul')
os.execute('mkdir "' .. state_output .. '" 2>nul')
local log = assert(io.open(output .. "\\natural_reload.log", "w"))
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
local function shot(name)
  local path = output .. "\\" .. name .. ".png"
  local ok, err = pcall(client.screenshot, path)
  write(string.format("SHOT %s frame=%s ok=%s %s", name, tostring(emu.framecount()), tostring(ok), tostring(err)))
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
local loaded, load_err = pcall(savestate.load, q1)
write(string.format("LOADSTATE %s %s  %s", tostring(loaded), tostring(load_err), q1))
if not loaded then
  log:close()
  client.exit()
  return
end
advance(4)
shot("natural_reload_s00")
press("A")
write("SEQ A -> frame=" .. tostring(emu.framecount()))
shot("natural_reload_s01")
advance(120)
write("SEQ w120 -> frame=" .. tostring(emu.framecount()))
shot("natural_reload_s02")
press("B")
write("SEQ B -> frame=" .. tostring(emu.framecount()))
shot("natural_reload_s03")
advance(120)
write("SEQ w120 -> frame=" .. tostring(emu.framecount()))
shot("natural_reload_s04")
local saved, save_err = pcall(
  savestate.save, state_output .. "\\natural_reload_final.State")
write(string.format("SAVESTATE %s %s  %s", tostring(saved), tostring(save_err), state_output .. "\\natural_reload_final.State"))
write("DONE frame=" .. tostring(emu.framecount()))
log:close()
client.exit()
