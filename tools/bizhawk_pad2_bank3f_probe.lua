-- Optional helper while testing pad2_bank3f_*.wsc
-- Shows whether tagged Hangul indices fall in pad1 (<96) or pad2 (>=96),
-- and prints the file offsets the blitter *should* use for bank41 vs bank3F.
--
-- Load manually in BizHawk Lua Console (no auto-input).
-- If WRAM domain is missing on Cygne, ignore this and use screenshots only.

local FLAG = 0x19FF
local BASE = 0x1A6E
local N = 20
local H0 = 0x820

local function w16(a)
  return memory.read_u16_le(a, "WRAM")
end

client.SetGameExtraPadding(0, 0, 0, 56)

while true do
  local ok, flag = pcall(function()
    return memory.read_u8(FLAG, "WRAM")
  end)
  if not ok then
    gui.text(2, 2, "NO WRAM — use screenshot A/B only")
  else
    local lines = { string.format("19FF=%d", flag) }
    for i = 0, N - 1 do
      local v = w16(BASE + i * 2)
      if v ~= 0 then
        local tagged = v >= 0x8000
        local raw = v % 0x8000
        local slot = raw - H0
        if tagged and slot >= 0 then
          local kind = (slot < 96) and "p1" or "p2"
          local off41 = 0x41E4F4 + math.max(0, slot - 96) * 16
          local off3f = 0x3FC5CE + math.max(0, slot - 96) * 16
          table.insert(
            lines,
            string.format(
              "%s s=%d %s 41:%06X 3F:%06X",
              kind,
              slot,
              tagged and "T" or ".",
              off41,
              off3f
            )
          )
        end
      end
    end
    gui.text(2, 2, table.concat(lines, "\n"))
  end
  emu.frameadvance()
end
