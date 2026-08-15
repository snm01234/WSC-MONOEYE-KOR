local state=assert(os.getenv('MONOEYE_STATE'))
local out=assert(os.getenv('MONOEYE_OUT'))
os.execute('mkdir "'..out..'" 2>nul')
local log=assert(io.open(out..'\\a1d4_object_probe.log','w'))
local function w(s) log:write(s..'\n'); log:flush(); console.log(s) end
local function regs() local ok,r=pcall(emu.getregisters); if ok and type(r)=='table' then return r else return {} end end
local function rw(a) local lo=memory.readbyte(a%0x10000,'RAM'); local hi=memory.readbyte((a+1)%0x10000,'RAM'); return lo+hi*256 end
local function rb(a) return memory.readbyte(a%0x10000,'RAM') end
emu.frameadvance(); local ok,err=pcall(savestate.load,state); w('LOAD='..tostring(ok)..' '..tostring(err)..' frame='..emu.framecount())
if not ok then w('DONE load failed'); log:close(); client.exit(); return end
while emu.framecount()<25305 do emu.frameadvance() end
w('ARM frame='..emu.framecount())
local hits=0
local seen={}
local scope=nil
for _,v in pairs(event.availableScopes()) do if string.find(string.lower(tostring(v)),'system') then scope=tostring(v) end end
local function cb(addr,value,flags)
  local r=regs(); local pc=tonumber(r.PC or 0); local ps=tonumber(r.PS or 0)
  if ps~=0x8000 or pc~=0xA1D4 then return end
  hits=hits+1
  local bp=tonumber(r.BP or r.BW or 0)
  local obj=rw(bp-8); local objseg=rw(bp-6)
  local key=string.format('%04X:%04X',objseg,obj)
  if not seen[key] then
    seen[key]=true
    local raw={}; for i=0,31 do raw[#raw+1]=string.format('%02X',rb(obj+i)) end
    w(string.format('HIT n=%d frame=%d BP=%04X OBJ=%s startTile=%04X x=%04X y=%04X cols=%02X uploaded=%04X flags=%04X raw=%s',hits,emu.framecount(),bp,key,rw(obj+0x0A),rw(obj+6),rw(obj+8),rb(obj+0x14),rw(obj+0x18),rw(obj),table.concat(raw,'')))
  end
end
local eok,h=pcall(event.onmemoryexecuteany,cb,'a1d4_obj',scope); w('REGISTER='..tostring(eok)..' '..tostring(h))
for i=1,2 do emu.frameadvance() end
w('DONE hits='..hits..' frame='..emu.framecount()); log:close(); client.exit()
