/* game.json part_draw: which object part wrote which of a scene's vertices, the ground for in-between frames built
 * from game state (each part re-posed between two ticks with one matrix, everything else drawn as the tick drew it).
 *
 * The game's per-part 3D draw is wrapped (port/tools/part_draw.py): port_part_begin and _end bracket each call, and the
 * list write positions (the game's own live ones, part_draw.lists, or the SDK's: pvr_parts_lists) say how far each got.
 * The lists are parsed only at the render (ta_parse.c), so a vertex is tied to its part there by its RAM address:
 * pvr_parts_vertex walks each list's part ranges in step with the parse.
 *
 *   --interp-parts-log on  one line every 600 scenes: the part draws, how many wrote to a list, and how many of the
 *                          scenes' polygon vertices fell inside a part's range (the rest: 2D, text, HUD, effects); and
 *                          the fit check below
 *
 * The fit check (log on only): a rigid part's vertices, as (x w, y w, w) with w = 1/z, are one affine map of its model
 * space in every tick, so between two ticks they are one affine map of each other, whatever the game's matrices and
 * screen mapping are. For each part that moved and kept its vertex count, that 3x4 map is fitted from the vertices
 * (least squares) and the worst vertex's distance in pixels from where the map puts it is counted under 0.5 and 2.
 * If nearly all fit, an in-between frame needs no game matrix: each part is its own vertices through the fitted map
 * blended from identity. Also tried with w = z, in case the TA's z is w itself. On the user's fight recording about 90%
 * of moved parts fit to 0.5 px and 95% to 2 px; the rest were vertices clamped at the near plane and weighted joints.
 * So a part keeps its vertices' order from tick to tick, and the in-between frames need no fit:
 *
 *   --interp-parts on      in-between frames from the parts (with --interp-scene): each vertex of a part that kept its
 *                          object, stamp and vertex count is blended from its own vertex a tick before, in (x w, y w,
 *                          w) with w = 1/z, so it moves as the part and the camera moved, in perspective. A part the
 *                          game culled or clipped to another count: each vertex at one place in it both ticks from
 *                          its own place then, the rest by its map no further than those moved, else as now
 *                          (pvr_parts_map_places in pvr_parts_fit.c). Vertices of no part (2D,
 *                          text, HUD, fades) and of a part that is new, gone or unmatched are drawn as the latest tick
 *                          drew them. A vertex the game clamped at the near plane (pvr_parts_near: no point
 *                          of its part) moves across the screen, still clamped: from its own place a tick before if
 *                          it was clamped then too, by its part's map with a mapped part, else it is drawn as now
 *                          (left as now, a big polygon near the eye swung its edge across the view while its other
 *                          vertices moved half way). Colours and texture coordinates are the latest tick's. No game code
 *                          runs and nothing is paired by screen position, so nothing can ghost. On by default
 *                          for a game with part_draw (PORT_PART_DRAW); off: screen blending (pvr_blend.c)
 *
 * What a blend can't show is held (holds): a scene where half the parts' vertices are new or gone (no same part a tick before: a cut, a menu's face)
 * blends by screen as with parts off (a split screen's frame gets none), as does one where more than half of the paired
 * vertices came 64 pixels or more in a tick (the view jumped: a respawn, another camera; a blend across it swings the
 * whole scene through places it never was), and a part that turned back the way it came (a pulse
 * at its peak, a blow that recoils) is drawn where it was a tick before: a straight line through the turn puts it where
 * it never was. Checked against the real frames between with port/tools/interp_truth.py. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json part_draw */
#endif
#ifndef PORT_PART_DRAW
#define PORT_PART_DRAW 0
#endif

#define MAX_PARTS 4096u
#define MAX_VTX 131072u /* polygon vertices of parts in a scene (a fight has about 14,000) */
#define NONE 0xffffffffu
#define LOG_EVERY 600u
#define RAM_CACHED 0x8c000000u
#define RAM_MASK 0x00ffffffu
#define HOLD_SHARE 2u /* 1 in HOLD_SHARE of a scene's part vertices new or gone: no in-between frames */
#define MAP_MIN 8u   /* a culled part's unique pairs, at least, and its map within 2 px for 9 in 10 */
#define TURN_COS -0.5 /* a part's two moves (each 1 px or more, rms) more than 120 degrees apart: it turned */
#define CUT_PX 64.0f /* more than half of a scene's paired vertices (CUT_MIN or more) this far from a tick before: a cut */
#define CUT_MIN 64u

typedef struct Part {
    const void *obj, *part;
    s32 stamp;
    f32 world[16];
    u32 from[PVRX_LISTS], to[PVRX_LISTS];
    u32 first, count; /* its vertices in the sorted store, for the fit check */
    f32 cx, cy;       /* their middle on the screen: a part drawn more than once pairs with the nearest */
} Part;

typedef struct Seen {
    u32 part, pos; /* pos: its place in the store sorted by part */
    f32 x, y, z;
    u32 key; /* the attribute words after x, y and z (texture coordinates, colours), hashed */
} Seen;

static Part g_parts[MAX_PARTS];
static u32 g_nparts, g_depth, g_over, g_log, g_nseen, g_seq, g_pair[MAX_PARTS];
int g_parts_blend = PORT_PART_DRAW;
static Seen g_seen[MAX_VTX];
static f32 g_vtx[MAX_VTX][3];
static u32 g_key[MAX_VTX], g_ia[MAX_VTX], g_ib[MAX_VTX];
static PartMap g_map[MAX_PARTS];
static u8 g_mapped[MAX_PARTS]; /* paired through g_map: another count */
static u32 g_hold_frame, g_hold_any, g_holds; /* any: a slot of the frame holds */
/* A render's parts as the same render a tick later pairs with them: one per slot of the frame (a split screen's views,
 * pvr_blend_hold.c), the first here, the others made when first used. */
typedef struct Prev {
    Part part[MAX_PARTS];
    f32 vtx[MAX_VTX][3], vtx2[MAX_VTX][3]; /* 2: two ticks before */
    u32 key[MAX_VTX], first2[MAX_PARTS], pair_was[MAX_PARTS], n, nvtx, clip[4];
    u8 mapped_was[MAX_PARTS];
} Prev;
static Prev g_prev0, *g_slot_prev[PVR_SLOTS] = {&g_prev0}, *g_pv = &g_prev0;
static u8 g_turned[MAX_PARTS], g_ident[MAX_PARTS], g_used[MAX_PARTS]; /* ident, used: the same part a tick before */
static f32 g_clamp; /* the game's near clamp (pvr_parts_near): such a vertex is no point of its part */
static const u32 *g_ptrs; /* the SDK's write positions, one per list (uncached addresses) */
static u32 g_at[PVRX_LISTS], g_last_list = PVRX_LISTS, g_last_addr;
static struct {
    u32 scenes, draws, wrote, nested, vertices, tagged, most, moved, fit[2][2], hist[5], shown, held, turned, recount, mapped;
} g_n;

int pvr_parts_option(const char *value) { return g_log = !strcmp(value, "on"), 2; }
int pvr_parts_blend_option(const char *value) { return g_parts_blend = !strcmp(value, "on"), 2; }
u32 pvr_parts_seq(void) { return g_seq; }
int pvr_parts_hold_frame(void) { return g_parts_blend && g_hold_any; }
int pvr_parts_holds(void) { return g_parts_blend && g_holds; }

static u32 cached(u32 a) { return RAM_CACHED | (a & RAM_MASK); }

void pvr_parts_lists(const u32 *ptrs) { g_ptrs = ptrs; }

void port_part_begin(const void *obj, const void *part, u32 stamp_at, u32 world_at, const u32 *live)
{
    Part *p;
    g_n.draws++;
    if (g_depth++) {
        g_n.nested++;
        return;
    }
    if (!g_ptrs || g_nparts >= MAX_PARTS) {
        g_over += g_ptrs != NULL;
        return;
    }
    p = &g_parts[g_nparts];
    p->obj = obj, p->part = part;
    p->stamp = *(const s16 *)((const u8 *)obj + stamp_at);
    memcpy(p->world, (const u8 *)part + world_at, sizeof p->world);
    for (u32 l = 0; l < PVRX_LISTS; l++) p->from[l] = cached((live ? live : g_ptrs)[l]);
}

void port_part_end(const u32 *live)
{
    Part *p = &g_parts[g_nparts];
    int wrote = 0;
    if (!g_depth || --g_depth || !g_ptrs || g_nparts >= MAX_PARTS) return;
    for (u32 l = 0; l < PVRX_LISTS; l++) {
        p->to[l] = cached((live ? live : g_ptrs)[l]);
        wrote |= p->to[l] > p->from[l];
    }
    if (wrote) g_nparts++, g_n.wrote++;
}

/* ta_parse.c, for each polygon vertex in list order: the part whose range holds it (1-based), 0 for none. */
u32 pvr_parts_vertex(u32 list, u32 addr)
{
    u32 *at = &g_at[list < PVRX_LISTS ? list : 0];
    g_seq = 0;
    if (list >= PVRX_LISTS) return 0;
    if (list != g_last_list || addr < g_last_addr) *at = 0; /* a list or a pass starts again */
    g_last_list = list, g_last_addr = addr;
    g_n.vertices++;
    while (*at < g_nparts && (g_parts[*at].to[list] <= addr || g_parts[*at].to[list] <= g_parts[*at].from[list]))
        ++*at;
    if (*at >= g_nparts || addr < g_parts[*at].from[list]) return 0;
    g_n.tagged++;
    if ((g_log || g_parts_blend) && g_nseen < MAX_VTX) { /* the TA's x, y and z follow the control word */
        const f32 *f = (const f32 *)(uintptr_t)addr;
        const u32 *w = (const u32 *)(uintptr_t)addr; /* a TA vertex is 8 words at least */
        g_seen[g_nseen++] = (Seen){*at, 0u, f[1], f[2], f[3], w[4] * 0x9e3779b1u ^ w[5] ^ w[6] * 0x85ebca6bu};
        g_seq = g_nseen;
    } else if (g_nseen >= MAX_VTX)
        g_over++;
    return *at + 1u;
}

/* ---- the fit check ---- */

/* The worst pixel distance of a part's vertices a tick before from its vertices now through the fitted 3x4 map. */
static double fit_error(const Part *now, const Part *was, int wz, u32 *off)
{
    PartMap m;
    const f32(*a)[3] = (const f32(*)[3])&g_vtx[now->first], (*b)[3] = (const f32(*)[3])&g_pv->vtx[was->first];
    return pvr_parts_fit(a, b, NULL, NULL, now->count, wz, &m) ? pvr_parts_fit_worst(&m, a, b, NULL, NULL, now->count, off)
                                                               : 1e9;
}

/* A part culled or clipped to another count than a tick before: by place, the rest by map (pvr_parts_map_places). */
static u32 g_own[MAX_VTX]; /* a mapped part's vertex paired by place: its own vertex a tick before */
static int map_part(const Part *now, const Part *was, PartMap *m)
{
    static u32 oa[MAX_VTX], ob[MAX_VTX];
    u32 n = pvr_parts_map_places((const f32(*)[3])&g_vtx[now->first], (const f32(*)[3])&g_pv->vtx[was->first], &g_key[now->first],
                                 now->count, &g_pv->key[was->first], was->count, g_clamp, MAP_MIN, g_ia, g_ib, MAX_VTX, m, oa, ob);
    for (u32 j = 0; j < n; j++) g_own[now->first + g_ia[j]] = was->first + g_ib[j];
    return m->lim >= 0.0f || n * 10u >= now->count * 9u; /* no map: 9 in 10 paired by place */
}

/* This scene's vertices sorted by part, and each part paired with the same object part a tick before: of the unpaired
 * draws with the same object, part and stamp, the one whose middle was nearest on the screen (the same model in two
 * split-screen views, or two things drawn through one reused node, would otherwise pair by draw order). */
static void pair_parts(void)
{
    static u32 start[MAX_PARTS + 1u];
    memset(start, 0, (g_nparts + 1u) * sizeof start[0]);
    for (u32 k = 0; k < g_nseen; k++) start[g_seen[k].part + 1u]++;
    for (u32 p = 0; p < g_nparts; p++) { /* counts to firsts */
        g_parts[p].first = start[p];
        g_parts[p].count = start[p + 1u];
        start[p + 1u] += start[p];
    }
    for (u32 k = 0; k < g_nseen; k++) {
        g_seen[k].pos = start[g_seen[k].part]++;
        memcpy(g_vtx[g_seen[k].pos], &g_seen[k].x, sizeof g_vtx[0]);
        g_key[g_seen[k].pos] = g_seen[k].key;
    }
    for (u32 p = 0; p < g_nparts; p++) {
        Part *now = &g_parts[p];
        now->cx = now->cy = 0.0f;
        for (u32 k = 0; k < now->count; k++) now->cx += g_vtx[now->first + k][0], now->cy += g_vtx[now->first + k][1];
        if (now->count) now->cx /= (f32)now->count, now->cy /= (f32)now->count;
    }
    memset(g_used, 0, g_pv->n);
    memset(g_own, 0xff, g_nseen * sizeof g_own[0]);
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p];
        g_pair[p] = NONE, g_mapped[p] = 0;
        u32 best = NONE;
        f32 dist = 0.0f;
        for (u32 q = 0; q < g_pv->n; q++) {
            const Part *was = &g_pv->part[q];
            f32 dx = was->cx - now->cx, dy = was->cy - now->cy;
            if (g_used[q] || was->obj != now->obj || was->part != now->part || was->stamp != now->stamp) continue;
            if (best == NONE || dx * dx + dy * dy < dist) best = q, dist = dx * dx + dy * dy;
        }
        if ((g_ident[p] = best != NONE) != 0) {
            const Part *was = &g_pv->part[best];
            g_used[best] = 1;
            if (was->count == now->count && pvr_parts_in_order((const f32(*)[3])&g_vtx[now->first], /* not clipped otherwise */
                                                              (const f32(*)[3])&g_pv->vtx[was->first], now->count, g_clamp,
                                                              g_ia, g_ib))
                g_pair[p] = best;
            else if (g_n.recount++, map_part(now, was, &g_map[p])) g_pair[p] = best, g_mapped[p] = 1, g_n.mapped++;
        }
    }
}

static void check(void)
{
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p], *was = g_pair[p] != NONE ? &g_pv->part[g_pair[p]] : NULL;
        if (!was || g_mapped[p] || now->count < 4u || !memcmp(&g_vtx[now->first], &g_pv->vtx[was->first], now->count * sizeof g_vtx[0]))
            continue;
        g_n.moved++;
        for (int wz = 0; wz < 2; wz++) {
            u32 off = 0;
            double e = fit_error(now, was, wz, &off);
            g_n.fit[wz][0] += e <= 0.5;
            g_n.fit[wz][1] += e <= 2.0;
            if (!wz) g_n.hist[e <= 0.5 ? 0 : e <= 2 ? 1 : e <= 8 ? 2 : e <= 64 ? 3 : 4]++;
            if (!wz && e > 2.0 && g_n.shown < 8u) { /* what a part that doesn't fit looks like */
                f32 lo[3] = {1e30f, 1e30f, 1e30f}, hi[3] = {-1e30f, -1e30f, -1e30f};
                for (u32 k = 0; k < now->count; k++)
                    for (int r = 0; r < 3; r++) {
                        lo[r] = fminf(lo[r], g_vtx[now->first + k][r]);
                        hi[r] = fmaxf(hi[r], g_vtx[now->first + k][r]);
                    }
                g_n.shown++;
                port_log("parts: no fit: obj %p part %p stamp %d, %u vertices, %u over 2 px, worst %.1f px; "
                         "x %.0f-%.0f y %.0f-%.0f 1/w %.4g-%.4g\n", now->obj, now->part, (int)now->stamp,
                         now->count, off, e, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]);
            }
        }
    }
}

/* The holds (see the top): whether this scene holds, and which parts turned since the two ticks before. */
static void holds(void)
{
    u32 kept = 0, changed = 0, jumped = 0, seen = 0;
    g_holds = 0;
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p];
        u32 q = g_pair[p], r = q != NONE && !g_mapped[p] && !g_pv->mapped_was[q] ? g_pv->pair_was[q] : NONE; /* same counts */
        double d = 0.0, n1 = 0.0, n2 = 0.0;
        g_turned[p] = 0;
        if (g_ident[p]) kept += now->count; /* paired, or the same part clipped past a map: drawn as now, no cut */
        else changed += now->count;
        for (u32 k = 0; r != NONE && k < now->count; k++) {
            const f32 *a = g_vtx[now->first + k], *b = g_pv->vtx[g_pv->part[q].first + k], *c = g_pv->vtx2[g_pv->first2[r] + k];
            for (int i = 0; i < 2 && a[2] != g_clamp && b[2] != g_clamp && c[2] != g_clamp; i++) { /* across, down */
                double v1 = b[i] - c[i], v2 = a[i] - b[i];
                d += v1 * v2, n1 += v1 * v1, n2 += v2 * v2;
            }
        }
        g_turned[p] = r != NONE && n1 >= now->count && n2 >= now->count && d < TURN_COS * sqrt(n1 * n2);
        for (u32 k = 0; q != NONE && k < now->count; k++) { /* how far its vertices came, by pair or by map */
            const f32 *a = g_vtx[now->first + k], *b;
            f32 m[3] = {0.0f, 0.0f, 0.0f};
            if (a[2] == g_clamp || (g_mapped[p] && g_own[now->first + k] == NONE && !pvr_parts_map(&g_map[p], a, m)))
                continue;
            b = !g_mapped[p] ? g_pv->vtx[g_pv->part[q].first + k] : g_own[now->first + k] != NONE ? g_pv->vtx[g_own[now->first + k]] : m;
            if (b[2] == g_clamp) continue;
            seen++;
            jumped += fabsf(a[0] - b[0]) >= CUT_PX || fabsf(a[1] - b[1]) >= CUT_PX;
        }
        g_holds += g_turned[p];
    }
    for (u32 q = 0; q < g_pv->n; q++) changed += g_used[q] ? 0u : g_pv->part[q].count;
    g_hold_frame = (changed && changed * HOLD_SHARE >= kept + changed) || (seen >= CUT_MIN && jumped * 2u > seen);
    g_hold_any = (pvr_slot() ? g_hold_any : 0u) | g_hold_frame;
    g_n.held += g_hold_frame, g_n.turned += g_holds;
}

/* The render's slot (pvr_slot): its parts a tick before. One drawn with another clip than then is another view there
 * now (a split screen's views changed): nothing pairs. */
static int use_slot(void)
{
    u32 i = pvr_slot();
    if (!g_slot_prev[i] && (g_slot_prev[i] = calloc(1, sizeof(Prev))) == NULL) return 0;
    g_pv = g_slot_prev[i];
    if (memcmp(g_pv->clip, g_pvr.clip, sizeof g_pv->clip)) g_pv->n = g_pv->nvtx = 0;
    memcpy(g_pv->clip, g_pvr.clip, sizeof g_pv->clip);
    return 1;
}

/* ta_parse.c, a scene parsed (the game drew all its parts before the render): where each of its vertices was a tick
 * before, for --interp-parts; then this scene's parts are the ones the same slot's next scene pairs with. */
void pvr_parts_from(PvrScene *s)
{
    s->has_from = 0;
    if ((!g_log && !g_parts_blend) || !use_slot()) return;
    g_clamp = pvr_parts_near(s->vtx, s->nvtx);
    pair_parts();
    if (g_log) check();
    if (g_parts_blend) holds();
    if (g_parts_blend && s->nvtx && s->seq) {
        if (s->nvtx > s->from_cap) {
            f32(*f)[3] = realloc(s->from, (size_t)s->nvtx * sizeof *f);
            if (!f) return;
            s->from = f;
            s->from_cap = s->nvtx;
        }
        for (u32 i = 0; i < s->nvtx; i++) {
            const PvrVtx *v = &s->vtx[i];
            u32 k = s->seq[i];
            s->from[i][0] = v->x, s->from[i][1] = v->y, s->from[i][2] = v->z;
            if (k && k <= g_nseen && g_pair[g_seen[k - 1u].part] != NONE) {
                const Seen *e = &g_seen[k - 1u];
                f32 m[3];
                if (v->z == g_clamp) { /* clamped at the near plane: across the screen, still clamped */
                    const f32 *b = g_pv->vtx[g_pv->part[g_pair[e->part]].first + e->pos - g_parts[e->part].first];
                    if (!g_mapped[e->part]) {
                        if (b[2] == g_clamp) memcpy(s->from[i], b, sizeof s->from[0]); /* else as now */
                    } else if (pvr_parts_map(&g_map[e->part], &e->x, m) && m[2] > 0.0f)
                        s->from[i][0] = m[0] * g_clamp / m[2], s->from[i][1] = m[1] * g_clamp / m[2];
                    continue;
                }
                if (g_mapped[e->part]) { /* by place, else by map no further than its pairs moved, else as now */
                    if (g_own[e->pos] != NONE) memcpy(s->from[i], g_pv->vtx[g_own[e->pos]], sizeof s->from[0]);
                    else if (pvr_parts_map(&g_map[e->part], &e->x, m) && fmaxf(fabsf(m[0] - v->x), fabsf(m[1] - v->y)) <= g_map[e->part].lim)
                        memcpy(s->from[i], m, sizeof s->from[0]);
                    continue;
                }
                memcpy(s->from[i], g_pv->vtx[g_pv->part[g_pair[e->part]].first + e->pos - g_parts[e->part].first],
                       sizeof s->from[0]);
                if (s->from[i][2] == g_clamp) s->from[i][0] = v->x, s->from[i][1] = v->y, s->from[i][2] = v->z; /* then */
                else if (g_turned[e->part] && s->from[i][2] > 0.0f) s->from[i][2] = -s->from[i][2]; /* held */
            }
        }
        s->has_from = 1;
    }
    /* --interp-truth: kept only after an odd frame (pvr_blend_keep keeps it at the next render, an even frame), so
     * the in-between frame after an odd frame blends the scene two before */
    if (pvr_blend_truth_t(0.0f) != 0.0f && !pvr_blend_truth_skip()) return;
    for (u32 q = 0; q < g_pv->n; q++) g_pv->first2[q] = g_pv->part[q].first;
    memcpy(g_pv->vtx2, g_pv->vtx, g_pv->nvtx * sizeof g_vtx[0]);
    memcpy(g_pv->pair_was, g_pair, g_nparts * sizeof g_pair[0]);
    memcpy(g_pv->mapped_was, g_mapped, g_nparts);
    memcpy(g_pv->key, g_key, g_nseen * sizeof g_key[0]);
    memcpy(g_pv->part, g_parts, g_nparts * sizeof g_parts[0]);
    memcpy(g_pv->vtx, g_vtx, g_nseen * sizeof g_vtx[0]);
    g_pv->n = g_nparts, g_pv->nvtx = g_nseen;
}

/* kamui2_scene.c kmBeginScene: the parts of the scene before are spent. */
void pvr_parts_scene(void)
{
    if (g_nparts > g_n.most) g_n.most = g_nparts;
    if (g_log && g_ptrs && ++g_n.scenes % LOG_EVERY == 0) {
        port_log("parts: %u scenes: %u part draws, %u wrote to a list (%u nested, %u past the table, most %u in a "
                 "scene); %u of %u polygon vertices in a part's range; of %u parts moved between ticks, one affine "
                 "map fits to 0.5 / 2 pixels: %u / %u (w = 1/z), %u / %u (w = z); worst px (w = 1/z) to 0.5, 2, 8, "
                 "64, more: %u %u %u %u %u; held: %u scenes, %u parts turned; %u parts with another count (%u mapped)\n", g_n.scenes,
                 g_n.draws, g_n.wrote, g_n.nested, g_over, g_n.most, g_n.tagged, g_n.vertices, g_n.moved, g_n.fit[0][0],
                 g_n.fit[0][1], g_n.fit[1][0], g_n.fit[1][1], g_n.hist[0], g_n.hist[1], g_n.hist[2], g_n.hist[3],
                 g_n.hist[4], g_n.held, g_n.turned, g_n.recount, g_n.mapped);
        memset(&g_n, 0, sizeof g_n);
        g_over = 0;
    }
    g_nparts = g_nseen = 0;
    g_last_list = PVRX_LISTS;
}
