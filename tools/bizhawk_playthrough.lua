local log_path = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\playthrough.log"
local log = io.open(log_path, "w")

local function write(message)
  log:write(message .. "\n")
  log:flush()
end

local function pulse(button)
  local key = "P1 " .. button
  for hold = 1, 12 do
    joypad.set({[key] = true})
    if hold == 1 then
      local state = joypad.get()
      write("STATE " .. key .. "=" .. tostring(state[key]))
    end
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({[key] = false})
    emu.frameadvance()
  end
  if client.getwindowsize() ~= 2 then
    client.setwindowsize(2)
  end
  write("PULSE frame=" .. emu.framecount() .. " button=" .. button)
end

local function pulse_all()
  local buttons = {
    ["P1 Start"] = true,
    ["P1 A"] = true,
    ["P1 B"] = true,
    ["P1 X1"] = true,
    ["P1 X2"] = true,
    ["P1 X3"] = true,
    ["P1 X4"] = true,
    ["P1 Y1"] = true,
    ["P1 Y2"] = true,
    ["P1 Y3"] = true,
    ["P1 Y4"] = true,
    ["P2 Start"] = true,
    ["P2 A"] = true,
    ["P2 B"] = true,
    ["P2 X1"] = true,
    ["P2 X2"] = true,
    ["P2 X3"] = true,
    ["P2 X4"] = true,
    ["P2 Y1"] = true,
    ["P2 Y2"] = true,
    ["P2 Y3"] = true,
    ["P2 Y4"] = true,
  }
  for _ = 1, 30 do
    joypad.set(buttons)
    emu.frameadvance()
  end
  write("PULSE_ALL frame=" .. emu.framecount())
end

local function pulse_p2(button)
  local key = "P2 " .. button
  for hold = 1, 12 do
    joypad.set({[key] = true})
    if hold == 1 then
      local state = joypad.get()
      write("STATE " .. key .. "=" .. tostring(state[key]))
    end
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({[key] = false})
    emu.frameadvance()
  end
  write("PULSE_P2 frame=" .. emu.framecount() .. " button=" .. button)
end

write("ROM=" .. gameinfo.getromname())
write("START")
client.setwindowsize(2)

for frame = 1, 3600 do
  if frame == 600 or frame == 750 then
    pulse("Start")
  elseif frame == 675 or frame == 825 then
    pulse_p2("Start")
  elseif frame >= 900 and frame % 90 == 0 then
    pulse("A")
  end
  if frame % 120 == 0 then
    write("FRAME=" .. emu.framecount())
  end
  emu.frameadvance()
end

write("DONE")
log:close()
client.exit()
