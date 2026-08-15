-- Longer autoplay to reach seed dialogue (600005+).
local out_dir = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\hook09b"
local log_path = out_dir .. "\\hangul_check.log"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = io.open(log_path, "w")

local function write(msg)
  log:write(msg .. "\n")
  log:flush()
end

local function pulse(button, hold)
  hold = hold or 10
  local key = "P1 " .. button
  for _ = 1, hold do
    joypad.set({[key] = true})
    emu.frameadvance()
  end
  for _ = 1, 4 do
    joypad.set({[key] = false})
    emu.frameadvance()
  end
end

local function shot(tag)
  local path = string.format("%s\\%s_f%05d.png", out_dir, tag, emu.framecount())
  client.screenshot(path)
  write("SHOT " .. path)
end

write("ROM=" .. gameinfo.getromname())
client.setwindowsize(3)

for _ = 1, 400 do emu.frameadvance() end
shot("t0_boot")

-- title -> menu
pulse("Start", 15)
for _ = 1, 90 do emu.frameadvance() end
shot("t1_menu")

-- new game (assume top item already selected)
pulse("A", 12)
for _ = 1, 90 do emu.frameadvance() end
shot("t2_confirm")

pulse("A", 12)
for _ = 1, 60 do emu.frameadvance() end

-- mash through prologue / dialogue
local n = 0
for frame = 1, 5000 do
  if frame % 45 == 0 then
    pulse("A", 8)
    n = n + 1
    if n % 8 == 0 then
      shot(string.format("m%02d", n))
    end
  else
    emu.frameadvance()
  end
end

shot("end")
write("DONE frame=" .. emu.framecount() .. " pulses=" .. n)
log:close()
client.exit()
