-- Load selector-variant states, advance two frames, capture and save final state.
-- This does not synthesize menu input; the game rebuilds the focus sprites from
-- the one varied serialized wsRAM control byte.

local input_dir = assert(os.getenv("MONOEYE_SWEEP_INPUT"), "MONOEYE_SWEEP_INPUT required")
local output_dir = assert(os.getenv("MONOEYE_SWEEP_OUTPUT"), "MONOEYE_SWEEP_OUTPUT required")
local lo = tonumber(os.getenv("MONOEYE_SWEEP_LO") or "0")
local hi = tonumber(os.getenv("MONOEYE_SWEEP_HI") or "31")
local settle = tonumber(os.getenv("MONOEYE_SWEEP_SETTLE") or "2")

os.execute('mkdir "' .. output_dir .. '" 2>nul')
local log = assert(io.open(output_dir .. "\\selector_sweep.log", "w"))
local function write(message)
  log:write(message .. "\n")
  log:flush()
  console.log(message)
end
local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))
emu.frameadvance()

for value = lo, hi do
  local hex = string.format("%02X", value)
  local input = input_dir .. "\\selector_" .. hex .. ".State"
  local final = output_dir .. "\\selector_" .. hex .. "_final.State"
  local shot = output_dir .. "\\selector_" .. hex .. ".png"
  local load_ok, load_err = pcall(savestate.load, input)
  if load_ok then
    advance(settle)
    local shot_ok, shot_err = pcall(client.screenshot, shot)
    local save_ok, save_err = pcall(savestate.save, final)
    write(string.format(
      "VALUE %s LOAD %s SHOT %s SAVE %s FRAME %d ERR %s|%s|%s",
      hex, tostring(load_ok), tostring(shot_ok), tostring(save_ok), emu.framecount(),
      tostring(load_err), tostring(shot_err), tostring(save_err)))
  else
    write(string.format("VALUE %s LOAD false ERR %s", hex, tostring(load_err)))
  end
end

write("DONE")
log:close()
client.exit()
