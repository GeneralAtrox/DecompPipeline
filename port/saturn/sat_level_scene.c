/* The level editor's scene for the edited arena (sat_level.c; sat_level_priv.h shares what its files need): the arena
 * file's meshes and textures from the disc, then the start points, layout records, stands, floor, edge, light and
 * blockers as the base's LevelScene, and the numbers each entity saves (sat_level_save.c writes them).
 * Nearest existing: sat_level.c - split from it at 400 lines (its scene section). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_disc.h"
#include "console.h"
#include "sat_game.h"
#include "game_log.h"
#include "host.h"
#include "level_edit.h"
#include "sat_level_priv.h"

#ifdef SAT_LEVEL_EDIT
static const char *const k_kind_name[] = SAT_LEVEL_KIND_NAMES;
static const char *const k_kind_type[] = SAT_LEVEL_KIND_TYPES;
#define NKINDS ((int)(sizeof k_kind_name / sizeof *k_kind_name))

/* ---- the editor's scene: the arena file's meshes and textures, the layout, the start points, the light, the edge ---- */
typedef struct {
    s32 model, yaw, scale_y;
    LevelMesh m;
    f32 lo[3], hi[3];
} Variant;
typedef struct {
    u8 *d;
    u32 n;
    u32 nmodel, model[32];        /* each model's PDATA address */
    u32 ntex;
    LevelTex *tex;
    u32 **pixels;
    Variant var[MAXVAR];
    u32 nvar;
    LevelEnt *ent;
    u32 nent;
    s32 light_loaded[3], handle_loaded[3]; /* the light as read, and its handle: unmoved, it saves as read */
    s32 spawn_y[2];
    s16 yaw[MAXREC];
    u8 flag[MAXREC], pad[MAXREC];
    s16 kind[MAXREC];
    u32 nrec;
} Scene;
static Scene S;

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static u16 be16(const u8 *p) { return (u16)(p[0] << 8 | p[1]); }
static const u8 *at(u32 addr, u32 len) { return addr >= G.file_base && addr - G.file_base + len <= S.n ? S.d + (addr - G.file_base) : NULL; }

static int read_arena(void)
{
    u32 sectors = 0, bytes = 0, fad = sat_disc_find(SAT_LEVEL_FILE, &sectors, &bytes);
    const u8 *p;
    if (!fad || !bytes || !(S.d = (u8 *)malloc(bytes)) || !sat_disc_file_bytes(fad, 0, bytes, S.d)) {
        game_log_line("level editor: %s could not be read from the disc", SAT_LEVEL_FILE);
        return 0;
    }
    S.n = bytes;
    for (u32 e = be32(S.d + 0x0c); (p = at(e, 8)) != NULL && be32(p) && S.nmodel < 32; e += 8) {
        const u8 *o = at(be32(p), 4);
        S.model[S.nmodel++] = o ? be32(o) : 0;
    }
    for (u32 e = be32(S.d + 0x10); (p = at(e, 4)) != NULL && be32(p); e += 4) S.ntex++;
    S.tex = (LevelTex *)calloc(S.ntex ? S.ntex : 1, sizeof *S.tex);
    S.pixels = (u32 **)calloc(S.ntex ? S.ntex : 1, sizeof *S.pixels);
    for (u32 i = 0; i < S.ntex; i++) {
        const u8 *tr = at(be32(at(be32(S.d + 0x10) + 4u * i, 4)), 12), *px;
        u32 w = tr ? be16(tr) : 0, h = tr ? be16(tr + 2) : 0, mode = tr ? be16(tr + 4) : 0;
        if (tr && mode == 1 && w && h && w <= 1024 && h <= 1024 && (px = at(be32(tr + 8), w * h * 2u)) != NULL) {
            S.pixels[i] = (u32 *)malloc((size_t)w * h * 4u);
            for (u32 k = 0; S.pixels[i] && k < w * h; k++) {
                u16 c = be16(px + 2u * k);
                S.pixels[i][k] = c ? 0xff000000u | (u32)((c >> 10) & 31) << 19 | (u32)((c >> 5) & 31) << 11 | (u32)(c & 31) << 3 : 0;
            }
        }
        if (!S.pixels[i]) S.pixels[i] = (u32 *)malloc(4), S.pixels[i][0] = 0xff808080u, w = h = 1; /* not RGB555: grey */
        S.tex[i].w = w, S.tex[i].h = h, S.tex[i].rgba = S.pixels[i];
    }
    return S.nmodel > 0;
}

/* a model as drawn: turned yaw degrees about Y (SGL's slRotY), its height scaled (16.16); triangles with UVs */
static int variant(s32 model, s32 yaw, s32 scale_y)
{
    const u8 *pd, *pt, *pl, *aa;
    u32 npt, npl, nv = 0;
    LevelVert *v;
    Variant *w;
    double c = cos(yaw * 3.14159265358979 / 180.0), s = sin(yaw * 3.14159265358979 / 180.0), sy = scale_y / 65536.0;
    for (u32 i = 0; i < S.nvar; i++)
        if (S.var[i].model == model && S.var[i].yaw == yaw && S.var[i].scale_y == scale_y) return (int)i;
    if (model < 0 || (u32)model >= S.nmodel || S.nvar >= MAXVAR || !(pd = at(S.model[model], 20))) return -1;
    npt = be32(pd + 4), npl = be32(pd + 12);
    if (!(pt = at(be32(pd), npt * 12u)) || !(pl = at(be32(pd + 8), npl * 20u)) || !(aa = at(be32(pd + 16), npl * 12u))) return -1;
    v = (LevelVert *)malloc((size_t)npl * 6u * sizeof *v);
    if (!v) return -1;
    w = &S.var[S.nvar];
    memset(w, 0, sizeof *w);
    w->model = model, w->yaw = yaw, w->scale_y = scale_y;
    for (int k = 0; k < 3; k++) w->lo[k] = 1e30f, w->hi[k] = -1e30f;
    for (u32 i = 0; i < npl; i++) {
        const u8 *q = pl + 20u * i, *t = aa + 12u * i;
        u16 vi[4], texno = be16(t + 2), colno = be16(t + 6), dir = be16(t + 10);
        static const f32 cu[4] = {0, 1, 1, 0}, cv[4] = {0, 0, 1, 1};
        static const int tri[2][3] = {{0, 1, 2}, {0, 2, 3}};
        int textured = (t[1] & 4) && texno != 69 && texno < S.ntex;
        u32 rgba = textured ? 0xffffffffu
                            : (colno & 0x8000u) ? 0xff000000u | (u32)((colno >> 10) & 31) << 19 | (u32)((colno >> 5) & 31) << 11 | (u32)(colno & 31) << 3
                                                : 0xff909090u;
        for (int k = 0; k < 4; k++) vi[k] = be16(q + 12 + 2 * k);
        for (int r = 0; r < 2; r++) {
            const int *tr = tri[r];
            if (vi[tr[0]] == vi[tr[1]] || vi[tr[1]] == vi[tr[2]] || vi[tr[0]] == vi[tr[2]] || vi[tr[0]] >= npt || vi[tr[1]] >= npt ||
                vi[tr[2]] >= npt)
                continue;
            for (int k = 0; k < 3; k++) {
                int corner = tr[k];
                u32 pi = vi[corner];
                double x, y, z;
                LevelVert *o = &v[nv++];
                x = (s32)be32(pt + 12u * pi) / 65536.0, y = (s32)be32(pt + 12u * pi + 4) / 65536.0 * sy, z = (s32)be32(pt + 12u * pi + 8) / 65536.0;
                o->x = (f32)(x * c + z * s), o->y = (f32)y, o->z = (f32)(-x * s + z * c);
                o->u = (dir & 0x10) ? 1.0f - cu[corner] : cu[corner];
                o->v = (dir & 0x20) ? 1.0f - cv[corner] : cv[corner];
                o->rgba = rgba, o->tex = textured ? (s32)texno : -1;
                if (o->x < w->lo[0]) w->lo[0] = o->x;
                if (o->y < w->lo[1]) w->lo[1] = o->y;
                if (o->z < w->lo[2]) w->lo[2] = o->z;
                if (o->x > w->hi[0]) w->hi[0] = o->x;
                if (o->y > w->hi[1]) w->hi[1] = o->y;
                if (o->z > w->hi[2]) w->hi[2] = o->z;
            }
        }
    }
    w->m.v = v, w->m.nv = nv;
    return (int)S.nvar++;
}

/* a model's own extent along one axis (its simple model's, for collision as the game measures it), whole units */
static double extent(s32 model, int axis, double *lo)
{
    const u8 *pd = model >= 0 && (u32)model < S.nmodel ? at(S.model[model], 20) : NULL, *pt;
    u32 npt;
    double mn = 1e30, mx = -1e30;
    if (!pd || !(pt = at(be32(pd), (npt = be32(pd + 4)) * 12u))) return *lo = 0, 0;
    for (u32 i = 0; i < npt; i++) {
        double x = (s32)be32(pt + 12u * i + 4u * (u32)axis) / 65536.0;
        if (x < mn) mn = x;
        if (x > mx) mx = x;
    }
    *lo = mn;
    return mx - mn;
}

static void ent_init(LevelEnt *e, const char *id, u32 kind, const char *name, const char *type)
{
    memset(e, 0, sizeof *e);
    snprintf(e->id, sizeof e->id, "%s", id);
    snprintf(e->name, sizeof e->name, "%s", name);
    snprintf(e->type, sizeof e->type, "%s", type);
    e->kind = kind, e->mesh = -1;
}
static LevelEnt *add_ent(const char *id, u32 kind, const char *name, const char *type)
{
    LevelEnt *e = &S.ent[S.nent++];
    ent_init(e, id, kind, name, type);
    return e;
}
/* the floor's top for the editor: the game draws the floor's surface as a background plane (no polygons), so its model is only
   the rim; an octagon of the edge's half-size at the rim's top, one plain colour, display only (nothing saved changes) */
static void floor_cap(Variant *w)
{
    const f32 r = (f32)G.edge_half, k = r * 1.41421356f - r, y = w->lo[1]; /* +Y down: the rim's top is its lowest y */
    const f32 pts[8][2] = {{r, -k}, {r, k}, {k, r}, {-k, r}, {-r, k}, {-r, -k}, {-k, -r}, {k, -r}};
    LevelVert *v = G.edge_half ? (LevelVert *)realloc((void *)w->m.v, (size_t)(w->m.nv + 24u) * sizeof *v) : NULL;
    if (!v) return;
    w->m.v = v;
    for (int i = 0; i < 8; i++) {
        const f32 *a = pts[i], *b = pts[(i + 1) % 8], c[3][2] = {{0, 0}, {a[0], a[1]}, {b[0], b[1]}};
        for (int j = 0; j < 3; j++) {
            LevelVert *o = &v[w->m.nv++];
            o->x = c[j][0], o->y = y, o->z = c[j][1], o->u = o->v = 0, o->rgba = 0xff505c50u, o->tex = -1;
        }
    }
}

static void box_of(LevelEnt *e, const Variant *w)
{
    for (int k = 0; k < 3; k++) e->lo[k] = (s32)(w->lo[k] * 65536.0f), e->hi[k] = (s32)(w->hi[k] * 65536.0f);
}
static s32 fx(double v) { return (s32)floor(v * 65536.0 + 0.5); }

/* a blocker: an invisible box standing at pos (whole units, y above the floor), half x, height, half z */
void sat_lv_blocker(LevelEnt *e, const char *id, const char *name, const s32 k[6])
{
    ent_init(e, id, LEVEL_BLOCKER, name, "Blocking volume (invisible box)");
    for (int j = 0; j < 3; j++) e->pos[j] = e->orig[j] = k[j] * 65536;
    e->add[1] = (s32)G.floor, e->axes = 7, e->quantum = 65536, e->shape = LEVEL_SHAPE_BOX;
    e->lo[0] = -k[3] * 65536, e->hi[0] = k[3] * 65536, e->lo[1] = -k[4] * 65536, e->hi[1] = 0, e->lo[2] = -k[5] * 65536, e->hi[2] = k[5] * 65536;
    snprintf(e->info, sizeof e->info, "Added by the level, not the game: an invisible box %d x %d x %d (half x, height, half z) that "
             "robots cannot pass (the kit tests it after the game's own objects). Thrown items pass through it.", k[3], k[4], k[5]);
}

/* the light's handle: the target less the unit direction times the schema's distance, in whole units */
static void handle_of(const s32 dir[3], const s32 target[3], s32 out[3])
{
    double l = sqrt((double)dir[0] * dir[0] + (double)dir[1] * dir[1] + (double)dir[2] * dir[2]);
    for (int k = 0; k < 3; k++) out[k] = (s32)((s64)floor(target[k] / 65536.0 - dir[k] / l * G.handle + 0.5) * 65536);
}

void sat_lv_scene_close(LevelScene *sc)
{
    for (u32 i = 0; i < S.nvar; i++) free((void *)S.var[i].m.v);
    for (u32 i = 0; i < S.ntex; i++) free(S.pixels[i]);
    free(S.pixels), free(S.tex), free(S.ent), free(S.d);
    memset(&S, 0, sizeof S);
    if (sc) sc->ent = NULL, sc->nent = 0, sc->mesh = NULL, sc->nmesh = 0, sc->tex = NULL, sc->ntex = 0;
}

int sat_lv_scene_open(LevelScene *sc)
{
    static LevelMesh meshes[MAXVAR];
    char path[300];
    Edits ed;
    s32 floor_y = (s32)G.floor, target[3] = {0, (s32)G.floor, 0};
    double lo;
    u32 fixed;
    sat_lv_scene_close(NULL);
    if (!read_arena()) return sat_lv_scene_close(NULL), 0;
    sat_lv_level_path(path, sizeof path);
    sat_lv_load(path, &ed);
    S.ent = (LevelEnt *)calloc(MAXENT, sizeof *S.ent);
    if (!S.ent) return sat_lv_scene_close(NULL), 0;
    memset(sc, 0, sizeof *sc);
    snprintf(sc->title, sizeof sc->title, "%s", SAT_LEVEL_TITLE);
    sc->unit = 65536.0f, sc->y_down = 1, sc->snap = 1;
    sc->grid[0] = 10 << 16, sc->grid[1] = 50 << 16, sc->grid[2] = 100 << 16, sc->grid[3] = 500 << 16;
    memcpy(sc->light_target, target, sizeof target);
    snprintf(sc->saved_to, sizeof sc->saved_to, "%s", path);
    /* the start points: the game's own (the schema's numbers), or the file's x and z */
    for (int k = 0; k < 2; k++) {
        char id[16], name[32];
        LevelEnt *e;
        snprintf(id, sizeof id, "spawn-%d", k + 1), snprintf(name, sizeof name, "Player %d start", k + 1);
        e = add_ent(id, LEVEL_SPAWN, name, "Start point (spawn)");
        for (int j = 0; j < 3; j++) e->orig[j] = (s32)G.spawn[k][j];
        memcpy(e->pos, e->orig, sizeof e->pos);
        if (ed.has_spawn >> k & 1u) e->pos[0] = ed.spawn[k][0], e->pos[2] = ed.spawn[k][2];
        S.spawn_y[k] = e->orig[1];
        e->axes = 5, e->quantum = 1, e->collides = 1, e->shape = LEVEL_SHAPE_CYLINDER, e->radius = (s32)G.radius << 16;
        e->lo[0] = e->lo[2] = -e->radius, e->hi[0] = e->hi[2] = e->radius, e->lo[1] = -400 << 16, e->hi[1] = 0;
        snprintf(e->info, sizeof e->info, "Where robot %d stands when a one-against-one fight starts (x, z; it stands on the floor).\n"
                 "16.16 units, as the game computes them. Facing: the game's own.", k + 1);
    }
    /* the layout records: the game's table in work RAM, the file's positions over it */
    for (int i = 0; i < (int)G.max && i < MAXREC; i++) {
        s16 kind = (s16)p16(rec(i));
        u32 d;
        s32 mA, mB, v;
        char id[16], name[64];
        LevelEnt *e;
        if (kind == -1) break;
        S.nrec = (u32)i + 1u;
        d = G.defs + 6u * (u32)kind;
        mA = (s16)p16(d), mB = (s16)p16(d + 2u);
        S.kind[i] = kind, S.yaw[i] = (s16)p16(rec(i) + 8u), S.flag[i] = p8(rec(i) + 10u), S.pad[i] = p8(rec(i) + 11u);
        snprintf(id, sizeof id, "object-%02d", i);
        snprintf(name, sizeof name, "%s %02d", kind >= 0 && kind < NKINDS ? k_kind_name[kind] : "Object", i);
        e = add_ent(id, p8(d + 5u) ? LEVEL_OBJECT : LEVEL_MESH, name, kind >= 0 && kind < NKINDS ? k_kind_name[kind] : "Object");
        for (int j = 0; j < 3; j++) e->orig[j] = (s32)(s16)p16(rec(i) + 2u + 2u * (u32)j) * 65536;
        memcpy(e->pos, e->orig, sizeof e->pos);
        if ((ed.has_rec >> i & 1u) && ed.kind[i] == kind)
            for (int j = 0; j < 3; j++) e->pos[j] = (s32)ed.pos[i][j] * 65536;
        e->add[1] = floor_y, e->axes = 7, e->quantum = 65536;
        v = variant(mB, S.yaw[i], 65536);
        if (v >= 0) e->mesh = v, box_of(e, &S.var[v]);
        e->collides = 1, e->shape = LEVEL_SHAPE_CYLINDER;
        e->radius = fx(extent(mA, 2, &lo) / 2), e->lo[1] = -fx(extent(mA, 1, &lo)), e->hi[1] = 0;
        snprintf(e->info, sizeof e->info, "Layout record %d (0x%08x): kind %d, model %d (drawn), %d (collision)\nYaw %d degrees, flag %u%s%s\n"
                 "Whole units; y is above the floor (%.2f), +Y down as the game counts.", i, rec(i), kind, mB, mA, S.yaw[i], S.flag[i],
                 p8(d + 4u) ? ", robots stand on it" : "", p8(d + 5u) ? ", robots lift and throw it" : "", floor_y / 65536.0);
    }
    /* the stands (movable scenery), the floor (locked) and the edge (locked): drawn and tested by the code at the centre */
    {
        LevelEnt *e = add_ent("stands", LEVEL_MESH, "Stands", "Arena mesh (scenery)");
        int v = variant((s32)G.stands, 0, (s32)G.stands_scale_y);
        e->add[1] = floor_y, e->axes = 7, e->quantum = 65536;
        for (int j = 0; j < 3; j++) e->pos[j] = ed.has_stands ? ed.stands[j] * 65536 : 0;
        if (v >= 0) e->mesh = v, box_of(e, &S.var[v]);
        snprintf(e->info, sizeof e->info, "Drawn by the game's code every frame, at half height; scenery, no collision. Moving it "
                 "moves the drawing only (whole units).");
        e = add_ent("floor", LEVEL_MESH, "Floor", "Arena mesh (floor)");
        v = variant((s32)G.floor_model, 0, 65536);
        e->add[1] = floor_y + (s32)G.floor_y;
        if (v >= 0) floor_cap(&S.var[v]), e->mesh = v, box_of(e, &S.var[v]);
        snprintf(e->info, sizeof e->info, "Drawn by the game's code at the centre. Robots stand at the floor height (%.2f) the code "
                 "sets; the ring's edge is code (Arena edge).", floor_y / 65536.0);
        e = add_ent("edge", LEVEL_BLOCKER, "Arena edge", "Blocking volume (ring-out)");
        e->add[1] = floor_y, e->shape = LEVEL_SHAPE_OCTAGON, e->radius = (s32)G.edge_half << 16;
        e->lo[0] = e->lo[2] = -e->radius, e->hi[0] = e->hi[2] = e->radius, e->lo[1] = -((s32)G.edge_height << 16), e->hi[1] = 0;
        snprintf(e->info, sizeof e->info, "The game's ring-out test, in code: |x| and |z| up to %u, |x| + |z| up to %u x sqrt 2 (an "
                 "octagon). A robot past it is out. Add blocker places invisible boxes of the level's own.", G.edge_half, G.edge_half);
    }
    /* the light: a handle above the arena aimed at its centre */
    {
        LevelEnt *e = add_ent("sun", LEVEL_LIGHT, "Key light", "Directional light");
        for (int j = 0; j < 3; j++) S.light_loaded[j] = ed.has_light ? ed.light[j] : (s32)G.light_args[j];
        handle_of(S.light_loaded, target, S.handle_loaded);
        {
            s32 o[3], og[3] = {(s32)G.light_args[0], (s32)G.light_args[1], (s32)G.light_args[2]};
            handle_of(og, target, o);
            memcpy(e->orig, o, sizeof o);
        }
        memcpy(e->pos, S.handle_loaded, sizeof e->pos);
        e->axes = 7, e->quantum = 65536;
        for (int j = 0; j < 3; j++) e->lo[j] = -60 << 16, e->hi[j] = 60 << 16;
        snprintf(e->info, sizeof e->info, "The fight's one light: a direction (the game passes it to slCalcPoint, then slLight, every "
                 "frame). Move the handle; it shines from the handle towards the arena's centre.\nThe game's: (-0.5, 0.5, 0.5).");
    }
    /* the level's blockers, numbered in file order; Add blocker places more from the template */
    fixed = S.nent;
    for (u32 b = 0; b < ed.nblock; b++) {
        char id[32], name[32];
        snprintf(id, sizeof id, "blocker-%02u", b + 1), snprintf(name, sizeof name, "Blocker %02u", b + 1);
        sat_lv_blocker(&S.ent[S.nent++], id, name, ed.block[b]);
    }
    {
        s32 k[6] = {0, 0, 0, (s32)G.bsize[0], (s32)G.bsize[1], (s32)G.bsize[2]};
        sat_lv_blocker(&sc->place_tmpl, "blocker", "Blocker", k);
    }
    for (u32 i = 0; i < S.nvar; i++) meshes[i] = S.var[i].m;
    sc->ent = S.ent, sc->nent = S.nent, sc->cap = fixed + MAXBLOCK, sc->can_place = 1;
    sc->mesh = meshes, sc->nmesh = S.nvar, sc->tex = S.tex, sc->ntex = S.ntex;
    return 1;
}

/* ---- saving ---- */
/* the numbers an entity saves: 1 pos, 2 dir (the light), 3 pos and size (a blocker); 0 none; -1 out of the game's range */
int sat_lv_saved(const LevelScene *sc, const LevelEnt *e, s64 v[3], s64 sz[3])
{
    const char *id = e->id;
    if (!strncmp(id, "spawn-", 6)) {
        v[0] = e->pos[0], v[1] = S.spawn_y[id[6] - '1'], v[2] = e->pos[2];
        return 1;
    }
    if (!strncmp(id, "object-", 7) || !strcmp(id, "stands") || !strncmp(id, "blocker-", 8)) {
        for (int j = 0; j < 3; j++) {
            if (e->pos[j] % 65536) return -1;
            v[j] = e->pos[j] / 65536;
            if (v[j] < -32768 || v[j] > 32767) return -1;
        }
        if (id[0] != 'b') return 1;
        sz[0] = e->hi[0] / 65536, sz[1] = -e->lo[1] / 65536, sz[2] = e->hi[2] / 65536;
        return 3;
    }
    if (!strcmp(id, "sun")) {
        if (!memcmp(e->pos, S.handle_loaded, sizeof S.handle_loaded)) {
            for (int j = 0; j < 3; j++) v[j] = S.light_loaded[j];
        } else { /* towards the target from the handle, as long as the game's own vector */
            double d[3], l = 0, og = 0;
            for (int j = 0; j < 3; j++) d[j] = (double)sc->light_target[j] - e->pos[j], l += d[j] * d[j], og += (double)(s32)G.light_args[j] * (s32)G.light_args[j];
            if (l <= 0) return -1;
            for (int j = 0; j < 3; j++) v[j] = (s64)floor(d[j] / sqrt(l) * sqrt(og) + 0.5);
        }
        return 2;
    }
    return 0;
}

/* an entity's whole line, as a fresh file writes it (no indent, no comma) */
int sat_lv_line_of(const LevelScene *sc, const LevelEnt *e, char *b, size_t cap)
{
    s64 v[3], z[3];
    int r = sat_lv_saved(sc, e, v, z);
    const char *id = e->id;
    if (r <= 0) return r;
    if (!strncmp(id, "spawn-", 6)) snprintf(b, cap, "{\"id\": \"%s\", \"type\": \"spawn\", \"units\": \"fixed\", \"pos\": [%lld, %lld, %lld]}", id, v[0], v[1], v[2]);
    else if (r == 2) snprintf(b, cap, "{\"id\": \"%s\", \"type\": \"light\", \"units\": \"fixed\", \"dir\": [%lld, %lld, %lld]}", id, v[0], v[1], v[2]);
    else if (r == 3)
        snprintf(b, cap, "{\"id\": \"%s\", \"type\": \"blocker\", \"units\": \"whole\", \"pos\": [%lld, %lld, %lld], \"size\": [%lld, %lld, %lld]}", id, v[0],
                 v[1], v[2], z[0], z[1], z[2]);
    else if (!strcmp(id, "stands")) snprintf(b, cap, "{\"id\": \"%s\", \"type\": \"scenery\", \"units\": \"whole\", \"pos\": [%lld, %lld, %lld]}", id, v[0], v[1], v[2]);
    else {
        int k = atoi(id + 7);
        snprintf(b, cap, "{\"id\": \"%s\", \"type\": \"%s\", \"kind\": %d, \"units\": \"whole\", \"pos\": [%lld, %lld, %lld], \"yaw\": %d, \"flag\": %u, \"pad\": %u}",
                 id, S.kind[k] >= 0 && S.kind[k] < NKINDS ? k_kind_type[S.kind[k]] : "object", S.kind[k], v[0], v[1], v[2], S.yaw[k], S.flag[k], S.pad[k]);
    }
    return 1;
}
#endif
