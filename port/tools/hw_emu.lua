-- The hardware check's emulator half (tools/hw_check.py appends it to lockstep_emu.lua; runtime/hw_check.c is the
-- port's half). Each render's primitives as Flycast's TA decoded them, per display list, in the port's format (LS_HW):
--   "C context tick label" as the TA takes a context's first block (label: the game's frame label then; tick: the
--   emulated time); "R context tick render" for each render (tick: its first primitive's), then per list
--   "L list strips vertices sx sy sz seq..": a polygon strip's x, y and z extent ends (min + max) are summed; seq is
--   each primitive's vertex count, after "h pcw isp tsp tcw" (hex) when its header differs from the previous one's;
--   "T address size sha256" (after its R) for each texture a render samples whose bytes differ from the last line for
--   that address. The background plane (primitive kind 1) is not a list's.
-- The two event queues are delivered apart, so C lines may come after their R; the compare joins a render to its
-- context's last C before it in emulated time (the primitives' context generation is not the blocks').
local hw = assert(io.open(LS_HW, "w"))
hw:setvbuf("full", 1048576)
local KIND_BACKGROUND, KIND_STRIP = 1, 2
local EOS = 0x10000000
local last_ctx, last_gen = nil, nil
local cur_render, cur_ctx, cur_tick, lists, tex_lines = nil, nil, nil, {}, {}
local tex_size, tex_sha = {}, {}
local flushed, blocked = 0, 0
local prims, stats

-- lockstep_emu.lua's TA block callback: the context's label, from the block's guest snapshot
function hw_block(ev, label)
  if ev.context_address == last_ctx and ev.context_generation == last_gen then return end
  last_ctx, last_gen = ev.context_address, ev.context_generation
  hw:write(string.format("C %x %.0f %s\n", last_ctx, ev.tick, label))
  blocked = blocked + 1
  if blocked % 64 == 0 then stats("c" .. blocked) end
end

local function flush()
  if not cur_render then return end
  hw:write(string.format("R %x %.0f %d\n", cur_ctx, cur_tick, cur_render))
  for l = 0, 7 do
    local s = lists[l]
    if s then
      hw:write(string.format("L %d %d %d %.9g %.9g %.9g %s\n", l, s.strips, s.verts, s.sx, s.sy, s.sz,
        table.concat(s.seq, " ")))
    end
  end
  for _, t in ipairs(tex_lines) do hw:write("T ", t, "\n") end
  lists, tex_lines = {}, {}
  flushed = flushed + 1
  if flushed % 256 == 0 then -- the run ends with the emulator killed: keep what came
    stats("r" .. flushed)
    hw:flush()
  end
end

local function on_prim(ev)
  if ev.render_generation ~= cur_render then
    flush()
    cur_render, cur_ctx, cur_tick = ev.render_generation, ev.context_address, ev.tick
  end
  local kind = ev.primitive_kind
  if kind == KIND_BACKGROUND then return end
  local s = lists[ev.list_type]
  if not s then
    s = {strips = 0, verts = 0, sx = 0, sy = 0, sz = 0, seq = {}}
    lists[ev.list_type] = s
  end
  local p, i, t, c = bit32.band(ev.pcw, bit32.bnot(EOS)), ev.isp, ev.tsp, ev.tcw
  if p ~= s.p or i ~= s.i or t ~= s.t or c ~= s.c then
    s.seq[#s.seq + 1] = string.format("h %x %x %x %x", p, i, t, c)
    s.p, s.i, s.t, s.c = p, i, t, c
  end
  s.seq[#s.seq + 1] = ev.count
  s.strips, s.verts = s.strips + 1, s.verts + ev.count
  local b = ev.bounds
  if kind == KIND_STRIP and b then
    s.sx = s.sx + b.minimum_x + b.maximum_x
    s.sy = s.sy + b.minimum_y + b.maximum_y
    s.sz = s.sz + b.minimum_z + b.maximum_z
  end
  local st = ev.sampled_texture
  if st and st.source_sha256 then
    local a = st.source_address
    if tex_size[a] ~= st.source_size or tex_sha[a] ~= st.source_sha256 then
      tex_size[a], tex_sha[a] = st.source_size, st.source_sha256
      tex_lines[#tex_lines + 1] = string.format("%x %x %s", a, st.source_size, st.source_sha256)
    end
  end
end

stats = function(tag)
  for name, token in pairs({prims = prims, blocks = ls_blocks}) do
    local s = token and flycast.research.subscription_stats(token)
    if s then hw:write(string.format("S %s %s %d %d %d\n", tag, name, s.delivered, s.dropped, s.queued)) end
  end
end

function hw_start()
  prims = flycast.research.subscribe({event = "pvr-primitive", payload = "sha256", queue_capacity = 65536}, on_prim)
end

function hw_close()
  flush()
  stats("end")
  hw:close()
end
