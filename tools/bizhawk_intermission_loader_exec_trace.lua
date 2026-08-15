-- Trace the intermission loader execution and test code-byte domains.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_loader_exec"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return {} end
  return r
end
local function tryread(addr, domain)
  local ok, value = pcall(memory.readbyte, addr, domain)
  return tostring(ok) .. ":" .. (ok and string.format("%02X", tonumber(value) or 0) or tostring(value))
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _ = 1, 3 do emu.frameadvance() end
local hits = 0
local handles = {}
local scopes = event.availableScopes()
local scope = nil
for _, v in pairs(scopes) do
  if string.find(string.lower(tostring(v)), "system") then scope = tostring(v) end
end
write("SCOPE=" .. tostring(scope))
local function callback(addr, value, flags)
  local r = regs()
  local pc = tonumber(r.PC or 0)
  local frame = emu.framecount()
  if frame < 1820 or frame > 1850 then return end
  if pc < 0x9D30 or pc > 0x9E50 then return end
  hits = hits + 1
  if hits <= 10000 then
    write(string.format("EXEC n=%d frame=%d addr=%05X value=%s flags=%s PS=%04X PC=%04X DS0=%04X DS1=%04X IX=%04X IY=%04X AW=%04X BW=%04X CW=%04X DW=%04X bus16=%s bus20=%s rom16=%s rom20=%s",
      hits, frame, tonumber(addr) or -1, tostring(value), tostring(flags),
      tonumber(r.PS or 0), pc, tonumber(r.DS0 or 0), tonumber(r.DS1 or 0),
      tonumber(r.IX or 0), tonumber(r.IY or 0), tonumber(r.AW or 0), tonumber(r.BW or 0),
      tonumber(r.CW or 0), tonumber(r.DW or 0),
      tryread(pc, "System Bus"), tryread((tonumber(r.PS or 0) * 16 + pc) % 0x100000, "System Bus"),
      tryread(pc, "ROM"), tryread((tonumber(r.PS or 0) * 16 + pc) % 0x100000, "ROM")))
  end
end
local eok, handle = pcall(event.onmemoryexecuteany, callback, "intermission_loader_any", scope)
write("REGISTER any ok=" .. tostring(eok) .. " handle=" .. tostring(handle))
if eok then table.insert(handles, handle) end
for _ = 1, 8 do joypad.set({["P1 A"] = true}); emu.frameadvance() end
for _ = 1, 3 do joypad.set({["P1 A"] = false}); emu.frameadvance() end
for _ = 1, 180 do emu.frameadvance() end
write("DONE hits=" .. tostring(hits))
log:close(); client.exit()
