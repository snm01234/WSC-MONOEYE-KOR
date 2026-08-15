-- Trace the routine that rebuilds the intermission static-layer tilemap.
--
-- Environment:
--   MONOEYE_STATE  source intermission savestate
--   MONOEYE_OUT    output directory

local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\tilemap_trace.log", "w"))
local function write(s)
  log:write(s .. "\n")
  log:flush()
  console.log(s)
end

local function regs()
  local ok, values = pcall(emu.getregisters)
  if not ok or type(values) ~= "table" then return "registers unavailable: " .. tostring(values) end
  local keys = {}
  for key, _ in pairs(values) do table.insert(keys, key) end
  table.sort(keys)
  local out = {}
  for _, key in ipairs(keys) do table.insert(out, key .. "=" .. tostring(values[key])) end
  return table.concat(out, " ")
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
write("DOMAINS=" .. table.concat(memory.getmemorydomainlist(), ","))
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then log:close(); client.exit(); return end
for _ = 1, 3 do emu.frameadvance() end
write("REGS_AFTER_LOAD " .. regs())

local count = 0
local function callback(addr, value, flags)
  count = count + 1
  if count <= 80 then
    write(string.format("WRITE n=%d frame=%d addr=%04X value=%s flags=%s %s",
      count, emu.framecount(), tonumber(addr) or -1, tostring(value), tostring(flags), regs()))
  end
end

local handles = {}
for _, address in ipairs({0x3800, 0x3856, 0x3C1C, 0x3C66}) do
  local cb_ok, handle = pcall(event.onmemorywrite, callback, address,
    string.format("intermission_tm_%04X", address))
  write(string.format("REGISTER %04X ok=%s handle=%s", address, tostring(cb_ok), tostring(handle)))
  if cb_ok then table.insert(handles, handle) end
end

local function advance(n) for _ = 1, n do emu.frameadvance() end end
local function press(key, frames)
  for _ = 1, frames do joypad.set({["P1 " .. key] = true}); emu.frameadvance() end
  for _ = 1, 3 do joypad.set({["P1 " .. key] = false}); emu.frameadvance() end
end

press("A", 8)
advance(180)
press("B", 8)
advance(300)
write("DONE writes=" .. tostring(count) .. " frame=" .. tostring(emu.framecount()))
log:close()
client.exit()
