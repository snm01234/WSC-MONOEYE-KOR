-- Trace the intermission renderer wrapper path during a clean boot.
-- Environment: MONOEYE_OUT, MONOEYE_TAG
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "boot_wrapper"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function regs()
  local ok, r = pcall(emu.getregisters)
  if not ok or type(r) ~= "table" then return nil end
  return r
end
local function fmt(r)
  if not r then return "regs=none" end
  return string.format("PS=%04X PC=%04X SP=%04X SS=%04X AW=%04X BW=%04X CW=%04X DW=%04X IX=%04X IY=%04X",
    tonumber(r.PS or 0), tonumber(r.PC or 0), tonumber(r.SP or 0), tonumber(r.SS or 0),
    tonumber(r.AW or 0), tonumber(r.BW or 0), tonumber(r.CW or 0), tonumber(r.DW or 0),
    tonumber(r.IX or 0), tonumber(r.IY or 0))
end
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local points = {
  {"hook", 0x89C4D},
  {"outer", 0xDFF00},
  {"old", 0x8FCD3},
  {"original_target", 0x8DEB5},
}
local hits = {}
for _, p in ipairs(points) do
  hits[p[1]] = 0
  local name, address = p[1], p[2]
  local ok, handle = pcall(event.onmemoryexecute, function(addr, value, flags)
    hits[name] = hits[name] + 1
    if hits[name] <= 12 then
      write(string.format("HIT %s #%d addr=%05X frame=%d %s", name, hits[name], tonumber(addr) or -1, emu.framecount(), fmt(regs())))
    end
  end, address, "boot_" .. name)
  write(string.format("REGISTER %s %05X ok=%s handle=%s", name, address, tostring(ok), tostring(handle)))
end
emu.frameadvance()
for i=1,220 do
  emu.frameadvance()
  if i == 30 or i == 90 or i == 150 or i == 210 then
    write(string.format("FRAME %d emu=%d %s", i, emu.framecount(), fmt(regs())))
  end
end
write(string.format("DONE hook=%d outer=%d old=%d original=%d", hits.hook, hits.outer, hits.old, hits.original_target))
log:close()
client.exit()
