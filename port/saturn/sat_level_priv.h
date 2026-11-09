/* What the edited arena's files share (sat_level.c, sat_level_file.c, sat_level_scene.c, sat_level_save.c): the level
 * schema's numbers (game.json level_edit, as sat_game.h's SAT_LEVEL_EDIT), what an edited level changes, the work-RAM
 * readers, and the functions one file calls in another. Included after sat_game.h; empty when the game has no level_edit.
 * Nearest existing: sat.h - the kit's interface to the base and the game; these are private to the edited arena. */
#ifndef SAT_LEVEL_PRIV_H
#define SAT_LEVEL_PRIV_H
#ifdef SAT_LEVEL_EDIT
enum { MAXREC = 28, MAXPOKE = 8, MAXMOVE = 8, MAXVAR = 32, MAXBLOCK = 16, MAXENT = MAXREC + 8 + MAXBLOCK, MAGIC = 0x4c564c31 };
typedef struct {
    u32 arena_var, arena, tier_var, tier, file_base, recs, defs, max, build;
    u32 floor_var, floor;
    u32 spawn_fn, spawn_pos, spawn_step, count, mode, spawn[2][3], radius;
    u32 light_fn, light_args[3], fight, handle;
    u32 edge_half, edge_height;
    u32 stands, floor_model, stands_scale_y, floor_y;
    u32 menu, cursor, cursor_value, setup, npokes, pokes[MAXPOKE][3];
    u32 collide, next_pos, robot_r, bsize[3];          /* blockers */
    u32 draw, stands_ptr, put_polygon, push, translate, pop; /* the arena's draw and SGL's matrix calls */
} LevelGame;
static const LevelGame G = SAT_LEVEL_EDIT;

/* what an edited level changes, as kept in the recording */
typedef struct {
    u32 magic, play, has_rec, has_spawn, has_light, has_stands, nblock; /* bit i: record i given; bits 0-1: start points */
    s16 kind[MAXREC], pos[MAXREC][3];
    s32 spawn[2][3], light[3], stands[3];
    s32 block[MAXBLOCK][6]; /* x, y, z, half x, height, half z: whole units, y above the floor (+Y down) */
} Edits;

/* the work RAM's bytes and the arena's layout records (sat_level.c's hooks, sat_level_scene.c's scene) */
static __inline u8 p8(u32 a) { return (u8)(sat_peek32(a & ~3u) >> (24u - 8u * (a & 3u))); }
static __inline u16 p16(u32 a) { return (u16)(sat_peek32(a & ~3u) >> (16u - 8u * (a & 2u))); }
static __inline u32 rec(int i) { return G.recs + 12u * (u32)i; }

extern const LevelEdProvider sat_level_provider; /* the editor's side (sat_level_save.c) */

/* sat_level.c: where the level file is */
void sat_lv_level_path(char *out, size_t cap);
/* sat_level_file.c: the level file read */
char *sat_lv_read_all(const char *path, size_t *len);
const char *sat_lv_field(const char *line, const char *end, const char *key);
int sat_lv_id_of(const char *line, const char *end, char *id, size_t cap);
int sat_lv_load(const char *path, Edits *e);
/* sat_level_scene.c: the editor's scene, and the numbers each entity saves */
int sat_lv_scene_open(LevelScene *sc);
void sat_lv_scene_close(LevelScene *sc);
void sat_lv_blocker(LevelEnt *e, const char *id, const char *name, const s32 k[6]);
int sat_lv_saved(const LevelScene *sc, const LevelEnt *e, s64 v[3], s64 sz[3]);
int sat_lv_line_of(const LevelScene *sc, const LevelEnt *e, char *b, size_t cap);
/* sat_level_save.c: saving */
int sat_lv_scene_save_to(LevelScene *sc, const char *path, char *msg, size_t cap);
#endif
#endif
