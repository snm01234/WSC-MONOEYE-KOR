-- Trace every System Bus read from the bank-54 intermission resource command area.
-- Environment: MONOEYE_STATE, MONOEYE_OUT
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\resource_read_any.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return {} end
  return r
end
local scopes = event.availableScopes()
local scope = nil
for _, v in pairs(scopes) do
  if string.find(string.lower(tostring(v)), "system") then scope = tostring(v) end
end
write("SCOPE=" .. tostring(scope))
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end
local hits = 0
local counts = {}
local first = {}
local function callback(addr, value, flags)
  local a = tonumber(addr) or -1
  local frame = emu.framecount()
  if frame < 1820 or frame > 1850 then return end
  if not (0x341E0 <= a and a < 0x343C0) then return end
  hits = hits + 1
  counts[a] = (counts[a] or 0) + 1
  if first[a] == nil then
    first[a] = true
    local r = regs()
    write(string.format("FIRST frame=%d addr=%05X val=%02X flags=%s PS=%04X PC=%04X DS0=%04X DS1=%04X IX=%04X IY=%04X AW=%04X BW=%04X CW=%04X DW=%04X",
      frame, a, tonumber(value) or 0, tostring(flags), tonumber(r.PS or 0), tonumber(r.PC or 0),
      tonumber(r.DS0 or 0), tonumber(r.DS1 or 0), tonumber(r.IX or 0), tonumber(r.IY or 0),
      tonumber(r.AW or 0), tonumber(r.BW or 0), tonumber(r.CW or 0), tonumber(r.DW or 0)))
  end
end
local rok, handle = pcall(event.on_bus_read, callback, nil, "resource_read_any", scope)
write("REGISTER ok=" .. tostring(rok) .. " handle=" .. tostring(handle))
for _=1,8 do joypad.set({["P1 A"]=true}); emu.frameadvance() end
for _=1,3 do joypad.set({["P1 A"]=false}); emu.frameadvance() end
for _=1,200 do emu.frameadvance() end
local addresses = {}
for a,_ in pairs(counts) do table.insert(addresses,a) end
table.sort(addresses)
for _,a in ipairs(addresses) do write(string.format("COUNT addr=%05X n=%d",a,counts[a])) end
write("DONE hits=" .. tostring(hits) .. " unique=" .. tostring(#addresses))
log:close(); client.exit()
