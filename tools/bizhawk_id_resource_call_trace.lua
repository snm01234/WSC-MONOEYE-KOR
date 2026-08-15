-- Trace calls to ROM routine stock 0x789C5A (bus execute addr 0x89C5A).
local state=assert(os.getenv('MONOEYE_STATE'),'MONOEYE_STATE required')
local out=assert(os.getenv('MONOEYE_OUT'),'MONOEYE_OUT required')
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out..'\\resource_call.log','w'))
local function w(s) log:write(s..'\n');log:flush();console.log(s) end
local function rb(a) local ok,v=pcall(memory.readbyte,a,'RAM');return ok and tonumber(v) or nil end
local function rw(a) local l=rb(a);local h=rb((a+1)%0x10000);if not l or not h then return nil end;return l+h*256 end
local function dump(a,n) local t={};for i=0,n-1 do local v=rb((a+i)%0x10000);t[#t+1]=v and string.format('%02X',v) or '??' end;return table.concat(t,' ') end
emu.frameadvance();local ok,err=pcall(savestate.load,state);w('LOAD='..tostring(ok)..' '..tostring(err));if not ok then log:close();client.exit();return end
for _=1,2 do emu.frameadvance() end
local hits=0;local scope=nil
for _,v in pairs(event.availableScopes()) do if string.find(string.lower(tostring(v)),'system') then scope=tostring(v) end end
local function cb(addr,value,flags)
 if tonumber(addr)~=0x89C5A then return end
 hits=hits+1;if hits>40 then return end
 local okr,r=pcall(emu.getregisters);if not okr then return end
 local sp=tonumber(r.SP or 0);local bp=tonumber(r.BP or 0)
 local aoff=rw((sp+2)%0x10000);local aseg=rw((sp+4)%0x10000)
 w(string.format('H n=%d frame=%d SP=%04X BP=%04X ARGPTR=%04X:%04X AX=%04X BX=%04X CX=%04X DX=%04X SI=%04X DI=%04X DS0=%04X DS1=%04X STACK=%s ARGDATA=%s',hits,emu.framecount(),sp,bp,aseg or 0,aoff or 0,tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0),tonumber(r.IX or 0),tonumber(r.IY or 0),tonumber(r.DS0 or 0),tonumber(r.DS1 or 0),dump(sp,12),aoff and dump(aoff,16) or ''))
end
local rok,h=pcall(event.onmemoryexecuteany,cb,'id_resource_call',scope);w('REGISTER='..tostring(rok)..' '..tostring(h))
for _=1,300 do emu.frameadvance() end
w('DONE hits='..tostring(hits));log:close();client.exit()
