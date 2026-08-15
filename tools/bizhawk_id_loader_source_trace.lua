-- Find the common ID-plaque ROM reader by matching known Korean ↑공격 tile bytes on System Bus.
local state=assert(os.getenv('MONOEYE_STATE')); local out_dir=assert(os.getenv('MONOEYE_OUT')); local tag=os.getenv('MONOEYE_TAG') or 'id_loader_source_trace'
os.execute('mkdir "'..out_dir..'" 2>nul'); local log=assert(io.open(out_dir..'\\'..tag..'.log','w'))
local function write(s) log:write(s..'\n'); log:flush(); console.log(s) end
local function regs() local ok,r=pcall(emu.getregisters); if ok and type(r)=='table' then return r end; return {} end
local scope=nil; for _,v in pairs(event.availableScopes()) do local s=tostring(v); if string.find(string.lower(s),'system') then scope=s end end
local pat={0xFF,0xFF,0xFF,0xFF,0xED,0xCC,0xCC,0xCC,0xBE,0xDC,0xCC,0xCC,0xFB,0xED,0xCC,0xCC}
local frame_lo=tonumber(os.getenv('MONOEYE_FRAME_LO') or '25306')
local frame_hi=tonumber(os.getenv('MONOEYE_FRAME_HI') or '25335')
local advance_frames=tonumber(os.getenv('MONOEYE_ADVANCE') or '35')
local vals={}; local addrs={}; local hits=0; local reads=0
emu.frameadvance(); local ok,err=pcall(savestate.load,state); write('LOAD='..tostring(ok)..' '..tostring(err)); if not ok then log:close();client.exit();return end
local function cb(addr,value,flags)
 local f=emu.framecount(); if f<frame_lo or f>frame_hi then return end
 reads=reads+1; table.insert(vals,tonumber(value) or 0); table.insert(addrs,tonumber(addr) or -1)
 if #vals>#pat then table.remove(vals,1);table.remove(addrs,1) end
 if #vals==#pat then
  local same=true;for i=1,#pat do if vals[i]~=pat[i] then same=false;break end end
  if same then
   hits=hits+1;local r=regs();local aa={};for i=1,#addrs do aa[#aa+1]=string.format('%05X',addrs[i]) end
   write(string.format('MATCH frame=%d addrs=%s flags=%s PS=%04X PC=%04X DS0=%04X DS1=%04X SS=%04X SP=%04X IX=%04X IY=%04X AW=%04X BW=%04X CW=%04X DW=%04X',f,table.concat(aa,','),tostring(flags),tonumber(r.PS or 0),tonumber(r.PC or 0),tonumber(r.DS0 or 0),tonumber(r.DS1 or 0),tonumber(r.SS or 0),tonumber(r.SP or 0),tonumber(r.IX or 0),tonumber(r.IY or 0),tonumber(r.AW or 0),tonumber(r.BW or 0),tonumber(r.CW or 0),tonumber(r.DW or 0)))
  end
 end
end
local rok,h=pcall(event.on_bus_read,cb,nil,'id_loader_source_trace',scope); write('REGISTER='..tostring(rok)..' '..tostring(h))
for _=1,advance_frames do emu.frameadvance() end
write('DONE frame='..emu.framecount()..' reads='..reads..' hits='..hits);log:close();client.exit()
