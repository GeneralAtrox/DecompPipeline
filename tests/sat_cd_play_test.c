/* The CD block's play and seek (sat_cd.c, included whole for its static functions): a disc of one data and two audio
 * tracks, commands written to CR1..CR4 as a game does, one sector a frame. Checks a pause (seek 0xffffff) and a resume
 * (play 0xffffff) as Robo Pit's pause menu sends them, and fast loads: a load fills the buffer in a frame, a movie (its
 * first sector starts "FILM") keeps the drive's rate, and that the NetLink modem's window (absent) is not decoded as CD
 * registers. Run by tests/test_sat_cd_play.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_cd.c"

static int g_fail;
static u32 g_pushed, g_stored, g_room = (u32)CD_BLOCKS, g_last = 0xffffffffu;
static const SatTrack g_tracks[3] = {{0x41, 150}, {0x01, 1000}, {0x01, 2000}};

/* a stand-in for each kit function sat_cd.c calls */
void port_log(const char *fmt, ...) { (void)fmt; }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr) { (void)lo, (void)hi, (void)name, (void)rd, (void)wr; }
void sat_idle_ask(void) {}
u32 sat_sys_hz(void) { return 75; }
u32 sat_vblank_count(void) { return 0; }
int sat_sys_det(void) { return 1; }
int sat_sys_virtual(void) { return 1; }
int sat_sys_fast(void) { return 0; }
u64 sat_clock_ticks(u32 rate) { (void)rate; return 0; }
int sat_disc_open(const char *cue) { (void)cue; return 0; }
int sat_disc_present(void) { return 1; }
u32 sat_disc_ntracks(void) { return 3; }
const SatTrack *sat_disc_track(u32 tno) { return tno >= 1 && tno <= 3 ? &g_tracks[tno - 1] : NULL; }
u32 sat_disc_leadout(void) { return 3000; }
int sat_disc_read(u32 fad, u8 *raw)
{
    memcpy(raw, &fad, 4);
    memcpy(raw + 16, fad == 400 ? "FILM" : "DATA", 4); /* a movie file starts at 400 */
    return fad < 3000;
}
/* Saturn Bomberman's disc: the loader /0, the game's folder BOMSS (its program PROGRAM.BIN) and XBAND/SUB */
static const SatFile g_root[] = {{170, 2048, 0, 0, 0, 2, 20, "."}, {170, 2048, 0, 0, 0, 2, 20, ".."}, {183, 435296, 0, 0, 0, 0, 33, "0"},
                                 {171, 2048, 0, 0, 0, 2, 21, "BOMSS"}, {181, 2048, 0, 0, 0, 2, 31, "XBAND"}};
static const SatFile g_xband[] = {{181, 2048, 0, 0, 0, 2, 31, "."}, {170, 2048, 0, 0, 0, 2, 20, ".."}, {190, 2048, 0, 0, 0, 2, 40, "SUB"}};
int sat_disc_root(SatFile *root) { *root = g_root[0]; return 0; }
int sat_disc_dir(u32 dir_lba, u32 size, SatFile *out, int max)
{
    const SatFile *d = dir_lba == 31 ? g_xband : g_root;
    int n = dir_lba == 31 ? 3 : 5;
    (void)size;
    for (int i = 0; i < n && i < max; i++) out[i] = d[i];
    return n;
}
void sat_cdda_flush(void) {}
void sat_cdda_push(const u8 *raw) { memcpy(&g_last, raw, 4); g_pushed++; }
void cdb_reset(int selectors_too) { (void)selectors_too; }
void cdb_set_lengths(int get_code, int put_code) { (void)get_code, (void)put_code; }
CdFilter *cdb_filter(u32 n) { static CdFilter f; (void)n; return &f; }
void cdb_connect_cd(u32 selector) { (void)selector; }
u32 cdb_cd_selector(void) { return 0; }
u32 cdb_last_part(void) { return 0; }
int cdb_store(const u8 *raw, u32 fad) { (void)raw, (void)fad; return ++g_stored <= g_room; }
u32 cdb_free(void) { return CD_BLOCKS; }
u32 cdb_count(u32 part) { (void)part; return 0; }
int cdb_info(u32 part, u32 pos, u32 *fad, u8 *fn, u8 *cn, u8 *sm, u8 *ci) { (void)part, (void)pos, (void)fad, (void)fn, (void)cn, (void)sm, (void)ci; return 0; }
void cdb_delete(u32 part, u32 pos, u32 num) { (void)part, (void)pos, (void)num; }
u32 cdb_actual_size(u32 part, u32 pos, u32 num) { (void)part, (void)pos, (void)num; return 0; }
void xfer_blocks(u32 part, u32 pos, u32 num, int delete_after) { (void)part, (void)pos, (void)num, (void)delete_after; }
void xfer_bytes(const void *data, u32 len) { (void)data, (void)len; }
u32 xfer_read(u32 size) { (void)size; return 0; }
u32 xfer_end(void) { return 0; }
int xfer_active(void) { return 0; }

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

/* a command: CR1 is the command and the position's top byte, CR3 the mode and the end's top byte */
static void cmd(u32 op, u32 pos24, u32 mode, u32 epos)
{
    reg_write(0x05890018u, 2, op << 8 | (pos24 >> 16 & 0xffu));
    reg_write(0x0589001cu, 2, pos24 & 0xffffu);
    reg_write(0x05890020u, 2, mode << 8 | (epos >> 16 & 0xffu));
    reg_write(0x05890024u, 2, epos & 0xffffu);
}

static void frames(int n)
{
    for (int i = 0; i < n; i++) sat_cd_frame();
}

int main(void)
{
    sat_cd_init("disc.cue");

    cmd(0x10, 0x800000u | 1000u, 0x00, 0x800000u | 100u); /* track 2's first 100 frames, no repeat */
    frames(30);
    check(g_last == 1029u, "play: one sector a frame from the start");

    cmd(0x11, 0xffffffu, 0, 0); /* the game pauses */
    g_pushed = 0;
    frames(10);
    check(g_pushed == 0, "seek 0xffffff: the music stops");

    cmd(0x10, 0xffffffu, 0xff, 0xffffffu); /* the game resumes */
    frames(10);
    check(g_last == 1039u, "play 0xffffff: the music carries on where it stopped");
    frames(100);
    check(g_last == 1099u && (g_hirq & H_PEND) && !g_playing, "a resumed play keeps its end and its repeat count");

    cmd(0x10, 0x800000u | 2000u, 0x0f, 0x800000u | 10u); /* track 3's first 10 frames, looping */
    frames(5);
    cmd(0x11, 0xffffffu, 0, 0);
    frames(3);
    cmd(0x10, 0xffffffu, 0xff, 0xffffffu);
    frames(7);
    check(g_last == 2001u && g_playing, "a resumed loop goes back to the play's start, not the pause");

    g_stored = 0;
    cmd(0x10, 0x800000u | 200u, 0x00, 0x800000u | 300u); /* a load: 150 sectors a second, 2 a frame at 75 Hz */
    frames(1);
    check(g_stored == 2u, "fast loads off: a load at the drive's rate");
    sat_cd_fast_loads = 1;
    g_stored = 0;
    cmd(0x10, 0x800000u | 200u, 0x00, 0x800000u | 300u);
    frames(1);
    check(g_stored == (u32)CD_BLOCKS && g_pos == 200u + (u32)CD_BLOCKS, "fast loads: a load fills the empty buffer in a frame");
    g_stored = 0, g_room = 50, g_hirq &= ~(u32)H_BFUL;
    cmd(0x10, 0x800000u | 200u, 0x00, 0x800000u | 300u);
    frames(1);
    check(g_stored == 51u && g_pos == 250u && (g_hirq & H_BFUL), "fast loads: a load stops when the buffer is full");
    g_room = (u32)CD_BLOCKS;
    g_stored = 0;
    cmd(0x10, 0x800000u | 400u, 0x00, 0x800000u | 100u);
    frames(1);
    check(g_stored == 2u, "fast loads: a movie keeps the drive's rate");

    g_hirq = 0x07c1u; /* the NetLink modem's probe (Saturn Bomberman's H2HLIBUS): UART registers 2 and 7 at 0x25895001 + 4n */
    reg_write(0x05895009u, 1, 0);
    reg_write(0x0589501du, 1, 0x22);
    check(g_hirq == 0x07c1u, "no NetLink: a write to the modem's window leaves HIRQ (CMOK) alone");
    check(reg_read(0x0589501du, 1) == 0xffu, "no NetLink: the modem's scratch register reads open bus, not what was written");

    /* the boot ROM's hand-over (game.json boot_dir): the CD block's file system holds the folder above, the id names it */
    g_ndir = 0;
    check(sat_cd_boot_dir("BOMSS") == 3 && g_ndir == 5, "boot folder BOMSS: file id 3 of the root, which the CD block holds");
    cmd(0x73, 0, 0, 3); /* Get File Info of file id 3 */
    check(g_cr[1] == 6 && g_dir[3].fad == 171u, "boot folder: the CD block answers for its file id");
    check(sat_cd_boot_dir("XBAND/SUB") == 2 && g_ndir == 3 && g_dir[0].fad == 181u, "a nested boot folder: its parent is the current directory");
    check(sat_cd_boot_dir("bomss") == 3, "boot folder: ISO names match in any case");
    check(sat_cd_boot_dir("0") == -1 && sat_cd_boot_dir("NOPE") == -1, "boot folder: a file or a missing folder is not one");
    check(sat_cd_boot_dir("") == 0 && g_ndir == 5, "boot folder: the root itself");

    puts(g_fail ? "FAILED" : "all ok");
    return g_fail;
}
