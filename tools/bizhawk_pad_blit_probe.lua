-- Pad-blit path probe: for each tagged 1A6E index, dump ROM bytes at
--   stock  = 400440 + raw*16
--   pad    = 40F9F8 + (raw-0x820)*16
-- so we can confirm the file image the CPU *should* fetch via 3000:off.
--
-- Load with 15 or 16 (or 10):
--   EmuHawk.exe --lua=tools/bizhawk_pad_blit_probe.lua 15_tag_to_stock_addr.wsc

local ROOT = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\pad_ab"
local FLAG = 0x19FF
local BASE = 0x1A6E
local N = 24
local H0 = 0x820
local PAD = 0xF9F8
local STOCK = 0x0440

os.execute('mkdir "' .. ROOT .. '" 2>nul')
local log = assert(io.open(ROOT .. "\\pad_blit_probe.log", "w"))

local function write(msg)
  console.log(msg)
  log:write(msg .. "\n")
  log:flush()
end

local function pulse(button, hold)
  hold = hold or 12
  local key = "P1 " .. button
  for _ = 1, hold do
    joypad.set({ [key] = true })
    emu.frameadvance()
  end
  for _ = 1, 4 do
    joypad.set({ [key] = false })
    emu.frameadvance()
  end
end

local function rom_u8(addr)
  local ok, v = pcall(memory.read_u8, addr, "ROM")
  if ok then return v end
  return nil
end

local function ink_at(file_off)
  local ink, nz = 0, 0
  for i = 0, 15 do
    local b = rom_u8(file_off + i)
    if b == nil then return nil end
    if b ~= 0 then nz = nz + 1 end
    -- rough: count set bit-pairs
    for shift = 0, 6, 2 do
      if ((b >> shift) & 3) ~= 0 then ink = ink + 1 end
    end
  end
  return ink, nz
end

local function sample(tag)
  local parts = { string.format("%s f=%d", tag, emu.framecount()) }
  local any_t = false
  for i = 0, N - 1 do
    local ok, v = pcall(memory.read_u16_le, BASE + i * 2, "WRAM")
    if not ok or v == nil or v == 0 then goto continue end
    local tagged = (v >= 0x8000)
    local raw = v % 0x8000
    if tagged then
      any_t = true
      local stock_off = 0x400000 + STOCK + raw * 16
      local pad_off = 0x400000 + PAD + (raw - H0) * 16
      local si, sn = ink_at(stock_off)
      local pi, pn = ink_at(pad_off)
      table.insert(
        parts,
        string.format(
          "%02d:%04XT raw=%04X stock_ink=%s pad_ink=%s pad_file=%06X",
          i, v, raw, tostring(si), tostring(pi), pad_off
        )
      )
    end
    ::continue::
  end
  write(table.concat(parts, " | "))
  if any_t then
    gui.text(2, 2, "pad-blit: tagged indices logged")
  end
  return any_t
end

write("ROM=" .. gameinfo.getromname())
write("HASH=" .. gameinfo.getromhash())
pcall(client.setwindowsize, 3)

for _ = 1, 500 do emu.frameadvance() end
pulse("Start", 12)
for _ = 1, 90 do emu.frameadvance() end
pulse("Start", 12)
for _ = 1, 90 do emu.frameadvance() end
pulse("A", 12)
for _ = 1, 90 do emu.frameadvance() end
pulse("A", 12)
for _ = 1, 60 do emu.frameadvance() end

local seen = 0
for frame = 1, 5000 do
  if frame % 45 == 0 then
    pulse("A", 8)
    if sample("SAMPLE") then seen = seen + 1 end
  else
    emu.frameadvance()
  end
  if frame == 600 or frame == 1200 or frame == 2000 or frame == 3000 then
    local path = string.format("%s\\%s_f%05d.png", ROOT, "shot", emu.framecount())
    pcall(client.screenshot, path)
    sample("SHOT")
  end
end

write(string.format("DONE tagged_samples=%d", seen))
log:close()
client.exit()
