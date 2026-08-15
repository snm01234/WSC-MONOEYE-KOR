-- New-game probe: boot, start a new game, sample the framebuffer, exit.
--
-- Used to tell "reached the opening narration" from "event error 257 / 2049"
-- without a human watching. The probe writes a per-checkpoint framebuffer digest
-- plus screenshots; the driver compares the digests against a reference run of a
-- ROM known to reach the opening.
--
-- Environment:
--   PROBE_OUT   output directory (created if missing)
--   PROBE_TAG   filename tag for this ROM
--   PROBE_HOLD  frames to hold each button press        (default 6)
--   PROBE_WAIT  frames to wait between presses          (default 90)
--   PROBE_POST_STEPS number of post-opening A presses  (default 0)

local out_dir = os.getenv("PROBE_OUT")
local tag = os.getenv("PROBE_TAG") or "rom"
local hold = tonumber(os.getenv("PROBE_HOLD") or "6")
local wait = tonumber(os.getenv("PROBE_WAIT") or "90")
local post_steps = tonumber(os.getenv("PROBE_POST_STEPS") or "0")
local post_wait = tonumber(os.getenv("PROBE_POST_WAIT") or tostring(wait * 2))
if out_dir == nil then
  error("PROBE_OUT must be set")
end
os.execute('mkdir "' .. out_dir .. '" 2>nul')

local log = io.open(out_dir .. "\\" .. tag .. ".log", "w")
local function write(msg)
  log:write(msg .. "\n")
  log:flush()
end

write("ROM=" .. gameinfo.getromname())
write("HASH=" .. tostring(gameinfo.getromhash()))

local function advance(n)
  for _ = 1, n do emu.frameadvance() end
end

local function press(button, frames)
  for _ = 1, frames do
    joypad.set({ [button] = true })
    emu.frameadvance()
  end
  joypad.set({ [button] = false })
end

local function checkpoint(label)
  local path = string.format("%s\\%s_%s_f%04d.png", out_dir, tag, label, emu.framecount())
  client.screenshot(path)
  -- Cygne 2.11 does not expose client.getscreenpixel/emu.framebuffer.
  -- The Python driver hashes this exact PNG after the emulator writes it.
  write(string.format("CP %s frame=%d shot=%s", label, emu.framecount(), path))
end

-- The title has a short logo-clear phase. The second Start opens the menu,
-- whose default selection is New Game; A confirms it.
advance(590)
checkpoint("title")

advance(10)
press("P1 Start", hold)
advance(135)
checkpoint("after_start")

press("P1 Start", hold)
advance(95)
checkpoint("menu")

press("P1 A", hold)
advance(wait * 2)
checkpoint("opening_1")

advance(wait * 2)
checkpoint("opening_2")

press("P1 A", hold)
advance(wait * 2)
checkpoint("opening_3")

-- Continue the same conversation one text advance at a time.  The default
-- remains the original opening-only probe; post-opening runs opt in through
-- PROBE_POST_STEPS so the old checkpoint contract stays unchanged.
for i = 1, post_steps do
  press("P1 A", hold)
  advance(post_wait)
  checkpoint(string.format("post_%02d", i))
end

write("DONE frames=" .. tostring(emu.framecount()))
log:close()
client.exit()
