-- Trace reads from the actual bank-54 intermission static atlas during QuickSave5 entry.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_static_read_trace"
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
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _ = 1, 3 do emu.frameadvance() end
local scopes = event.availableScopes()
local scope = nil
for _, value in pairs(scopes) do
  if string.find(string.lower(tostring(value)), "system") then scope = tostring(value) end
end
write("SCOPE=" .. tostring(scope))
local hits = 0
local handles = {}
local addresses = {
  0x342C0, 0x34300, 0x343B0, 0x343C0,
  0x34840, 0x34860, 0x34980, 0x34A40,
  0x369E0, 0x37180, 0x372A0, 0x373C0,
  0x89D69, 0x89E12,
}
for _, address in ipairs(addresses) do
  local function callback(addr, value, flags)
    hits = hits + 1
    if hits <= 500 then
      write(string.format("READ n=%d frame=%d addr=%05X value=%02X flags=%s %s",
        hits, emu.framecount(), tonumber(addr) or -1, tonumber(value) or 0,
        tostring(flags), regs()))
    end
  end
  local rok, handle = pcall(event.on_bus_read, callback, address, string.format("static_%05X", address), scope)
  write(string.format("REGISTER %05X ok=%s handle=%s", address, tostring(rok), tostring(handle)))
  if rok then table.insert(handles, handle) end
end
for _ = 1, 8 do joypad.set({["P1 A"] = true}); emu.frameadvance() end
for _ = 1, 3 do joypad.set({["P1 A"] = false}); emu.frameadvance() end
for _ = 1, 240 do emu.frameadvance() end
write("DONE hits=" .. tostring(hits) .. " frame=" .. tostring(emu.framecount()) .. " " .. regs())
log:close(); client.exit()
