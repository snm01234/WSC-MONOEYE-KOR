-- Poll intermission tilemap cells frame-by-frame across the final confirmation.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_entry_poll"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return "regs unavailable" end
  return string.format("PS=%04X PC=%04X SP=%04X SS=%04X DS0=%04X DS1=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
    tonumber(r.PS or 0), tonumber(r.PC or 0), tonumber(r.SP or 0), tonumber(r.SS or 0),
    tonumber(r.DS0 or 0), tonumber(r.DS1 or 0), tonumber(r.AW or 0), tonumber(r.BW or 0),
    tonumber(r.CW or 0), tonumber(r.DW or 0), tonumber(r.IX or 0), tonumber(r.IY or 0))
end
local addresses = {0x384C, 0x3856, 0x3C5C, 0x3C60, 0x3C66, 0x3C6A}
local function u16(addr)
  return memory.readbyte(addr) + 0x100 * memory.readbyte(addr + 1)
end
local function values()
  local out = {}
  for _, a in ipairs(addresses) do table.insert(out, string.format("%04X=%04X", a, u16(a))) end
  return table.concat(out, " ")
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _ = 1, 3 do emu.frameadvance() end
local previous = values()
write(string.format("START frame=%d %s %s", emu.framecount(), previous, regs()))
for i = 1, 8 do joypad.set({["P1 A"] = true}); emu.frameadvance() end
for i = 1, 3 do joypad.set({["P1 A"] = false}); emu.frameadvance() end
for i = 1, 240 do
  local current = values()
  if current ~= previous then
    write(string.format("CHANGE frame=%d %s %s", emu.framecount(), current, regs()))
    previous = current
  end
  if i <= 20 or i % 30 == 0 then
    write(string.format("FRAME frame=%d %s %s", emu.framecount(), current, regs()))
  end
  emu.frameadvance()
end
write("DONE frame=" .. tostring(emu.framecount()) .. " " .. values() .. " " .. regs())
log:close(); client.exit()
