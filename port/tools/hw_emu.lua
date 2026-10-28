-- The hardware check's emulator half (tools/hw_check.py appends it to lockstep_emu.lua; runtime/hw_check.c is the
-- port's half). Each render's primitives as Flycast's TA decoded them, per display list, in the port's format (LS_HW):
--   "C context tick label" as the TA takes a context's first block (label: the game's frame label then; tick: the
--   emulated time); "R context tick render" for each render (tick: its first primitive's), then per list
--   "L list strips vertices sx sy sz seq..": a polygon strip's x, y and z extent ends (min + max) are summed; seq is
--   each primitive's vertex count, after "h pcw isp tsp tcw" (hex) when its header differs from the previous one's;
--   "T address size sha256" (after its R) for each texture a render samples whose bytes differ from the last line for
--   that address. The background plane (primitive kind 1) is not a list's.
--   "D list h0 all from1 from2" (after its L) for a list of polygon strips and sprites: the vertex digest
--   (tools/ta_digest.py has the fields each mode uses and their sources; runtime/ta_digest.c is the port's half): each
--   strip's masked fields hashed, h0 the first strip's, then all strips' hashes chained from strip 0, 1 and 2 (a list
--   whose first sprite the console gets twice compares from 2 against the port's from 1); then "H list h.." each
--   strip's hash (its first lane), so the compare can align the strips past a strip that differs.
--   LS_HW_DUMP (optional, render generations {r, ..}): each one's masked fields go to LS_HW .. ".dump" ("R render",
--   then "S list strip kind count pcw tsp" and "V" with each field in hex per strip), "END" after the last of them
--   (ta_digest.py detail).
-- The two event queues are delivered apart, so C lines may come after their R; the compare joins a render to its
-- context's last C before it in emulated time (the primitives' context generation is not the blocks').
local hw = assert(io.open(LS_HW, "w"))
hw:setvbuf("full", 1048576)
hw:write("X offscreen\n") -- H hashes mark a strip off the screen (ta_digest.py: older recordings have no mark)
local KIND_BACKGROUND, KIND_STRIP = 1, 2
local EOS = 0x10000000
local last_ctx, last_gen = nil, nil
local cur_render, cur_ctx, cur_tick, lists, tex_lines = nil, nil, nil, {}, {}
local tex_size, tex_sha = {}, {}
local flushed, blocked = 0, 0
local prims, stats
local P1, M1, P2, M2 = 4294967291, 65599, 4294967279, 31337
local band, KIND_SPRITE, PCW_TWO_VOLUMES = bit32.band, 3, 0x40
local dump, dump_set, dump_left = LS_HW_DUMP and assert(io.open(LS_HW .. ".dump", "w")), {}, 0
for _, r in ipairs(LS_HW_DUMP or {}) do dump_set[r], dump_left = true, dump_left + 1 end

local function mix(h, x)
  h[1] = (h[1] * M1 + x) % P1
  h[2] = (h[2] * M2 + x) % P2
end

local function argb(hex) -- Flycast's colour bytes (r g b a) as the TA's a r g b word
  local v = tonumber(hex or "0", 16) or 0
  return (v % 256) * 16777216 + math.floor(v / 256)
end

-- the fields a strip's mode uses (ta_digest.py MASKS): its texture coordinates, base and offset colour bits
local function masks(pcw, tsp)
  local tex, bm, om = band(pcw, 8) ~= 0, 0xffffffff, 0
  if band(tsp, 0x100000) == 0 then bm = 0x00ffffff end
  if tex then
    local shad = band(math.floor(tsp / 64), 3)
    if shad == 0 then bm = 0 elseif shad == 1 then bm = band(bm, 0x00ffffff) end
    if band(pcw, 4) ~= 0 then om = band(math.floor(tsp / 4194304), 3) == 1 and 0xffffffff or 0x00ffffff end
  end
  return tex, bm, om
end

local function put(out, v, tex, xyz)
  out[#out + 1] = v.x_bits; out[#out + 1] = v.y_bits
  if xyz then out[#out + 1] = v.z_bits end
  if tex and xyz then out[#out + 1] = v.u_bits; out[#out + 1] = v.v_bits end
end

local function colours(out, v, bm, om)
  out[#out + 1] = band(argb(v.base_color_hex), bm)
  out[#out + 1] = band(argb(v.offset_color_hex), om)
end

-- a primitive wholly left of or above the screen (every vertex's x, or every y, negative, not -0) draws no pixel
local function offscreen(vs)
  local nx, ny = 0, 0
  for _, v in ipairs(vs) do
    if v.x_bits > 0x80000000 then nx = nx + 1 end
    if v.y_bits > 0x80000000 then ny = ny + 1 end
  end
  return nx == #vs or ny == #vs
end

-- a strip's masked fields in the port's order: a sprite as a, b, c (Flycast's vertices 3, 4, 2) and d's x y (1: its
-- z u v are the plane's), then its colours; a polygon strip's vertices, colours from the third on when flat shaded;
-- one off the screen x y z only
local function fields(ev, pcw)
  local vs, out = ev.vertices, {}
  local tex, bm, om = masks(pcw, ev.tsp)
  local off = offscreen(vs)
  if off then tex = false end
  if ev.primitive_kind == KIND_SPRITE then
    if #vs ~= 4 then return nil end
    put(out, vs[3], tex, true); put(out, vs[4], tex, true); put(out, vs[2], tex, true); put(out, vs[1], tex, false)
    if not off then colours(out, vs[3], bm, om) end
  elseif band(pcw, PCW_TWO_VOLUMES) ~= 0 then
    for _, v in ipairs(vs) do put(out, v, false, true) end -- two volumes: x y z only (not modelled)
  else
    local smooth = band(pcw, 2) ~= 0
    for i, v in ipairs(vs) do
      put(out, v, tex, true)
      if not off and (smooth or i >= 3) then colours(out, v, bm, om) end
    end
  end
  return out, off
end

local function digest(s, ev, pcw, kind)
  local out, off
  if ev.vertices then out, off = fields(ev, pcw) end
  if not out then s.nodig = true; return end
  local h = {1, 1}
  for _, x in ipairs(out) do mix(h, x) end
  if s.strips == 0 then s.h0 = h end
  s.hs[#s.hs + 1] = string.format("%08x", h[1]) .. (off and "o" or "")
  for k = 0, math.min(s.strips, 2) do mix(s.acc[k], h[1]); mix(s.acc[k], h[2]) end
  if dump and dump_set[cur_render] then
    dump:write(string.format("S %d %d %d %d %x %x\n", ev.list_type, s.strips, kind, ev.count, pcw, ev.tsp))
    local t = {}
    for i, x in ipairs(out) do t[i] = string.format("%x", x) end
    dump:write("V ", table.concat(t, " "), "\n")
  end
end

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
      if s.h0 and not s.nodig then
        local hx = function(h) return string.format("%08x%08x", h[1], h[2]) end
        hw:write(string.format("D %d %s %s %s %s\n", l, hx(s.h0), hx(s.acc[0]), hx(s.acc[1]), hx(s.acc[2])))
        hw:write("H ", l, " ", table.concat(s.hs, " "), "\n")
      end
    end
  end
  for _, t in ipairs(tex_lines) do hw:write("T ", t, "\n") end
  if dump and dump_set[cur_render] then
    dump_left = dump_left - 1
    if dump_left == 0 then dump:write("END\n"); dump:close(); dump = nil end
  end
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
    if dump and dump_set[cur_render] then dump:write("R ", cur_render, "\n") end
  end
  local kind = ev.primitive_kind
  if kind == KIND_BACKGROUND then return end
  local s = lists[ev.list_type]
  if not s then
    s = {strips = 0, verts = 0, sx = 0, sy = 0, sz = 0, seq = {}, hs = {}, acc = {[0] = {1, 1}, {1, 1}, {1, 1}}}
    lists[ev.list_type] = s
  end
  local p, i, t, c = bit32.band(ev.pcw, bit32.bnot(EOS)), ev.isp, ev.tsp, ev.tcw
  if p ~= s.p or i ~= s.i or t ~= s.t or c ~= s.c then
    s.seq[#s.seq + 1] = string.format("h %x %x %x %x", p, i, t, c)
    s.p, s.i, s.t, s.c = p, i, t, c
  end
  s.seq[#s.seq + 1] = ev.count
  if kind == KIND_STRIP or kind == KIND_SPRITE then digest(s, ev, p, kind) end
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

local function subscribe()
  prims = flycast.research.subscribe({event = "pvr-primitive", payload = "sha256", queue_capacity = 65536}, on_prim)
end

function hw_start()
  if not LS_HW_FROM then subscribe() end
end

-- LS_HW_FROM (hw_check.py --hw-from): renders from that vertical blank on; until then the emulator runs at lockstep's
-- pace (hashing each primitive's texture is what slows it)
function hw_vblank(v)
  if not prims and LS_HW_FROM and v >= LS_HW_FROM then subscribe() end
end

function hw_close()
  flush()
  stats("end")
  hw:close()
end
