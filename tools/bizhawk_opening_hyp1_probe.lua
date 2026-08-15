-- Opening hyp-1 probe: tagged Hangul path vs VRAM/decoder mismatch.
--
-- Target ROM (load via EmuHawk --lua=...):
--   out/patch/bisect/10_marked_ui_isolation_poc.wsc
--
-- What it measures
--   1) Memory domains (Cygne usually: ROM / SRAM / iEEPROM only — no WRAM/VRAM)
--   2) WRAM flag DS:19FF + glyph index buffer DS:1A6E (bit15 = Hangul tag "T")
--      when a WRAM-like domain exists
--   3) ROM padding glyphs at 40:F9F8 vs stock 40:E740 slot (always readable)
--   4) Autoplay title → New Game → mash A through opening narration
--   5) Periodic savestates + best-effort screenshots for offline Core.bin scan
--
-- Verdict lines written to hyp1_verdict.log:
--   NO_WRAM_DOMAIN          → cannot prove T-flag on this core; use Core.bin scan
--   TAG_SEEN / TAG_NEVER    → store/blitter tagging path live or not
--   FLAG_PULSED / FLAG_STUCK0
--   PAD_NONEMPTY / PAD_EMPTY

local ROOT = "C:\\Users\\SangGeun\\monoeye\\out\\bizhawk\\hyp1"
local ROM_PAD_FILE = 0x40F9F8 -- bank40:F9F8 first Hangul padding glyph
local ROM_STOCK_E740 = 0x408640 -- 0x400440 + 0x820*16
local FLAG_ADDR = 0x19FF
local IDX_BASE = 0x1A6E
local IDX_N = 32
local HANGUL_LO = 0x820
local HANGUL_HI = 0x820 + 96 -- exclusive

os.execute('mkdir "' .. ROOT .. '" 2>nul')

local log = assert(io.open(ROOT .. "\\hyp1_probe.log", "w"))
local verdict = assert(io.open(ROOT .. "\\hyp1_verdict.log", "w"))

local function write(msg)
  console.log(msg)
  log:write(msg .. "\n")
  log:flush()
end

local function vwrite(msg)
  write("VERDICT " .. msg)
  verdict:write(msg .. "\n")
  verdict:flush()
end

local function pulse(button, hold)
  hold = hold or 12
  local key = "P1 " .. button
  for _ = 1, hold do
    joypad.set({ [key] = true })
    emu.frameadvance()
  end
  for _ = 1, 4 do
    joypad.set({ [key] = false })
    emu.frameadvance()
  end
end

local function shot(tag)
  local path = string.format("%s\\%s_f%05d.png", ROOT, tag, emu.framecount())
  local ok, err = pcall(client.screenshot, path)
  write(string.format("SHOT %s ok=%s err=%s", path, tostring(ok), tostring(err)))
end

local function save(tag)
  local path = string.format("%s\\%s_f%05d.State", ROOT, tag, emu.framecount())
  local ok, err = pcall(savestate.save, path)
  write(string.format("STATE %s ok=%s err=%s", path, tostring(ok), tostring(err)))
end

-- ---------- domain discovery ----------
local domains = {}
local wram_domain = nil
local rom_domain = nil

for _, name in ipairs(memory.getmemorydomainlist()) do
  local size = 0
  local ok, sz = pcall(memory.getmemorydomainsize, name)
  if ok then size = sz end
  domains[#domains + 1] = { name = name, size = size }
  write(string.format("DOMAIN %s size=%d", name, size))
  local lower = string.lower(name)
  if lower == "rom" then
    rom_domain = name
  end
  if lower:find("wram", 1, true)
    or lower:find("main.?ram", 1)
    or lower == "ram"
    or lower:find("system.?bus", 1)
    or lower:find("work.?ram", 1)
  then
    if size >= 0x2000 then
      wram_domain = name
    end
  end
end

if not wram_domain then
  -- Probe every non-ROM domain for readable 1A6E pattern later; mark none for now.
  vwrite("NO_WRAM_DOMAIN")
else
  vwrite("WRAM_DOMAIN=" .. wram_domain)
end

local function try_u8(domain, addr)
  local ok, v = pcall(memory.read_u8, addr, domain)
  if ok then return v end
  return nil
end

local function try_u16(domain, addr)
  local ok, v = pcall(memory.read_u16_le, addr, domain)
  if ok then return v end
  return nil
end

local function rom_u8(addr)
  if not rom_domain then return nil end
  return try_u8(rom_domain, addr)
end

local function ink16(addr)
  -- Count non-zero bytes in one 16B compact glyph at file offset.
  local ink = 0
  for i = 0, 15 do
    local b = rom_u8(addr + i)
    if b == nil then return nil end
    if b ~= 0 then ink = ink + 1 end
  end
  return ink
end

local pad_ink = ink16(ROM_PAD_FILE)
local stock_ink = ink16(ROM_STOCK_E740)
write(string.format("ROM_PAD_INK_BYTES=%s STOCK_E740_INK_BYTES=%s", tostring(pad_ink), tostring(stock_ink)))
if pad_ink and pad_ink > 0 then
  vwrite("PAD_NONEMPTY")
else
  vwrite("PAD_EMPTY_OR_UNREADABLE")
end

-- ---------- live WRAM stats (optional) ----------
local stats = {
  samples = 0,
  flag_nonzero = 0,
  tagged_slots = 0,
  hangul_range_slots = 0,
  hangul_tagged_slots = 0,
  max_nonzero_indices = 0,
}

local function sample_wram(label)
  if not wram_domain then
    return
  end
  stats.samples = stats.samples + 1
  local flag = try_u8(wram_domain, FLAG_ADDR)
  if flag and flag ~= 0 then
    stats.flag_nonzero = stats.flag_nonzero + 1
  end
  local parts = {
    string.format(
      "f=%s flag19FF=%s",
      tostring(emu.framecount()),
      flag ~= nil and string.format("%02X", flag) or "?"
    ),
  }
  local nonzero = 0
  for i = 0, IDX_N - 1 do
    local v = try_u16(wram_domain, IDX_BASE + i * 2)
    if v and v ~= 0 then
      nonzero = nonzero + 1
      local tagged = (v >= 0x8000)
      local raw = v % 0x8000
      local in_h = (raw >= HANGUL_LO and raw < HANGUL_HI)
      if tagged then stats.tagged_slots = stats.tagged_slots + 1 end
      if in_h then stats.hangul_range_slots = stats.hangul_range_slots + 1 end
      if tagged and in_h then stats.hangul_tagged_slots = stats.hangul_tagged_slots + 1 end
      table.insert(
        parts,
        string.format("%02d:%04X%s%s", i, v, tagged and "T" or ".", in_h and "H" or "")
      )
    end
  end
  if nonzero > stats.max_nonzero_indices then
    stats.max_nonzero_indices = nonzero
  end
  write(label .. " " .. table.concat(parts, " "))
  -- On-screen HUD when WRAM works
  gui.text(2, 2, table.concat(parts, " "))
  if stats.hangul_tagged_slots > 0 and not stats._tag_verdict_written then
    stats._tag_verdict_written = true
    vwrite("TAG_SEEN")
    vwrite("ALT_DECODER_HYPOTHESIS=REFUTED")
  end
end

-- Also try reading "WRAM" literally even if not listed (some builds accept it).
if not wram_domain then
  local ok, v = pcall(memory.read_u8, FLAG_ADDR, "WRAM")
  if ok and v ~= nil then
    wram_domain = "WRAM"
    vwrite("WRAM_DOMAIN=WRAM (unlisted-but-readable)")
  end
end

-- ---------- playback ----------
write("ROM=" .. gameinfo.getromname())
write("HASH=" .. gameinfo.getromhash())
pcall(client.setwindowsize, 3)
client.SetGameExtraPadding(0, 0, 0, 40)

for _ = 1, 500 do
  emu.frameadvance()
end
shot("boot")
save("boot")
sample_wram("SAMPLE")

pulse("Start", 12)
for _ = 1, 90 do emu.frameadvance() end
pulse("Start", 12)
for _ = 1, 90 do emu.frameadvance() end
shot("menu")
save("menu")
sample_wram("SAMPLE")

pulse("A", 12)
for _ = 1, 90 do emu.frameadvance() end
shot("newgame")
save("newgame")

pulse("A", 12)
for _ = 1, 60 do emu.frameadvance() end
sample_wram("SAMPLE")

-- Mash through prologue / opening narration (~same length as marked10 run).
local pulses = 0
for frame = 1, 7200 do
  if frame % 50 == 0 then
    pulse("A", 8)
    pulses = pulses + 1
    sample_wram("SAMPLE")
  else
    emu.frameadvance()
    if wram_domain and (frame % 10 == 0) then
      -- light HUD refresh without log spam
      local flag = try_u8(wram_domain, FLAG_ADDR) or 0
      gui.text(2, 2, string.format("hyp1 flag19FF=%02X pulses=%d", flag, pulses))
    end
  end

  if frame == 600 or frame == 1200 or frame == 1800 or frame == 2400
    or frame == 3600 or frame == 4800 or frame == 6000 or frame == 7200 then
    shot(string.format("adv_%04d", frame))
    save(string.format("adv_%04d", frame))
    sample_wram("CHECKPOINT")
  end
end

shot("end")
save("end")
sample_wram("FINAL")

-- ---------- final verdict from live WRAM ----------
if wram_domain then
  if stats.flag_nonzero > 0 then
    vwrite(string.format("FLAG_PULSED samples=%d nonzero=%d", stats.samples, stats.flag_nonzero))
  else
    vwrite(string.format("FLAG_STUCK0 samples=%d", stats.samples))
  end
  if stats.tagged_slots > 0 then
    vwrite(string.format(
      "TAG_SEEN tagged=%d hangul_range=%d hangul_tagged=%d max_idx=%d",
      stats.tagged_slots, stats.hangul_range_slots, stats.hangul_tagged_slots, stats.max_nonzero_indices
    ))
  else
    vwrite(string.format(
      "TAG_NEVER hangul_range=%d max_idx=%d (hook path likely not used OR marker not consumed)",
      stats.hangul_range_slots, stats.max_nonzero_indices
    ))
  end
  if stats.hangul_range_slots > 0 and stats.hangul_tagged_slots == 0 then
    vwrite("HYP1_LEAN_ALT_DECODER_OR_MARKER_MISS")
  elseif stats.hangul_tagged_slots > 0 then
    vwrite("HYP1_LEAN_TAG_OK_IF_STILL_GARBLED_THEN_VRAM_OR_BLIT")
  end
else
  vwrite("LIVE_WRAM_UNAVAILABLE_USE_COREBIN_SCAN")
end

write(string.format(
  "DONE frame=%d pulses=%d samples=%d",
  emu.framecount(), pulses, stats.samples
))
log:close()
verdict:close()
client.exit()
