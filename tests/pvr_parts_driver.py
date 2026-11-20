"""tests/test_pvr_parts.py's driver: the C program it builds with port/pvr/pvr_parts.c and runs, hand-made ticks
of part draws in guest RAM and the scene vertices each gives (apart from the test only for its 400-line cap).
"""

DRIVER_C = r"""#include <stdio.h>
#include <string.h>
#include <windows.h>
#include "pvr_int.h"
#include "platform.h"
void port_log(const char *fmt, ...) { (void)fmt; }
PvrState g_pvr;
static u32 g_slot;
u32 pvr_slot(void) { return g_slot; }
static int g_truth, g_frame;
int pvr_blend_truth_skip(void) { return g_truth && (g_frame & 1); }
f32 pvr_blend_truth_t(f32 t) { return g_truth ? 0.5f : t; }
void port_part_begin(const void *obj, const void *part, u32 stamp_at, u32 world_at, const u32 *live);
void port_part_end(const u32 *live);
void pvr_parts_lists(const u32 *ptrs);
void pvr_parts_scene(void);

#define RAM 0x8c000000u
static u32 g_ptrs[PVRX_LISTS], g_w, g_n;
static u32 g_addr[64];
static u8 g_obj[4][64], g_part[4][128]; /* stamp at obj + 0x24, world matrix at part + 0x28 */

static void begin_tick(void) { pvr_parts_scene(); g_w = 0x1000u, g_n = 0; g_ptrs[0] = RAM + g_w; }

/* a vertex whose attribute words (after x, y and z) are key, 0, 0, 0 */
static void vtxk(f32 x, f32 y, f32 z, u32 key)
{
    u32 *w = (u32 *)(uintptr_t)(RAM + g_w);
    f32 f[3] = {x, y, z};
    w[0] = 0xe0000000u;
    memcpy(&w[1], f, sizeof f);
    w[4] = key, w[5] = w[6] = w[7] = 0u;
    g_addr[g_n++] = RAM + g_w;
    g_w += 32u;
    g_ptrs[0] = RAM + g_w;
}

static void vtx(f32 x, f32 y, f32 z) { vtxk(x, y, z, 0u); }

/* a part draw writing n vertices, x step 10 from x0, at y and 1/w z */
static void part(int obj, int prt, s16 stamp, u32 n, f32 x0, f32 y, f32 z)
{
    memcpy(&g_obj[obj][0x24], &stamp, sizeof stamp);
    port_part_begin(g_obj[obj], g_part[prt], 0x24u, 0x28u, NULL);
    for (u32 i = 0; i < n; i++) vtx(x0 + 10.0f * (f32)i, y, z);
    port_part_end(NULL);
}

static PvrVtx g_v[64];
static u32 g_seq[64];
static PvrScene g_s;

/* the render: the parse tags each vertex, one scene vertex each */
static void render(void)
{
    g_s.vtx = g_v, g_s.seq = g_seq, g_s.nvtx = g_n, g_s.vcap = g_s.seq_cap = 64u;
    for (u32 i = 0; i < g_n; i++) {
        const f32 *f = (const f32 *)(uintptr_t)(g_addr[i] + 4u);
        pvr_parts_vertex(0u, g_addr[i]);
        memset(&g_v[i], 0, sizeof g_v[i]);
        g_v[i].x = f[0], g_v[i].y = f[1], g_v[i].z = f[2];
        g_seq[i] = pvr_parts_seq();
    }
    pvr_parts_from(&g_s);
}

static void show(const char *name, f32 t)
{
    u32 n = 0;
    const PvrVtx *v = pvr_parts_blend(&g_s, t, &n);
    printf("%s %d:", name, g_s.has_from);
    for (u32 i = 0; v && i < g_s.nvtx; i++) printf(" %.4f,%.4f,%.5f", v[i].x, v[i].y, v[i].z);
    printf("\n");
}

int main(void)
{
    if (!VirtualAlloc((void *)(uintptr_t)RAM, 0x10000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        printf("noram\n");
        return 0;
    }
    pvr_parts_lists(g_ptrs);
    pvr_parts_blend_option("on");
    /* tick 1: A (obj 0 part 0) one vertex; a 2D vertex; C (obj 1 part 1) 2 vertices; D (obj 2 part 2) stamp 7;
       E (obj 3 part 3) drawn twice */
    begin_tick();
    part(0, 0, 5, 1, 100.0f, 50.0f, 0.1f);
    vtx(50.0f, 50.0f, 1.0f);
    part(1, 1, 5, 2, 300.0f, 60.0f, 0.5f);
    part(2, 2, 7, 1, 400.0f, 70.0f, 0.5f);
    part(3, 3, 5, 1, 10.0f, 80.0f, 0.5f);
    part(3, 3, 5, 1, 300.0f, 80.0f, 0.5f);
    render();
    /* tick 2: A moved and nearer; the 2D vertex; C has 3 vertices now; D's stamp is 8; E twice, each 10 on; B new */
    begin_tick();
    part(0, 0, 5, 1, 200.0f, 50.0f, 0.2f);
    vtx(50.0f, 50.0f, 1.0f);
    part(1, 1, 5, 3, 310.0f, 60.0f, 0.5f);
    part(2, 2, 8, 1, 410.0f, 70.0f, 0.5f);
    part(3, 3, 5, 1, 20.0f, 80.0f, 0.5f);
    part(3, 3, 5, 1, 310.0f, 80.0f, 0.5f);
    part(0, 1, 5, 4, 500.0f, 90.0f, 0.5f);
    render();
    printf("hold %d\n", pvr_parts_hold_frame());
    show("t0", 0.0f);
    show("t05", 0.5f);
    show("t1", 1.0f);
    /* E drawn twice again, 10 on, but in the other order (split-screen views drawn the other way round) */
    begin_tick();
    part(3, 3, 5, 1, 320.0f, 80.0f, 0.5f);
    part(3, 3, 5, 1, 30.0f, 80.0f, 0.5f);
    render();
    show("swap", 0.5f);
    /* --interp-truth: frames 0..3 with A on its way from tick 2's x 200: 210, 220, 230, 240 */
    g_truth = 1;
    for (g_frame = 0; g_frame < 4; g_frame++) {
        begin_tick();
        part(0, 0, 5, 1, 210.0f + 10.0f * (f32)g_frame, 50.0f, 0.5f);
        render();
    }
    show("truth", 0.5f);
    printf("paired %d\n", pvr_parts_hold_frame());
    /* turns: F (obj 0 part 2) x 0, 10, 0; G (obj 1 part 2) 0, 10, 20; H (obj 2 part 3) 0, 0.5, 0 */
    g_truth = 0;
    for (int k = 0; k < 3; k++) {
        const f32 turn[3] = {0.0f, 10.0f, 0.0f}, wobble[3] = {0.0f, 0.5f, 0.0f};
        begin_tick();
        part(0, 2, 5, 1, turn[k], 50.0f, 0.5f);
        part(1, 2, 5, 1, 10.0f * (f32)k, 60.0f, 0.5f);
        part(2, 3, 5, 1, wobble[k], 70.0f, 0.5f);
        render();
    }
    printf("turned %d %d\n", pvr_parts_hold_frame(), pvr_parts_holds());
    show("turn", 0.5f);
    show("turn1", 1.0f);
    /* culled: K (obj 0 part 3) 12 vertices on a grid, unique attribute words; then 20 on with two culled and one new */
    for (int k = 0; k < 2; k++) {
        s16 st = 9;
        begin_tick();
        memcpy(&g_obj[0][0x24], &st, sizeof st);
        port_part_begin(g_obj[0], g_part[3], 0x24u, 0x28u, NULL);
        for (u32 i = 0; i < 12u; i++)
            if (!k || (i != 2u && i != 6u))
                vtxk(100.0f + 20.0f * (f32)k + 10.0f * (f32)(i % 4u), 100.0f + 10.0f * (f32)(i / 4u), 0.5f, 1u + i);
        if (k) vtxk(150.0f, 150.0f, 0.5f, 99u);
        port_part_end(NULL);
        render();
    }
    printf("culled %d\n", pvr_parts_hold_frame());
    show("cull", 0.5f);
    /* flat: P (obj 2 part 2) a 4x3 grid at 1/w 0.5, unique attribute words, and 4 corners at 1/w 0.25, each written twice
       with one key; then 20 on in x, the grid vertex 5 culled */
    for (int k = 0; k < 2; k++) {
        s16 st = 9;
        begin_tick();
        memcpy(&g_obj[2][0x24], &st, sizeof st);
        port_part_begin(g_obj[2], g_part[2], 0x24u, 0x28u, NULL);
        for (u32 i = 0; i < 12u; i++)
            if (!k || i != 5u) vtxk(100.0f + 20.0f * (f32)k + 10.0f * (f32)(i % 4u), 100.0f + 10.0f * (f32)(i / 4u), 0.5f, 1u + i);
        for (u32 j = 0; j < 8u; j++) vtxk(100.0f + 20.0f * (f32)k + 10.0f * (f32)(j / 2u), 140.0f, 0.25f, 50u + j / 2u);
        port_part_end(NULL);
        render();
    }
    printf("flatheld %d\n", pvr_parts_hold_frame());
    show("flat", 0.5f);
    /* split screen: V (obj 1 part 3) in two views, a render (slot) each with its clip: 100, 110 and 400, 390 */
    for (int k = 0; k < 2; k++)
        for (u32 view = 0; view < 2u; view++) {
            g_slot = view;
            g_pvr.clip[0] = 320u * view, g_pvr.clip[2] = 320u * view + 319u, g_pvr.clip[3] = 479u;
            begin_tick();
            part(1, 3, 5, 4, view ? 400.0f - 10.0f * (f32)k : 100.0f + 10.0f * (f32)k, 50.0f, 0.5f);
            render();
            if (k) show(view ? "view1" : "view0", 0.5f);
        }
    printf("views %d\n", pvr_parts_hold_frame());
    g_slot = 0, g_pvr.clip[0] = 320u, g_pvr.clip[2] = 639u; /* view 1's clip in slot 0 */
    begin_tick();
    part(1, 3, 5, 4, 120.0f, 50.0f, 0.5f);
    render();
    printf("reclip %d\n", pvr_parts_hold_frame());
    /* near clamp: N (obj 2 part 1) 4 vertices; then two clamped at 1/w 0.1 far off the screen; then none clamped */
    for (int k = 0; k < 3; k++) {
        const f32 x[3][4] = {{100.0f, 110.0f, 120.0f, 130.0f}, {120.0f, 130.0f, -9000.0f, 9000.0f}, {125.0f, 135.0f, 140.0f, 150.0f}};
        const f32 y[3][4] = {{100.0f, 100.0f, 100.0f, 100.0f}, {100.0f, 100.0f, 300.0f, 300.0f}, {100.0f, 100.0f, 100.0f, 100.0f}};
        const f32 z[3][4] = {{0.05f, 0.05f, 0.05f, 0.05f}, {0.06f, 0.06f, 0.1f, 0.1f}, {0.06f, 0.06f, 0.06f, 0.06f}};
        s16 st = 5;
        begin_tick();
        memcpy(&g_obj[2][0x24], &st, sizeof st);
        port_part_begin(g_obj[2], g_part[1], 0x24u, 0x28u, NULL);
        for (u32 i = 0; i < 4u; i++) vtx(x[k][i], y[k][i], z[k][i]);
        port_part_end(NULL);
        if (k == 1) vtx(50.0f, 50.0f, 1.0f); /* 2D, nearer than the clamp */
        render();
        if (k) show(k == 1 ? "clamp" : "clamped", 0.5f);
    }
    /* same count, other order: R (obj 3 part 2) 12 corners on a grid, each drawn twice (two polygons), unique attribute
       words; then 20 on, the same drawn from the sixth and the eighth on (clipped otherwise to the same count) */
    for (int k = 0; k < 2; k++) {
        s16 st = 5;
        begin_tick();
        memcpy(&g_obj[3][0x24], &st, sizeof st);
        port_part_begin(g_obj[3], g_part[2], 0x24u, 0x28u, NULL);
        for (u32 j = 0; j < 24u; j++) {
            u32 i = k ? (j + (j < 12u ? 5u : 7u)) % 12u : j % 12u;
            vtxk(100.0f + 20.0f * (f32)k + 10.0f * (f32)(i % 4u), 100.0f + 10.0f * (f32)(i / 4u), 0.5f, 1u + i + j / 12u * 100u);
        }
        port_part_end(NULL);
        render();
    }
    printf("reordered %d\n", pvr_parts_hold_frame());
    show("reorder", 0.5f);
    /* two pieces: T (obj 1 part 0) a body of 16 on a grid and a rotor of 8 round its middle, unique attribute words;
       then the body 20 on with one culled, the rotor 20 on and a quarter turn round */
    for (int k = 0; k < 2; k++) {
        const f32 rx[8] = {6.0f, 4.243f, 0.0f, -4.243f, -6.0f, -4.243f, 0.0f, 4.243f}, ry[8] = {0.0f, 4.243f, 6.0f, 4.243f, 0.0f, -4.243f, -6.0f, -4.243f};
        s16 st = 9;
        begin_tick();
        memcpy(&g_obj[1][0x24], &st, sizeof st);
        port_part_begin(g_obj[1], g_part[0], 0x24u, 0x28u, NULL);
        for (u32 i = 0; i < 16u; i++)
            if (!k || i != 5u) vtxk(200.0f + 20.0f * (f32)k + 10.0f * (f32)(i % 4u), 200.0f + 10.0f * (f32)(i / 4u), 0.5f, 1u + i);
        for (u32 j = 0; j < 8u; j++)
            vtxk(215.0f + 20.0f * (f32)k + (k ? -ry[j] : rx[j]), 215.0f + (k ? rx[j] : ry[j]), 0.5f, 50u + j);
        port_part_end(NULL);
        render();
    }
    printf("twopiece %d\n", pvr_parts_hold_frame());
    show("two", 0.5f);
    /* a view jump: W (obj 3 part 0) 64 vertices, then all 100 on, then 10 on */
    for (int k = 0; k < 3; k++) {
        const f32 x0[3] = {0.0f, 100.0f, 110.0f};
        begin_tick();
        part(3, 0, 5, 64, x0[k], 50.0f, 0.5f);
        render();
        if (k) printf(k == 1 ? "jump %d\n" : "glide %d\n", pvr_parts_hold_frame());
    }
    /* clamped in both ticks: M (obj 2 part 0), two vertices at the clamp, 1/w 0.1, far off the screen */
    for (int k = 0; k < 2; k++) {
        const f32 x[2][4] = {{100.0f, 110.0f, -9000.0f, 9000.0f}, {120.0f, 130.0f, -8000.0f, 9400.0f}};
        const f32 y[2][4] = {{100.0f, 100.0f, 300.0f, 300.0f}, {100.0f, 100.0f, 320.0f, 320.0f}};
        s16 st = 5;
        begin_tick();
        memcpy(&g_obj[2][0x24], &st, sizeof st);
        port_part_begin(g_obj[2], g_part[0], 0x24u, 0x28u, NULL);
        for (u32 i = 0; i < 4u; i++) vtx(x[k][i], y[k][i], i < 2u ? 0.05f : 0.1f);
        port_part_end(NULL);
        vtx(50.0f, 50.0f, 1.0f); /* 2D, nearer than the clamp */
        render();
    }
    show("both", 0.5f);
    return 0;
}
"""
