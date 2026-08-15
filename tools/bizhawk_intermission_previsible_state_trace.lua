-- Replay an already-loaded, still-black intermission state through fade-in.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG

local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_previsible"
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
write("LOAD=" .. tostring(loaded) .. " error=" .. tostring(load_err))
if not loaded then
  log:close()
  client.exit()
  return
end

joypad.set({ ["P1 A"] = false, ["P1 B"] = false })
for index = 0, 44 do
  local frame = emu.framecount()
  local shot = output .. string.format("\\%s_%02d_f%d.png", tag, index, frame)
  local ok, err = pcall(client.screenshot, shot)
  local state_ok, state_err = true, nil
  if index >= 3 and index <= 33 and index % 3 == 0 then
    state_ok, state_err = pcall(
      savestate.save,
      output .. string.format("\\%s_%02d_f%d.State", tag, index, frame))
  end
  write(string.format(
    "FRAME index=%d emu=%d shot=%s state=%s error=%s|%s",
    index, frame, tostring(ok), tostring(state_ok), tostring(err), tostring(state_err)))
  emu.frameadvance()
end
write("DONE frame=" .. tostring(emu.framecount()))
log:close()
client.exit()
