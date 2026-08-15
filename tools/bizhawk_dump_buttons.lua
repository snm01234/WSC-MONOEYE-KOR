-- Dump the controller button names and framebuffer availability for this core.
-- The new-game probe needs the exact joypad key strings and a way to sample the
-- screen; both differ between BizHawk cores, so measure instead of guessing.
local out_dir = os.getenv("PROBE_OUT")
local tag = os.getenv("PROBE_TAG") or "buttons"
os.execute('mkdir "' .. out_dir .. '" 2>nul')
local log = io.open(out_dir .. "\\" .. tag .. ".log", "w")
local function write(msg)
  log:write(msg .. "\n")
  log:flush()
end

write("ROM=" .. gameinfo.getromname())
for _ = 1, 60 do emu.frameadvance() end

local ok, buttons = pcall(joypad.get)
write("joypad.get ok=" .. tostring(ok))
if ok and type(buttons) == "table" then
  for k, v in pairs(buttons) do
    write("BUTTON " .. tostring(k) .. " = " .. tostring(v))
  end
end

local okfb, fb = pcall(emu.framebuffer)
write("emu.framebuffer ok=" .. tostring(okfb) .. " type=" .. type(fb))
if okfb and type(fb) == "table" then
  local n = 0
  for _ in pairs(fb) do n = n + 1 end
  write("framebuffer entries=" .. tostring(n))
  write("fb[1]=" .. tostring(fb[1]) .. " fb[1000]=" .. tostring(fb[1000]))
end

write("client.getscreenpixel=" .. tostring(client.getscreenpixel))
write("bufferwidth=" .. tostring(client.bufferwidth()) ..
      " bufferheight=" .. tostring(client.bufferheight()))

local okd, domains = pcall(memory.getmemorydomainlist)
write("memory domains ok=" .. tostring(okd))
if okd then
  for _, d in ipairs(domains) do
    write("DOMAIN " .. tostring(d) .. " size=" .. tostring(memory.getmemorydomainsize(d)))
  end
end

log:close()
client.exit()
