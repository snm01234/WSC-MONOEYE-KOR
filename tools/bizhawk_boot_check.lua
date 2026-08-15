-- Boot smoke test: early + late screenshots, then exit.
local out_dir = os.getenv("BOOT_OUT") or "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\boot_bisect"
local tag = os.getenv("BOOT_TAG") or "rom"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = io.open(out_dir .. "\\" .. tag .. ".log", "w")
local function write(msg)
  log:write(msg .. "\n")
  log:flush()
end

write("ROM=" .. gameinfo.getromname())
write("HASH=" .. tostring(gameinfo.getromhash()))
client.setwindowsize(2)

local function shot(label)
  local path = string.format("%s\\%s_%s_f%04d.png", out_dir, tag, label, emu.framecount())
  client.screenshot(path)
  write("SHOT " .. path)
end

for i = 1, 30 do emu.frameadvance() end
shot("early")
for i = 1, 150 do emu.frameadvance() end
shot("late")
write("DONE frames=" .. tostring(emu.framecount()))
log:close()
client.exit()
