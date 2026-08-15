-- Trace WonderSwan DMA register writes during QuickSave5 -> intermission entry.
-- Environment: MONOEYE_STATE, MONOEYE_OUT
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\dma_trace.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return {} end
  return r
end
local function fmtregs()
  local r = regs()
  return string.format("PS=%04X PC=%04X SP=%04X SS=%04X DS0=%04X DS1=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
    tonumber(r.PS or 0), tonumber(r.PC or 0), tonumber(r.SP or 0), tonumber(r.SS or 0),
    tonumber(r.DS0 or 0), tonumber(r.DS1 or 0), tonumber(r.AW or 0), tonumber(r.BW or 0),
    tonumber(r.CW or 0), tonumber(r.DW or 0), tonumber(r.IX or 0), tonumber(r.IY or 0))
end
local scopes = event.availableScopes()
local scope = nil
for _, v in pairs(scopes) do
  if string.find(string.lower(tostring(v)), "system") then scope = tostring(v) end
end
write("SCOPE=" .. tostring(scope))
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end

local shadow = {}
local hits = 0
local handles = {}
local function cb(addr, value, flags)
  hits = hits + 1
  local a = tonumber(addr) or -1
  local v = tonumber(value) or 0
  shadow[a] = v
  local src = (shadow[0x40] or 0) + 0x100 * (shadow[0x41] or 0) + 0x10000 * (shadow[0x42] or 0)
  local dst = (shadow[0x44] or 0) + 0x100 * (shadow[0x45] or 0)
  local len = (shadow[0x46] or 0) + 0x100 * (shadow[0x47] or 0)
  write(string.format("WRITE n=%d frame=%d addr=%02X val=%02X flags=%s src=%05X dst=%04X len=%04X ctl=%02X %s",
    hits, emu.framecount(), a, v, tostring(flags), src, dst, len, shadow[0x48] or 0, fmtregs()))
end
for a=0x40,0x48 do
  local rok, handle = pcall(event.on_bus_write, cb, a, string.format("dma_%02X", a), scope)
  write(string.format("REGISTER %02X ok=%s handle=%s", a, tostring(rok), tostring(handle)))
  if rok then table.insert(handles, handle) end
end

local function press(key, n)
  for _=1,n do joypad.set({["P1 " .. key]=true}); emu.frameadvance() end
  for _=1,3 do joypad.set({["P1 " .. key]=false}); emu.frameadvance() end
end
press("A", 8)
for _=1,260 do emu.frameadvance() end
write("DONE hits=" .. hits .. " frame=" .. emu.framecount())
log:close(); client.exit()
