-- Dump system-bus code bytes around the intermission static loader.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_loader_code"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function dump(start_addr, size)
  for base = start_addr, start_addr + size - 1, 16 do
    local bytes = {}
    for i = 0, 15 do
      local ok, value = pcall(memory.readbyte, base + i, "System Bus")
      table.insert(bytes, ok and string.format("%02X", tonumber(value) or 0) or "??")
    end
    write(string.format("%05X: %s", base, table.concat(bytes, " ")))
  end
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then write("DONE load failed"); log:close(); client.exit(); return end
for _ = 1, 3 do emu.frameadvance() end
dump(0x0F380, 0x500)
write("DONE")
log:close(); client.exit()
