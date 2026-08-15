-- Load a supplied Cygne state and capture one deterministic frame.
local root = os.getenv("MONOEYE_OUT") or "D:\\monoeye\\out\\patch\\state_probe"
local tag = os.getenv("MONOEYE_TAG") or "state"
local state_path = os.getenv("STATE_PATH")
os.execute('mkdir "' .. root .. '" 2>nul')
local log = assert(io.open(root .. "\\" .. tag .. ".log", "w"))
local function w(s) log:write(s .. "\n"); log:flush() end
w("ROM=" .. gameinfo.getromname())
w("STATE=" .. tostring(state_path))
if state_path then
  local ok, err = pcall(savestate.load, state_path)
  w("LOAD=" .. tostring(ok) .. " " .. tostring(err))
end
for _ = 1, 10 do emu.frameadvance() end
local shot = root .. "\\" .. tag .. ".png"
local ok, err = pcall(client.screenshot, shot)
w("SHOT=" .. tostring(ok) .. " " .. tostring(err))
w("FRAME=" .. emu.framecount())
w("DONE")
log:close()
client.exit()
