/* The CD block: HIRQ and the four command registers at 0x05890000, the data port at 0x05818000. A command written to
 * CR1..CR4 (CR4 last) runs at once and leaves its response in CR1..CR4 with HIRQ.CMOK; with no command's response
 * waiting, the registers carry the periodic status. The disc is read as the game plays it: sectors flow from the
 * drive through the selectors (sat_cdbuf.c) at a fixed rate per frame, and the ISO 9660 file commands answer from the
 * cue sheet's data track (sat_disc.c).
 *
 * Fast loads (Display > Fast disc loads; sat_main.c decides). At the drive's 2x a game's loads take seconds of black
 * screen; with fast loads a data play fills the buffer as fast as the game empties it, unless its first sector starts
 * a Sega FILM movie, whose frames are paced on the drive's rate.
 * Nearest existing: none for the Saturn; the Dreamcast kit's GD-ROM command set (port/dc) is a different device. */
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include "sat_cd.h"
#include "sat_sys.h"

enum { ST_PAUSE = 1, ST_PLAY = 3, ST_NODISC = 7, ST_ERROR = 9, ST_PERIODIC = 0x20, FRAME_SECTORS = 24 };

static u32 g_hirq = 0x07c1u, g_mask; /* as the BIOS leaves it: no disc-change flag */
static u16 g_cr[4];
static int g_unread, g_playing, g_repeat, g_quick;
u32 sat_cd_data_rate = 150; /* sectors a second a data play delivers: a 2x drive turns 150 (--cdrate N). The movies pace their frames on it; 1200 made them play in bursts */
int sat_cd_fast_loads = -1; /* 1: a load fills the buffer at once; 0: the drive's rate; -1: not set (sat_main.c decides) */
static u32 g_state = ST_PAUSE, g_pos = 150, g_play_start, g_play_end, g_actual, g_acc;
static SatFile g_dir[SAT_MAX_FILES];
static int g_ndir;

static const SatTrack *track_at(u32 fad, u32 *tno)
{
    u32 best = 1;
    for (u32 t = 1; t <= sat_disc_ntracks(); t++)
        if (sat_disc_track(t)->fad <= fad) best = t;
    if (tno) *tno = best;
    return sat_disc_track(best);
}

static void status_regs(void)
{
    u32 tno = 1;
    const SatTrack *t = track_at(g_pos, &tno);
    u32 state = sat_disc_present() ? g_state : ST_NODISC;
    g_cr[0] = (u16)((state | ST_PERIODIC) << 8 | (g_repeat & 15)); /* the 0x20 flag says this is the drive's own report, not a command's answer */
    g_cr[1] = (u16)((t ? t->ctrl : 0x41) << 8 | tno);
    g_cr[2] = (u16)(1u << 8 | ((g_pos >> 16) & 0xff));
    g_cr[3] = (u16)(g_pos & 0xffff);
}

static void reply_state(void)
{
    g_cr[0] = (u16)((sat_disc_present() ? g_state : ST_NODISC) << 8);
    g_cr[1] = g_cr[2] = g_cr[3] = 0;
}

/* a data play the game waits on: anything on the data track but a movie (its first sector starts "FILM") */
static int quick(u32 start)
{
    u8 raw[SAT_SECTOR];
    const SatTrack *t = track_at(start, NULL);
    return sat_cd_fast_loads > 0 && t && (t->ctrl & 0x40) && sat_disc_read(start, raw) && memcmp(raw + 16, "FILM", 4) != 0;
}

static void play(u32 start, u32 at, u32 end, int repeat)
{
    sat_cdda_flush();
    g_quick = quick(start);
    g_play_start = start;
    g_pos = at;
    g_play_end = end;
    g_repeat = repeat;
    g_playing = 1;
    g_state = ST_PLAY;
}

static void stop(void)
{
    sat_cdda_flush();
    g_playing = 0;
    g_state = ST_PAUSE;
}

static void toc(void)
{
    u32 e[102];
    memset(e, 0xff, sizeof e);
    if (sat_disc_ntracks()) {
        for (u32 t = 1; t <= sat_disc_ntracks(); t++) e[t - 1] = (u32)sat_disc_track(t)->ctrl << 24 | sat_disc_track(t)->fad;
        e[99] = (u32)sat_disc_track(1)->ctrl << 24 | 1u << 16;
        e[100] = (u32)sat_disc_track(sat_disc_ntracks())->ctrl << 24 | sat_disc_ntracks() << 16;
        e[101] = (u32)0x01 << 24 | sat_disc_leadout();
    }
    for (int i = 0; i < 102; i++) e[i] = _byteswap_ulong(e[i]);
    xfer_bytes(e, sizeof e);
}

static void read_dir(u32 fid)
{
    SatFile root;
    if (fid == 0xffffffu || fid >= (u32)g_ndir || !(g_dir[fid].attr & 2)) {
        if (sat_disc_root(&root)) return;
        g_ndir = sat_disc_dir(root.dir_lba, root.size, g_dir, SAT_MAX_FILES);
    } else {
        SatFile d = g_dir[fid];
        g_ndir = sat_disc_dir(d.dir_lba, d.size, g_dir, SAT_MAX_FILES);
    }
}

static void put_file(u8 *o, const SatFile *f)
{
    u32 fad = _byteswap_ulong(f->fad), size = _byteswap_ulong(f->size);
    memcpy(o, &fad, 4);
    memcpy(o + 4, &size, 4);
    o[8] = f->gap, o[9] = f->unit, o[10] = f->fn, o[11] = f->attr;
}

/* Play: a start, end or mode of all ones keeps the one in force, so a start of 0xffffff resumes a paused play where it
 * stopped, with the same loop. The end in frame form is a sector count. */
static void cmd_play(u32 c3, u32 c4, u32 pos24)
{
    u32 epos = (c3 & 0xff) << 16 | c4, mode = c3 >> 8, start = g_play_start, at = g_pos, end = g_play_end;
    int fad = pos24 != 0xffffffu && (pos24 & 0x800000u);
    if (fad) {
        start = at = pos24 & 0xfffffu;
    } else if (pos24 != 0xffffffu) {
        u32 t0 = (pos24 >> 8) & 0xff;
        start = at = sat_disc_track(t0 ? t0 : 1) ? sat_disc_track(t0 ? t0 : 1)->fad : 150;
    }
    if (epos != 0xffffffu && (fad || (epos & 0x800000u))) {
        end = start + (epos & 0xfffffu);
    } else if (epos != 0xffffffu) {
        u32 t1 = (epos >> 8) & 0xff;
        end = t1 && t1 < sat_disc_ntracks() ? sat_disc_track(t1 + 1)->fad : sat_disc_leadout();
    }
    port_log("cd: play frames %u..%u from %u mode %02x at vertical blank %u\n", start, end, at, mode, sat_vblank_count());
    play(start, at, end, mode == 0xffu ? g_repeat : (int)(mode & 15u));
}

static u32 cmd_file(u32 cmd, u32 c1, u32 c2, u32 c3, u32 c4)
{
    u32 fid = (c3 & 0xff) << 16 | c4, pos24 = (c1 & 0xff) << 16 | c2;
    CdFilter *f = cdb_filter(c3 >> 8);
    u8 rec[SAT_MAX_FILES * 12];
    reply_state();
    switch (cmd) {
    case 0x70: case 0x71: read_dir(fid); return H_EFLS;
    case 0x72: g_cr[1] = (u16)(g_ndir > 2 ? g_ndir - 2 : 0), g_cr[2] = 0x0100, g_cr[3] = 2; return 0;
    case 0x73:
        if (fid == 0xffffffu) {
            for (int i = 0; i < g_ndir; i++) put_file(rec + 12 * i, &g_dir[i]);
            g_cr[1] = (u16)(g_ndir * 6);
            xfer_bytes(rec, (u32)g_ndir * 12u);
        } else if (fid < (u32)g_ndir) {
            put_file(rec, &g_dir[fid]);
            g_cr[1] = 6;
            xfer_bytes(rec, 12);
        }
        return H_DRDY;
    case 0x74:
        if (fid < (u32)g_ndir && f) {
            u32 first = g_dir[fid].fad + pos24, n = (g_dir[fid].size + 2047u) / 2048u - pos24;
            f->mode = 1, f->fad = first, f->count = n;
            cdb_connect_cd(c3 >> 8);
            play(first, first, first + n, 0);
            reply_state();
        }
        return 0;
    default: stop(); reply_state(); return H_EFLS; /* 0x75 abort file */
    }
}

static u32 cmd_filter(u32 cmd, u32 c1, u32 c2, u32 c3, u32 c4)
{
    CdFilter *f = cdb_filter(c3 >> 8);
    reply_state();
    switch (cmd) {
    case 0x30: cdb_connect_cd(c3 >> 8); break;
    case 0x31: g_cr[2] = (u16)(cdb_cd_selector() << 8); return 0;
    case 0x32: g_cr[2] = (u16)(cdb_last_part() << 8); return 0;
    case 0x40:
        if (f) f->fad = (c1 & 0xff) << 16 | c2, f->count = (c3 & 0xff) << 16 | c4;
        break;
    case 0x41:
        if (f) g_cr[0] |= (u16)(f->fad >> 16 & 0xff), g_cr[1] = (u16)f->fad, g_cr[2] = (u16)(f->count >> 16 & 0xff), g_cr[3] = (u16)f->count;
        return 0;
    case 0x42:
        if (f) f->chan = (u8)c1, f->smmask = (u8)(c2 >> 8), f->cimask = (u8)c2, f->fid = (u8)c3, f->smval = (u8)(c4 >> 8), f->cival = (u8)c4;
        break;
    case 0x44:
        if (f && (c1 & 0x80)) memset(f, 0, offsetof(CdFilter, ctrue));
        else if (f) f->mode = (u8)c1;
        break;
    case 0x45: if (f) g_cr[0] |= f->mode; return 0;
    case 0x46:
        if (f && (c1 & 1)) f->ctrue = (u8)(c2 >> 8);
        if (f && (c1 & 2)) f->cfalse = (u8)c2;
        break;
    case 0x47: if (f) g_cr[1] = (u16)(f->ctrue << 8 | f->cfalse); return 0;
    default: /* 0x48 reset selector */
        if ((c1 & 0xff) == 0) cdb_delete(c3 >> 8, 0, 0xffff);
        else cdb_reset((c1 & 0xfb) != 0);
        break;
    }
    return H_ESEL;
}

static u32 cmd_buffer(u32 cmd, u32 c1, u32 c2, u32 c3, u32 c4)
{
    u32 sel = c3 >> 8;
    reply_state();
    switch (cmd) {
    case 0x50: g_cr[1] = CD_BLOCKS, g_cr[2] = CD_SELECTORS << 8, g_cr[3] = (u16)cdb_free(); return 0;
    case 0x51: g_cr[3] = (u16)cdb_count(sel); return 0;
    case 0x52: g_actual = cdb_actual_size(sel, c2, c4); return H_ESEL;
    case 0x53: g_cr[0] |= (u16)(g_actual >> 17 & 0xff), g_cr[1] = (u16)(g_actual >> 1); return 0;
    case 0x54: {
        u32 fad = 0;
        u8 fn = 0, cn = 0, sm = 0, ci = 0;
        if (cdb_info(sel, c2 & 0xff, &fad, &fn, &cn, &sm, &ci))
            g_cr[0] |= (u16)(fad >> 16 & 0xff), g_cr[1] = (u16)fad, g_cr[2] = (u16)(fn << 8 | cn), g_cr[3] = (u16)(sm << 8 | ci);
        return 0;
    }
    case 0x60:
        cdb_set_lengths((c1 & 0xff) == 0xff ? -1 : (int)(c1 & 0xff), (c2 >> 8) == 0xff ? -1 : (int)(c2 >> 8));
        return H_ESEL;
    case 0x61: case 0x63: xfer_blocks(sel, c2, c4, cmd == 0x63); return H_DRDY;
    default: cdb_delete(sel, c2, c4); return H_EHST; /* 0x62: the host's I/O with the buffer is over */
    }
}

static u32 command(void)
{
    u32 c1 = g_cr[0], c2 = g_cr[1], c3 = g_cr[2], c4 = g_cr[3], cmd = c1 >> 8, pos24 = (c1 & 0xff) << 16 | c2;
    if ((cmd >= 0x30 && cmd <= 0x32) || (cmd >= 0x40 && cmd <= 0x48 && cmd != 0x43)) return cmd_filter(cmd, c1, c2, c3, c4);
    if ((cmd >= 0x50 && cmd <= 0x54) || cmd == 0x60 || (cmd >= 0x61 && cmd <= 0x63)) return cmd_buffer(cmd, c1, c2, c3, c4);
    if (cmd >= 0x70 && cmd <= 0x75) return cmd_file(cmd, c1, c2, c3, c4);
    switch (cmd) {
    case 0x00: status_regs(); g_cr[0] &= (u16)~(ST_PERIODIC << 8); return 0; /* a command's answer: Robo Pit starts its music only on a status of exactly "paused" */
    case 0x01: reply_state(); g_cr[1] = 0x0201, g_cr[3] = 0x0400; return 0;
    case 0x02: reply_state(); g_cr[1] = 0x00cc; toc(); return H_DRDY;
    case 0x03:
        reply_state();
        g_cr[2] = (u16)(1u << 8 | (c1 & 0xff ? 0u : (sat_disc_leadout() >> 16) & 0xff));
        g_cr[3] = (u16)(c1 & 0xff ? 0u : sat_disc_leadout() & 0xffff); /* a session's start is its LBA (0): the game adds the 150 */
        return 0;
    case 0x04:
        if (c1 & 1) stop(), cdb_reset(1);
        reply_state();
        return H_ESEL;
    case 0x06: {
        int active = xfer_active();
        u32 n = xfer_end();
        reply_state();
        g_cr[0] |= active ? (u16)((n >> 17) & 0xff) : 0xff;
        g_cr[1] = active ? (u16)((n >> 1) & 0xffff) : 0xffff;
        g_hirq &= ~(u32)H_DRDY;
        return H_EHST;
    }
    case 0x10: cmd_play(c3, c4, pos24); reply_state(); return 0;
    case 0x11: /* seek: 0xffffff pauses where the play is (a game's pause; play 0xffffff resumes it) */
        if (pos24 != 0xffffffu && (pos24 & 0x800000u)) g_pos = pos24 & 0xfffffu;
        stop(); reply_state(); return 0;
    case 0xe0: reply_state(); return H_EFLS;
    case 0xe1: reply_state(); g_cr[1] = 4; return 0;
    default: {
        static u32 seen[8];
        static int n;
        int known = 0;
        for (int i = 0; i < n; i++) known |= seen[i] == cmd;
        if (!known && n < 8) {
            seen[n++] = cmd;
            port_log("cd: command %02x is not implemented (rejected)\n", cmd);
        }
        g_cr[0] = 0xff00, g_cr[1] = g_cr[2] = g_cr[3] = 0;
        return 0;
    }
    }
}

/* ---- the registers */
static u32 reg_read(u32 p, u32 size)
{
    u32 v = 0;
    if ((p & 0xffffu) == 0x8000u) return xfer_read(size); /* the data transfer register's mirror in the register block */
    switch (p & 0x3cu) {
    case 0x08: v = g_hirq; break;
    case 0x0c: v = g_mask; break;
    case 0x18: v = g_cr[0]; break;
    case 0x1c: v = g_cr[1]; break;
    case 0x20: v = g_cr[2]; break;
    case 0x24: v = g_cr[3], g_unread = 0; break;
    default: break;
    }
    return v;
}

static void reg_write(u32 p, u32 size, u32 value)
{
    u32 o = p & 0x3cu;
    (void)size;
    if (o == 0x08) {
        g_hirq &= value;
    } else if (o == 0x0c) {
        g_mask = value & 0xffff;
    } else if (o >= 0x18 && o <= 0x24) {
        g_cr[(o - 0x18) / 4] = (u16)value;
        if (o == 0x24) {
            u32 asked = g_cr[0] >> 8;
            g_hirq |= command() | H_CMOK;
            g_unread = 1;
            if (asked == 0x00u || asked == 0x51u) sat_idle_ask(); /* the drive delivers once a frame */
        }
    }
}

static u32 dtr_read(u32 p, u32 size)
{
    (void)p;
    return xfer_read(size);
}

static void dtr_write(u32 p, u32 size, u32 value) { (void)p, (void)size, (void)value; }

/* once a frame: the drive delivers sectors, the periodic status refreshes */
void sat_cd_frame(void)
{
    static u64 audio_at;
    int live = !sat_sys_det() && !sat_sys_virtual() && !sat_sys_fast();
    u64 audio_now = live ? sat_clock_ticks(75u) : 0, audio_due = audio_now - audio_at;
    audio_at = audio_now;
    if (g_playing) {
        const SatTrack *t = track_at(g_pos, NULL);
        int data = t && (t->ctrl & 0x40);
        u32 n = FRAME_SECTORS;
        if (data && g_quick) {
            n = CD_BLOCKS; /* as many as the buffer takes: cdb_store stops at full */
        } else if (data) {
            g_acc += sat_cd_data_rate;
            n = g_acc / sat_sys_hz();
            g_acc %= sat_sys_hz();
        } else if (live) {
            n = audio_due < 75u ? (u32)audio_due : 75u; /* the music keeps its speed when a frame runs late */
        } else {
            g_acc += 75u;
            n = g_acc / sat_sys_hz();
            g_acc %= sat_sys_hz();
        }
        for (u32 i = 0; i < n && g_playing; i++) {
            u8 raw[SAT_SECTOR];
            if (g_pos >= g_play_end) {
                if (!g_repeat) {
                    stop();
                    g_hirq |= H_PEND;
                    break;
                }
                g_pos = g_play_start;
                if (g_repeat != 15) g_repeat--;
            }
            if (data) {
                int stored;
                if (!sat_disc_read(g_pos, raw)) {
                    g_state = ST_ERROR;
                    g_playing = 0;
                    break;
                }
                stored = cdb_store(raw, g_pos);
                if (!stored) {
                    g_hirq |= H_BFUL;
                    break;
                }
                g_hirq |= H_CSCT;
            } else if (sat_disc_read(g_pos, raw)) {
                sat_cdda_push(raw);
            }
            g_pos++;
        }
    }
    if (!g_unread) status_regs();
    g_hirq |= H_SCDQ;
}

/* sat_movie.c: the host played a movie itself; the drive is left paused where the original's play of the file left it */
void sat_cd_played(u32 start, u32 at, u32 end) { stop(), g_play_start = start, g_pos = at, g_play_end = end; }

int sat_cd_playing_audio(u32 *fad, u32 *end) /* for the sound path: CD-DA is playing */
{
    const SatTrack *t = track_at(g_pos, NULL);
    if (!g_playing || !t || (t->ctrl & 0x40)) return 0;
    *fad = g_pos, *end = g_play_end;
    return 1;
}

void sat_cd_init(const char *cue)
{
    if (sat_disc_open(cue)) port_log("cd: no disc (%s)\n", cue && *cue ? cue : "no cue given");
    cdb_reset(1);
    status_regs();
    sat_map(0x05890000u, 0x058a0000u, "CD", reg_read, reg_write);
    sat_map(0x05810000u, 0x05820000u, "CDDTR", dtr_read, dtr_write);
}
