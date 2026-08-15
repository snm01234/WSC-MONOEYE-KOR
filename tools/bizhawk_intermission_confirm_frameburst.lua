-- Capture every frame around the A-button confirmation animation.
-- Environment: MONOEYE_STATE, MONOEYE_OUT


local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "frameburst"
os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\" .. tag .. ".log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function capture(index, phase)
  local stem = output .. string.format("\\%s_frame_%02d_%s", tag, index, phase)
  local shot_ok, shot_err = pcall(client.screenshot, stem .. ".png")
  local state_ok, state_err = pcall(savestate.save, stem .. ".State")
  write(string.format(
    "FRAME index=%d emu=%d phase=%s SHOT=%s STATE=%s ERROR=%s|%s",
    index, emu.framecount(), phase, tostring(shot_ok), tostring(state_ok),
    tostring(shot_err), tostring(state_err)))
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
local loaded, load_err = pcall(savestate.load, state)
write("LOAD=" .. tostring(loaded) .. " ERROR=" .. tostring(load_err))
if not loaded then log:close(); client.exit(); return end
for _ = 1, 2 do emu.frameadvance() end
capture(0, "before")
for index = 1, 8 do
  joypad.set({ ["P1 A"] = true })
  emu.frameadvance()
  capture(index, "hold")
end
for index = 9, 32 do
  joypad.set({ ["P1 A"] = false })
  emu.frameadvance()
  capture(index, "release")
end
write("DONE")
log:close()
client.exit()
