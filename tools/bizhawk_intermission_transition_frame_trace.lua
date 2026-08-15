-- Capture and measure the natural QuickSave5 -> intermission resource reload.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_TAG, MONOEYE_END_FRAME

local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local output = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local tag = os.getenv("MONOEYE_TAG") or "intermission_transition_frame_trace"
local end_frame = tonumber(os.getenv("MONOEYE_END_FRAME")) or 1875

os.execute('mkdir "' .. output .. '" 2>nul')
local log = assert(io.open(output .. "\\" .. tag .. ".log", "w"))

local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end

local regions = {
  { "map0", 0x3800, 0x4000 },
  { "gfx0", 0x4000, 0x8000 },
  { "gfx1", 0x8000, 0xC000 },
}

local function snapshot(lo, hi)
  local result = {}
  for address = lo, hi - 1 do
    result[#result + 1] = memory.readbyte(address)
  end
  return result
end

local previous = {}
local function measure_regions()
  local fields = {}
  for _, region in ipairs(regions) do
    local name, lo, hi = region[1], region[2], region[3]
    local current = snapshot(lo, hi)
    local changed = 0
    if previous[name] then
      for index = 1, #current do
        if current[index] ~= previous[name][index] then changed = changed + 1 end
      end
    end
    previous[name] = current
    fields[#fields + 1] = string.format("%s=%d", name, changed)
  end
  return table.concat(fields, " ")
end

local probes = { 0x384C, 0x3856, 0x3C5C, 0x3C60, 0x3C66, 0x3C6A }
local function u16(address)
  return memory.readbyte(address) + 0x100 * memory.readbyte(address + 1)
end

local function probe_values()
  local fields = {}
  for _, address in ipairs(probes) do
    fields[#fields + 1] = string.format("%04X=%04X", address, u16(address))
  end
  return table.concat(fields, " ")
end

local function capture(index)
  local frame = emu.framecount()
  local stem = output .. string.format("\\%s_%03d_f%d", tag, index, frame)
  local ok, err = pcall(client.screenshot, stem .. ".png")
  local state_ok, state_err = true, nil
  if frame == 1842 or frame == 1848 or frame == 1849 or frame == 1850
      or frame == 1851 or frame == 1863 or frame == 1866 or frame == 1875 then
    state_ok, state_err = pcall(savestate.save, stem .. ".State")
  end
  write(string.format(
    "FRAME index=%d emu=%d %s %s shot=%s state=%s error=%s|%s",
    index, frame, measure_regions(), probe_values(), tostring(ok),
    tostring(state_ok), tostring(err), tostring(state_err)))
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()
local loaded, load_err = pcall(savestate.load, state)
write("LOAD=" .. tostring(loaded) .. " error=" .. tostring(load_err))
if not loaded then
  log:close()
  client.exit()
  return
end

for _ = 1, 3 do emu.frameadvance() end
measure_regions()
for _ = 1, 8 do
  joypad.set({ ["P1 A"] = true })
  emu.frameadvance()
end
for _ = 1, 3 do
  joypad.set({ ["P1 A"] = false })
  emu.frameadvance()
end

-- The current fixture changes the intermission map around frames 1839-1851.
-- Capture a wider window so future timing drift is visible too.
local index = 0
while emu.framecount() < 1818 do emu.frameadvance() end
while emu.framecount() <= end_frame do
  capture(index)
  index = index + 1
  emu.frameadvance()
end

write("DONE frame=" .. tostring(emu.framecount()))
log:close()
client.exit()
