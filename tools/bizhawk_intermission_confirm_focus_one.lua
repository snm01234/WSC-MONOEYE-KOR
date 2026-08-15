-- Capture one leaf label at the frame where confirmation replaces its focus atlas.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG

local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "confirm_focus"
os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\" .. tag .. ".log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
local loaded, load_err = pcall(savestate.load, state)
write("LOAD=" .. tostring(loaded) .. " ERROR=" .. tostring(load_err))
if not loaded then log:close(); client.exit(); return end
for _ = 1, 2 do emu.frameadvance() end
pcall(client.screenshot, output .. "\\" .. tag .. "_before.png")
for _ = 1, 8 do
  joypad.set({ ["P1 A"] = true })
  emu.frameadvance()
end
local shot_ok, shot_err = pcall(client.screenshot, output .. "\\" .. tag .. "_confirm.png")
local state_ok, state_err = pcall(savestate.save, output .. "\\" .. tag .. "_confirm.State")
write(string.format(
  "CAPTURE frame=%d SHOT=%s STATE=%s ERROR=%s|%s",
  emu.framecount(), tostring(shot_ok), tostring(state_ok),
  tostring(shot_err), tostring(state_err)
))
write("DONE")
log:close()
client.exit()
