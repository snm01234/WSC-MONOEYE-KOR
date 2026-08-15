-- Save compact timeline states around the known ID-command upload window.
local state=assert(os.getenv("MONOEYE_STATE"),"MONOEYE_STATE required")
local out=assert(os.getenv("MONOEYE_OUT"),"MONOEYE_OUT required")
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out.."\\timeline.log","w"))
local function w(s) log:write(s.."\n");log:flush();console.log(s) end
emu.frameadvance();local ok,err=pcall(savestate.load,state);w("LOAD="..tostring(ok).." "..tostring(err));if not ok then log:close();client.exit();return end
for _=1,2 do emu.frameadvance() end
for i=0,28 do
 local p=string.format("%s\\idtl_%02d_f%d.State",out,i,emu.framecount())
 local sok,serr=pcall(savestate.save,p); w(string.format("SAVE %02d frame=%d ok=%s %s",i,emu.framecount(),tostring(sok),tostring(serr)))
 for _=1,10 do emu.frameadvance() end
end
w("DONE frame="..tostring(emu.framecount()));log:close();client.exit()
