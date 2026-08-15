-- Aggregate executed physical PCs around the QuickSave6 ID-command plaque transition.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "id_exec_window"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return {} end
  return r
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
local counts = {}
local first = {}
local scopes = event.availableScopes()
local scope = nil
for _, v in pairs(scopes) do if string.find(string.lower(tostring(v)), "system") then scope=tostring(v) end end
write("SCOPE="..tostring(scope).." START="..tostring(emu.framecount()))
local function cb(addr, value, flags)
  local f = emu.framecount()
  if f < 25305 or f > 25340 then return end
  local r=regs(); local pc=tonumber(r.PC or 0); local ps=tonumber(r.PS or 0)
  local phys=(ps*16+pc)%0x100000
  local key=string.format("%05X",phys)
  counts[key]=(counts[key] or 0)+1
  if first[key]==nil then first[key]=string.format("frame=%d PS=%04X PC=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X DS0=%04X DS1=%04X",f,ps,pc,tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0),tonumber(r.IX or 0),tonumber(r.IY or 0),tonumber(r.DS0 or 0),tonumber(r.DS1 or 0)) end
end
local eok, handle=pcall(event.onmemoryexecuteany,cb,"id_exec_window",scope)
write("REGISTER="..tostring(eok).." "..tostring(handle))
for _=1,40 do emu.frameadvance() end
local rows={}
for k,n in pairs(counts) do table.insert(rows,{k=k,n=n}) end
table.sort(rows,function(a,b) if a.n==b.n then return a.k<b.k else return a.n>b.n end end)
for i=1,math.min(#rows,600) do local row=rows[i]; write(string.format("PC %s n=%d %s",row.k,row.n,first[row.k] or "")) end
write("DONE unique="..tostring(#rows).." frame="..tostring(emu.framecount()))
log:close(); client.exit()
