-- Trace only the OBJ entries corresponding to plaque display column x=116
-- (the fifth 8px column; in the shield capture this is the unwanted second ㅐ).
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "id_obj_write"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r=pcall(emu.getregisters); if not ok or type(r)~="table" then return {} end; return r
end
local scope=nil
for _,v in pairs(event.availableScopes()) do if string.find(string.lower(tostring(v)),"system") then scope=tostring(v) end end
emu.frameadvance(); local ok,err=pcall(savestate.load,state)
write("LOAD="..tostring(ok).." "..tostring(err).." SCOPE="..tostring(scope)); if not ok then write("DONE loadfail"); log:close(); client.exit(); return end
for _=1,2 do emu.frameadvance() end
write("START="..tostring(emu.framecount()))
local hits=0; local handles={}
local starts={0x1A40,0x1A58,0x2FD4,0x2FEC}
local function cb(addr,value,flags)
  hits=hits+1; local r=regs(); local ps=tonumber(r.PS or 0); local pc=tonumber(r.PC or 0)
  write(string.format("W n=%d frame=%d addr=%04X val=%02X PHYS=%05X PS=%04X PC=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X DS0=%04X DS1=%04X flags=%s",
    hits,emu.framecount(),tonumber(addr) or -1,tonumber(value) or 0,(ps*16+pc)%0x100000,ps,pc,tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0),tonumber(r.IX or 0),tonumber(r.IY or 0),tonumber(r.DS0 or 0),tonumber(r.DS1 or 0),tostring(flags)))
end
for _,s in ipairs(starts) do
  for a=s,s+3 do local rok,h=pcall(event.on_bus_write,cb,a,string.format("id_obj_%04X",a),scope); if rok then table.insert(handles,h) else write("REGFAIL "..string.format("%04X",a).." "..tostring(h)) end end
end
write("REGISTERED="..tostring(#handles))
for _=1,275 do emu.frameadvance() end
write("DONE frame="..tostring(emu.framecount()).." hits="..tostring(hits)); log:close(); client.exit()
