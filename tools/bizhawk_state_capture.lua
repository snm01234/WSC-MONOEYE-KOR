-- Load a savestate, optionally drive input, and capture native framebuffers.
--
-- The initial menu ignores every direction key under Lua input, so the
-- intermission cannot be reached by scripted input. A savestate made by hand
-- sidesteps that: load it and the screen is there, deterministically, in ~2 s.
--
-- Environment
--   MONOEYE_OUT     output directory (required)
--   MONOEYE_TAG     file tag (default "state")
--   MONOEYE_STATE   savestate path (required)
--   MONOEYE_SETTLE  frames to advance after loading before the first shot (default 4)
--   MONOEYE_SEQ     optional semicolon steps after the first shot; w<n> waits,
--                   anything else is a button. A shot is taken after each step.
--   MONOEYE_HOLD    frames to hold a button (default 8)
--   MONOEYE_SAVE_FINAL optional path for a savestate after the last sequence step
--
-- Output
--   <OUT>/<TAG>.log,  <OUT>/<TAG>_s00.png (and _s01.. per MONOEYE_SEQ step)

local out_dir = os.getenv("MONOEYE_OUT")
local tag = os.getenv("MONOEYE_TAG") or "state"
local state = os.getenv("MONOEYE_STATE")
local settle = tonumber(os.getenv("MONOEYE_SETTLE") or "4")
local seq = os.getenv("MONOEYE_SEQ")
local hold = tonumber(os.getenv("MONOEYE_HOLD") or "8")
local save_final = os.getenv("MONOEYE_SAVE_FINAL")

if out_dir == nil or out_dir == "" then error("MONOEYE_OUT must be set") end
if state == nil or state == "" then error("MONOEYE_STATE must be set") end
os.execute('mkdir "' .. out_dir .. '" 2>nul')

local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(m)
  log:write(m .. "\n"); log:flush(); console.log(m)
end

local function advance(n) for _ = 1, n do emu.frameadvance() end end

local function press(key, frames)
  local full = string.find(key, " ") and key or ("P1 " .. key)
  for _ = 1, frames do joypad.set({ [full] = true }); emu.frameadvance() end
  for _ = 1, 3 do joypad.set({ [full] = false }); emu.frameadvance() end
end

local shot_n = 0
local function shot()
  local path = string.format("%s\\%s_s%02d.png", out_dir, tag, shot_n)
  local ok, err = pcall(client.screenshot, path)
  write(string.format("SHOT %02d frame=%d w=%s h=%s ok=%s %s", shot_n, emu.framecount(),
    tostring(client.bufferwidth()), tostring(client.bufferheight()), tostring(ok), tostring(err)))
  shot_n = shot_n + 1
end

write("ROM=" .. tostring(gameinfo.getromname()))
write("HASH=" .. tostring(gameinfo.getromhash()))

-- One frame before loading: some cores refuse a load at frame 0.
emu.frameadvance()
local ok, err = pcall(savestate.load, state)
write("LOADSTATE " .. tostring(ok) .. " " .. tostring(err) .. "  " .. state)
if not ok then
  write("DONE (load failed)")
  log:close()
  client.exit()
  return
end

advance(settle)
shot()

if seq ~= nil and seq ~= "" then
  for step in string.gmatch(seq, "[^;]+") do
    local n = string.match(step, "^w(%d+)$")
    if n then advance(tonumber(n)) else press(step, hold) end
    write("SEQ " .. step .. " -> frame=" .. emu.framecount())
    shot()
  end
end

if save_final ~= nil and save_final ~= "" then
  local save_ok, save_err = pcall(savestate.save, save_final)
  write("SAVESTATE " .. tostring(save_ok) .. " " .. tostring(save_err) .. "  " .. save_final)
end

write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
