/* New head types in the game's own part choice (game.json heads; --new-heads on|off). A model part's versions
 * (game.json part_choice, sat_part.c) become types of their own after the game's last one, each on one robot only:
 * Robo Pit's ROBOT MAKING "Select a body." goes TYPE A .. TYPE O as before, then TYPE P .. TYPE Z, TYPE A1 .. (the animal
 * heads), then round to TYPE A again; LEFT goes back the same way.
 *
 * In the game's own data a new type is always its host, the original type its part is a version of (Robo Pit: TYPE N,
 * the bunny the animal heads are made from), so every table the game reads by type, its stats, its fights and its save
 * are the host's, and the game's save holds only values the original game knows. The kit keeps which robot has which new
 * type, by the robot's record, and changes three things around the game's own functions, named in game.json:
 *
 *   next / prev (field, avail, count, min)  the part choice's step right and left. When the field is a record's type, the
 *                 step past the last original type goes to the first new type (the field stays the host), the steps
 *                 among the new types keep the host, and the step past the last new type goes to the first original.
 *   setup (fighter, robot) and ring (robot, ...)  the game building a robot's model (its preview, its fights) and the part
 *                 choice's ring of parts; while one runs for a robot with a new type,
 *   node, ring_node (node, object)  the game copying a part's model into the robot's own (node) or into the ring's
 *                 (ring_node, the ring's other copy): the host part's OBJECT is swapped for a copy whose PDATA is the
 *                 version, put together (sat_part_compose) in RAM the kit maps where a RAM cartridge sits, after
 *                 sat_part.c's. The game copies it, so the shared model data is never changed. The ring is built again
 *                 after each step, so its host part shows the robot's new type.
 *   panel (robot, ...)  the INFORMATION panel: while it runs for a robot with a new type, the host's name in the types'
 *                 table reads the new type's ("TYPE P"), put back when it returns. The stats shown are the host's.
 *   offered (n, offered)  the game adding up the types offered, for the panel's count ("BODIES"): while the panel runs
 *                 and the host is offered, the new types are added. Only the shown number; nothing is written.
 *   making (robot)  ROBOT MAKING starting on a record: a new type kept there before is dropped.
 *
 * The kit's own save, beside the game's (the --save file's name with .heads, a text file): when the game writes its file
 * (game.json heads file) through the backup library (sat_bup.c), each robot that is in use, has the host type and a new
 * type is written as its record, the new type's name and the robot's name; when the game reads its file, they are read
 * back, and a robot takes its new type only while its record is in use, has the host type and the same name (the first
 * match confirms it; later renames keep it). Without the kit's file the save loads exactly as before.
 *
 * On by default in an unscripted play run; off in checks, scripted and --det runs unless asked for; off in an online match.
 * Read at start and kept in the recording as a session note when on (the picks themselves are pad reads). Off, the hooks
 * are never called and the game.json file is never read or written.
 *
 * Nearest existing: sat_team.c (game.json names the game's functions and the kit acts around them), sat_part.c (the
 * version put together in mapped RAM) and sat_menus.c (a start-time switch kept as a session note). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_net.h"
#include "sat_part.h"
#include "sat_game.h"
#include "session.h"
#include "host.h"

int sat_heads_on; /* sat_call.c calls in only while it is set */
u8 sat_heads_mark[256]; /* and only for a function marked here (others may share a mark: they are let through) */
static int g_want = -1; /* --new-heads (-1: not given: on in a play run) */
void sat_heads_option(const char *value) { g_want = strcmp(value, "off") != 0; }

#ifdef SAT_HEADS
#define HEADS_LO 0x02410000u /* after sat_part.c's 64 KB at 0x02400000, a RAM cartridge's place on the A-bus */
#define HEADS_SIZE ((SAT_HEADS_N * SAT_HEADS_SLOT + 0xffffu) & ~0xffffu)
#define OBJ_SIZE 44u /* an SGL OBJECT: PDATA*, pos[3], ang[3], pad, scl[3], child*, sibling* */
#define NOTE "*new-heads"
typedef struct {
    u32 records, size, count, body, used, name, name_len, host, names, entry, label_len, setup, ring, node, next, prev, panel, making, ring_node, offered;
} HeadsGame;
static const HeadsGame H = SAT_HEADS;
static const char *const k_type[SAT_HEADS_N][2] = SAT_HEADS_TYPES; /* {the name shown, part_choice's version} */
typedef struct {
    u8 k;              /* the new type, 1..SAT_HEADS_N; 0 none */
    u8 loaded;         /* read from the kit's save and not yet matched to its robot */
    u8 name[16];       /* the robot's name then */
} Entry;
static Entry *g_e;
static int g_stack[16], g_sp; /* the new type of the robot whose model is being built, innermost last */
static u8 *g_ram;             /* the mapped RAM: a slot per new type, its OBJECT then its PDATA */
static u8 g_put[SAT_HEADS_N + 1];
static u8 g_name_was[16];
static int g_named;           /* the host's name is the new type's until the panel returns */
static int g_panel;           /* the panel is running */
static u32 g_field, g_old, g_oldk, g_stepping;
static int g_known;           /* the game read its file, or a robot took a new type: the kit's save is current */

/* the kit's reads have no side effect: rd8/rd16 count towards the busy-wait detector (sat_team.c's reason) */
static u32 p8(u32 a) { return (sat_peek32(a & ~3u) >> (24u - 8u * (a & 3u))) & 0xffu; }
static u32 p16(u32 a) { return (sat_peek32(a & ~3u) >> (16u - 8u * (a & 2u))) & 0xffffu; }
static u32 rec(u32 i) { return H.records + i * H.size; }
static int same_name(u32 i, const u8 *name)
{
    for (u32 k = 0; k < H.name_len && k < 16; k++)
        if (p8(rec(i) + H.name + k) != name[k]) return 0;
    return 1;
}

/* the new type of robot i, 0 when none (or its record no longer fits what was kept) */
static int head_of(u32 i)
{
    Entry *e;
    if (!g_e || i >= H.count || !(e = &g_e[i])->k || p16(rec(i) + H.body) != H.host) return 0;
    if (e->loaded) {
        if (!p8(rec(i) + H.used) || !same_name(i, e->name)) return 0;
        e->loaded = 0; /* matched: from here on it is the robot's, whatever it is renamed */
    }
    return e->k;
}
static void set(u32 i, int k)
{
    if (!g_e || i >= H.count) return;
    g_known |= k > 0;
    memset(&g_e[i], 0, sizeof g_e[i]);
    g_e[i].k = (u8)k;
}

static u32 ram_read(u32 phys, u32 size)
{
    u32 v = 0, o = phys - HEADS_LO;
    for (u32 i = 0; i < size; i++) v = v << 8 | g_ram[(o + i) % HEADS_SIZE];
    return v;
}
static void ram_write(u32 phys, u32 size, u32 value)
{
    u32 o = phys - HEADS_LO;
    for (u32 i = 0; i < size; i++) g_ram[(o + i) % HEADS_SIZE] = (u8)(value >> (8u * (size - 1u - i)));
}

/* new type k's OBJECT: the host part's, its PDATA the version; 0 when it cannot be put together */
static u32 place(int k)
{
    u32 at = HEADS_LO + (u32)(k - 1) * SAT_HEADS_SLOT, size;
    u8 *slot;
    if (!g_ram) {
        if (!(g_ram = (u8 *)calloc(1, HEADS_SIZE))) return 0;
        sat_map(HEADS_LO, HEADS_LO + HEADS_SIZE, "heads", ram_read, ram_write);
    }
    slot = g_ram + (at - HEADS_LO);
    if (g_put[k] == 2) return 0;
    if (!g_put[k]) {
        const u8 *edit = sat_part_edit(k_type[k - 1][1]);
        size = edit ? sat_part_compose(edit, slot + 48, at + 48u, SAT_HEADS_SLOT - 48u) : 0;
        g_put[k] = size ? 1 : 2;
        port_log("heads: %s (%s) %s at %08x, %u bytes\n", k_type[k - 1][0], k_type[k - 1][1], size ? "put together" : "NOT put together", at, size);
        if (!size) return 0;
    }
    for (u32 i = 0; i < OBJ_SIZE; i++) slot[i] = (u8)p8(SAT_HEADS_OBJECT + i); /* the host part's OBJECT as it is now */
    slot[0] = (u8)((at + 48u) >> 24), slot[1] = (u8)((at + 48u) >> 16), slot[2] = (u8)((at + 48u) >> 8), slot[3] = (u8)(at + 48u);
    return at;
}

/* the record whose type field is at address f, or -1 */
static int type_field(u32 f)
{
    u32 o = f - H.records;
    return f >= H.records && o / H.size < H.count && o % H.size == H.body ? (int)(o / H.size) : -1;
}
static u32 avail_end(u32 avail, u32 count, int last) /* the first (or last) original type the choice offers */
{
    for (u32 n = 0; n < count; n++) {
        u32 i = last ? count - 1u - n : n;
        if ((s8)p8(avail + i) > 0) return i;
    }
    return H.host;
}

const TcArgs *sat_heads_enter(u32 fn, const TcArgs *a, TcArgs *tmp)
{
    int k;
    if (sat_cpu_id() != 0) return a;
    if (fn == H.setup || fn == H.ring) {
        s32 i = (s16)a->r[fn == H.setup ? 1 : 0];
        if (g_sp < 16) g_stack[g_sp] = i >= 0 ? head_of((u32)i) : 0;
        g_sp++;
    } else if (fn == H.node || fn == H.ring_node) {
        k = g_sp > 0 && g_sp <= 16 ? g_stack[g_sp - 1] : 0;
        u32 o = k && (a->r[1] & 0x1fffffffu) == SAT_HEADS_OBJECT ? place(k) : 0;
        if (o) {
            *tmp = *a;
            tmp->r[1] = o;
            return tmp;
        }
    } else if (fn == H.next || fn == H.prev) {
        int i = type_field(a->r[0]);
        g_stepping = i >= 0;
        if (i >= 0) g_field = a->r[0], g_old = p16(a->r[0]), g_oldk = (u32)head_of((u32)i);
    } else if (fn == H.panel) {
        s32 i = (s16)a->r[0];
        k = i >= 0 ? head_of((u32)i) : 0;
        g_panel++;
        if (k && !g_named) {
            u32 at = H.names + H.host * H.entry;
            const char *s = k_type[k - 1][0];
            for (u32 n = 0; n < H.label_len && n < 16; n++) g_name_was[n] = (u8)p8(at + n);
            for (u32 n = 0; n < H.label_len && n < 16 && *s; n++) wr8(at + n, (u8)*s++); /* the game's own padding after it */
            g_named = 1;
        }
    } else if (fn == H.making) {
        s32 i = (s16)a->r[0];
        if (i >= 0) set((u32)i, 0);
    }
    return a;
}

u32 sat_heads_leave(u32 fn, const TcArgs *a, u32 r)
{
    if (sat_cpu_id() != 0) return r;
    if (fn == H.offered && g_panel > 0 && (s16)a->r[0] > (s16)H.host && (s8)p8(a->r[1] + H.host) > 0)
        return r + SAT_HEADS_N; /* the panel's count of the types offered: the new ones come with their host */
    if (fn == H.setup || fn == H.ring) {
        if (g_sp > 0) g_sp--;
    } else if (fn == H.panel) {
        if (g_panel > 0) g_panel--;
        if (g_named) for (u32 n = 0; n < H.label_len && n < 16; n++) wr8(H.names + H.host * H.entry + n, g_name_was[n]);
        g_named = 0;
    } else if ((fn == H.next || fn == H.prev) && g_stepping && a->r[0] == g_field) {
        u32 now = p16(g_field), i = (u32)type_field(g_field), k = g_oldk, next = fn == H.next;
        g_stepping = 0;
        if ((s8)p8(a->r[1] + H.host) <= 0) return r; /* the host is not offered: nor are its versions */
        if (k) { /* on a new type: the next one, or round to the originals */
            if (next ? k < SAT_HEADS_N : k > 1) wr16(g_field, (u16)H.host), set(i, (int)(next ? k + 1 : k - 1));
            else wr16(g_field, (u16)avail_end(a->r[1], (u32)(s16)a->r[2], !next)), set(i, 0);
        } else if (next ? (s16)now < (s16)g_old : (s16)now > (s16)g_old) { /* past the last original (or the first) */
            wr16(g_field, (u16)H.host), set(i, next ? 1 : SAT_HEADS_N);
        }
        if (head_of(i) != (int)k) port_log("heads: robot %u %s\n", i, head_of(i) ? k_type[head_of(i) - 1][0] : "original type");
    }
    return r;
}

/* the kit's save: the --save file's name, .heads for its extension */
static void heads_path(char *out, size_t n)
{
    const char *p = sat_bup_where(), *dot = strrchr(p, '.'), *sl = strrchr(p, '\\');
    size_t len = dot && (!sl || dot > sl) && !strchr(dot, '/') ? (size_t)(dot - p) : strlen(p);
    snprintf(out, n, "%.*s.heads", (int)len, p);
}

void sat_heads_bup(u32 name, int wrote)
{
    char path[600], line[200];
    FILE *f;
    if (!sat_heads_on || !g_e) return;
    for (u32 k = 0; k < 11; k++)
        if (p8(name + k) != (u8)SAT_HEADS_FILE[k]) return;
    heads_path(path, sizeof path);
    if (wrote) { /* the game wrote its save: each robot with a new type, as it is now */
        int n = 0;
        if (!g_known) return; /* a write before the game read its file (its boot check): the kit's save stays */
        for (u32 i = 0; i < H.count; i++) n += p8(rec(i) + H.used) && head_of(i);
        if (!n) {
            remove(path);
            port_log("heads: saved, no robot with a new type (%s removed)\n", path);
            return;
        }
        if (!(f = fopen(path, "w"))) return;
        fprintf(f, "saturn new head types 1\n");
        for (u32 i = 0; i < H.count; i++) {
            int k = p8(rec(i) + H.used) ? head_of(i) : 0;
            if (!k) continue;
            fprintf(f, "%u %s ", i, k_type[k - 1][1]);
            for (u32 c = 0; c < H.name_len && c < 16; c++) fprintf(f, "%02x", p8(rec(i) + H.name + c));
            fprintf(f, "\n");
        }
        fclose(f);
        port_log("heads: saved %d robot(s) with a new type in %s\n", n, path);
        return;
    }
    memset(g_e, 0, H.count * sizeof g_e[0]); /* the game read its save: only what the kit's save says */
    g_known = 1;
    if (!(f = fopen(path, "r"))) {
        port_log("heads: loaded, no %s\n", path);
        return;
    }
    while (fgets(line, sizeof line, f)) {
        unsigned i = 0;
        char value[32] = {0}, hex[40] = {0};
        if (sscanf(line, "%u %31s %39s", &i, value, hex) != 3 || i >= H.count) continue;
        for (int k = 1; k <= SAT_HEADS_N; k++) {
            if (strcmp(k_type[k - 1][1], value)) continue;
            g_e[i].k = (u8)k, g_e[i].loaded = 1;
            for (u32 c = 0; c < H.name_len && c < 16; c++) {
                unsigned v = 0;
                if (sscanf(hex + 2 * c, "%2x", &v) == 1) g_e[i].name[c] = (u8)v;
            }
            port_log("heads: loaded robot %u %s\n", i, k_type[k - 1][0]);
        }
    }
    fclose(f);
}

void sat_heads_start(int play_run)
{
    u8 on = 0;
    if (session_note_replayed(NOTE, &on, 1)) on = on != 0;
    else if (session_replaying()) on = 0;
    else on = (u8)(g_want < 0 ? play_run && !input_scripted() : g_want);
    if (on && sat_net_on()) on = 0; /* a match: both sides play the game's own types */
    if (on) session_save_read(NOTE, &on, 1, 1);
    if (on && !g_e) g_e = (Entry *)calloc(H.count, sizeof(Entry));
    sat_heads_on = on && g_e;
    if (sat_heads_on) {
        const u32 fn[] = {H.setup, H.ring, H.node, H.next, H.prev, H.panel, H.making, H.ring_node, H.offered};
        for (u32 i = 0; i < sizeof fn / sizeof fn[0]; i++) sat_heads_mark[(fn[i] >> 1) & 255u] = 1;
    }
    port_log("heads: new head types %s (%d after the game's own)\n", sat_heads_on ? "on" : "off", SAT_HEADS_N);
}

#else /* the game has no new head types */
const TcArgs *sat_heads_enter(u32 fn, const TcArgs *a, TcArgs *tmp) { (void)fn, (void)tmp; return a; }
u32 sat_heads_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
void sat_heads_bup(u32 name, int wrote) { (void)name, (void)wrote; }
void sat_heads_start(int play_run) { (void)play_run; }
#endif
