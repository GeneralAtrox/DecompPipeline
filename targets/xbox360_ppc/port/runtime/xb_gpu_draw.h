/* xb_gpu.c, shaders, blending, sampling, the draw, resolve, present and the frame dump (one translation unit with it, for its device; here for the 400-line cap) */
/* A row's host shader objects, from the build's bytecode (gen_xbox.py compiles every converted shader once and links it in, so the game never waits
 * on the compiler: the user's run of 2026-11-12 compiled 203 during play, 8.2 s in all). A row with no bytecode (a build without the compiler) is
 * compiled here from its HLSL as before. *compiled says which */
extern int g_fcap_on;   /* port/base/frame_capture.h: xb_gpu.c takes it in after this file (xb_gpu_fcap.h); the indexed draw tests it here */
static void make_host(HostShader *h, const XbShader *s, int *compiled)
{
    *compiled = 0;
    h->addr = s->addr;
    if (s->is_ps) {
        if (s->code_len) ID3D11Device_CreatePixelShader(s_dev, xb_shader_dxbc + s->code_off, s->code_len, 0, &h->ps);
        else {
            ID3DBlob *code = compile(s->hlsl, "ps_5_0", "pixel shader");
            ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &h->ps);
            ID3D10Blob_Release(code);
            *compiled = 1;
        }
        return;
    }
    if (s->code_len) h->vs_bytes = xb_shader_dxbc + s->code_off, h->vs_len = s->code_len;
    else {
        h->vs_code = compile(s->hlsl, "vs_5_0", "vertex shader");
        h->vs_bytes = ID3D10Blob_GetBufferPointer(h->vs_code), h->vs_len = ID3D10Blob_GetBufferSize(h->vs_code);
        *compiled = 1;
    }
    ID3D11Device_CreateVertexShader(s_dev, h->vs_bytes, h->vs_len, 0, &h->vs);
    if (s->gs_rect) {
        if (s->gs_len) ID3D11Device_CreateGeometryShader(s_dev, xb_shader_dxbc + s->gs_off, s->gs_len, 0, &h->gs);
        else {
            ID3DBlob *code = compile(s->gs_rect, "gs_5_0", "rect list geometry shader");
            ID3D11Device_CreateGeometryShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &h->gs);
            ID3D10Blob_Release(code);
            *compiled = 1;
        }
    }
}

/* Before play: worker threads make every row with bytecode (the device is free-threaded), and the first draw waits for them, so no shader is made
 * during play. XB_SHADER_PRECREATE=0 leaves each to its first draw, as before (from bytecode, not compiled). The line "shaders' objects made
 * before play" gives the start-up cost; a shader made at a draw after the first shown frame logs "made in play" */
static volatile LONG s_pre_next, s_pre_left, s_pre_made;
static HANDLE s_pre_event;
static LARGE_INTEGER s_pre_t0;
static unsigned s_in_play;

static DWORD WINAPI precreate_worker(LPVOID unused)
{
    (void)unused;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    for (;;) {
        LONG i = InterlockedIncrement(&s_pre_next) - 1;
        const XbShader *s;
        int compiled;
        if ((unsigned)i >= xb_shader_count) break;
        s = &xb_shader_table[i];
        if (!s->hlsl || !s->code_len) continue;
        if (InterlockedCompareExchange(&s_shader_state[i], 1, 0) != 0) continue;
        make_host(&s_shader[i], s, &compiled);
        InterlockedExchange(&s_shader_state[i], 2);
        InterlockedIncrement(&s_pre_made);
    }
    if (InterlockedDecrement(&s_pre_left) == 0) {
        LARGE_INTEGER t1, hz;
        QueryPerformanceCounter(&t1), QueryPerformanceFrequency(&hz);
        port_log("d3d host: %ld shaders' objects made before play from build-time bytecode in %.0f ms (%u rows)\n", (long)s_pre_made,
                 (double)(t1.QuadPart - s_pre_t0.QuadPart) * 1000.0 / (double)hz.QuadPart, xb_shader_count);
        SetEvent(s_pre_event);
    }
    return 0;
}

static void shaders_precreate_start(void)
{
    const char *e = getenv("XB_SHADER_PRECREATE");
    unsigned i, n, with_code = 0;
    SYSTEM_INFO si;
    if (s_shader) return;
    s_shader = (HostShader *)calloc(xb_shader_count ? xb_shader_count : 1, sizeof *s_shader);
    s_shader_state = (volatile LONG *)calloc(xb_shader_count ? xb_shader_count : 1, sizeof *s_shader_state);
    if (!s_shader || !s_shader_state) xb_stop("d3d_host", "no memory for the shader table's host objects", xb_shader_count);
    for (i = 0; i < xb_shader_count; i++) with_code += xb_shader_table[i].hlsl && xb_shader_table[i].code_len;
    if ((e && e[0] == '0') || !with_code) {
        port_log("d3d host: shaders made at their first draw (%s; %u of %u rows have build-time bytecode)\n", with_code ? "XB_SHADER_PRECREATE=0" : "no bytecode in this build",
                 with_code, xb_shader_count);
        return;
    }
    GetSystemInfo(&si);
    n = si.dwNumberOfProcessors > 2 ? si.dwNumberOfProcessors / 2 : 1;
    if (n > 8) n = 8;
    s_pre_left = (LONG)n;
    s_pre_event = CreateEventA(0, TRUE, FALSE, 0);
    QueryPerformanceCounter(&s_pre_t0);
    port_log("d3d host: making %u shaders' objects from build-time bytecode on %u threads before play\n", with_code, n);
    for (i = 0; i < n; i++) {
        HANDLE t = CreateThread(0, 0, precreate_worker, 0, 0, 0);
        if (t) CloseHandle(t);
        else if (InterlockedDecrement(&s_pre_left) == 0) SetEvent(s_pre_event);
    }
}

static HostShader *shader_for(const XbShader *s)
{
    size_t row = (size_t)(s - xb_shader_table);
    LARGE_INTEGER t0, t1, hz;
    int compiled;
    double ms;
    static double ms_all;   /* the time shaders made at a draw have taken: each one holds the frame it is first drawn in */
    if (row >= xb_shader_count) xb_stop("d3d_host", "a shader not in the converted table", s->addr);
    if (s_pre_event) {   /* the first draw waits for the workers: nothing is made during play */
        static int waited;
        if (!waited) {
            DWORD r;
            QueryPerformanceCounter(&t0);
            r = WaitForSingleObject(s_pre_event, 120000);
            QueryPerformanceCounter(&t1), QueryPerformanceFrequency(&hz);
            waited = 1;
            port_log("d3d host: the first draw (frame %u) waited %.0f ms for the shaders made before play%s\n", s_frames,
                     (double)(t1.QuadPart - t0.QuadPart) * 1000.0 / (double)hz.QuadPart, r == WAIT_OBJECT_0 ? "" : " and went on after 120 s without them");
        }
    }
    if (s_shader_state[row] == 2) return &s_shader[row];
    if (InterlockedCompareExchange(&s_shader_state[row], 1, 0) != 0) {   /* a worker is making it now */
        while (s_shader_state[row] != 2) Sleep(0);
        return &s_shader[row];
    }
    port_log("d3d host: making shader %08x (%u bytes of HLSL, %u of bytecode)\n", s->addr, (unsigned)strlen(s->hlsl), s->code_len);   /* before, so one that never ends is named */
    QueryPerformanceCounter(&t0);
    make_host(&s_shader[row], s, &compiled);
    InterlockedExchange(&s_shader_state[row], 2);
    QueryPerformanceCounter(&t1), QueryPerformanceFrequency(&hz);
    ms = (double)(t1.QuadPart - t0.QuadPart) * 1000.0 / (double)hz.QuadPart;
    ms_all += ms;
    s_shader_n++;
    if (s_frames) s_in_play++;
    port_log("d3d host: shader %08x %s in %.0f ms at frame %u%s (%.0f ms in %u shaders made at a draw, %u in play)\n", s->addr,
             compiled ? "converted from the offline HLSL and compiled" : "made from build-time bytecode", ms, s_frames, s_frames ? ", made in play" : "", ms_all, s_shader_n, s_in_play);
    return &s_shader[row];
}

const XbShader *xb_shader_find(u32 addr)   /* the table is by address (tools/shader_convert.py c_table) */
{
    unsigned lo = 0, hi = xb_shader_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (xb_shader_table[mid].addr == addr) {
            /* XB_SHADER_NO_PRED=1, for side-by-side runs: a shader that uses the predicate (tools/shader_convert.py, 2026-11-07) is
             * treated as it was before then, a row with no text, so its draws are skipped again */
            static int no_pred = -1;
            static XbShader blank[4];
            static unsigned next;
            if (no_pred < 0) no_pred = getenv("XB_SHADER_NO_PRED") != 0;
            if (xb_shader_table[mid].hlsl && ((no_pred && strstr(xb_shader_table[mid].hlsl, "bool p0")) || (!xb_cube_on() && strstr(xb_shader_table[mid].hlsl, "xb_cube")))) {   /* XB_CUBE=0 likewise for cube maps */
                XbShader *b = &blank[next++ & 3];
                *b = xb_shader_table[mid], b->hlsl = 0, b->why = strstr(xb_shader_table[mid].hlsl, "xb_cube") && !xb_cube_on() ? "a cube map (XB_CUBE=0)" : "predicates (XB_SHADER_NO_PRED)";
                return b;
            }
            return &xb_shader_table[mid];
        }
        if (xb_shader_table[mid].addr < addr) lo = mid + 1;
        else hi = mid;
    }
    return 0;
}

/* XB_CONST_FAST (on by default; =0 every draw copies and uploads all 512 registers, as before): registers are copied only when they differ,
 * and a constant buffer is mapped and filled only when one of its registers changed since its last upload (s_const_dirty, cleared before the
 * copy so a register set during it is sent at the next draw). XB_CONST_CHECK=1 keeps the last upload and counts skipped uploads that would
 * have sent other bytes (logged once at the first) */
static volatile LONG s_const_dirty[2] = {1, 1};
static float s_const_sent[2][256 * 4];   /* XB_CONST_CHECK: the last upload */
static LONG64 s_const_skips, s_const_skips_bad;

static int gpu_const_fast(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CONST_FAST"); on = !(e && e[0] == '0'); }
    return on;
}

static int gpu_const_check(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CONST_CHECK"); on = e && e[0] == '1'; }
    return on;
}

void xb_gpu_set_constants(int pixel, unsigned first, unsigned count, const float *v)
{
    float *d;
    if (first + count > 256) count = first < 256 ? 256 - first : 0;
    d = &s_const[pixel ? 1 : 0][first * 4];
    if (gpu_const_fast() && !memcmp(d, v, count * 16)) return;
    memcpy(d, v, count * 16);
    s_const_dirty[pixel ? 1 : 0] = 1;
}

const float *xb_gpu_constants(int pixel) { return s_const[pixel ? 1 : 0]; }

/* Draw nverts vertices of nattr float4 attributes each (the vertex shader's inputs, in its fetch order). prim is the platform's
 * primitive type: 4 triangle list, 6 triangle strip, 8 rectangle list (three vertices, the fourth derived by the geometry shader). */
/* The game's blend states as the host's, made once for each combination. The numbers are the rows of 824b0900 and what the game passes: render states 60 enable, 64 separate alpha,
 * 72/76/80 source, destination, operation, 84/88/92 the same for alpha; blend 0 zero, 1 one, 4 source colour, 5 its inverse, 6 source alpha, 7 its inverse, 8 destination
 * colour, 9 its inverse, 10 destination alpha, 11 its inverse, 12 the constant colour (render state 68, the blend factor), 13 its inverse, 14 the
 * constant's alpha, 15 its inverse. Direct3D 11 has one factor colour for both: blend_factor gives it (a, a, a, a) when the colour factors take
 * the constant's alpha. Too Human: 14, first on A at Start Campaign (build box A mn12a, vertical blank 1306) */
static D3D11_BLEND host_blend(u32 v)
{
    switch (v) {
    case 12: case 14: return D3D11_BLEND_BLEND_FACTOR;
    case 13: case 15: return D3D11_BLEND_INV_BLEND_FACTOR;
    case 0: return D3D11_BLEND_ZERO;
    case 1: return D3D11_BLEND_ONE;
    case 4: return D3D11_BLEND_SRC_COLOR;
    case 5: return D3D11_BLEND_INV_SRC_COLOR;
    case 6: return D3D11_BLEND_SRC_ALPHA;
    case 7: return D3D11_BLEND_INV_SRC_ALPHA;
    case 8: return D3D11_BLEND_DEST_COLOR;
    case 9: return D3D11_BLEND_INV_DEST_COLOR;
    case 10: return D3D11_BLEND_DEST_ALPHA;
    case 11: return D3D11_BLEND_INV_DEST_ALPHA;
    }
    xb_stop("d3d_state", "a blend factor the host has no case for", v);
    return D3D11_BLEND_ONE;
}

/* XB_DEPTH=1: the blend state of a draw with no pixel shader, every colour write masked. Direct3D 11 leaves a target's colour undefined
 * under a draw with no pixel shader. With these drawn unmasked, 3 of 31 depth-on title frames on build box A had a white rectangle at the bottom
 * left; masked (and skipped when z is off), none of 31 (2026-11-07) */
static ID3D11BlendState *no_colour_blend(void)
{
    static ID3D11BlendState *b;
    if (!b) {
        D3D11_BLEND_DESC d;
        memset(&d, 0, sizeof d);
        d.RenderTarget[0].SrcBlend = d.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE;
        d.RenderTarget[0].DestBlend = d.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ZERO;
        d.RenderTarget[0].BlendOp = d.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
        d.RenderTarget[0].RenderTargetWriteMask = 0;
        ID3D11Device_CreateBlendState(s_dev, &d, &b);
    }
    return b;
}

/* XB_DEPTH=1: the game's depth states as a host depth-stencil state, one per key. Render state 40 is z enable, 44 the comparison in the hardware's
 * order (never, less, equal, less-equal, greater, not equal, greater-equal, always; the Direct3D 11 order plus one) and 48 z write: the library's
 * rows give them the defaults 1, 3 (less-equal) and 1, and their setters are the three before cull mode (52, 56) and blend enable (60). Off:
 * depth stays off for every draw, as before */
/* The draw's depth-control word, the device's (XB_DEV_DEPTHCONTROL) at its entry or as its command buffer recorded it: the game's inline code writes
 * z enable, write and comparison there with no state call (its depth-only pre-pass turns z and write on for draws whose render states say z off;
 * read from the states alone, the 311 pre-pass draws of the menu's frame were left and its colour draws had no depth test, 2026-11-13).
 * On by default again since 2026-11-14; XB_DEPTH_WORD=0 turns it off and reads render states 40, 44 and 48 instead. It was off from 2026-11-13
 * (go-live of x360-th-menu-gfx), when with the word (and the words below) on the title's hall drew as corrupted, skewed planes at title frame 600
 * (.work\menu-gfx sheets golive_title600b.png, golive_menu.png). With the upside-down depth range's z = w - z (XB_ZFLIP_VS) and the fog pass no
 * longer given a white colour copy as its depth (XB_ZVIEW_COLOUR), the words-on title and menu draw right on build box A (2026-11-14,
 * .work_pic\zv\sheet_zv.png: title 250/320/600 and menu 481 drawn, the tree, hall and Norn, no green squares, and 6 of 6 title runs at vblank 280
 * with no pale-blue frame). The word is kept and recorded in frame captures (xb_gpu_depth_word, s_dc) whatever the switch says */
static __declspec(thread) u32 s_dc;   /* each thread's: set and read by the thread that draws (xb_d3d.c draw_entry_state) */
static __declspec(thread) int s_dc_known;
void xb_gpu_depth_word(u32 word) { s_dc = word, s_dc_known = 1; }

/* The draw's colour mask, colour control and mode control words, taken the same way (xb_game.h XB_DEV_COLORMASK): the game clears target 0's
 * colour mask for its depth-only pass, turns alpha test on per material and sets the cull mode per material inline. They stand in for render
 * states 212..224 (the masks, now applied without XB_EDRAM too), 96 and 104 (alpha test) and, with XB_CULL_WORD=1, 56 (cull). On by default again since 2026-11-14, on the
 * same evidence as the depth word above (off 2026-11-13 to 2026-11-14 for the title corruption): XB_DEV_WORDS=0 turns them off and reads the states. The words are still kept and recorded in frame captures (colour_mask_word, colour_control_word, mode_control_word). The menu's
 * 57 gold-ring draws (fc0dd8c1/f9c3df8d) have mask ffff, alpha test off and no culling in the words too: the words don't make its cyan hexagons */
static __declspec(thread) u32 s_dw[3];
static __declspec(thread) int s_dw_known;
void xb_gpu_dev_words(u32 colour_mask, u32 colour_control, u32 mode_control) { s_dw[0] = colour_mask, s_dw[1] = colour_control, s_dw[2] = mode_control, s_dw_known = 1; }

static int dev_words_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_DEV_WORDS"); on = !(e && e[0] == '0'); }   /* on unless "0" (2026-11-14) */
    return on && s_dw_known;
}

static int depth_word_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_DEPTH_WORD"); on = !(e && e[0] == '0'); }   /* on unless "0" (2026-11-14) */
    return on && s_dc_known;
}

/* The depth the console stores for a viewport whose range is upside down (Too Human's 1..0): 1 - z/w. The host refuses that viewport and gets 0..1
 * (xb_gpu.c bind_targets), where it stores z/w: near 0, far 1, the console's order reversed, while the word's comparison (greater-equal), the clear
 * (0, the console's far) and an exported depth are the console's. So with the word on the depth buffer kept the farthest surface: the menu's pre-pass
 * (records 32-342, 2026-11-13 ring2) left the farthest layer, every nearer colour draw failed greater-equal against it (the stone dish 447 changed
 * no pixel of the top corners), and only what was farthest showed: the title's flat sky-lit quads (fdaa05a7/f40146ca, white) as squares.
 * On: the converted vertex shader writes z = w - z for such a viewport (xb_vte.w, shader_convert.py), so the host stores the console's value and the
 * comparison, clear and exported depth stay as the console has them. Only with the depth word on: without it the comparison is mirrored instead
 * (depth_for_draw). XB_ZFLIP_VS=0 turns it off */
static int zflip_vs_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_ZFLIP_VS"); on = !(e && e[0] == '0'); }
    return on && depth_word_on() && s_vp.zmin > s_vp.zmax;
}

static ID3D11DepthStencilState *depth_for_draw(void)
{
    /* The depth test (render states 40 ZENABLE, 44 ZFUNC, 48 ZWRITEENABLE) and the stencil test (108 STENCILENABLE, 112 TWOSIDEDSTENCILMODE,
     * 116..128 the front faces' fail, z-fail and pass operations and comparison, 136 STENCILMASK, 140 STENCILWRITEMASK, 144..156 the same four for
     * the back faces; 132 the reference, set with the state at the draw). The hardware's operations (keep 0, zero 1, replace 2, increment and clamp
     * 3, decrement and clamp 4, invert 5, increment 6, decrement 7) and comparisons (never 0 .. always 7) are the host's, one lower. Before
     * 2026-11-13 the kit had no stencil: the menu's 311 draws a frame with no pixel shader and z off (stencil writes) were left as changing nothing.
     * The front-face states go to the host's front faces, which raster_for_draw sets from the same cull mode the hardware reads */
    static struct { unsigned long long key; ID3D11DepthStencilState *st; } cache[256];
    static unsigned used;
    static const char *const name[8] = {"never", "less", "equal", "less-equal", "greater", "not equal", "greater-equal", "always"};
    static const unsigned char mirror[8] = {0, 4, 2, 6, 1, 5, 3, 7};
    static int flip = -1;
    unsigned long long key = 0;
    unsigned func = 0, z, st, i, front = 0, back = 0;
    if (!xb_depth_on()) return s_depth_off;
    z = depth_word_on() ? (s_dc >> 1 & 1u) : xb_rs(40) != 0;
    st = xb_rs(108) != 0;
    if (!z && !st) return s_depth_off;
    if (z) {
        if (!depth_word_on() && xb_rs(44) > 7) xb_stop("d3d_state", "a z comparison past the hardware's eight", xb_rs(44));
        /* A viewport with its depth range upside down (Too Human sets 1..0 and clears depth to 0): the comparison mirrored (less-equal tests as
         * greater-equal). Unmirrored, every depth-tested draw of the title (z on, write off, less-equal; nothing in the frame writes depth) failed
         * against the clear's 0 and frames went dark where they drew; mirrored, the same moments match XB_DEPTH=0 (build box A, 2026-11-07). How the
         * console passes them is not traced: the library's setter (8274c938) puts the value in its depth-control word unchanged. XB_DEPTH_FLIP=0
         * keeps the comparison as the state says, for a check */
        if (flip < 0) { const char *e = getenv("XB_DEPTH_FLIP"); flip = !(e && e[0] == '0'); }
        func = depth_word_on() ? (s_dc >> 4 & 7u) : xb_rs(44);
        /* The word holds the comparison the console tests with: the game writes greater-equal (6) there for its 1..0 range itself, so it is not
         * mirrored (mirroring render state 44 stood in for the word: the title's draws said less-equal there and greater-equal in the word) */
        if (flip && s_vp.zmin > s_vp.zmax && !depth_word_on()) {
            static int said;
            if (!said++) port_log("d3d viewport depth range %g..%g, upside down: z comparisons mirrored (%u tests as %u)\n", (double)s_vp.zmin, (double)s_vp.zmax, func, mirror[func]);
            func = mirror[func];
        }
        key = 1ull | (unsigned long long)func << 1 | ((depth_word_on() ? (s_dc & 4u) : xb_rs(48)) ? 16ull : 0ull);
    }
    if (st) {
        if ((xb_rs(116) | xb_rs(120) | xb_rs(124) | xb_rs(128) | xb_rs(144) | xb_rs(148) | xb_rs(152) | xb_rs(156)) > 7)
            xb_stop("d3d_state", "a stencil operation or comparison past the hardware's eight", xb_rs(116));
        front = xb_rs(116) | xb_rs(120) << 3 | xb_rs(124) << 6 | xb_rs(128) << 9;
        back = xb_rs(112) ? (xb_rs(144) | xb_rs(148) << 3 | xb_rs(152) << 6 | xb_rs(156) << 9) : front;
        key |= 32ull | (unsigned long long)(xb_rs(136) & 255u) << 6 | (unsigned long long)(xb_rs(140) & 255u) << 14
             | (unsigned long long)front << 22 | (unsigned long long)back << 34;
    }
    for (i = 0; i < used; i++)
        if (cache[i].key == key) return cache[i].st;
    {
        D3D11_DEPTH_STENCIL_DESC ds;
        ID3D11DepthStencilState *made = 0;
        memset(&ds, 0, sizeof ds);
        ds.DepthEnable = z ? TRUE : FALSE;
        ds.DepthWriteMask = (key & 16ull) ? D3D11_DEPTH_WRITE_MASK_ALL : D3D11_DEPTH_WRITE_MASK_ZERO;
        ds.DepthFunc = z ? (D3D11_COMPARISON_FUNC)(func + 1) : D3D11_COMPARISON_ALWAYS;
        if (st) {
            ds.StencilEnable = TRUE;
            ds.StencilReadMask = (UINT8)(key >> 6);
            ds.StencilWriteMask = (UINT8)(key >> 14);
            ds.FrontFace.StencilFailOp = (D3D11_STENCIL_OP)((front & 7u) + 1);
            ds.FrontFace.StencilDepthFailOp = (D3D11_STENCIL_OP)((front >> 3 & 7u) + 1);
            ds.FrontFace.StencilPassOp = (D3D11_STENCIL_OP)((front >> 6 & 7u) + 1);
            ds.FrontFace.StencilFunc = (D3D11_COMPARISON_FUNC)((front >> 9 & 7u) + 1);
            ds.BackFace.StencilFailOp = (D3D11_STENCIL_OP)((back & 7u) + 1);
            ds.BackFace.StencilDepthFailOp = (D3D11_STENCIL_OP)((back >> 3 & 7u) + 1);
            ds.BackFace.StencilPassOp = (D3D11_STENCIL_OP)((back >> 6 & 7u) + 1);
            ds.BackFace.StencilFunc = (D3D11_COMPARISON_FUNC)((back >> 9 & 7u) + 1);
        }
        ID3D11Device_CreateDepthStencilState(s_dev, &ds, &made);
        if (used < sizeof cache / sizeof cache[0]) cache[used].key = key, cache[used].st = made, used++;
        if (st)
            port_log("d3d depth state: test %s, write %s; stencil front fail %u z-fail %u pass %u %s, back fail %u z-fail %u pass %u %s, masks %02x/%02x\n",
                     z ? name[func] : "off", (key & 16ull) ? "on" : "off", front & 7u, front >> 3 & 7u, front >> 6 & 7u, name[front >> 9 & 7u],
                     back & 7u, back >> 3 & 7u, back >> 6 & 7u, name[back >> 9 & 7u], (unsigned)(key >> 6 & 255u), (unsigned)(key >> 14 & 255u));
        else
            port_log("d3d depth state: test %s, write %s\n", name[func], (key & 16ull) ? "on" : "off");
        return made;
    }
}

/* XB_DEPTH=1: the game's cull mode, render state 56, as the rasterizer's. Its bits are the hardware's: 1 cull front faces, 2 cull back faces, 4 front
 * faces wound clockwise (the row's default 6: back faces culled, front clockwise, Direct3D 9's counter-clockwise cull). The title draws with 0
 * (no culling) in the frames logged (2026-11-07), so it changes nothing there yet. Depth off (XB_DEPTH=0): no culling, as before */
static ID3D11RasterizerState *raster_for_draw(void)
{
    static ID3D11RasterizerState *cache[8];
    unsigned v;
    if (!xb_depth_on()) return s_raster;
    /* XB_CULL_WORD=1: the cull mode from the mode control word (8299a5f8 writes 2 or 6 there per material). Off by default: on (build box A build i,
     * 2026-11-13) the menu's frame 481 drew the rune ring and lost the centre's shape, the title's frame 281 lost its hall, with either winding
     * (XB_CULL_FLIP); neither matches Xenia, so it waits for the host's winding to be traced */
    static int cw = -1;
    if (cw < 0) { const char *e = getenv("XB_CULL_WORD"); cw = e && e[0] == '1'; }
    v = (cw && dev_words_on() ? s_dw[2] : xb_rs(56)) & 7u;
    if ((v & 3u) == 3u) {   /* both faces culled: nothing would draw; not seen yet, drawn with no culling and logged */
        static int said;
        if (!said++) port_log("d3d cull mode %x culls both faces: drawn with no culling\n", xb_rs(56));
        return s_raster;
    }
    if (!cache[v]) {
        D3D11_RASTERIZER_DESC rs;
        memset(&rs, 0, sizeof rs);
        rs.FillMode = D3D11_FILL_SOLID;
        rs.CullMode = (v & 1u) ? D3D11_CULL_FRONT : (v & 2u) ? D3D11_CULL_BACK : D3D11_CULL_NONE;
        static int flip = -1;   /* XB_CULL_FLIP=1: the other winding taken as front, for a check of which the host's matches */
        if (flip < 0) { const char *e = getenv("XB_CULL_FLIP"); flip = e && e[0] == '1'; }
        rs.FrontCounterClockwise = ((v & 4u) ? FALSE : TRUE) ^ flip;
        rs.DepthClipEnable = TRUE;
        ID3D11Device_CreateRasterizerState(s_dev, &rs, &cache[v]);
        port_log("d3d cull mode %x: %s faces culled, front faces %s\n", v, (v & 1u) ? "front" : (v & 2u) ? "back" : "no", (v & 4u) ? "clockwise" : "counter-clockwise");
    }
    return cache[v];
}

/* XB_EDRAM=1: the colour write masks too, render states 212, 216, 220 and 224 (D3DRS_COLORWRITEENABLE..3: red 1, green 2, blue 4, alpha 8, the
 * host's order), each target its own. Off, every channel of every target is written, as before */
static ID3D11BlendState *blend_for_draw(void)
{
    static struct { u32 key[7]; ID3D11BlendState *st; } cache[96];
    static unsigned n;
    u32 key[7] = {0, 0, 0, 0, 0, 0, 0}, m = 0xffff, on = xb_rs(60);
    unsigned i;
    D3D11_BLEND_DESC bl;
    if (dev_words_on()) m = s_dw[0] & 0xffffu;
    else if (xb_edram_on()) m = (xb_rs(212) & 15) | (xb_rs(216) & 15) << 4 | (xb_rs(220) & 15) << 8 | (xb_rs(224) & 15) << 12;
    if (!on && m == 0xffff) return s_blend;
    if (on) {
        key[0] = xb_rs(72); key[1] = xb_rs(76); key[2] = xb_rs(80);
        key[3] = xb_rs(64) ? xb_rs(84) : key[0];
        key[4] = xb_rs(64) ? xb_rs(88) : key[1];
        key[5] = xb_rs(64) ? xb_rs(92) : key[2];
    }
    key[6] = m | on << 16;
    for (i = 0; i < n; i++)
        if (!memcmp(cache[i].key, key, sizeof key)) return cache[i].st;
    if (n == 96) xb_stop("d3d_state", "more than 96 blend states", n);
    if (!on) {   /* no blending, some channels masked */
        memset(&bl, 0, sizeof bl);
        bl.IndependentBlendEnable = TRUE;
        for (i = 0; i < 4; i++) {
            bl.RenderTarget[i].SrcBlend = bl.RenderTarget[i].SrcBlendAlpha = D3D11_BLEND_ONE;
            bl.RenderTarget[i].DestBlend = bl.RenderTarget[i].DestBlendAlpha = D3D11_BLEND_ZERO;
            bl.RenderTarget[i].BlendOp = bl.RenderTarget[i].BlendOpAlpha = D3D11_BLEND_OP_ADD;
            bl.RenderTarget[i].RenderTargetWriteMask = (UINT8)((m >> (4 * i)) & 15);
        }
        memcpy(cache[n].key, key, sizeof key);
        ID3D11Device_CreateBlendState(s_dev, &bl, &cache[n].st);
        return cache[n++].st;
    }
    if (key[2] || key[5]) xb_stop("d3d_state", "a blend operation the host has no case for", key[2]);
    if ((key[0] == 12 || key[0] == 13 || key[1] == 12 || key[1] == 13) && (key[0] == 14 || key[0] == 15 || key[1] == 14 || key[1] == 15))
        xb_stop("d3d_state", "a blend state with both the constant colour and the constant alpha", key[0] << 8 | key[1]);
    memset(&bl, 0, sizeof bl);
    bl.RenderTarget[0].BlendEnable = TRUE;
    bl.RenderTarget[0].SrcBlend = host_blend(key[0]);
    bl.RenderTarget[0].DestBlend = host_blend(key[1]);
    bl.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].SrcBlendAlpha = host_blend(key[3] == 4 || key[3] == 5 ? key[3] + 2 : key[3]);
    bl.RenderTarget[0].DestBlendAlpha = host_blend(key[4] == 4 || key[4] == 5 ? key[4] + 2 : key[4]);
    bl.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].RenderTargetWriteMask = (UINT8)(m & 15);
    if (m != 0xffff) {   /* XB_EDRAM: each target its own mask */
        bl.IndependentBlendEnable = TRUE;
        for (i = 1; i < 4; i++) bl.RenderTarget[i] = bl.RenderTarget[0], bl.RenderTarget[i].RenderTargetWriteMask = (UINT8)((m >> (4 * i)) & 15);
    }
    memcpy(cache[n].key, key, sizeof key);
    ID3D11Device_CreateBlendState(s_dev, &bl, &cache[n].st);
    return cache[n++].st;
}

/* The factor colour for blend factors 12-15: render state 68 (D3DCOLOR, ARGB), as it is for the constant colour, (a, a, a, a) when the colour
 * factors take the constant's alpha. The alpha channel reads the fourth either way */
static void blend_factor(float bf[4])
{
    u32 c = xb_rs(68), k0 = xb_rs(72), k1 = xb_rs(76);
    float a = (float)(c >> 24) / 255.0f;
    int alpha = k0 == 14 || k0 == 15 || k1 == 14 || k1 == 15;
    bf[0] = alpha ? a : (float)(c >> 16 & 255u) / 255.0f;
    bf[1] = alpha ? a : (float)(c >> 8 & 255u) / 255.0f;
    bf[2] = alpha ? a : (float)(c & 255u) / 255.0f;
    bf[3] = a;
}

/* XB_FETCH_CLAMP (on by default; XB_FETCH_CLAMP=0 is the old path below, kept as the answer key): a texture bound from the fetch constant takes its
 * address modes from that constant, as the GPU does: word 0 clamp x/y/z (bits 10..18) and word 5 border colour (bits 0..1). Too Human writes its fetch
 * constants straight into the device, so its sampler states stay at wrap while the constant says clamp: the intro's post pass fea9caac then wraps its
 * 256x1 lookup at u 0.9997 into texel 0 and draws black. Xenos clamp 0 wrap, 1 mirror, 2 clamp to the last texel, 3 mirror once, 4 clamp half way (to
 * clamp), 5 mirror once half way (to mirror once), 6 border, 7 mirror once to the border (to border: D3D11 has no mirror once with a border). Border colour
 * 0 transparent black, 1 white, 2 and 3 (the two YCbCr blacks) to transparent black. The filter still comes from the sampler states */
/* XB_SHADER_KILL (on by default since 2026-11-15; =0 the old picture, every pixel drawn, as an answer key): the converted pixel shaders' kill
 * instructions (shader_convert.py KILL_VEC, KILL_SCA) discard when the kit constant xb_gam.z is 1. Too Human's leaves (f9c3df8d) are cut out by one */
static int shader_kill_on(void)
{
    static int on = -1;
    if (on < 0) {
        const char *e = getenv("XB_SHADER_KILL");
        on = !(e && e[0] == '0' && !e[1]);
        port_log("d3d pixel shader kills %s (XB_SHADER_KILL=0 draws every pixel)\n", on ? "on" : "off");
    }
    return on;
}

static int fetch_clamp_on(void)
{
    static int on = -1;
    if (on < 0) {
        const char *e = getenv("XB_FETCH_CLAMP");
        on = !(e && e[0] == '0' && !e[1]);
        port_log("d3d sampler address modes from the fetch constant %s (XB_FETCH_CLAMP=0 takes them from the sampler states)\n", on ? "on" : "off");
    }
    return on;
}

/* The sampler a slot's states ask for: sampler states 0 and 4 address (0 wrap, 1 mirror, 2 clamp), 16 and 20 mag and min filter (0 point, 1 linear);
 * with XB_FETCH_CLAMP on, a slot bound from the fetch constant takes its address modes from the constant instead (above) */
static ID3D11SamplerState *sampler_for_draw(unsigned slot)
{
    static ID3D11SamplerState *cache[3][3][2];
    u32 u = xb_ss(slot, 0), v = xb_ss(slot, 4), lin = xb_ss(slot, 16) || xb_ss(slot, 20);
    static const D3D11_TEXTURE_ADDRESS_MODE am[3] = {D3D11_TEXTURE_ADDRESS_WRAP, D3D11_TEXTURE_ADDRESS_MIRROR, D3D11_TEXTURE_ADDRESS_CLAMP};
    if (slot < 16 && xb_slot_fetch_w05[slot][2] && fetch_clamp_on()) {
        static ID3D11SamplerState *fc[5][5][5][4][2];
        static const D3D11_TEXTURE_ADDRESS_MODE mode[5] = {D3D11_TEXTURE_ADDRESS_WRAP, D3D11_TEXTURE_ADDRESS_MIRROR, D3D11_TEXTURE_ADDRESS_CLAMP,
                                                           D3D11_TEXTURE_ADDRESS_MIRROR_ONCE, D3D11_TEXTURE_ADDRESS_BORDER};
        static const unsigned xenos[8] = {0, 1, 2, 3, 2, 3, 4, 4};   /* Xenos clamp value to the index in mode[] */
        u32 w0 = xb_slot_fetch_w05[slot][0];
        unsigned a = xenos[(w0 >> 10) & 7u], b = xenos[(w0 >> 13) & 7u], c = xenos[(w0 >> 16) & 7u];
        unsigned bc = a == 4 || b == 4 || c == 4 ? xb_slot_fetch_w05[slot][1] & 3u : 0;   /* the border colour keys the cache only when a border is sampled */
        if (!fc[a][b][c][bc][lin]) {
            D3D11_SAMPLER_DESC d;
            float one = bc == 1 ? 1.0f : 0.0f;
            memset(&d, 0, sizeof d);
            d.Filter = lin ? D3D11_FILTER_MIN_MAG_MIP_LINEAR : D3D11_FILTER_MIN_MAG_MIP_POINT;
            d.AddressU = mode[a];
            d.AddressV = mode[b];
            d.AddressW = mode[c];
            d.BorderColor[0] = d.BorderColor[1] = d.BorderColor[2] = d.BorderColor[3] = one;
            d.MaxLOD = D3D11_FLOAT32_MAX;
            ID3D11Device_CreateSamplerState(s_dev, &d, &fc[a][b][c][bc][lin]);
        }
        return fc[a][b][c][bc][lin];
    }
    if (u > 2 || v > 2) xb_stop("d3d_state", "a texture address mode the host has no case for", u << 8 | v);
    if (!cache[u][v][lin]) {
        D3D11_SAMPLER_DESC d;
        memset(&d, 0, sizeof d);
        d.Filter = lin ? D3D11_FILTER_MIN_MAG_MIP_LINEAR : D3D11_FILTER_MIN_MAG_MIP_POINT;
        d.AddressU = am[u];
        d.AddressV = am[v];
        d.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        d.MaxLOD = D3D11_FLOAT32_MAX;
        ID3D11Device_CreateSamplerState(s_dev, &d, &cache[u][v][lin]);
    }
    return cache[u][v][lin];
}

/* XB_DIAG_REGION=frame:x0,y0,x1,y1 (bring-up): in that frame, each draw that changes the part of render target 0 between those thousandths of its width
 * and height is logged with its shaders, render states and textures (the fetch constant, decoded or a resolve's host copy, and the alpha the host holds
 * for it) and the pixels it changed. The part after the draw goes to XB_DIAG_REGION_DIR as region_<draw>.bmp at half size, for the first 64 such draws */
static struct { int on; unsigned frame, x0, y0, x1, y1, draw0, last, saved, said_tex; ID3D11Texture2D *st; unsigned sw, sh; DXGI_FORMAT sf; unsigned char *before; } s_reg;
static unsigned char half_byte(unsigned h);

/* tex read back, its region box (whole: all of it): 8 bytes a texel as 16-bit floats (*half set) or 4 as 8-bit; 0 if it cannot */
static unsigned char *tex_read(ID3D11Texture2D *tex, int whole, unsigned *pw, unsigned *ph, int *half)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_BOX b;
    D3D11_MAPPED_SUBRESOURCE m;
    ID3D11Texture2D *st = 0;
    unsigned char *out = 0;
    unsigned y, bpp;
    ID3D11Texture2D_GetDesc(tex, &d);
    if (d.SampleDesc.Count != 1 || (d.Format != DXGI_FORMAT_R16G16B16A16_FLOAT && d.Format != DXGI_FORMAT_R8G8B8A8_UNORM)) return 0;
    bpp = d.Format == DXGI_FORMAT_R16G16B16A16_FLOAT ? 8 : 4;
    *half = bpp == 8;
    b.left = whole ? 0 : s_reg.x0 * d.Width / 1000, b.right = whole ? d.Width : s_reg.x1 * d.Width / 1000;
    b.top = whole ? 0 : s_reg.y0 * d.Height / 1000, b.bottom = whole ? d.Height : s_reg.y1 * d.Height / 1000;
    b.front = 0, b.back = 1;
    if (b.right > d.Width) b.right = d.Width;
    if (b.bottom > d.Height) b.bottom = d.Height;
    if (b.right <= b.left || b.bottom <= b.top) return 0;
    *pw = b.right - b.left, *ph = b.bottom - b.top;
    if (whole || !s_reg.st || s_reg.sw != *pw || s_reg.sh != *ph || s_reg.sf != d.Format) {
        DXGI_FORMAT fmt = d.Format;
        d.Width = *pw, d.Height = *ph, d.MipLevels = 1, d.ArraySize = 1, d.Usage = D3D11_USAGE_STAGING, d.BindFlags = 0, d.CPUAccessFlags = D3D11_CPU_ACCESS_READ, d.MiscFlags = 0;
        if (FAILED(ID3D11Device_CreateTexture2D(s_dev, &d, 0, &st))) return 0;
        if (!whole) {
            if (s_reg.st) ID3D11Texture2D_Release(s_reg.st);
            s_reg.st = st, s_reg.sw = *pw, s_reg.sh = *ph, s_reg.sf = fmt;
        }
    } else st = s_reg.st;
    ID3D11DeviceContext_CopySubresourceRegion(s_ctx, (ID3D11Resource *)st, 0, 0, 0, 0, (ID3D11Resource *)tex, 0, &b);
    if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)st, 0, D3D11_MAP_READ, 0, &m))) {
        out = (unsigned char *)malloc((size_t)*pw * *ph * bpp);
        if (out)
            for (y = 0; y < *ph; y++) memcpy(out + (size_t)y * *pw * bpp, (unsigned char *)m.pData + (size_t)y * m.RowPitch, (size_t)*pw * bpp);
        ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)st, 0);
    }
    if (whole) ID3D11Texture2D_Release(st);
    return out;
}

static unsigned px_chan(const unsigned char *p, size_t k, unsigned c, int half)
{
    return half ? half_byte(((const unsigned short *)p)[k * 4 + c]) : p[k * 4 + c];
}

static void region_before(void)
{
    unsigned w, h;
    int half;
    if (!s_reg.on) {
        const char *e = getenv("XB_DIAG_REGION");
        s_reg.on = e && sscanf(e, "%u:%u,%u,%u,%u", &s_reg.frame, &s_reg.x0, &s_reg.y0, &s_reg.x1, &s_reg.y1) == 5 && s_reg.x1 > s_reg.x0 && s_reg.y1 > s_reg.y0 ? 1 : -1;
    }
    free(s_reg.before), s_reg.before = 0;
    if (s_reg.on < 0 || xb_frame_number() != s_reg.frame) return;
    if (s_reg.last != s_reg.frame + 1) s_reg.last = s_reg.frame + 1, s_reg.draw0 = s_draws;   /* the frame's first draw */
    if (s_target[0] && s_target[0]->tex) s_reg.before = tex_read(s_target[0]->tex, 0, &w, &h, &half);
}

static void region_after(unsigned prim, unsigned nverts)
{
    unsigned char *a;
    unsigned w, h, i, c, bpp;
    int half;
    size_t k, n, changed = 0;
    double sum[4] = {0, 0, 0, 0};
    if (!s_reg.before || !s_target[0] || !s_target[0]->tex) return;
    a = tex_read(s_target[0]->tex, 0, &w, &h, &half);
    if (!a) return;
    n = (size_t)w * h, bpp = half ? 8 : 4;
    for (k = 0; k < n; k++)
        if (memcmp(a + k * bpp, s_reg.before + k * bpp, bpp)) {
            changed++;
            for (c = 0; c < 4; c++) sum[c] += px_chan(a, k, c, half);
        }
    if (changed) {
        char rs[1800], sl[2400];
        int at = 0, as = 0;
        rs[0] = sl[0] = 0;
        for (i = 0; i < XB_RS_COUNT * 4u; i += 4)
            if (xb_rs(i)) at += sprintf_s(rs + at, sizeof rs - at, " %u:%x", i, xb_rs(i));
        for (i = 0; i < 16; i++) {
            char al[80];
            al[0] = 0;
            if (!s_tex[i] || !s_tex[i]->tex) continue;
            if (s_reg.said_tex < 48) {   /* the alpha the host holds for that texture, all of it read back */
                unsigned tw, th;
                int t_half;
                unsigned char *t = tex_read(s_tex[i]->tex, 1, &tw, &th, &t_half);
                if (t) {
                    size_t q, tn = (size_t)tw * th, a0 = 0, a1 = 0;
                    for (q = 0; q < tn; q++) {
                        unsigned v = px_chan(t, q, 3, t_half);
                        a0 += v == 0, a1 += v == 255;
                    }
                    sprintf_s(al, sizeof al, ", host alpha 0 in %.0f%% 255 in %.0f%%", 100.0 * a0 / tn, 100.0 * a1 / tn);
                    free(t);
                    s_reg.said_tex++;
                }
            }
            as += sprintf_s(sl + as, sizeof sl - as, " [%u %ux%u %s format %u swizzle %03x order %u at %08x%s]", i, s_tex[i]->w, s_tex[i]->h,
                            xb_slot_fetch[i][3] == 2 ? "resolved" : "decoded", xb_slot_fetch[i][0] & 63, (xb_slot_fetch[i][2] >> 1) & 0xfff,
                            (xb_slot_fetch[i][0] >> 6) & 3, xb_slot_fetch[i][0] & ~0xfffu, al);
        }
        port_log("diag region draw %u of frame %u: vs %08x ps %08x prim %u verts %u; %u of %ux%u pixels changed, their mean rgba %.0f %.0f %.0f %.0f; target 0 %ux%u bias %d; textures%s | render states%s\n",
                 s_draws - s_reg.draw0, s_reg.frame, s_vs_addr, s_ps_addr, prim, nverts, (unsigned)changed, w, h, sum[0] / changed, sum[1] / changed, sum[2] / changed,
                 sum[3] / changed, s_target[0]->w, s_target[0]->h, s_target_bias[0], sl, rs);
        if (s_reg.saved < 64 && getenv("XB_DIAG_REGION_DIR")) {   /* the part after the draw, half size, 24-bit BMP */
            char path[300];
            FILE *f = 0;
            unsigned ow = w / 2, oh = h / 2, row = (ow * 3 + 3) & ~3u, y2, x2;
            unsigned long sz = 54 + (unsigned long)row * oh;
            unsigned char hdr[54], *line = (unsigned char *)calloc(1, row + 4);
            sprintf_s(path, sizeof path, "%s/region_%04u.bmp", getenv("XB_DIAG_REGION_DIR"), s_draws - s_reg.draw0);
            if (line && ow && oh) f = fopen(path, "wb");
            if (f) {
                memset(hdr, 0, sizeof hdr);
                hdr[0] = 'B', hdr[1] = 'M', hdr[10] = 54, hdr[14] = 40, hdr[26] = 1, hdr[28] = 24;
                memcpy(hdr + 2, &sz, 4), memcpy(hdr + 18, &ow, 4), memcpy(hdr + 22, &oh, 4);
                fwrite(hdr, 1, 54, f);
                for (y2 = oh; y2-- > 0;) {
                    for (x2 = 0; x2 < ow; x2++) {
                        k = (size_t)(2 * y2) * w + 2 * x2;
                        line[x2 * 3] = (unsigned char)px_chan(a, k, 2, half), line[x2 * 3 + 1] = (unsigned char)px_chan(a, k, 1, half), line[x2 * 3 + 2] = (unsigned char)px_chan(a, k, 0, half);
                    }
                    fwrite(line, 1, row, f);
                }
                fclose(f);
                s_reg.saved++;
            }
            free(line);
        }
    }
    free(a);
}

/* XB_DIAG_PS=key[,key...][@every] (bring-up): a draw with one of those pixel shaders logs its pixel constants c0..c7 and, for texture slots 0..3, the
 * host texture's format and each channel's mean, least and most over all of it, with NaN and infinity counts: what a post pass reads. The first 3 of
 * each key, then one frame in every `every` (default 100), at most 64 lines. @m<n> instead logs each draw in the n frames from the scene marker's latest
 * start (XB_MARK, xb_d3d.c). After each logged draw its render target 0 is read back the same way (ps_output_diag) */
static float half_float(unsigned h)
{
    unsigned e = (h >> 10) & 31, m = h & 1023;
    float f = e == 31 ? (m ? NAN : INFINITY) : e ? ldexpf((float)(m | 1024), (int)e - 25) : ldexpf((float)m, -24);
    return h & 0x8000 ? -f : f;
}

static int s_ps_diag_out;   /* the draw ps_inputs_diag logged: its target 0 is read back after it (ps_output_diag) */

static void ps_inputs_diag(void)
{
    static int on = -1, lines;
    static u32 keys[8];
    static unsigned nkeys, every = 100, seen[8], win;
    unsigned i, k, c;
    if (on < 0) {
        const char *e = getenv("XB_DIAG_PS"), *p = e;
        on = 0;
        while (p && *p && nkeys < 8) {
            keys[nkeys++] = (u32)strtoul(p, (char **)&p, 16);
            if (*p == '@' && p[1] == 'm') win = (unsigned)atoi(p + 2), p += strlen(p);   /* @m<n>: n frames from each scene marker start */
            else if (*p == '@') every = (unsigned)atoi(p + 1), p += strlen(p);
            if (*p == ',') p++;
        }
        on = nkeys > 0;
    }
    if (!on || lines >= 64) return;
    for (k = 0; k < nkeys && keys[k] != s_ps_addr; k++) {}
    if (k == nkeys) return;
    if (win) {
        unsigned mk = xb_mark_frame(), f = xb_frame_number();
        if (mk == ~0u || f < mk || f >= mk + win) return;
    } else if (seen[k]++ >= 3 && xb_frame_number() % (every ? every : 1)) return;
    lines++;
    s_ps_diag_out = 1;
    port_log("diag ps %08x frame %u (vs %08x), target 0 %ux%u bias %d; pixel constants:", s_ps_addr, xb_frame_number(), s_vs_addr, s_target[0] ? s_target[0]->w : 0,
             s_target[0] ? s_target[0]->h : 0, s_target_bias[0]);
    {   /* XB_DIAG_PS_C=lo-hi: the pixel constants logged, default c0..c7 (Too Human's post pass fea9caac reads its UserVectorInputs at c32..c47) */
        static unsigned clo = ~0u, chi;
        if (clo == ~0u) {
            const char *e = getenv("XB_DIAG_PS_C");
            clo = 0, chi = 7;
            if (e && *e) { char *q; clo = (unsigned)strtoul(e, &q, 10); chi = *q == '-' ? (unsigned)strtoul(q + 1, 0, 10) : clo; if (chi > 255) chi = 255; if (clo > chi) clo = chi; }
        }
        for (i = clo; i <= chi; i++) port_log(" c%u %g %g %g %g;", i, s_const[1][i * 4], s_const[1][i * 4 + 1], s_const[1][i * 4 + 2], s_const[1][i * 4 + 3]);
    }
    port_log("\n");
    for (i = 0; i < 4; i++) {
        unsigned tw, th;
        int half;
        unsigned char *t;
        size_t q, n, nan = 0, inf = 0;
        double sum[4] = {0, 0, 0, 0};
        float lo[4] = {1e30f, 1e30f, 1e30f, 1e30f}, hi[4] = {-1e30f, -1e30f, -1e30f, -1e30f};
        if (!s_tex[i] || !s_tex[i]->tex) continue;
        port_log("   slot %u fetch word 0 %08x (clamp x %u y %u z %u) word 3 filter mag %u min %u word 5 border %u; sampler states: address u %u v %u, filter mag %u min %u; address from %s\n", i,
                 xb_slot_fetch_w05[i][0], (xb_slot_fetch_w05[i][0] >> 10) & 7u, (xb_slot_fetch_w05[i][0] >> 13) & 7u, (xb_slot_fetch_w05[i][0] >> 16) & 7u,
                 (xb_slot_fetch[i][2] >> 19) & 3u, (xb_slot_fetch[i][2] >> 21) & 3u, xb_slot_fetch_w05[i][1] & 3u,
                 xb_ss(i, 0), xb_ss(i, 4), xb_ss(i, 16), xb_ss(i, 20), xb_slot_fetch_w05[i][2] && fetch_clamp_on() ? "the fetch constant" : "the sampler states");
        t = tex_read(s_tex[i]->tex, 1, &tw, &th, &half);
        if (!t) { port_log("   slot %u %ux%u: not read back (host format not 8-bit or 16-bit float)\n", i, s_tex[i]->w, s_tex[i]->h); continue; }
        n = (size_t)tw * th;
        for (q = 0; q < n; q++)
            for (c = 0; c < 4; c++) {
                float v = half ? half_float(((const unsigned short *)t)[q * 4 + c]) : t[q * 4 + c] / 255.0f;
                if (v != v) { nan++; continue; }
                if (v > 65504.0f || v < -65504.0f) { inf++; continue; }
                sum[c] += v;
                if (v < lo[c]) lo[c] = v;
                if (v > hi[c]) hi[c] = v;
            }
        port_log("   slot %u %ux%u %s format %u at %08x, host %s: mean %.4g %.4g %.4g %.4g, least %.4g %.4g %.4g %.4g, most %.4g %.4g %.4g %.4g, NaN %u, infinite %u\n",
                 i, tw, th, xb_slot_fetch[i][3] == 2 ? "resolved" : "decoded", xb_slot_fetch[i][0] & 63, xb_slot_fetch[i][0] & ~0xfffu, half ? "16-bit float" : "8-bit",
                 sum[0] / n, sum[1] / n, sum[2] / n, sum[3] / n, lo[0], lo[1], lo[2], lo[3], hi[0], hi[1], hi[2], hi[3], (unsigned)nan, (unsigned)inf);
        if (n <= 256 && !half) {   /* a lookup (Too Human's 256x1 UserTextures): every texel, rgba bytes as the host holds them, and the slot's gamma channels */
            port_log("   slot %u texels (gamma channels %x):", i, i < 16 ? xb_tex_gamma[i] : 0u);
            for (q = 0; q < n; q++) port_log(" %02x%02x%02x%02x", t[q * 4], t[q * 4 + 1], t[q * 4 + 2], t[q * 4 + 3]);
            port_log("\n");
        }
        free(t);
    }
}

/* After a draw ps_inputs_diag logged: its render target 0, read back whole, each channel's mean, least and most with NaN and infinity counts */
static void ps_output_diag(void)
{
    unsigned tw, th, c;
    int half;
    size_t q, n, nan = 0, inf = 0;
    double sum[4] = {0, 0, 0, 0};
    float lo[4] = {1e30f, 1e30f, 1e30f, 1e30f}, hi[4] = {-1e30f, -1e30f, -1e30f, -1e30f};
    unsigned char *t = s_target[0] && s_target[0]->tex ? tex_read(s_target[0]->tex, 1, &tw, &th, &half) : 0;
    if (!t) { port_log("   output: target 0 not read back\n"); return; }
    n = (size_t)tw * th;
    for (q = 0; q < n; q++)
        for (c = 0; c < 4; c++) {
            float v = half ? half_float(((const unsigned short *)t)[q * 4 + c]) : t[q * 4 + c] / 255.0f;
            if (v != v) { nan++; continue; }
            if (v > 65504.0f || v < -65504.0f) { inf++; continue; }
            sum[c] += v;
            if (v < lo[c]) lo[c] = v;
            if (v > hi[c]) hi[c] = v;
        }
    port_log("   output: target 0 %ux%u: mean %.4g %.4g %.4g %.4g, least %.4g %.4g %.4g %.4g, most %.4g %.4g %.4g %.4g, NaN %u, infinite %u\n", tw, th,
             sum[0] / n, sum[1] / n, sum[2] / n, sum[3] / n, lo[0], lo[1], lo[2], lo[3], hi[0], hi[1], hi[2], hi[3], (unsigned)nan, (unsigned)inf);
    free(t);
}

/* nverts vertices drawn: nrows rows in vdata, each drawn once in order (idx 0), or drawn by nverts 32-bit indices idx into them (XB_VTX_UNIQUE; the
 * index buffer kept at ikeep as the vertex buffer at keep). A strip's cut index (all ones) is never one of them: rows are under 2^24 */
static void gpu_draw(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata, void **keep, unsigned nrows, const u32 *idx, void **ikeep)
{
    const XbShader *vsd = xb_shader_find(s_vs_addr), *psd = xb_shader_find(s_ps_addr);
    HostShader *vs, *ps;
    D3D11_BUFFER_DESC bd;
    D3D11_SUBRESOURCE_DATA sr;
    ID3D11Buffer *vb = 0, *ixb = 0;
    D3D11_MAPPED_SUBRESOURCE m;
    ID3D11ShaderResourceView *srv[16];
    unsigned i, stride = nattr * 16;
    D3D11_PRIMITIVE_TOPOLOGY topo;
    float bf[4] = {0, 0, 0, 0};

    gpu_init();
    {   /* XB_SKIP_PS=hex[,hex...] (bring-up): draws with those pixel shaders are left out on frames whose (frame / 20) is odd, so dumps show them with and without */
        static u32 skip[4], n_skip = ~0u;
        unsigned q;
        if (n_skip == ~0u) {
            const char *e = getenv("XB_SKIP_PS");
            char *end;
            for (n_skip = 0; e && *e && n_skip < 4; e = *end ? end + 1 : end) skip[n_skip++] = (u32)strtoul(e, &end, 16);
        }
        for (q = 0; q < n_skip; q++)
            if (s_ps_addr == skip[q] && ((xb_frame_number() / 20u) & 1u)) return;
    }
    if (!vsd) xb_stop("d3d_draw", "the bound vertex shader is not one of the converted containers", s_vs_addr);
    if (!s_ps_addr && !xb_depth_on()) {   /* no pixel shader: a depth-only pass (first at frame 635). Depth off (no XB_DEPTH) it changes nothing */
        static unsigned n;
        if (!n++ || n % 1000 == 0) port_log("d3d draw with no pixel shader (%u so far): a depth-only pass, which changes nothing while the host keeps depth off\n", n);
        s_draws++, s_draws_nothing++;
        xb_fcap_draw(prim, nverts, nattr, "no pixel shader and host depth off: changes nothing");
        return;
    }
    if (!s_ps_addr && depth_for_draw() == s_depth_off) {   /* XB_DEPTH=1, no pixel shader and z off: nothing it could change */
        static unsigned n;
        if (!n++ || n % 1000 == 0) port_log("d3d draw with no pixel shader and z off (%u so far): skipped, it changes nothing\n", n);
        s_draws++, s_draws_nothing++;
        xb_fcap_draw(prim, nverts, nattr, "no pixel shader and z off: changes nothing");
        return;
    }
    if (!s_ps_addr) {   /* XB_DEPTH=1: drawn with no pixel shader, depth only, colour writes masked (no_colour_blend) */
        static unsigned n;
        if (!n++ || n % 1000 == 0) port_log("d3d draw with no pixel shader (%u so far): drawn as a depth-only pass\n", n);
        psd = 0;
    } else if (!psd) xb_stop("d3d_draw", "the bound pixel shader is not one of the converted containers", s_ps_addr);
    if (!vsd->hlsl) xb_stop("d3d_draw", "the vertex shader has an opcode the converter cannot do (shaders.json names it)", s_vs_addr);
    if (psd && !psd->hlsl) xb_stop("d3d_draw", "the pixel shader has an opcode the converter cannot do (shaders.json names it)", s_ps_addr);
    if (vsd->n_inputs != nattr) xb_stop("d3d_draw", "the declaration has a different number of elements than the shader fetches", nattr);
    if (prim == 8 && !vsd->gs_rect) xb_stop("d3d_draw", "no rectangle-list geometry shader for this vertex shader", s_vs_addr);
#ifdef XB_MOVIE_PS
    if (psd && psd->addr == XB_MOVIE_PS) xb_flow_movie_picture();
    if (psd && psd->addr == XB_MOVIE_PS && getenv("XB_MOVIES") && getenv("XB_MOVIES")[0] == '0') {   /* xb_game.h: XB_MOVIES=0 draws it black */
        static const float black[4] = {0, 0, 0, 1};
        static int said;
        if (!said++) port_log("d3d movie frame (pixel shader %08x) drawn black, as XB_MOVIES=0 asks\n", psd->addr);
        bind_targets();
        xb_gpu_clear(0xF0, black, 0, 0);
        xb_fcap_draw(prim, nverts, nattr, "movie frame drawn black (XB_MOVIES=0)");
        s_draws++;
        return;
    }
#endif
    vs = shader_for(vsd);
    ps = psd ? shader_for(psd) : 0;
    if (!vs->layout || vs->layout_n != nattr) {
        D3D11_INPUT_ELEMENT_DESC el[32];
        if (nattr > 32) xb_stop("d3d_draw", "more than 32 vertex attributes", nattr);
        for (i = 0; i < nattr; i++) {
            el[i].SemanticName = "ATTR";
            el[i].SemanticIndex = i;
            el[i].Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
            el[i].InputSlot = 0;
            el[i].AlignedByteOffset = i * 16;
            el[i].InputSlotClass = D3D11_INPUT_PER_VERTEX_DATA;
            el[i].InstanceDataStepRate = 0;
        }
        if (vs->layout) ID3D11InputLayout_Release(vs->layout);
        if (FAILED(ID3D11Device_CreateInputLayout(s_dev, el, nattr, vs->vs_bytes, vs->vs_len, &vs->layout)))
            xb_stop("d3d_draw", "input layout does not match the vertex shader", s_vs_addr);
        vs->layout_n = nattr;
    }
    if (keep && *keep) vb = (ID3D11Buffer *)*keep;   /* the same vertices as an earlier draw's: its buffer */
    else {
        memset(&bd, 0, sizeof bd);
        bd.ByteWidth = stride * nrows;
        bd.Usage = D3D11_USAGE_IMMUTABLE;
        bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        sr.pSysMem = vdata;
        sr.SysMemPitch = sr.SysMemSlicePitch = 0;
        if (FAILED(ID3D11Device_CreateBuffer(s_dev, &bd, &sr, &vb))) vb = 0;
        if (keep) *keep = vb;
        s_up_verts += nrows;
    }
    if (!vb) { s_draws++; xb_fcap_draw(prim, nverts, nattr, "no host vertex buffer"); return; }   /* no vertices, or no room on the host for them */
    if (idx) {   /* XB_VTX_UNIQUE: the entries' rows */
        if (ikeep && *ikeep) ixb = (ID3D11Buffer *)*ikeep;
        else {
            memset(&bd, 0, sizeof bd);
            bd.ByteWidth = 4u * nverts;
            bd.Usage = D3D11_USAGE_IMMUTABLE;
            bd.BindFlags = D3D11_BIND_INDEX_BUFFER;
            sr.pSysMem = idx;
            sr.SysMemPitch = sr.SysMemSlicePitch = 0;
            if (FAILED(ID3D11Device_CreateBuffer(s_dev, &bd, &sr, &ixb))) ixb = 0;
            if (ikeep) *ikeep = ixb;
        }
        if (!ixb) {
            if (!keep) ID3D11Buffer_Release(vb);
            s_draws++; xb_fcap_draw(prim, nverts, nattr, "no host index buffer"); return;
        }
    }

    for (i = 0; i < 2; i++) {
        if (gpu_const_fast() && !InterlockedExchange(&s_const_dirty[i], 0)) {   /* unchanged since its last upload: the buffer holds it */
            if (gpu_const_check()) {
                s_const_skips++;
                if (memcmp(s_const_sent[i], s_const[i], 256 * 16) && !s_const_skips_bad++)
                    port_log("const check (XB_CONST_CHECK): a skipped upload of constant buffer %u would have sent other bytes\n", i);
                if (s_const_skips % 100000 == 0) port_log("const check (XB_CONST_CHECK): %lld uploads skipped, %lld would have sent other bytes\n", s_const_skips, s_const_skips_bad);
            }
            continue;
        }
        if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_cb[i], 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            memcpy(m.pData, s_const[i], 256 * 16);
            ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_cb[i], 0);
            if (gpu_const_check()) memcpy(s_const_sent[i], s_const[i], 256 * 16);
        } else
            s_const_dirty[i] = 1;   /* not sent: again at the next draw */
    }
    bind_targets();
    ID3D11DeviceContext_VSSetConstantBuffers(s_ctx, 0, 1, &s_cb[0]);
    ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 1, 1, &s_cb[1]);
    if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_kit_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        /* render state 304 off (the library writes 0x400 where on is 0x43f to the viewport control): the vertices are the game's pixels, whose centres are at whole numbers.
         * Render states 96 alpha test, 100 reference, 104 comparison; 7 is always */
        float *k = (float *)m.pData;
        k[0] = 2.0f / (float)s_game_w;
        k[1] = 2.0f / (float)s_game_h;
        k[2] = xb_rs(304) ? 0.0f : 1.0f;
        k[3] = zflip_vs_on() ? 1.0f : 0.0f;   /* xb_vte.w: z = w - z, the console's depth for an upside-down range (zflip_vs_on) */
        k[4] = (float)xb_rs(100);
        k[5] = dev_words_on() ? ((s_dw[1] & 8u) ? (float)(s_dw[1] & 7u) : 7.0f) : xb_rs(96) ? (float)xb_rs(104) : 7.0f;
        /* xb_at.z: the converted pixel shader's colour times 2^(render target 0's exponent bias) with XB_COGBOX_RT_BIAS=1 (COGBox's rule,
         * xb_xenos_colour.h; shader_convert.py's epilogue), else exactly 1 */
        k[6] = xb_cogbox_rt_bias() ? xenos_exp_bias_factor(s_target_bias[0]) : 1.0f;
        k[7] = target_clamp_mode();
        {   /* xb_gam: each bound decoded texture's gamma channels (xb_xenos_gamma.h), slots 0..7 in .x and 8..15 in .y, 4 bits a slot */
            u32 *g = (u32 *)m.pData + 8;
            g[0] = g[1] = g[2] = g[3] = 0;
            g[2] = shader_kill_on() ? 1u : 0u;   /* xb_gam.z: XB_SHADER_KILL */
            for (i = 0; i < 16; i++)
                if (s_tex[i]) g[i >> 3] |= (xb_tex_gamma[i] & 15u) << ((i & 7u) * 4u);
        }
        ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_kit_cb, 0);
    }
    ID3D11DeviceContext_VSSetConstantBuffers(s_ctx, 2, 1, &s_kit_cb);
    ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 2, 1, &s_kit_cb);
    {
        ID3D11SamplerState *sm[16];
        for (i = 0; i < 16; i++) sm[i] = s_tex[i] ? sampler_for_draw(i) : s_sampler;
        ID3D11DeviceContext_PSSetSamplers(s_ctx, 0, 16, sm);
    }
    for (i = 0; i < 16; i++) srv[i] = s_tex[i] ? s_tex[i]->srv : 0;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 16, srv);
    ID3D11DeviceContext_RSSetState(s_ctx, raster_for_draw());
    if (xb_rs(60)) blend_factor(bf);
    ID3D11DeviceContext_OMSetBlendState(s_ctx, !psd ? no_colour_blend() : s_flat_ps ? 0 : blend_for_draw(), bf, 0xFFFFFFFF);
    ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, depth_for_draw(), xb_rs(132) & 255u);   /* render state 132: the stencil reference */
    ID3D11DeviceContext_IASetInputLayout(s_ctx, vs->layout);
    {
        UINT st = stride, off = 0;
        ID3D11DeviceContext_IASetVertexBuffers(s_ctx, 0, 1, &vb, &st, &off);
    }
    topo = prim == 6 ? D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP : D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST;
    ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, topo);
    ID3D11DeviceContext_VSSetShader(s_ctx, vs->vs, 0, 0);
    ID3D11DeviceContext_GSSetShader(s_ctx, prim == 8 ? vs->gs : 0, 0, 0);
    ID3D11DeviceContext_PSSetShader(s_ctx, !ps ? 0 : s_flat_ps ? s_flat_ps : ps->ps, 0, 0);
    if (zlog_frame()) {
        XbHostTex *dz = depth_tex();
        port_log("zdraw %u: vs %08x ps %08x prim %u verts %u, z %u func %u write %u, cull %x, blend %u, vp %g,%g %gx%g z %g..%g, target %ux%u, depth %ux%u%s\n",
                 xb_frame_number(), s_vs_addr, s_ps_addr, prim, nverts, xb_rs(40), xb_rs(44), xb_rs(48), xb_rs(56), xb_rs(60), (double)s_vp.x, (double)s_vp.y,
                 (double)s_vp.w, (double)s_vp.h, (double)s_vp.zmin, (double)s_vp.zmax, s_target[0] ? s_target[0]->w : 0, s_target[0] ? s_target[0]->h : 0,
                 dz ? dz->w : 0, dz ? dz->h : 0, xb_depth_on() ? ", depth on" : "");
    }
    {   /* XB_DIAG_VS=hex: the render states 40..108 and every other one not 0, and sampler 0's states 0..44 of the first draws of that vertex shader from frame 150 (bring-up) */
        static u32 diag = ~0u;
        static unsigned said[4];
        static u32 ps_of[4];
        unsigned q;
        if (diag == ~0u) {
            const char *e = getenv("XB_DIAG_VS");
            diag = e ? (u32)strtoul(e, 0, 16) : 0;
        }
        {   /* XB_DIAG_TEX=WxH: the first 16 shader pairs that draw with a texture 0 of that size from frame 150, once each */
            static unsigned tw = ~0u, th, n_pair;
            static u32 pair[16][2];
            unsigned z;
            if (tw == ~0u) {
                const char *e = getenv("XB_DIAG_TEX");
                tw = e ? (unsigned)atoi(e) : 0, th = e && strchr(e, 'x') ? (unsigned)atoi(strchr(e, 'x') + 1) : 0;
            }
            if (tw && n_pair < 16 && s_tex[0] && s_tex[0]->w == tw && s_tex[0]->h == th && xb_frame_number() >= 150) {
                for (z = 0; z < n_pair && !(pair[z][0] == s_vs_addr && pair[z][1] == s_ps_addr); z++) {}
                if (z == n_pair) {
                    pair[n_pair][0] = s_vs_addr, pair[n_pair++][1] = s_ps_addr;
                    port_log("diag texture %ux%u drawn at frame %u by vs %08x ps %08x, prim %u verts %u; blend %u %u %u, alpha test %u ref %u func %u, z %u %u write %u\n", tw, th,
                             xb_frame_number(), s_vs_addr, s_ps_addr, prim, nverts, xb_rs(60), xb_rs(72), xb_rs(76), xb_rs(96), xb_rs(100), xb_rs(104), xb_rs(40), xb_rs(44), xb_rs(48));
                }
            }
        }
        for (q = 0; q < 4 && ps_of[q] && ps_of[q] != s_ps_addr; q++) {}   /* the first 3 draws of each of 4 pixel shaders */
        if (diag && s_vs_addr == diag && xb_frame_number() >= 150 && q < 4 && said[q] < 3) {
            char rs[1700], ss[300];
            int at = 0, as = 0;
            ps_of[q] = s_ps_addr, said[q]++;
            for (i = 0; i < XB_RS_COUNT * 4u; i += 4)
                if (xb_rs(i) || (i >= 40 && i <= 108)) at += sprintf_s(rs + at, sizeof rs - at, " %u:%x", i, xb_rs(i));
            for (i = 0; i <= 44; i += 4) as += sprintf_s(ss + as, sizeof ss - as, " %u:%x", i, xb_ss(0, i));
            port_log("diag draw %u: vs %08x ps %08x prim %u verts %u, texture 0 %ux%u | render states%s | sampler 0%s\n", xb_frame_number(), s_vs_addr, s_ps_addr, prim, nverts,
                     s_tex[0] ? s_tex[0]->w : 0, s_tex[0] ? s_tex[0]->h : 0, rs, ss);
        }
    }
    ps_inputs_diag();
    region_before();
    if (s_resize_check > 0) {   /* XB_RESIZE_AT: what the device really holds at the draw, against what the kit bound */
        D3D11_VIEWPORT a[4];
        UINT n = 4;
        D3D11_RECT sc[4];
        UINT nsc = 4;
        ID3D11RenderTargetView *r = 0;
        ID3D11Resource *res = 0;
        D3D11_TEXTURE2D_DESC td;
        memset(a, 0, sizeof a), memset(&td, 0, sizeof td), memset(sc, 0, sizeof sc);
        ID3D11DeviceContext_RSGetViewports(s_ctx, &n, a);
        ID3D11DeviceContext_RSGetScissorRects(s_ctx, &nsc, sc);
        ID3D11DeviceContext_OMGetRenderTargets(s_ctx, 1, &r, 0);
        if (r) {
            ID3D11RenderTargetView_GetResource(r, &res);
            if (res) ID3D11Texture2D_GetDesc((ID3D11Texture2D *)res, &td), ID3D11Resource_Release(res);
            ID3D11RenderTargetView_Release(r);
        }
        s_resize_check--;
        port_log("resize check: frame %u vs %08x ps %08x prim %u verts %u stride %u: device viewports %u, first %g %g %gx%g; device target %ux%u; kit viewport %g %g %gx%g scale %g, kit target %ux%u; scissor %ld %ld %ld %ld; game size %ux%u; render state 304 %x\n",
                 s_frames, s_vs_addr, s_ps_addr, prim, nverts, stride, n, (double)a[0].TopLeftX, (double)a[0].TopLeftY, (double)a[0].Width, (double)a[0].Height, td.Width, td.Height,
                 (double)s_vp.x, (double)s_vp.y, (double)s_vp.w, (double)s_vp.h, (double)s_scale, s_target[0] ? s_target[0]->w : 0, s_target[0] ? s_target[0]->h : 0,
                 (long)sc[0].left, (long)sc[0].top, (long)sc[0].right, (long)sc[0].bottom, s_game_w, s_game_h, xb_rs(304));
    }
    if (idx && g_fcap_on) {   /* XB_FCAP_DRAWS: each entry's row, as a draw of rows in order records them */
        float *e = (float *)malloc((size_t)nverts * stride + 16);
        if (e)
            for (i = 0; i < nverts; i++) memcpy(e + (size_t)i * nattr * 4u, vdata + (size_t)idx[i] * nattr * 4u, stride);
        fcap_draw_data(e ? nverts : 0, nattr, e ? e : vdata);
        free(e);
    } else
        fcap_draw_data(nverts, nattr, vdata);   /* XB_FCAP_DRAWS: before its record takes the number */
    xb_fcap_draw(prim, nverts, nattr, 0);
    if (ixb) {
        ID3D11DeviceContext_IASetIndexBuffer(s_ctx, ixb, DXGI_FORMAT_R32_UINT, 0);
        ID3D11DeviceContext_DrawIndexed(s_ctx, nverts, 0, 0);
    } else
        ID3D11DeviceContext_Draw(s_ctx, nverts, 0);
    fcap_draw_after();
    region_after(prim, nverts);
    if (!keep) ID3D11Buffer_Release(vb);
    if (ixb && !ikeep) ID3D11Buffer_Release(ixb);
    if (s_ps_diag_out) s_ps_diag_out = 0, ps_output_diag();
    if (psd && xb_edram_on())   /* XB_EDRAM: each target whose write mask lets a channel through is now the newest writer of its tiles */
        for (i = 0; i < 4; i++)
            if (s_target[i] && (xb_rs(212 + 4 * i) & 15)) xb_edram_written(s_target[i]);
    s_draws++;
}

void xb_gpu_draw_kept(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata, void **keep) { gpu_draw(prim, nverts, nattr, vdata, keep, nverts, 0, 0); }
void xb_gpu_draw_indexed(unsigned prim, unsigned nrows, unsigned nattr, const float *vdata, unsigned nidx, const u32 *idx, void **keep, void **ikeep)
{
    gpu_draw(prim, nidx, nattr, vdata, keep, nrows, idx, ikeep);
}
void xb_gpu_draw(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata) { xb_gpu_draw_kept(prim, nverts, nattr, vdata, 0); }
void xb_gpu_buffer_free(void *vb) { if (vb) ID3D11Buffer_Release((ID3D11Buffer *)vb); }

/* The draw the bound shaders and states would make changes nothing: no pixel shader, and z off or the host's depth off (the two cases xb_gpu_draw_kept
 * leaves too, after its caller has read, hashed and converted the vertices). Counted as a draw. Too Human's menu: about 300 of a frame's 870 */
int xb_gpu_draw_nothing(void)
{
    if (s_ps_addr || !xb_draw_fast()) return 0;
    if (xb_depth_on() && depth_for_draw() != s_depth_off) return 0;
    s_draws++, s_draws_nothing++;
    xb_fcap_skip("no pixel shader, z off: changes nothing (left before its vertices are read)", s_vs_addr, s_ps_addr);
    return 1;
}

/* The game's resolve: its render target copied into a texture it samples later (the original's copy out of the render target into memory; here both are host
 * textures, so it is one copy). Both are made at the same output scale; a size or format mismatch stops. An exponent bias scales every texel by 2^bias:
 * then the copy is a draw of the source into the destination through s_scale_ps (every draw binds its own state again, so none is kept) */
void xb_gpu_resolve(XbHostTex *src, XbHostTex *dst, int exp_bias)
{
    gpu_init();
    if (!src || !dst || !src->tex || !dst->tex) xb_stop("d3d_resolve", "a resolve with a missing host surface", 0);
    fcap_resolve(src, dst, exp_bias);
    {   /* a target is rounded up to its tiles (the first such, 82093a98's texture at the object +256, is the frame's size and the target taller): the part both have */
        D3D11_BOX box;
        static int said;
        box.left = box.top = box.front = 0;
        box.right = src->w < dst->w ? src->w : dst->w;
        box.bottom = src->h < dst->h ? src->h : dst->h;
        box.back = 1;
        if ((src->w != dst->w || src->h != dst->h) && !said++) port_log("d3d resolve %ux%u into %ux%u\n", src->w, src->h, dst->w, dst->h);
        if (!src->depth && (!exp_bias || getenv("XB_RESOLVE_UNSCALED"))) {   /* XB_RESOLVE_UNSCALED=1, a diagnostic: every resolve a plain copy. A depth
                                                                              * source is never a plain copy: its bits are drawn as a colour */
            ID3D11DeviceContext_CopySubresourceRegion(s_ctx, (ID3D11Resource *)dst->tex, 0, 0, 0, 0, (ID3D11Resource *)src->tex, 0, &box);
            return;
        }
        if (src == dst) {   /* the host cannot read and write one texture in a draw; not seen yet */
            static int said_self;
            if (!said_self++) port_log("d3d resolve of a target into itself with exponent bias %d: left unscaled\n", exp_bias);
            return;
        }
        if (!dst->rtv || !src->srv) xb_stop("d3d_resolve", "a scaled resolve between host textures without the views it needs", (u32)exp_bias);
        {
            D3D11_VIEWPORT vp;
            D3D11_MAPPED_SUBRESOURCE m;
            ID3D11ShaderResourceView *srv = src->srv, *none = 0;
            float k[4] = {xenos_exp_bias_factor(exp_bias), xb_cogbox_7e3() ? XENOS_7E3_MAX : 0.0f, src->depth && getenv("XB_DEPTH_RESOLVE_FLIP") ? 1.0f : 0.0f, 0};   /* k.z: XB_DEPTH_RESOLVE_FLIP=1, a diagnostic, a depth resolve gives 1 - z */   /* COGBox's colour rules, xb_xenos_colour.h */
            static int said_7e3;
            if (k[1] > 0 && !said_7e3++) port_log("d3d resolve (XB_COGBOX_7E3=1): the source clamped to the 7e3 range 0..%g before the exponent bias %d\n", k[1], exp_bias);
            /* the copy leaves the pipeline as it found it: the game's next draw may not set everything again (build box A, 2026-11-06: with this copy's state
             * left behind, the whole frame came out in the 160x90 corner its last viewport covered) */
            ID3D11RenderTargetView *o_rtv[4] = {0, 0, 0, 0};
            ID3D11DepthStencilView *o_dsv = 0;
            D3D11_VIEWPORT o_vp[4];
            UINT o_nvp = 4;
            ID3D11VertexShader *o_vs = 0;
            ID3D11PixelShader *o_ps = 0;
            ID3D11GeometryShader *o_gs = 0;
            ID3D11InputLayout *o_il = 0;
            D3D11_PRIMITIVE_TOPOLOGY o_topo;
            ID3D11BlendState *o_bs = 0;
            FLOAT o_bf[4];
            UINT o_sm = 0, o_sref = 0, n;
            ID3D11DepthStencilState *o_ds = 0;
            ID3D11RasterizerState *o_rs = 0;
            ID3D11ShaderResourceView *o_srv = 0;
            ID3D11Buffer *o_cb = 0;
            ID3D11DeviceContext_OMGetRenderTargets(s_ctx, 4, o_rtv, &o_dsv);
            ID3D11DeviceContext_RSGetViewports(s_ctx, &o_nvp, o_vp);
            ID3D11DeviceContext_VSGetShader(s_ctx, &o_vs, 0, 0);
            ID3D11DeviceContext_PSGetShader(s_ctx, &o_ps, 0, 0);
            ID3D11DeviceContext_GSGetShader(s_ctx, &o_gs, 0, 0);
            ID3D11DeviceContext_IAGetInputLayout(s_ctx, &o_il);
            ID3D11DeviceContext_IAGetPrimitiveTopology(s_ctx, &o_topo);
            ID3D11DeviceContext_OMGetBlendState(s_ctx, &o_bs, o_bf, &o_sm);
            ID3D11DeviceContext_OMGetDepthStencilState(s_ctx, &o_ds, &o_sref);
            ID3D11DeviceContext_RSGetState(s_ctx, &o_rs);
            ID3D11DeviceContext_PSGetShaderResources(s_ctx, 0, 1, &o_srv);
            ID3D11DeviceContext_PSGetConstantBuffers(s_ctx, 3, 1, &o_cb);
            if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_scale_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
                memcpy(m.pData, k, sizeof k);
                ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_scale_cb, 0);
            }
            vp.TopLeftX = vp.TopLeftY = 0;
            vp.Width = (float)box.right;
            vp.Height = (float)box.bottom;
            vp.MinDepth = 0;
            vp.MaxDepth = 1;
            ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 1, &dst->rtv, 0);
            ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
            ID3D11DeviceContext_RSSetState(s_ctx, s_raster);
            ID3D11DeviceContext_OMSetBlendState(s_ctx, 0, 0, 0xFFFFFFFF);
            ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, s_depth_off, 0);
            ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &srv);
            ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 3, 1, &s_scale_cb);
            ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            ID3D11DeviceContext_IASetInputLayout(s_ctx, 0);
            ID3D11DeviceContext_VSSetShader(s_ctx, s_blit_vs, 0, 0);
            ID3D11DeviceContext_GSSetShader(s_ctx, 0, 0, 0);
            ID3D11DeviceContext_PSSetShader(s_ctx, src->depth ? s_zcopy_ps : s_scale_ps, 0, 0);
            ID3D11DeviceContext_Draw(s_ctx, 3, 0);
            ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &none);
            (void)none;
            ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 4, o_rtv, o_dsv);
            if (o_nvp) ID3D11DeviceContext_RSSetViewports(s_ctx, o_nvp, o_vp);
            ID3D11DeviceContext_VSSetShader(s_ctx, o_vs, 0, 0);
            ID3D11DeviceContext_PSSetShader(s_ctx, o_ps, 0, 0);
            ID3D11DeviceContext_GSSetShader(s_ctx, o_gs, 0, 0);
            ID3D11DeviceContext_IASetInputLayout(s_ctx, o_il);
            ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, o_topo);
            ID3D11DeviceContext_OMSetBlendState(s_ctx, o_bs, o_bf, o_sm);
            ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, o_ds, o_sref);
            ID3D11DeviceContext_RSSetState(s_ctx, o_rs);
            ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &o_srv);
            ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 3, 1, &o_cb);
            for (n = 0; n < 4; n++)
                if (o_rtv[n]) ID3D11RenderTargetView_Release(o_rtv[n]);
            if (o_dsv) ID3D11DepthStencilView_Release(o_dsv);
            if (o_vs) ID3D11VertexShader_Release(o_vs);
            if (o_ps) ID3D11PixelShader_Release(o_ps);
            if (o_gs) ID3D11GeometryShader_Release(o_gs);
            if (o_il) ID3D11InputLayout_Release(o_il);
            if (o_bs) ID3D11BlendState_Release(o_bs);
            if (o_ds) ID3D11DepthStencilState_Release(o_ds);
            if (o_rs) ID3D11RasterizerState_Release(o_rs);
            if (o_srv) ID3D11ShaderResourceView_Release(o_srv);
            if (o_cb) ID3D11Buffer_Release(o_cb);
        }
    }
}

/* Show the game's frame: the first bound target, letterboxed in the output, never stretched. */
void xb_gpu_present(XbHostTex *src)
{
    D3D11_VIEWPORT vp;
    float clear[4] = {0, 0, 0, 1};
    float ar = (float)s_game_w / (float)s_game_h, w, h;
    ID3D11ShaderResourceView *srv;
    gpu_init();
    if (GetCurrentThreadId() != s_gfx_thread) xb_stop("d3d_thread", "a frame shown from another thread than the window's", 0);
    xb_flow_frame();
    fcap_present(src);
    if (!src || !src->srv) return;
    w = (float)g_gfx.frame_w;
    h = w / ar;
    if (h > (float)g_gfx.frame_h) {
        h = (float)g_gfx.frame_h;
        w = h * ar;
    }
    vp.TopLeftX = ((float)g_gfx.frame_w - w) * 0.5f;
    vp.TopLeftY = ((float)g_gfx.frame_h - h) * 0.5f;
    vp.Width = w;
    vp.Height = h;
    vp.MinDepth = 0;
    vp.MaxDepth = 1;
    if (s_resize_trace > 0) {
        s_resize_trace--;
        port_log("resize trace: frame %u shown from a %ux%u texture into the %ux%u frame (output %ux%u), viewport %g %g %gx%g\n", s_frames, src->w, src->h, g_gfx.frame_w, g_gfx.frame_h, g_gfx.out_w, g_gfx.out_h,
                 (double)vp.TopLeftX, (double)vp.TopLeftY, (double)vp.Width, (double)vp.Height);
    }
    ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 1, &g_gfx.frame_rtv, 0);
    ID3D11DeviceContext_ClearRenderTargetView(s_ctx, g_gfx.frame_rtv, clear);
    ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
    ID3D11DeviceContext_RSSetState(s_ctx, s_raster);
    srv = src->srv;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &srv);
    ID3D11DeviceContext_PSSetSamplers(s_ctx, 0, 1, &s_sampler);
    ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_IASetInputLayout(s_ctx, 0);
    ID3D11DeviceContext_VSSetShader(s_ctx, s_blit_vs, 0, 0);
    ID3D11DeviceContext_GSSetShader(s_ctx, 0, 0, 0);
    if (s_ramp_on && s_ramp_ps) {   /* the display's gamma ramp (xb_gpu_gamma_ramp) */
        D3D11_MAPPED_SUBRESOURCE m;
        if (s_ramp_dirty && SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_ramp_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            memcpy(m.pData, s_ramp, sizeof s_ramp);
            ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_ramp_cb, 0);
            s_ramp_dirty = 0;
        }
        ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 4, 1, &s_ramp_cb);
        ID3D11DeviceContext_PSSetShader(s_ctx, s_ramp_ps, 0, 0);
    } else {
        ID3D11DeviceContext_PSSetShader(s_ctx, s_blit_ps, 0, 0);
    }
    ID3D11DeviceContext_Draw(s_ctx, 3, 0);
    srv = 0;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &srv);
    if (!g_gfx.headless) {
        present_show();
        gfx_pump();
    }
    window_frame_shown();
    s_frames++, s_draws_last = s_draws - s_draws0, s_draws0 = s_draws;
    {   /* every 5 s: the game's frames a second, the draws of a frame and the longest time between two frames (the numbers a headless run has no "gfx:" line for) */
        static LARGE_INTEGER was, last;
        static unsigned f_was, d_was, n_was, u_was;
        static double longest;
        LARGE_INTEGER now, hz;
        QueryPerformanceCounter(&now), QueryPerformanceFrequency(&hz);
        if (last.QuadPart && (double)(now.QuadPart - last.QuadPart) * 1000.0 / (double)hz.QuadPart > longest) longest = (double)(now.QuadPart - last.QuadPart) * 1000.0 / (double)hz.QuadPart;
        last = now;
        if (!was.QuadPart) was = now, f_was = s_frames, d_was = s_draws, n_was = s_draws_nothing, u_was = s_up_verts;
        else if (now.QuadPart - was.QuadPart >= 5 * hz.QuadPart && s_frames > f_was) {
            double sec = (double)(now.QuadPart - was.QuadPart) / (double)hz.QuadPart;
            port_log("d3d frames: %.1f a second over %.1f s to frame %u, %u draws a frame (%u of them change nothing and are left), %u vertices uploaded a frame, longest frame %.1f ms, draw shortcuts %s\n",
                     (s_frames - f_was) / sec, sec, s_frames, (s_draws - d_was) / (s_frames - f_was), (s_draws_nothing - n_was) / (s_frames - f_was), (s_up_verts - u_was) / (s_frames - f_was), longest, xb_draw_fast() ? "on" : "off (XB_DRAW_FAST=0)");
            was = now, f_was = s_frames, d_was = s_draws, n_was = s_draws_nothing, u_was = s_up_verts, longest = 0;
        }
    }
}

/* A 16-bit float colour as a byte for a dump, clamped to 0..1 as the screen shows it */
static unsigned char half_byte(unsigned h)
{
    unsigned e = (h >> 10) & 31, m = h & 1023;
    float f;
    if (h & 0x8000) return 0;
    if (e == 31) return 255;
    f = e ? ldexpf((float)(m | 1024), (int)e - 25) : ldexpf((float)m, -24);
    return f >= 1.0f ? 255 : (unsigned char)(f * 255.0f + 0.5f);
}

/* Writes a texture as a 24-bit BMP (a readback, so a test run can show the picture without a window). 8-bit RGBA or 16-bit float RGBA. */
static int dump_tex(ID3D11Texture2D *tex, const char *path)
{
    D3D11_TEXTURE2D_DESC d;
    ID3D11Texture2D *st = 0;
    D3D11_MAPPED_SUBRESOURCE m;
    FILE *f;
    unsigned y, x, row;
    unsigned char hdr[54], *line;
    unsigned long sz;
    int done = 0;
    ID3D11Texture2D_GetDesc(tex, &d);
    d.Usage = D3D11_USAGE_STAGING;
    d.BindFlags = 0;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (FAILED(ID3D11Device_CreateTexture2D(s_dev, &d, 0, &st))) return 0;
    ID3D11DeviceContext_CopyResource(s_ctx, (ID3D11Resource *)st, (ID3D11Resource *)tex);
    if (FAILED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)st, 0, D3D11_MAP_READ, 0, &m))) {
        ID3D11Texture2D_Release(st);
        return 0;
    }
    row = (d.Width * 3 + 3) & ~3u;
    line = (unsigned char *)calloc(1, row);
    f = fopen(path, "wb");
    if (f && line) {
        sz = 54 + row * d.Height;
        memset(hdr, 0, sizeof hdr);
        hdr[0] = 'B'; hdr[1] = 'M';
        memcpy(hdr + 2, &sz, 4);
        hdr[10] = 54; hdr[14] = 40;
        memcpy(hdr + 18, &d.Width, 4);
        memcpy(hdr + 22, &d.Height, 4);
        hdr[26] = 1; hdr[28] = 24;
        fwrite(hdr, 1, 54, f);
        for (y = d.Height; y-- > 0;) {
            const unsigned char *px = (const unsigned char *)m.pData + y * m.RowPitch;
            const unsigned short *hp = (const unsigned short *)px;
            for (x = 0; x < d.Width; x++)   /* RGBA texture to BGR file */
                if (d.Format == DXGI_FORMAT_R16G16B16A16_FLOAT) {
                    line[x * 3] = half_byte(hp[x * 4 + 2]);
                    line[x * 3 + 1] = half_byte(hp[x * 4 + 1]);
                    line[x * 3 + 2] = half_byte(hp[x * 4]);
                } else {
                    line[x * 3] = px[x * 4 + 2];
                    line[x * 3 + 1] = px[x * 4 + 1];
                    line[x * 3 + 2] = px[x * 4];
                }
            fwrite(line, 1, row, f);
        }
        done = 1;
    }
    if (f) fclose(f);
    free(line);
    ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)st, 0);
    ID3D11Texture2D_Release(st);
    return done;
}

void xb_gpu_dump(XbHostTex *t, const char *path)
{
    if (t && t->tex) dump_tex(t->tex, path);
}

int xb_gpu_capture(const char *path) { return g_gfx.frame ? dump_tex(g_gfx.frame, path) : 0; }

XbHostTex *xb_gpu_main_target(void) { return s_target[0]; }
unsigned xb_gpu_draw_count(int frame) { return frame ? s_draws_last : s_draws; }   /* frame: the draws of the frame being shown */
unsigned xb_gpu_frame_count(void) { return s_frames; }
