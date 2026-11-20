/* The backup RAM's place (port/saturn/sat_bup.c, included whole): --save-dir and --save in either order, and an old save
 * in the current folder carried over once. Run by tests/test_sat_launcher.py, in a folder that may hold old saves, as:
 *   sat_save_dir_test dir DIR            --save-dir DIR, then the start (sat_bup_init) and one write of the internal file
 *   sat_save_dir_test named FILE DIR     --save FILE, then --save-dir DIR
 *   sat_save_dir_test dirfirst DIR FILE  --save-dir DIR, then --save FILE
 * It prints "where PATH" and the library's log lines. Guest memory is a flat array at 0x06000000, as sat_bup_test.c. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include "sat_bup.c"

static u8 g_ram[0x100000];
int sat_heads_on;
const SatGameHooks sat_game_table = {0};
u8 rd8(u32 a) { return g_ram[a - 0x06000000u]; }
void wr8(u32 a, u8 v) { g_ram[a - 0x06000000u] = v; }
u16 rd16(u32 a) { return (u16)(rd8(a) << 8 | rd8(a + 1)); }
void wr16(u32 a, u16 v) { wr8(a, (u8)(v >> 8)); wr8(a + 1, (u8)v); }
u32 rd32(u32 a) { return (u32)rd16(a) << 16 | rd16(a + 2); }
void wr32(u32 a, u32 v) { wr16(a, (u16)(v >> 16)); wr16(a + 2, (u16)v); }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
int port_option(const char *id) { return strcmp(id, "memory-cartridge") ? -1 : 0; }

int main(int argc, char **argv)
{
    if (argc < 3) return 2;
    if (!strcmp(argv[1], "dir")) {
        sat_bup_save_dir(argv[2]);
        sat_bup_init();
        load_all(&g_dev[0]);
        save_all(&g_dev[0]); /* the game's first write: the folder is made if it is not there */
    } else if (!strcmp(argv[1], "named") && argc > 3) {
        sat_bup_path(argv[2]);
        sat_bup_save_dir(argv[3]);
    } else if (!strcmp(argv[1], "dirfirst") && argc > 3) {
        sat_bup_save_dir(argv[2]);
        sat_bup_path(argv[3]);
    } else {
        return 2;
    }
    printf("where %s\n", sat_bup_where());
    return 0;
}
