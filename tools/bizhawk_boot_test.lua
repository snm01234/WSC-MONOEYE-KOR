local out = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk"
local log = io.open(out .. "\\lua_test.log", "w")
local function write(message)
  console.log(message)
  log:write(message .. "\n")
  log:flush()
end

write("ROM: " .. gameinfo.getromname())
for key, value in pairs(joypad.get()) do
  write("BUTTON " .. key .. "=" .. tostring(value))
end

for frame = 1, 600 do
  if frame == 120 or frame == 300 or frame == 600 then
    local ok, err = pcall(
      client.screenshot,
      out .. string.format("\\boot_%04d.png", frame)
    )
    write("SCREENSHOT " .. frame .. " " .. tostring(ok) .. " " .. tostring(err))
  end
  emu.frameadvance()
end

write("BOOT_TEST_DONE")
log:close()
client.exit()
