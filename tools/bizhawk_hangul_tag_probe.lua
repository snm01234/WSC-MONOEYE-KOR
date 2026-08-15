-- Manual Hangul tag diagnostic (NO auto-input).
-- Load in BizHawk Lua Console while viewing opening narration on
-- out/patch/bisect/10_marked_ui_isolation_poc.wsc
--
-- Checks:
--   WRAM 19FF = pending marker flag (should pulse 0/1 during Hangul decode)
--   WRAM 1A6E.. = glyph index words; Hangul should show bit15 set (OR 0x8000)
--
-- NOTE: BizHawk Cygne often exposes only ROM/SRAM/iEEPROM. If WRAM reads fail,
-- use tools/bizhawk_opening_hyp1_probe.lua + analyze_hyp1_corebin.py instead.

local FLAG = 0x19FF
local BASE = 0x1A6E
local N = 24

local function wram_u8(a)
  return memory.read_u8(a, "WRAM")
end

local function wram_u16(a)
  return memory.read_u16_le(a, "WRAM")
end

client.SetGameExtraPadding(0, 0, 0, 48)

local domains = table.concat(memory.getmemorydomainlist(), ",")
console.log("domains=" .. domains)

while true do
  local ok, flag = pcall(wram_u8, FLAG)
  if not ok then
    gui.text(2, 2, "NO WRAM domain (Cygne). Use hyp1 probe + Core.bin scan.")
  else
    local parts = { string.format("flag19FF=%d  indices:", flag) }
    for i = 0, N - 1 do
      local v = wram_u16(BASE + i * 2)
      if v ~= 0 then
        local tagged = (v >= 0x8000) and "T" or "."
        table.insert(parts, string.format("%02d:%04X%s", i, v, tagged))
      end
    end
    gui.text(2, 2, table.concat(parts, " "))
  end
  emu.frameadvance()
end
