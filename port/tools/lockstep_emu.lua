-- The emulator's half of the lockstep check (tools/lockstep.py writes the settings above this file and starts
-- Flycast with it; runtime/lockstep.c is the port's half). At every vertical blank it reads the watched state of the
-- spec; when the frame label (the spec's "label" item, the game's copy of the SDK timer) changes, the previous
-- vertical blank's sample is the state the labelled frame left, and it is written as one line:
--   "label vblank word.." (hex, "-" for a word outside RAM).
-- Items flagged io are hardware registers (read as they are, not checked against RAM).
-- Settings: LS_SPEC (the spec file), LS_OUT (the frames file), LS_DRAWS (the draws file, below), LS_INPUT
-- ({vblank, buttons, length, r trigger}..; vertical blanks from the first one; buttons are Flycast's DC_BTN bits).
-- LS_VBFILE (optional): the vertical blank count, rewritten at each one (tools/reference.py reads it while Flycast is
-- paused). LS_VB0 (optional): the run starts from a save state (Dreamcast.AutoLoadState) taken after that vertical
-- blank: the count resumes there and the input held then is pressed as the state loads.
-- LS_SAMPLE_PCS (optional, game.json lockstep_sample_pcs): the guest PCs where the port's vertical blank comes (the
-- SDK's render and vsync-wait entries). A frame's state is then its last sample at one of them, as the port takes it
-- after the frame's draw, not a vertical blank's inside the draw (words rewritten while drawing would read apart); a
-- frame that reached none keeps its vertical blank's sample, and io words (the RTC that drives the port) always do.
-- FlycastRedux's flycast.research.pc_hook calls back on the emulator's thread just before the instruction, adding no
-- cycles.
local r32 = flycast.memory.read32
local items, label_item = {}, nil

local function parse_address(text)
  local emu = text:match("^([^|]+)") or text
  if emu:sub(1, 1) == "@" or emu == "-" then return nil end
  local base, rest = emu:match("^(%x+)(.*)$")
  local steps = {}
  while rest ~= "" do
    if rest:sub(1, 1) == "*" then
      steps[#steps + 1] = "*"; rest = rest:sub(2)
    else
      local sign, hex, tail = rest:match("^([%+%-])(%x+)(.*)$")
      assert(sign, "bad address step in " .. text)
      steps[#steps + 1] = (sign == "-" and -1 or 1) * tonumber(hex, 16); rest = tail
    end
  end
  return {base = tonumber(base, 16), steps = steps}
end

for line in io.lines(LS_SPEC) do
  local fields = {}
  for word in line:gmatch("%S+") do fields[#fields + 1] = word end
  if #fields >= 3 and fields[1]:sub(1, 1) ~= "#" then
    local item = {name = fields[1], words = tonumber(fields[2]), addr = parse_address(fields[3]), flags = {}}
    for i = 4, #fields do
      if fields[i] == "label" then label_item = item end
      if fields[i] == "io" then item.io = true end
      item.flags[fields[i]] = true
    end
    items[#items + 1] = item
  end
end
assert(label_item and label_item.addr, "the spec needs a label item")

local function in_ram(a)
  local p = a % 0x20000000
  return a % 4 == 0 and p >= 0x0c000000 and p < 0x0d000000
end

local function item_at(item)
  local a = item.addr.base
  for _, s in ipairs(item.addr.steps) do
    if s == "*" then
      if not in_ram(a) then return nil end
      a = r32(a)
    else
      a = (a + s) % 0x100000000
    end
  end
  return a
end

local function sample()
  local out = {}
  for _, item in ipairs(items) do
    local at = item.addr and item_at(item)
    for j = 0, item.words - 1 do
      local a = at and at + 4 * j
      if item.addr == nil then
        out[#out + 1] = "-"
      elseif a and (item.io or in_ram(a)) then
        out[#out + 1] = string.format("%08x", r32(a))
      else
        out[#out + 1] = "-"
      end
    end
  end
  return out
end

local f = assert(io.open(LS_OUT, "w"))
f:setvbuf("full", 1048576)
local vb, prev_label, prev_sample, held, held_r = 0, nil, nil, 0, 0
local written = 0

-- The draws (LS_DRAWS): the game's frame words when the TA takes the first block of a frame's display list, read on
-- the game's own thread (FlycastRedux reads them as the block is accepted; the callbacks come later, in order). Each
-- (label, frame counter) seen once: "label frames ms msrem step gametime paused" (hex; 0 for a word the spec does
-- not name). The vertical blank samples miss frames that run faster than the display; the draws do not.
local DRAW_FLAGS = {"label", "frames", "ms", "msrem", "step", "gametime", "paused"}
local draw_words, draw_at, draws = {}, {}, nil
for _, flag in ipairs(DRAW_FLAGS) do
  for _, item in ipairs(items) do
    if item.flags[flag] and item.addr and #item.addr.steps == 0 then
      draw_at[flag] = #draw_words + 1
      draw_words[#draw_words + 1] = item.addr.base
      break
    end
  end
end
local draw_key, draw_count = nil, 0
local function on_block(ev)
  local s = ev.guest_u32_snapshot
  if not s or not s[1] or not s[1].available then return end
  local out = {}
  for i, flag in ipairs(DRAW_FLAGS) do
    local w = draw_at[flag] and s[draw_at[flag]]
    out[i] = string.format("%08x", w and w.available and w.value or 0)
  end
  if hw_block then hw_block(ev, out[1]) end -- the hardware check (hw_emu.lua), when appended
  local key = out[1] .. out[2]
  if key ~= draw_key then
    draw_key = key
    draws:write(table.concat(out, " "), "\n")
    draw_count = draw_count + 1
  end
end
if LS_DRAWS and draw_at.label then
  draws = assert(io.open(LS_DRAWS, "w"))
  draws:setvbuf("full", 1048576)
end

-- the samples at LS_SAMPLE_PCS: the last one under the current label and under the label before it
local hook_label, hook_sample, hook_prev_label, hook_prev_sample = nil, nil, nil, nil
local function on_sample_pc()
  local label = r32(label_item.addr.base)
  if label ~= hook_label then hook_prev_label, hook_prev_sample = hook_label, hook_sample end
  hook_label, hook_sample = label, sample()
end
if LS_SAMPLE_PCS and #LS_SAMPLE_PCS > 0 then
  assert(flycast.research and flycast.research.pc_hook, "LS_SAMPLE_PCS needs FlycastRedux's flycast.research.pc_hook")
  flycast.research.pc_hook(LS_SAMPLE_PCS, on_sample_pc)
end
-- the words of io items: hardware registers (the RTC that drives the port) keep the vertical blank's read
local io_words, nwords = {}, 0
for _, item in ipairs(items) do
  for _ = 1, item.words do
    nwords = nwords + 1
    if item.io then io_words[#io_words + 1] = nwords end
  end
end
local function frame_sample(label)
  local s = label == hook_label and hook_sample or label == hook_prev_label and hook_prev_sample or nil
  if not s then return prev_sample end
  for _, n in ipairs(io_words) do s[n] = prev_sample[n] end
  return s
end

local started = false
local vbfile = LS_VBFILE and assert(io.open(LS_VBFILE, "w"))

local function apply_input()
  local want, want_r = 0, 0
  for _, s in ipairs(LS_INPUT) do
    if vb >= s[1] and vb < s[1] + s[3] then
      want = bit32.bor(want, s[2]); want_r = math.max(want_r, s[4] or 0)
    end
  end
  if want ~= held then
    flycast.input.releaseButtons(1, bit32.band(held, bit32.bnot(want)))
    flycast.input.pressButtons(1, want)
    held = want
  end
  if want_r ~= held_r then flycast.input.setAxis(1, 6, want_r); held_r = want_r end
end

-- the subscriptions start at the first vertical blank, or as a save state loads (before its first frame's blocks)
local function begin()
  started = true
  if draws then
    -- a 3D frame is thousands of blocks, and only each context's first is needed: a small queue keeps the first
    -- ones of each frame (delivery runs once a frame) and leaves room in the queue all subscriptions share
    ls_blocks = flycast.research.subscribe({event = "pvr-ta-block", guest_u32_addresses = draw_words,
      queue_capacity = 4096}, on_block)
  end
  if hw_start then hw_start() end
end

flycast_callbacks = {
  loadState = function()
    if LS_VB0 and not started then
      vb = LS_VB0
      apply_input()
      begin()
    end
  end,
  vblank = function()
    vb = vb + 1
    if vbfile then
      vbfile:seek("set"); vbfile:write(string.format("%10d\n", vb)); vbfile:flush()
    end
    if not started then begin() end
    if draws and vb % 600 == 0 then draws:flush() end
    apply_input()
    if hw_vblank then hw_vblank(vb) end -- the hardware check's late start (hw_emu.lua), when appended
    local label = r32(label_item.addr.base)
    if prev_label ~= nil and label ~= prev_label then
      f:write(string.format("%08x %d %s\n", prev_label, vb - 1, table.concat(frame_sample(prev_label), " ")))
      written = written + 1
      if written % 256 == 0 then f:flush() end
    end
    prev_label, prev_sample = label, sample()
  end,
  terminate = function()
    f:close()
    if vbfile then vbfile:close() end
    if draws then draws:close() end
    if hw_close then hw_close() end
  end,
}
