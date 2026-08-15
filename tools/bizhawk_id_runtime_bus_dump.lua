local state=assert(os.getenv('MONOEYE_STATE'),'MONOEYE_STATE required')
local out=assert(os.getenv('MONOEYE_OUT'),'MONOEYE_OUT required')
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out..'\\bus_dump.log','w'))
local function w(s) log:write(s..'\n');log:flush();console.log(s) end
local function dump(a,d,n)
 local t={};for i=0,n-1 do local ok,v=pcall(memory.readbyte,a+i,d);t[#t+1]=ok and string.format('%02X',tonumber(v) or 0) or '??' end;return table.concat(t,' ')
end
emu.frameadvance();local ok,err=pcall(savestate.load,state);w('LOAD='..tostring(ok)..' '..tostring(err));if not ok then log:close();client.exit();return end
for _=1,2 do emu.frameadvance() end
local domains=memory.getmemorydomainlist();for _,d in pairs(domains) do w('DOMAIN '..tostring(d)) end
for _,a in ipairs({0x89B46,0x89C44,0x89C52,0x89D5F,0x89E7C,0x89F08}) do
 w(string.format('A=%05X BUS=%s ROM=%s',a,dump(a,'System Bus',32),dump(a,'ROM',32)))
end
w('DONE');log:close();client.exit()
