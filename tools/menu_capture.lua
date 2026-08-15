-- Deterministic title + initial-menu capture for the title graphics hunt.
--
-- Replaces the lost out/title_trace6/menu_capture.lua. Nothing is hard-coded:
-- the output directory and the file tag arrive through the environment, so the
-- same script serves the baseline runs and every bisect candidate.
--
-- Environment
--   MONOEYE_OUT   output directory (required)
--   MONOEYE_TAG   file tag        (default "rom")
--   MONOEYE_HOLD  frames to hold a button (default 12)
--
-- Output
--   <OUT>/<TAG>.log         DOMAIN/PULSE/SHOT/DONE lines
--   <OUT>/<TAG>_title.png
--   <OUT>/<TAG>_menu.png
--
-- What keeps the PNG hashes reproducible: a fixed frame schedule, no wall clock,
-- no savestates, no SaveRAM, and screenshots taken from the emulated framebuffer
-- rather than the host window.

local out_dir = os.getenv("MONOEYE_OUT")
local tag = os.getenv("MONOEYE_TAG") or "rom"
local hold = tonumber(os.getenv("MONOEYE_HOLD") or "12")

if out_dir == nil or out_dir == "" then
  error("MONOEYE_OUT must be set")
end
os.execute('mkdir "' .. out_dir .. '" 2>nul')

local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(msg)
  log:write(msg .. "\n")
  log:flush()
  console.log(msg)
end

local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end

local function pulse(button)
  local key = "P1 " .. button
  for _ = 1, hold do
    joypad.set({ [key] = true })
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({ [key] = false })
    emu.frameadvance()
  end
  write("PULSE " .. key .. " frame=" .. emu.framecount())
end

local function shot(label)
  local path = string.format("%s\\%s_%s.png", out_dir, tag, label)
  local ok, err = pcall(client.screenshot, path)
  write(string.format("SHOT %s frame=%d ok=%s %s", label, emu.framecount(),
    tostring(ok), tostring(err)))
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
for _, name in ipairs(memory.getmemorydomainlist()) do
  write("DOMAIN " .. name .. " size=" .. tostring(memory.getmemorydomainsize(name)))
end

-- Schedule copied from the run that produced out/title_trace/{title,menu}.png:
-- the title settles well before frame 590, the first Start clears the logo, the
-- second opens the three-button menu.
advance(590)
shot("title")

advance(10)
pulse("Start")
advance(135)
pulse("Start")
advance(95)
shot("menu")

write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
