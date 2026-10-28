-- The audio check's emulator half (tools/audio_check.py appends it to lockstep_emu.lua): Flycast's AICA output, sample
-- by sample, and what the sound driver set on the chip, from FlycastRedux's research bus (flycast.research.subscribe).
-- Settings: AU_OUT (the samples file), AU_KEYS (the key file).
-- Samples file: one line per output frame, "dl dr el er cl cr" (the voices' direct sum, the DSP's effect return and
-- the CD's contribution, left and right, before the final clamp); a line "@ ordinal vblank" before the first frame and
-- after any gap (frames the queue dropped), so the frames keep their place on the chip's clock.
-- Key file: "K vblank ordinal channel regs" for each key on (regs: the voice's 0x80 register bytes in hex), "F ..."
-- for each key off, and "D vblank hex" whenever the DSP's coefficients, addresses or program (0x700000+0x3000..0x3c00
-- as the SH-4 reads them) or the common registers change (checked every 60 vertical blanks).
local au_out = assert(io.open(AU_OUT, "w"))
local au_keys = assert(io.open(AU_KEYS, "w"))
local au_vb, au_next, au_buf = 0, nil, {}
local au_dsp_last = nil

local function au_flush()
  if #au_buf > 0 then
    au_out:write(table.concat(au_buf))
    au_buf = {}
  end
end

local au_sample_token = flycast.research.subscribe({event = "aica-sample", queue_capacity = 65536}, function(e)
  local o = e.sample_ordinal
  if o ~= au_next then au_buf[#au_buf + 1] = string.format("@ %d %d\n", o, au_vb) end
  au_next = o + 1
  au_buf[#au_buf + 1] = string.format("%d %d %d %d %d %d\n", e.dry_left, e.dry_right, e.dsp_contribution_left,
    e.dsp_contribution_right, e.cdda_contribution_left, e.cdda_contribution_right)
  if #au_buf >= 4096 then au_flush() end
end)

for _, kind in ipairs({{"aica-key-on", "K"}, {"aica-key-off", "F"}}) do
  flycast.research.subscribe({event = kind[1], queue_capacity = 4096}, function(e)
    au_keys:write(string.format("%s %d %d %d %s\n", kind[2], au_vb, e.sample_cut_ordinal, e.channel,
      e.channel_registers_hex or "-"))
  end)
end

local function au_dsp()
  local words = {}
  local r32 = flycast.memory.read32
  for a = 0xa0703000, 0xa0703bfc, 4 do words[#words + 1] = string.format("%04x", bit32.band(r32(a), 0xffff)) end
  for a = 0xa0702800, 0xa07028bc, 4 do words[#words + 1] = string.format("%04x", bit32.band(r32(a), 0xffff)) end
  return table.concat(words)
end

local au_base_vblank, au_base_terminate = flycast_callbacks.vblank, flycast_callbacks.terminate
flycast_callbacks.vblank = function()
  au_base_vblank()
  au_vb = au_vb + 1
  if au_vb % 60 == 0 then
    local d = au_dsp()
    if d ~= au_dsp_last then
      au_keys:write(string.format("D %d %s\n", au_vb, d))
      au_dsp_last = d
    end
    au_keys:flush()
  end
end
flycast_callbacks.terminate = function()
  au_base_terminate()
  au_flush()
  local s = flycast.research.subscription_stats(au_sample_token)
  au_keys:write(string.format("S dropped %d delivered %d\n", s.dropped, s.delivered))
  au_out:close()
  au_keys:close()
end
