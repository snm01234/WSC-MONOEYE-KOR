-- Longer runtime check for marked PoC: title -> menu -> new game -> prologue -> opening.
local out_dir = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\marked10"
local log_path = out_dir .. "\\runtime.log"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = io.open(log_path, "w")

local function write(msg)
  log:write(msg .. "\n")
  log:flush()
end

local function pulse(button, hold)
  hold = hold or 12
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
write("HASH=" .. gameinfo.getromhash())
client.setwindowsize(3)

for frame = 1, 9000 do
  if frame == 500 then
    shot("boot")
  elseif frame == 600 or frame == 750 then
    pulse("Start", 12)
    if frame == 750 then shot("menu") end
  elseif frame == 825 then
    pulse("A", 12)
    shot("newgame_a")
  elseif frame == 900 then
    pulse("A", 12)
    shot("confirm_a")
  elseif frame >= 960 and frame % 60 == 0 then
    pulse("A", 8)
    if frame == 1200 or frame == 1800 or frame == 2400 or frame == 3600 or frame == 4800 or frame == 6000 or frame == 7200 then
      shot(string.format("adv_%04d", frame))
    end
  end
  emu.frameadvance()
end

shot("end")
write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
