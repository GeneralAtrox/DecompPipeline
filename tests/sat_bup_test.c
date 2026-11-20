/* The backup-RAM library (sat_bup.c, included whole) with and without the plug-in memory cartridge. Run by
 * tests/test_sat_bup.py as: sat_bup_test DIR on|off. Guest memory is a flat array at 0x06000000. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_bup.c"

static u8 g_ram[0x100000];
static int g_fail, g_option;
int sat_heads_on;
const SatGameHooks sat_game_table = {0};
u8 rd8(u32 a) { return g_ram[a - 0x06000000u]; }
void wr8(u32 a, u8 v) { g_ram[a - 0x06000000u] = v; }
u16 rd16(u32 a) { return (u16)(rd8(a) << 8 | rd8(a + 1)); }
void wr16(u32 a, u16 v) { wr8(a, (u8)(v >> 8)); wr8(a + 1, (u8)v); }
u32 rd32(u32 a) { return (u32)rd16(a) << 16 | rd16(a + 2); }
void wr32(u32 a, u32 v) { wr16(a, (u16)(v >> 16)); wr16(a + 2, (u16)v); }
void port_log(const char *fmt, ...) { (void)fmt; }
int port_option(const char *id) { return strcmp(id, "memory-cartridge") ? -1 : g_option; }

static void check(int ok, const char *what)
{
    if (!ok) g_fail++;
    printf("%s %s\n", ok ? "ok  " : "FAIL", what);
}
static int exists(const char *p) { FILE *f = fopen(p, "rb"); if (f) fclose(f); return f != NULL; }
static long fsize(const char *p) { FILE *f = fopen(p, "rb"); long n = -1; if (f) { fseek(f, 0, SEEK_END); n = ftell(f); fclose(f); } return n; }
static u32 svc(int i, u32 a, u32 b, u32 c, u32 d) { return k_raw[i](a, b, c, d); }
enum { CFG = 0x06001000, DIRB = 0x06002000, DATA = 0x06020000, OUT = 0x06040000, NAME = 0x06004000 };

static void name_dir(const char *n, u32 size)
{
    for (u32 i = 0; i < 34; i++) wr8(DIRB + i, 0);
    for (u32 i = 0; n[i]; i++) wr8(DIRB + i, (u8)n[i]);
    wr32(DIRB + 28, size);
}

int main(int argc, char **argv)
{
    char internal[600], cart[600];
    int on;
    u32 size = 284u * 64u;
    if (argc < 3) return 2;
    on = !strcmp(argv[2], "on");
    g_option = on;
    snprintf(internal, sizeof internal, "%s/saturn_backup.bin", argv[1]);
    snprintf(cart, sizeof cart, "%s/saturn_backup_cart.bin", argv[1]);
    sat_bup_path(internal);
    svc(0, 0, 0, CFG, 0);
    check(rd16(CFG) == 1 && rd16(CFG + 2) == 1, "device 0 is the internal memory");
    if (!on) {
        check(rd16(CFG + 4) == 0, "option off: no second device in BUP_Init's table");
        check(svc(3, 1, 0, OUT, 0) == NON, "option off: Stat on device 1 says none");
        name_dir("DFORCE_REC", size);
        check(svc(4, 1, DIRB, DATA, 0) == NON, "option off: Write to device 1 refused");
        check(!exists(cart), "option off: no cartridge file made");
        printf(g_fail ? "FAILED\n" : "all ok\n");
        return g_fail != 0;
    }
    check(rd16(CFG + 4) == 2 && rd16(CFG + 6) == 1, "option on: device 1 is a cartridge with one partition");
    check(exists(cart), "the cartridge's file is made empty on first use");
    check(svc(1, 1, 1, 0, 0) == OK, "SelPart on device 1");
    check(svc(3, 1, 0, OUT, 0) == OK && rd32(OUT + 4) == 1024u && rd32(OUT + 8) == 512u && rd32(OUT + 16) >= 385u,
          "Stat on device 1: 4 Mbit, 512-byte blocks, 385 free blocks or more");
    check(svc(3, 0, 0, OUT, 0) == OK && rd32(OUT + 4) == 512u && rd32(OUT + 8) == 64u, "Stat on device 0 is unchanged");
    for (u32 i = 0; i < size; i++) wr8(DATA + i, (u8)(i * 7u + 3u));
    name_dir("DFORCE_REC", size);
    check(svc(4, 1, DIRB, DATA, 0) == OK, "Write a Record to the cartridge");
    check(fsize(cart) > (long)size, "the cartridge's file holds it");
    check(!exists(internal) || fsize(internal) == 8, "the internal file is not written by it");
    check(svc(3, 0, 0, OUT, 0) == OK && rd32(OUT + 16) == 512u - HEADER_BLOCKS, "the internal memory is still empty");
    for (u32 i = 0; i < size; i++) wr8(OUT + i, 0);
    for (u32 i = 0; i < 12; i++) wr8(NAME + i, rd8(DIRB + i));
    check(svc(5, 1, NAME, OUT, 0) == OK && !memcmp(&g_ram[OUT - 0x06000000u], &g_ram[DATA - 0x06000000u], size), "Read it back");
    check(svc(5, 0, NAME, OUT, 0) == NOT_FOUND, "it is not on device 0");
    check(svc(7, 1, 0x06005000u, 8, OUT) == 1u && svc(7, 0, 0x06005000u, 8, OUT) == 0u, "Dir lists the Record on device 1 only");
    check(svc(6, 1, NAME, 0, 0) == OK && svc(5, 1, NAME, OUT, 0) == NOT_FOUND, "Delete (the title's restore deletes it)");
    printf(g_fail ? "FAILED\n" : "all ok\n");
    return g_fail != 0;
}
