-- Enter the initial menu deterministically, then pause for external capture.
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
end

for _ = 1, 600 do emu.frameadvance() end
pulse("Start")
for _ = 1, 135 do emu.frameadvance() end
pulse("Start")
for _ = 1, 120 do emu.frameadvance() end
client.pause()
