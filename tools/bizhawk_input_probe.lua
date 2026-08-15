-- Find out which joypad keys this core accepts and what each does on the title.
--
-- The intermission runner needs Start / a direction / a confirm button. A first
-- attempt pulsed "P1 Start" then "P1 X3" and nothing moved, so before guessing
-- again: dump the real key strings, then press one candidate per savestate-reset
-- and screenshot the result. A no-input control run tells apart "input works" from
-- "the title advances on its own".
--
-- Environment
--   MONOEYE_OUT   output directory (required)
--   MONOEYE_TAG   file tag (default "input")
--   MONOEYE_KEYS  comma-separated buttons to try (default a broad set)
--   MONOEYE_HOLD  frames to hold        (default 12)
--   MONOEYE_WAIT  frames before pressing (default 600)
--   MONOEYE_AFTER frames after pressing  (default 150)
--   MONOEYE_PRELUDE  semicolon list run *before* the branch savestate, so a
--                    candidate can be probed from a deeper screen.
--                    Steps: "w<frames>" waits, anything else is a button.
--                    e.g. "w600;Start;w150" = title, press Start, settle in menu
--   MONOEYE_SAV   raw .sav injected into SRAM at frame 0

local out_dir = os.getenv("MONOEYE_OUT")
local tag = os.getenv("MONOEYE_TAG") or "input"
local hold = tonumber(os.getenv("MONOEYE_HOLD") or "12")
local wait = tonumber(os.getenv("MONOEYE_WAIT") or "600")
local after = tonumber(os.getenv("MONOEYE_AFTER") or "150")
local keys_env = os.getenv("MONOEYE_KEYS")

if out_dir == nil or out_dir == "" then error("MONOEYE_OUT must be set") end
os.execute('mkdir "' .. out_dir .. '" 2>nul')

local log = assert(io.open(out_dir .. "\\" .. tag .. ".log", "w"))
local function write(msg)
  log:write(msg .. "\n"); log:flush(); console.log(msg)
end

local function split(s)
  local t = {}
  for part in string.gmatch(s, "[^,]+") do t[#t + 1] = (part:gsub("^%s+", ""):gsub("%s+$", "")) end
  return t
end

local candidates = keys_env and split(keys_env)
  or {"NONE", "Start", "A", "B", "X1", "X2", "X3", "X4", "Y1", "Y2", "Y3", "Y4"}

local function advance(n) for _ = 1, n do emu.frameadvance() end end

local function press(key, frames)
  -- A key may carry its own player prefix ("P2 X3"); otherwise assume P1.
  local full = string.find(key, " ") and key or ("P1 " .. key)
  for _ = 1, frames do
    joypad.set({ [full] = true })
    emu.frameadvance()
  end
  for _ = 1, 3 do
    joypad.set({ [full] = false })
    emu.frameadvance()
  end
end

local function split_semi(s)
  local t = {}
  for part in string.gmatch(s, "[^;]+") do t[#t + 1] = (part:gsub("^%s+", ""):gsub("%s+$", "")) end
  return t
end

local function inject_sram(path)
  local fh = io.open(path, "rb")
  if fh == nil then return false, "missing " .. tostring(path) end
  local data = fh:read("*all")
  fh:close()
  local size = memory.getmemorydomainsize("SRAM")
  local n = math.min(#data, size or 0)
  local bytes = {}
  for i = 1, n do bytes[i - 1] = string.byte(data, i) end
  local ok, err = pcall(memory.write_bytes_as_dict, bytes, "SRAM")
  if not ok then
    ok, err = pcall(function()
      for i = 0, n - 1 do memory.writebyte(i, bytes[i], "SRAM") end
    end)
  end
  local nonzero = 0
  for i = 0, n - 1 do
    if memory.readbyte(i, "SRAM") ~= 0 then nonzero = nonzero + 1 end
  end
  return ok, string.format("bytes=%d readback_nonzero=%d %s", n, nonzero, tostring(err))
end

local function shot(label)
  local path = string.format("%s\\%s_%s.png", out_dir, tag, label)
  local ok, err = pcall(client.screenshot, path)
  write(string.format("SHOT %s frame=%d ok=%s %s", label, emu.framecount(), tostring(ok), tostring(err)))
end

write("ROM=" .. tostring(gameinfo.getromname()))
local ok, pad = pcall(joypad.get)
write("joypad.get ok=" .. tostring(ok))
if ok and type(pad) == "table" then
  local names = {}
  for k in pairs(pad) do names[#names + 1] = tostring(k) end
  table.sort(names)
  write("KEYS " .. table.concat(names, " | "))
end
local okd, dom = pcall(memory.getmemorydomainlist)
if okd then
  for _, d in ipairs(dom) do write("DOMAIN " .. d .. " size=" .. tostring(memory.getmemorydomainsize(d))) end
end

local sav = os.getenv("MONOEYE_SAV")
if sav ~= nil and sav ~= "" then
  local iok, inote = inject_sram(sav)
  write("SRAM_INJECT ok=" .. tostring(iok) .. " " .. tostring(inote))
end

-- Reach the branch point, then branch from a savestate so every candidate starts
-- from exactly the same machine state.
local prelude = os.getenv("MONOEYE_PRELUDE")
if prelude ~= nil and prelude ~= "" then
  for _, step in ipairs(split_semi(prelude)) do
    local frames = string.match(step, "^w(%d+)$")
    if frames then
      advance(tonumber(frames))
      write("PRELUDE wait " .. frames .. " -> frame=" .. emu.framecount())
    else
      press(step, hold)
      write("PRELUDE press " .. step .. " -> frame=" .. emu.framecount())
    end
  end
else
  advance(wait)
end
local state = out_dir .. "\\" .. tag .. "_title.State"
local sok, serr = pcall(savestate.save, state)
write("SAVESTATE " .. tostring(sok) .. " " .. tostring(serr))
shot("title")

for _, key in ipairs(candidates) do
  local lok, lerr = pcall(savestate.load, state)
  if not lok then write("LOADSTATE FAIL " .. tostring(lerr)) break end
  if key == "NONE" then
    advance(hold + 3)
    write("PRESS none frame=" .. emu.framecount())
  else
    local full = string.find(key, " ") and key or ("P1 " .. key)
    local accepted = pad ~= nil and pad[full] ~= nil
    press(key, hold)
    write(string.format("PRESS %s known=%s frame=%d", full, tostring(accepted), emu.framecount()))
  end
  advance(after)
  shot(key)
end

write("DONE frame=" .. emu.framecount())
log:close()
client.exit()
