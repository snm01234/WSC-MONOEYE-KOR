-- Collect renderer stock 0x789C5A live locals for plaque-sized resources.
local state=assert(os.getenv('MONOEYE_STATE'),'MONOEYE_STATE required')
local out=assert(os.getenv('MONOEYE_OUT'),'MONOEYE_OUT required')
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out..'\\stack_probe.log','w'))
local function w(s) log:write(s..'\n');log:flush();console.log(s) end
local function rb(a) local ok,v=pcall(memory.readbyte,a%0x10000,'RAM');return ok and tonumber(v) or 0 end
local function rw(a) return rb(a)+rb(a+1)*256 end
emu.frameadvance();local ok,err=pcall(savestate.load,state);w('LOAD='..tostring(ok)..' '..tostring(err));if not ok then log:close();client.exit();return end
local scope=nil;for _,v in pairs(event.availableScopes()) do if string.find(string.lower(tostring(v)),'system') then scope=tostring(v) end end
local hits=0;local kept=0;local seen={}
local function cb(addr,value,flags)
 local okr,r=pcall(emu.getregisters);if not okr then return end
 local ps=tonumber(r.PS or 0);local pc=tonumber(r.PC or 0);local phys=(ps*16+pc)%0x100000
 if phys~=0x8A101 then return end;hits=hits+1
 local bp=tonumber(r.BP or 0);local wid=rw(bp-0x1a);local hei=rw(bp-0x1c)
 if wid<4 or wid>8 or hei<1 or hei>3 then return end
 local arg_off=rw(bp+4);local arg_seg=rw(bp+6);local obj_off=rw(bp-8);local obj_seg=rw(bp-6);local key=string.format('%d,%d,%04X:%04X,%04X:%04X',wid,hei,arg_seg,arg_off,obj_seg,obj_off)
 if seen[key] then return end;seen[key]=true;kept=kept+1
 w(string.format('R n=%d frame=%d size=%dx%d BP=%04X arg=%04X:%04X obj=%04X:%04X tilebase=%04X srcptr=%04X:%04X x=%04X y=%04X flags=%04X pal=%04X tile=%04X',kept,emu.framecount(),wid,hei,bp,arg_seg,arg_off,obj_seg,obj_off,rw(bp-0x28),rw(bp-0x14),rw(bp-0x16),rw(bp-0x24),rw(bp-0x26),rw(bp-0x1e),rw(bp-0x12),rw(bp-0x18)))
end
local rok,h=pcall(event.onmemoryexecuteany,cb,'id_stack_probe',scope);w('REGISTER='..tostring(rok)..' '..tostring(h))
for _=1,4 do emu.frameadvance() end
w('DONE hits='..tostring(hits)..' kept='..tostring(kept));log:close();client.exit()
