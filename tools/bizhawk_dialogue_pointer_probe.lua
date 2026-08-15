-- Probe a saved scenario state for ROM-backed text pointers and line advances.
local root = os.getenv("MONOEYE_OUT") or "D:\\monoeye\\out\\patch\\dialogue_pointer_probe"
local tag = os.getenv("MONOEYE_TAG") or "probe"
local state_path = os.getenv("STATE_PATH")
local addresses_path = os.getenv("ADDRESS_LIST")
os.execute('mkdir "' .. root .. '" 2>nul')
local log = assert(io.open(root .. "\\" .. tag .. ".log", "w"))
local function w(s) log:write(s .. "\n"); log:flush() end
local function u8(domain, a)
  local ok, v = pcall(memory.read_u8, a, domain)
  if ok then return v end
  return nil
end
local seen = {}
local function scan(domain, length, wanted)
  if not length then return end
  local hits = {}
  for a = 0, length - 3 do
    local b0, b1, b2 = u8(domain, a), u8(domain, a + 1), u8(domain, a + 2)
    if b0 and b1 and b2 then
      local key = string.format("%02X%02X%02X", b0, b1, b2)
      if wanted[key] or (next(wanted) == nil and b0 >= 0x60 and b0 <= 0x63) then
        local marker = domain .. ":" .. a .. "=" .. key
        if not seen[marker] then
          seen[marker] = true
          table.insert(hits, string.format("%s:%04X=%s", domain, a, key))
        end
      end
    end
  end
  if #hits > 0 then w("HITS " .. table.concat(hits, " ")) end
end

if state_path then pcall(savestate.load, state_path) end
w("ROM=" .. gameinfo.getromname())
for _, domain in ipairs(memory.getmemorydomainlist()) do
  local ok, n = pcall(memory.getmemorydomainlength, domain)
  w("DOMAIN " .. domain .. " len=" .. tostring(ok and n or "?"))
end
local wanted = {}
if addresses_path then
  local f = io.open(addresses_path, "r")
  if f then
    for line in f:lines() do
      local a = line:match("^%s*([0-9A-Fa-f]+)%s*$")
      if a then
        local n = tonumber(a, 16)
        if n then wanted[string.format("%06X", n)] = true end
      end
    end
    f:close()
  end
end
for frame = 1, 180 do
  if frame == 10 or frame == 45 or frame == 90 or frame == 135 then
    joypad.set({["P1 A"] = true})
  elseif frame == 11 or frame == 46 or frame == 91 or frame == 136 then
    joypad.set({["P1 A"] = false})
  end
  if frame == 10 or frame == 45 or frame == 90 or frame == 135 then
    for _, domain in ipairs(memory.getmemorydomainlist()) do
      local ok, n = pcall(memory.getmemorydomainlength, domain)
      if ok and n and n <= 0x20000 then scan(domain, n, wanted) end
    end
    pcall(savestate.save, string.format("%s\\%s_f%03d.State", root, tag, frame))
    pcall(client.screenshot, string.format("%s\\%s_f%03d.png", root, tag, frame))
  end
  emu.frameadvance()
end
w("DONE frame=" .. emu.framecount())
log:close()
client.exit()
