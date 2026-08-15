-- Map frequently executed ID/battle renderer physical PCs back to ROM bytes.
local state=assert(os.getenv("MONOEYE_STATE"),"MONOEYE_STATE required")
local out=assert(os.getenv("MONOEYE_OUT"),"MONOEYE_OUT required")
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out.."\\code_map.log","w"))
local function w(s) log:write(s.."\n");log:flush();console.log(s) end
local function rb(a,d) local ok,v=pcall(memory.readbyte,a,d); if not ok then return nil end; return tonumber(v) end
local function dump(addr,domain,n) local t={};for i=0,n-1 do local v=rb(addr+i,domain);t[#t+1]=v and string.format("%02X",v) or "??" end;return table.concat(t," ") end
emu.frameadvance();local ok,err=pcall(savestate.load,state);w("LOAD="..tostring(ok).." "..tostring(err));if not ok then log:close();client.exit();return end
for _=1,2 do emu.frameadvance() end
local targets={0x89B46,0x89C44,0x89C52,0x89C69,0x89CB7,0x89CCD,0x89D5F,0x89D63,0x89D76,0x89E7C,0x89ED1,0x89F08}
local seen={};local handles={};local scope=nil
for _,v in pairs(event.availableScopes()) do if string.find(string.lower(tostring(v)),"system") then scope=tostring(v) end end
local function cb(addr,value,flags)
 local okr,r=pcall(emu.getregisters);if not okr then return end
 local a=tonumber(addr) or -1;if seen[a] then return end;seen[a]=true
 local pc=tonumber(r.PC or 0);local ps=tonumber(r.PS or 0);local lin=(ps*16+pc)%0x100000
 w(string.format("ADDR=%05X PC=%04X PS=%04X LIN=%05X BUS_ADDR=%s ROM_ADDR=%s BUS_LIN=%s ROM_LIN=%s AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
 a,pc,ps,lin,dump(a,"System Bus",24),dump(a,"ROM",24),dump(lin,"System Bus",24),dump(lin,"ROM",24),tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0),tonumber(r.IX or 0),tonumber(r.IY or 0)))
end
for _,a in ipairs(targets) do local rok,h=pcall(event.onmemoryexecute,cb,a,string.format("map_%05X",a),scope);if rok then handles[#handles+1]=h else w("REGFAIL "..string.format("%05X",a).." "..tostring(h)) end end
for _=1,8 do emu.frameadvance() end
local n=0;for _ in pairs(seen) do n=n+1 end;w("DONE seen="..tostring(n));log:close();client.exit()
