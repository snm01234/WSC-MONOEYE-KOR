-- Load all twelve localized intermission focus states in one BizHawk process.
--
-- Environment
--   MONOEYE_STATE_DIR  directory containing <name>_clean.State
--   MONOEYE_OUT        screenshot/log output directory


local state_dir = assert(os.getenv("MONOEYE_STATE_DIR"), "MONOEYE_STATE_DIR required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local names = {
  "mission_status", "scouting", "advance",
  "supply", "list", "assignment",
  "development_plan", "remodel", "disassemble",
  "save", "load", "library",
}

os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\all_focus_capture.log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
for _, name in ipairs(names) do
  local state = state_dir .. "\\" .. name .. "_clean.State"
  local loaded, load_err = pcall(savestate.load, state)
  local shot, shot_err = false, nil
  if loaded then
    advance(2)
    shot, shot_err = pcall(client.screenshot, output .. "\\" .. name .. ".png")
  end
  write(string.format(
    "%s LOAD=%s SHOT=%s ERROR=%s|%s",
    name, tostring(loaded), tostring(shot), tostring(load_err), tostring(shot_err)))
end
write("DONE")
log:close()
client.exit()
