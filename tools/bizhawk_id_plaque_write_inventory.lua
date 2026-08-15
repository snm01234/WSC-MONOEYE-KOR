-- Inventory System Bus write addresses around ID-command plaque upload.
local state=assert(os.getenv('MONOEYE_STATE')); local out_dir=assert(os.getenv('MONOEYE_OUT')); local tag=os.getenv('MONOEYE_TAG') or 'id_write_inventory'
os.execute('mkdir "'..out_dir..'" 2>nul'); local log=assert(io.open(out_dir..'\\'..tag..'.log','w'))
local function write(s) log:write(s..'\n'); log:flush(); console.log(s) end
local scope=nil; for _,v in pairs(event.availableScopes()) do local s=tostring(v); if string.find(string.lower(s),'system') then scope=s end end
emu.frameadvance(); local ok,err=pcall(savestate.load,state); write('LOAD='..tostring(ok)..' '..tostring(err)); if not ok then log:close();client.exit();return end
local counts={}; local first={}; local total=0
local function cb(addr,value,flags)
 local f=emu.framecount(); if f<25306 or f>25340 then return end
 local a=tonumber(addr) or -1; total=total+1; counts[a]=(counts[a] or 0)+1
 if first[a]==nil then first[a]={f,tonumber(value) or 0,tostring(flags)} end
end
local rok,h=pcall(event.on_bus_write,cb,nil,'id_write_inventory',scope); write('REGISTER='..tostring(rok)..' '..tostring(h))
for _=1,40 do emu.frameadvance() end
local aa={}; for a,_ in pairs(counts) do table.insert(aa,a) end; table.sort(aa)
write('SUMMARY total='..total..' unique='..#aa)
for _,a in ipairs(aa) do local q=first[a]; write(string.format('A %05X n=%d firstf=%d val=%02X flags=%s',a,counts[a],q[1],q[2],q[3])) end
write('DONE frame='..emu.framecount()); log:close();client.exit()
