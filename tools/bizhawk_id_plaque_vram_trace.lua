-- Trace ID-command plaque VRAM writes and CPU registers around QuickSave6.
-- Env: MONOEYE_STATE, MONOEYE_OUT, optional MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "id_plaque_vram_trace"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if ok and type(r) == "table" then return r end
  return {}
end
local scopes = event.availableScopes()
local scope = nil
for _,v in pairs(scopes) do
  local s=tostring(v)
  write("SCOPE_CANDIDATE="..s)
  if string.find(string.lower(s), "system") then scope=s end
end
write("SCOPE="..tostring(scope))
emu.frameadvance()
local ok,err=pcall(savestate.load,state)
write("LOAD="..tostring(ok).." "..tostring(err))
if not ok then log:close(); client.exit(); return end
local counts={}
local first_by_tile={}
local total=0
local function cb(addr,value,flags)
  local a=tonumber(addr) or -1
  local f=emu.framecount()
  if f < 25306 or f > 25340 then return end
  if not (0x4EC0 <= a and a < 0x5040) then return end
  total=total+1
  counts[a]=(counts[a] or 0)+1
  local tile=math.floor((a-0x4EC0)/0x20)
  if not first_by_tile[tile] then
    first_by_tile[tile]=true
    local r=regs()
    write(string.format("FIRST frame=%d addr=%05X tile=%02X val=%02X flags=%s PS=%04X PC=%04X DS0=%04X DS1=%04X SS=%04X SP=%04X IX=%04X IY=%04X AW=%04X BW=%04X CW=%04X DW=%04X",
      f,a,tile,tonumber(value) or 0,tostring(flags),
      tonumber(r.PS or 0),tonumber(r.PC or 0),tonumber(r.DS0 or 0),tonumber(r.DS1 or 0),tonumber(r.SS or 0),tonumber(r.SP or 0),
      tonumber(r.IX or 0),tonumber(r.IY or 0),tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0)))
  end
end
local rok,handle=pcall(event.on_bus_write,cb,nil,"id_plaque_vram_trace",scope)
write("REGISTER="..tostring(rok).." "..tostring(handle))
for _=1,40 do emu.frameadvance() end
write("DONE frame="..emu.framecount().." total="..total)
log:close(); client.exit()
