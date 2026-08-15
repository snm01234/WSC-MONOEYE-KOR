-- Capture deterministic title/menu screenshots and memory snapshots.
-- Run with:
--   EmuHawk.exe --lua=...\bizhawk_title_trace.lua original.wsc

local root = "C:\\Users\\SangGeun\\monoeye\\out\\title_trace5"
local log = assert(io.open(root .. "\\trace.log", "w"))

local function write(message)
  console.log(message)
  log:write(message .. "\n")
  log:flush()
end

local function pulse(button)
  local key = "P1 " .. button
  for _ = 1, 12 do
    joypad.set({[key] = true})
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({[key] = false})
    emu.frameadvance()
  end
  write("PULSE " .. key .. " frame=" .. emu.framecount())
end

local function snapshot(label)
  local state_ok, state_err = pcall(savestate.save, root .. "\\" .. label .. ".State")
  write("SAVESTATE " .. label .. " " .. tostring(state_ok) .. " " .. tostring(state_err))
end

write("ROM=" .. gameinfo.getromname())
for _, name in ipairs(memory.getmemorydomainlist()) do
  write("DOMAIN " .. name)
end

-- Existing captures reach the title by roughly ten seconds. Keep the input
-- schedule identical to the previously successful Lua playback.
for _ = 1, 590 do emu.frameadvance() end
snapshot("title")

for _ = 1, 10 do emu.frameadvance() end
pulse("Start")

for _ = 1, 135 do emu.frameadvance() end
pulse("Start")

for _ = 1, 95 do emu.frameadvance() end
snapshot("menu")

-- Move through all three menu rows and retain each selected state.
pulse("X2")
for _ = 1, 20 do emu.frameadvance() end
snapshot("menu_continue")
pulse("X2")
for _ = 1, 20 do emu.frameadvance() end
snapshot("menu_option")

write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
