-- The emulator's half of the lockstep check (tools/lockstep.py writes the settings above this file and starts
-- Flycast with it; runtime/lockstep.c is the port's half). At every vertical blank it reads the watched state of the
-- spec; when the frame label (the spec's "label" item, the game's copy of the SDK timer) changes, the previous
-- vertical blank's sample is the state the labelled frame left, and it is written as one line:
--   "label vblank word.." (hex, "-" for a word outside RAM).
-- Items flagged io are hardware registers (read as they are, not checked against RAM). An item flagged
-- if=ITEM:VALUE (if=ITEM[J]:VALUE: word J; VALUE hex) is read only while that earlier item's word equals VALUE, else
-- "-" (runtime/lockstep_spec.c reads it so too).
-- Settings: LS_SPEC (the spec file), LS_OUT (the frames file), LS_DRAWS (the draws file, below), LS_INPUT
-- ({vblank, buttons, length, r trigger}..; vertical blanks from the first one; buttons are Flycast's DC_BTN bits).
-- LS_VBFILE (optional): the vertical blank count, rewritten at each one (tools/reference.py reads it while Flycast is
-- paused). LS_VB0 (optional): the run starts from a save state (Dreamcast.AutoLoadState) taken after that vertical
-- blank: the count resumes there and the input held then is pressed as the state loads.
-- LS_SAMPLE_PCS (optional, game.json lockstep_sample_pcs): the guest PCs where the port's vertical blank comes (the
-- SDK's render and vsync-wait entries). A frame's state is then its last sample at one of them, as the port takes it
-- after the frame's draw, not a vertical blank's inside the draw (words rewritten while drawing would read apart); a
-- frame that reached none keeps its vertical blank's sample, and io words (the RTC that drives the port) do too. Each
-- frame that reaches one is written, also when several end between two vertical blanks (a load window), and LS_STEPS
-- (the steps file) records its frame words there: "label frames ms msrem step gametime paused" as in the draws file,
-- read on the game's thread at the frame's own point, so every frame is known with its own label (the draws file
-- misses frames there, or shows one under the next frame's label: the TA takes a list after the game has moved on).
-- LS_SCENE_PCS (optional, game.json lockstep_scene_pcs): where a scene begins (the SDK's kmBeginScene, where the port
-- calls lockstep_scene); each step then ends with scene: 1 if one began under the frame's label, as the port counts.
-- A written frame that began a scene but reached no sample PC (a load that starts a movie) gets one unsampled step,
-- its words read at the scene and two more fields "1 1" (scene, unsampled): the port's frame can end there.
-- LS_RTC_PCS (optional, game.json lockstep_rtc_pcs): the guest PCs where the game reads the RTC (the SDK's date read).
-- A frame that reads it keeps the io words as its first read saw them: within a load frame the read can come a second
-- before the frame's last vertical blank (a level seeding rand from the clock as it loads).
-- LS_ENTRY (optional): {pcs, from, to, words, off, n, out}: at each call of a guest function at one of pcs in vertical
-- blanks from..to, one line to out: "pc label frames arg w.. | j..": words words at the call's r4 and n at the pointer
-- at r4 + off. Optional: at (a byte offset from r4 for the words, default 0) and changes (true: a call is written only
-- when its words differ from the last written call's, and the line ends "| vblank calls" instead of the n words: the
-- vertical blank and the calls since the last line; for a function polled thousands of times a frame)
-- (the port's --lockstep-entry logs the same; tools/lockstep.py entries pairs them). It needs a pc_hook that passes
-- r4 (FlycastRedux's local tc-m1-reghook branch).
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
      local gname, gword, gvalue = fields[i]:match("^if=([^:%[]+)%[?(%d*)%]?:(%x+)$")
      if gname then
        for _, g in ipairs(items) do if g.name == gname then item.gate = g end end
        item.gate_word, item.gate_value = tonumber(gword) or 0, tonumber(gvalue, 16)
        assert(item.gate and item.gate_word < item.gate.words, "if=: no earlier item " .. fields[i])
      elseif fields[i]:sub(1, 3) == "if=" then
        error("if= is not ITEM:VALUE or ITEM[J]:VALUE: " .. fields[i])
      end
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

local function gate_open(item)
  local g = item.gate
  if not g then return true end
  local at = g.addr and gate_open(g) and item_at(g)
  local a = at and at + 4 * item.gate_word
  return a ~= nil and (g.io or in_ram(a)) and r32(a) == item.gate_value
end

local function sample()
  local out = {}
  for _, item in ipairs(items) do
    local at = item.addr and gate_open(item) and item_at(item)
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

-- the words of io items: hardware registers (the RTC that drives the port) keep the vertical blank's read
local io_words, nwords = {}, 0
for _, item in ipairs(items) do
  for _ = 1, item.words do
    nwords = nwords + 1
    if item.io then io_words[#io_words + 1] = nwords end
  end
end

-- The frames seen since the last line, in order: {label, sample: its last at LS_SAMPLE_PCS, io: the io words at its
-- first LS_RTC_PCS read}. The frame at the last vertical blank comes first; the last one is the frame running now.
local pending, steps = {}, nil
if LS_STEPS and draw_at.label then
  steps = assert(io.open(LS_STEPS, "w"))
  steps:setvbuf("full", 1048576)
end
local function frame_of(label)
  local p = pending[#pending]
  if not p or p.label ~= label then
    p = {label = label}
    pending[#pending + 1] = p
  end
  return p
end
local function step_words()
  local out = {}
  for i, flag in ipairs(DRAW_FLAGS) do
    out[i] = string.format("%08x", draw_at[flag] and r32(draw_words[draw_at[flag]]) or 0)
  end
  return out
end
local function on_sample_pc()
  local p = frame_of(r32(label_item.addr.base))
  p.sample = sample()
  if steps then
    local out = step_words()
    p.steps = p.steps or {}
    if not p.steps[1] or p.steps[#p.steps][2] ~= out[2] then p.steps[#p.steps + 1] = out end
  end
end
local rtc_pc = {}
for _, a in ipairs(LS_RTC_PCS or {}) do rtc_pc[a] = true end
local function on_rtc_pc()
  local p = frame_of(r32(label_item.addr.base))
  if p.io then return end
  local s = sample()
  p.io = {}
  for _, n in ipairs(io_words) do p.io[n] = s[n] end
end
local scene_pc, scenes = {}, LS_SCENE_PCS and #LS_SCENE_PCS > 0
for _, a in ipairs(LS_SCENE_PCS or {}) do scene_pc[a] = true end
local hooks = {}
local entry_out = LS_ENTRY and assert(io.open(LS_ENTRY.out, "w"))
local frames_item
for _, item in ipairs(items) do if item.flags.frames then frames_item = item end end
local entry_last, entry_calls = nil, 0
local function on_entry(pc, r4)
  if vb < LS_ENTRY.from or vb > LS_ENTRY.to then return end
  local w = {}
  for i = 0, LS_ENTRY.words - 1 do w[#w + 1] = string.format("%08x", r32(r4 + (LS_ENTRY.at or 0) + 4 * i)) end
  if LS_ENTRY.changes then
    local key = table.concat(w, " ")
    entry_calls = entry_calls + 1
    if key == entry_last then return end
    entry_last = key
    w[#w + 1] = string.format("| %d %d", vb, entry_calls)
    entry_calls = 0
    entry_out:write(string.format("%08x %08x %d %08x %s\n", pc, r32(label_item.addr.base),
                                  frames_item and r32(frames_item.addr.base) or 0, r4, table.concat(w, " ")))
    entry_out:flush()
    return
  end
  w[#w + 1] = "|"
  local j = r32(r4 + LS_ENTRY.off)
  local in_ram = j >= 0x8c000000 and j + 4 * LS_ENTRY.n <= 0x8d000000  -- else the port writes "-" too
  for i = 0, LS_ENTRY.n - 1 do w[#w + 1] = in_ram and string.format("%08x", r32(j + 4 * i)) or "-" end
  entry_out:write(string.format("%08x %08x %d %08x %s\n", pc, r32(label_item.addr.base),
                                frames_item and r32(frames_item.addr.base) or 0, r4, table.concat(w, " ")))
  entry_out:flush()  -- Flycast is killed at the run's end: an unflushed buffer is lost
end
local entry_pc = {}
for _, a in ipairs(LS_ENTRY and LS_ENTRY.pcs or {}) do hooks[#hooks + 1] = a; entry_pc[a] = true end
for _, a in ipairs(LS_SAMPLE_PCS or {}) do hooks[#hooks + 1] = a end
for _, a in ipairs(LS_RTC_PCS or {}) do hooks[#hooks + 1] = a end
for _, a in ipairs(LS_SCENE_PCS or {}) do hooks[#hooks + 1] = a end
if #hooks > 0 then
  assert(flycast.research and flycast.research.pc_hook,
         "LS_SAMPLE_PCS, LS_RTC_PCS and LS_SCENE_PCS need FlycastRedux's flycast.research.pc_hook")
  flycast.research.pc_hook(hooks, function(pc, r4)
    if entry_pc[pc] then on_entry(pc, r4)
    elseif rtc_pc[pc] then on_rtc_pc()
    elseif scene_pc[pc] then
      local p = frame_of(r32(label_item.addr.base))
      p.scene = 1
      if steps and not p.scene_step then p.scene_step = step_words() end
    else on_sample_pc() end
  end)
end

-- A vertical blank that reads a new label ends the frames before it: each is written with its sample at a sample PC,
-- or (the frame at the last vertical blank) that vertical blank's; a frame seen at neither is not written (as before
-- the steps: build_frames shares out its time). io words: the frame's RTC read, else the last vertical blank's. Its
-- steps go with it, each with the frame's scene.
local function write_frames(label)
  if not pending[1] or pending[1].label ~= prev_label then table.insert(pending, 1, {label = prev_label}) end
  local keep = {}
  for _, p in ipairs(pending) do
    if p.label == label then
      keep[#keep + 1] = p
    elseif p.sample or p.label == prev_label then
      local s = p.sample or prev_sample
      for _, n in ipairs(io_words) do s[n] = p.io and p.io[n] or prev_sample[n] end
      f:write(string.format("%08x %d %s\n", p.label, vb - 1, table.concat(s, " ")))
      for _, out in ipairs(p.steps or {}) do
        steps:write(table.concat(out, " "), scenes and " " .. (p.scene or 0) or "", "\n")
      end
      if steps and not p.steps and p.scene_step then -- a scene with no sample PC (a load): unsampled, at the scene
        steps:write(table.concat(p.scene_step, " "), " 1 1\n")
      end
      written = written + 1
      if written % 256 == 0 then f:flush() end
    end
  end
  pending = keep
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
    if vb % 600 == 0 then
      if draws then draws:flush() end
      if steps then steps:flush() end
    end
    apply_input()
    if hw_vblank then hw_vblank(vb) end -- the hardware check's late start (hw_emu.lua), when appended
    local label = r32(label_item.addr.base)
    if prev_label == nil then
      pending = {pending[#pending] and pending[#pending].label == label and pending[#pending] or nil}
    elseif label ~= prev_label then
      write_frames(label)
    end
    prev_label, prev_sample = label, sample()
  end,
  terminate = function()
    f:close()
    if steps then steps:close() end
    if vbfile then vbfile:close() end
    if draws then draws:close() end
    if hw_close then hw_close() end
  end,
}
