-- Trace the intermission compressed asset loader from the stock Continue state.
-- Environment: MONOEYE_STATE, MONOEYE_OUT
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\asset_loader_trace.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return {} end
  return r
end
local function fmtregs()
  local r = regs()
  return string.format("PS=%04X PC=%04X SP=%04X SS=%04X DS0=%04X DS1=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
    tonumber(r.PS or 0), tonumber(r.PC or 0), tonumber(r.SP or 0), tonumber(r.SS or 0),
    tonumber(r.DS0 or 0), tonumber(r.DS1 or 0), tonumber(r.AW or 0), tonumber(r.BW or 0),
    tonumber(r.CW or 0), tonumber(r.DW or 0), tonumber(r.IX or 0), tonumber(r.IY or 0))
end
local scopes = event.availableScopes()
local scope_list = {}
for k,v in pairs(scopes) do table.insert(scope_list, tostring(k) .. "=" .. tostring(v)) end
table.sort(scope_list)
write("SCOPES " .. table.concat(scope_list, ","))
write("DOMAINS " .. table.concat(memory.getmemorydomainlist(), ","))
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end
write("START frame=" .. emu.framecount() .. " " .. fmtregs())
local hits = 0
local function cb(kind)
  return function(addr, value, flags)
    hits = hits + 1
    if hits <= 300 then
      write(string.format("%s n=%d frame=%d addr=%05X val=%02X flags=%s %s",
        kind, hits, emu.framecount(), tonumber(addr) or -1, tonumber(value) or 0,
        tostring(flags), fmtregs()))
    end
  end
end
local handles = {}
local function reg_read(address, label, scope)
  local rok, handle = pcall(event.on_bus_read, cb("READ_" .. label), address, "read_" .. label, scope)
  write(string.format("REGISTER_READ %05X scope=%s ok=%s handle=%s", address, tostring(scope), tostring(rok), tostring(handle)))
  if rok then table.insert(handles, handle) end
end
local function reg_write(address, label, scope)
  local wok, handle = pcall(event.on_bus_write, cb("WRITE_" .. label), address, "write_" .. label, scope)
  write(string.format("REGISTER_WRITE %05X scope=%s ok=%s handle=%s", address, tostring(scope), tostring(wok), tostring(handle)))
  if wok then table.insert(handles, handle) end
end
local chosen_scope = nil
for _,v in pairs(scopes) do
  if string.find(string.lower(tostring(v)), "system") then chosen_scope = tostring(v) end
end
write("CHOSEN_SCOPE " .. tostring(chosen_scope))
for _, item in ipairs({
  {0x3B780,"header0"},{0x3B782,"mode"},{0x3B78A,"gfxptr"},
  {0x3BAD2,"comp0"},{0x3BAD3,"comp1"},{0x3BAD4,"comp2"},
  {0x3E3CC,"complast"},{0x3E3CD,"end"}
}) do reg_read(item[1], item[2], chosen_scope) end
for _, item in ipairs({
  {0x03800,"tilemap0"},{0x04000,"gfx4000"},{0x04020,"gfx4020"},
  {0x08000,"gfx8000"},{0x08020,"gfx8020"}
}) do reg_write(item[1], item[2], chosen_scope) end
local function advance(n) for _=1,n do emu.frameadvance() end end
local function press(key, n)
  for _=1,n do joypad.set({["P1 " .. key]=true}); emu.frameadvance() end
  for _=1,3 do joypad.set({["P1 " .. key]=false}); emu.frameadvance() end
end
press("A", 8)
advance(700)
write("DONE hits=" .. hits .. " frame=" .. emu.framecount() .. " " .. fmtregs())
log:close(); client.exit()
