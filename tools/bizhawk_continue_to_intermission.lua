-- Advance the stock Continue state until the intermission tilemap signature appears.
-- Environment: MONOEYE_STATE, MONOEYE_OUT, MONOEYE_FINAL_STATE
local state = assert(os.getenv("MONOEYE_STATE"), "MONOEYE_STATE required")
local out_dir = assert(os.getenv("MONOEYE_OUT"), "MONOEYE_OUT required")
local final_state = assert(os.getenv("MONOEYE_FINAL_STATE"), "MONOEYE_FINAL_STATE required")
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = assert(io.open(out_dir .. "\\continue_to_intermission.log", "w"))
local function write(s) log:write(s .. "\n"); log:flush(); console.log(s) end
local function read16(addr)
  local ok, v = pcall(memory.read_u16_le, addr)
  if ok then return tonumber(v), "default" end
  ok, v = pcall(mainmemory.read_u16_le, addr)
  if ok then return tonumber(v), "mainmemory" end
  return nil, tostring(v)
end
local signature = {0x0168,0x0169,0x016A,0x016B,0x016C,0x016D,0x016E,0x016F}
local function matches()
  for i, want in ipairs(signature) do
    local got = read16(0x3800 + (i - 1) * 2)
    if got ~= want then return false end
  end
  return true
end
local function sample()
  local out = {}
  for i=0,7 do
    local v, source = read16(0x3800 + i * 2)
    table.insert(out, v and string.format("%04X",v) or ("ERR:" .. tostring(source)))
  end
  return table.concat(out, ",")
end
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOAD=" .. tostring(ok) .. " " .. tostring(err))
if not ok then log:close(); client.exit(); return end
for _=1,3 do emu.frameadvance() end
write("START frame=" .. emu.framecount() .. " sig=" .. sample())
local found = false
local max_frames = tonumber(os.getenv("MONOEYE_MAX_FRAMES") or "30000")
local pulse_every = tonumber(os.getenv("MONOEYE_PULSE_EVERY") or "30")
for step=1,max_frames do
  local phase = step % pulse_every
  joypad.set({["P1 A"] = phase >= 1 and phase <= 6})
  emu.frameadvance()
  if step % 30 == 0 and matches() then
    found = true
    write("MATCH frame=" .. emu.framecount() .. " step=" .. step .. " sig=" .. sample())
    break
  end
  if step % 1000 == 0 then write("PROGRESS frame=" .. emu.framecount() .. " step=" .. step .. " sig=" .. sample()) end
end
joypad.set({["P1 A"] = false})
if found then
  local shot = out_dir .. "\\intermission_found.png"
  local sok, serr = pcall(client.screenshot, shot)
  local stok, sterr = pcall(savestate.save, final_state)
  write("SHOT=" .. tostring(sok) .. " " .. tostring(serr) .. " " .. shot)
  write("STATE=" .. tostring(stok) .. " " .. tostring(sterr) .. " " .. final_state)
else
  write("NO_MATCH frame=" .. emu.framecount() .. " sig=" .. sample())
end
write("DONE")
log:close(); client.exit()
