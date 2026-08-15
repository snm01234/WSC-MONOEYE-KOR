-- Probe execution callbacks around the idle PC of an intermission savestate.
-- Environment: MONOEYE_STATE, MONOEYE_OUT
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\exec_probe.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return nil end
  return r
end
local function fmtregs(r)
  if not r then return "none" end
  return string.format("PS=%04X PC=%04X SP=%04X SS=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
    tonumber(r.PS or 0), tonumber(r.PC or 0), tonumber(r.SP or 0), tonumber(r.SS or 0),
    tonumber(r.AW or 0), tonumber(r.BW or 0), tonumber(r.CW or 0), tonumber(r.DW or 0),
    tonumber(r.IX or 0), tonumber(r.IY or 0))
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end
local r = regs(); write("START " .. fmtregs(r))
local ps = tonumber(r and r.PS or 0)
local pc = tonumber(r and r.PC or 0)
local linear = (ps * 16 + pc) % 0x100000
local addresses = {pc, linear}
for delta=-16,16 do table.insert(addresses, (linear + delta) % 0x100000) end
local seen = {}
local hits = 0
local handles = {}
for _, address in ipairs(addresses) do
  if not seen[address] then
    seen[address] = true
    local function cb(addr, value, flags)
      hits = hits + 1
      if hits <= 120 then write(string.format("HIT n=%d addr=%05X value=%s flags=%s %s", hits, tonumber(addr) or -1, tostring(value), tostring(flags), fmtregs(regs()))) end
    end
    local eok, handle = pcall(event.onmemoryexecute, cb, address, string.format("exec_%05X", address))
    write(string.format("REGISTER %05X ok=%s handle=%s", address, tostring(eok), tostring(handle)))
    if eok then table.insert(handles, handle) end
  end
end
for _=1,120 do emu.frameadvance() end
write("DONE hits=" .. tostring(hits) .. " " .. fmtregs(regs()))
log:close(); client.exit()
