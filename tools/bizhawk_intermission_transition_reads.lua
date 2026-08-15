-- Aggregate all System Bus reads during the exact intermission tilemap transition.
-- Environment: MONOEYE_STATE, MONOEYE_OUT
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\transition_reads.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local scopes = event.availableScopes()
local scope = nil
for _, v in pairs(scopes) do if string.find(string.lower(tostring(v)), "system") then scope=tostring(v) end end
write("SCOPE="..tostring(scope))
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD="..tostring(ok).." "..tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end
local counts = {}
local values = {}
local total = 0
local function cb(addr, value, flags)
  local f = emu.framecount()
  if f < 1838 or f > 1842 then return end
  local a = tonumber(addr) or -1
  total = total + 1
  counts[a] = (counts[a] or 0) + 1
  if values[a] == nil then values[a] = tonumber(value) or 0 end
end
for _=1,8 do joypad.set({["P1 A"]=true}); emu.frameadvance() end
for _=1,3 do joypad.set({["P1 A"]=false}); emu.frameadvance() end
while emu.framecount() < 1837 do emu.frameadvance() end
local rok, handle = pcall(event.on_bus_read, cb, nil, "transition_reads", scope)
write("REGISTER="..tostring(rok).." "..tostring(handle).." frame="..tostring(emu.framecount()))
for _=1,6 do emu.frameadvance() end
if rok then pcall(event.unregisterbyid, handle) end
local addresses={}
for a,_ in pairs(counts) do table.insert(addresses,a) end
table.sort(addresses)
local ranges={}
if #addresses > 0 then
  local s=addresses[1]; local p=s; local n=counts[s]
  for i=2,#addresses do
    local a=addresses[i]
    if a==p+1 then n=n+counts[a] else table.insert(ranges,{s,p,n});s=a;n=counts[a] end
    p=a
  end
  table.insert(ranges,{s,p,n})
end
for _,r in ipairs(ranges) do write(string.format("RANGE %05X-%05X unique=%d reads=%d",r[1],r[2],r[2]-r[1]+1,r[3])) end
for _,a in ipairs(addresses) do
  if counts[a] >= 2 or a >= 0x10000 then write(string.format("ADDR %05X n=%d val=%02X",a,counts[a],values[a] or 0)) end
end
write("DONE total="..tostring(total).." unique="..tostring(#addresses).." ranges="..tostring(#ranges))
log:close(); client.exit()
