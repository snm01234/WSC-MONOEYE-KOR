-- Reload one intermission state for each WonderSwan button, then capture/save.

local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local hold = tonumber(os.getenv("MONOEYE_HOLD") or "6")
local buttons = { "X1", "X2", "X3", "X4", "Y1", "Y2", "Y3", "Y4" }

os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\input_probe.log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end
local function press(button)
  for _ = 1, hold do
    joypad.set({ ["P1 " .. button] = true })
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({ ["P1 " .. button] = false })
    emu.frameadvance()
  end
end

write("ROM=" .. tostring(gameinfo.getromname()))
emu.frameadvance()
for _, button in ipairs(buttons) do
  local load_ok, load_err = pcall(savestate.load, state)
  if load_ok then
    advance(2)
    press(button)
    local shot = output .. "\\button_" .. button .. ".png"
    local final = output .. "\\button_" .. button .. ".State"
    local shot_ok, shot_err = pcall(client.screenshot, shot)
    local save_ok, save_err = pcall(savestate.save, final)
    write(string.format(
      "BUTTON %s LOAD %s SHOT %s SAVE %s ERR %s|%s|%s",
      button, tostring(load_ok), tostring(shot_ok), tostring(save_ok),
      tostring(load_err), tostring(shot_err), tostring(save_err)))
  else
    write("BUTTON " .. button .. " LOAD false ERR " .. tostring(load_err))
  end
end
write("DONE")
log:close()
client.exit()
