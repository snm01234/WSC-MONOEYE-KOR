-- rom_reload capture with optional post-reload WSRAM tilemap patches.
-- Environment: MONOEYE_OUT, MONOEYE_TAG, MONOEYE_STATE, MONOEYE_SETTLE,
-- MONOEYE_SEQ, MONOEYE_HOLD, MONOEYE_SAVE_FINAL, MONOEYE_TILEMAP_PATCHES
--
-- MONOEYE_TILEMAP_PATCHES is a compact list: "col,row,entry;col,row,entry;..."
-- entry is a hex tilemap word (e.g. 0001). Applied after the last SEQ step.

local out_dir = os.getenv("MONOEYE_OUT")
local tag = os.getenv("MONOEYE_TAG") or "state"
local state = os.getenv("MONOEYE_STATE")
local settle = tonumber(os.getenv("MONOEYE_SETTLE") or "4")
local seq = os.getenv("MONOEYE_SEQ")
local hold = tonumber(os.getenv("MONOEYE_HOLD") or "8")
local save_final = os.getenv("MONOEYE_SAVE_FINAL")
local patches_env = os.getenv("MONOEYE_TILEMAP_PATCHES") or ""

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

local function read16(addr)
  local ok, v = pcall(memory.read_u16_le, addr)
  if ok then return tonumber(v) end
  ok, v = pcall(mainmemory.read_u16_le, addr)
  if ok then return tonumber(v) end
  return nil
end

local function write16(addr, value)
  local ok, err = pcall(memory.write_u16_le, addr, value)
  if ok then return true, "memory" end
  ok, err = pcall(mainmemory.write_u16_le, addr, value)
  if ok then return true, "mainmemory" end
  return false, tostring(err)
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

if patches_env ~= "" then
  write("PATCH_BEGIN " .. patches_env)
  for item in string.gmatch(patches_env, "[^;]+") do
    local col, row, entry_hex = string.match(item, "^(%d+),(%d+),(%x+)$")
    if col and row and entry_hex then
      local c = tonumber(col)
      local r = tonumber(row)
      local entry = tonumber(entry_hex, 16)
      local addr = 0x3800 + (r * 32 + c) * 2
      local before = read16(addr)
      local wok, wsrc = write16(addr, entry)
      local after = read16(addr)
      write(string.format(
        "PATCH col=%d row=%d addr=%04X before=%s entry=%04X after=%s ok=%s src=%s",
        c, r, addr,
        before and string.format("%04X", before) or "ERR",
        entry,
        after and string.format("%04X", after) or "ERR",
        tostring(wok), tostring(wsrc)
      ))
    else
      write("PATCH_BAD " .. item)
    end
  end
  advance(2)
  shot()
  write("PATCH_END")
end

if save_final ~= nil and save_final ~= "" then
  local save_ok, save_err = pcall(savestate.save, save_final)
  write("SAVESTATE " .. tostring(save_ok) .. " " .. tostring(save_err) .. "  " .. save_final)
end

write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
