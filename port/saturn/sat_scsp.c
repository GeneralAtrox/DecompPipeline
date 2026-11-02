/* The sound side: 512 KB of sound RAM (0x05a00000) and the SCSP's registers (0x05b00000), seen by the SH-2 and, as the
 * driver's own bus, by the 68000 (sat_m68k.c). SMPC SNDON starts the 68000 on the driver the game loaded into sound RAM;
 * each video frame runs it for one frame of clocks together with the SCSP's timers, interrupts and 32 voices
 * (sat_scsp_mix.c), and the mixed samples go to the wave device (sat_audio.c).
 *
 * A game whose game.json has no `sound_cpu` keeps the older stand-in: no 68000 runs, and game.json lists the mailbox bytes the
 * driver would clear (`sound_clear`) and a stream's progress reports (`sound_stream`). */
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_game.h"
#include "sat_sys.h"
#include "sat_scsp.h"
#include "sat_m68k.h"

#define SRAM_LO 0x05a00000u
#define SRAM_SIZE 0x00080000u
#define SCSP_LO 0x05b00000u
#define SCSP_SIZE 0x1000u

u8 sat_sound_ram_bytes[SRAM_SIZE];
u8 sat_scsp_regs[SCSP_SIZE];
#define g_sram sat_sound_ram_bytes
#define g_scsp sat_scsp_regs

static u32 load_be(const u8 *mem, u32 o, u32 size)
{
    u32 v = 0;
    for (u32 i = 0; i < size; i++) v = (v << 8) | mem[o + i];
    return v;
}

static void store_be(u8 *mem, u32 o, u32 size, u32 value)
{
    for (u32 i = 0; i < size; i++) mem[o + i] = (u8)(value >> (8u * (size - 1u - i)));
}

u8 *sat_sound_ram(void) { return g_sram; }

#ifdef SAT_SOUND_CPU
static int g_cpu_on;
static u32 g_scieb, g_scipd, g_mcieb, g_mcipd, g_mcipd_seen;
static u32 g_timer[3], g_tpre[3];
enum { IRQ_CPU = 0x20, IRQ_TIMER_A = 0x40, IRQ_SAMPLE = 0x400 };

static u32 reg16(u32 r) { return (u32)g_scsp[r] << 8 | g_scsp[r + 1]; }

static void update_irq(void)
{
    u32 p = g_scipd & g_scieb, ipl = 0;
    for (u32 i = 0; i < 11; i++) {
        u32 lv;
        if (!(p >> i & 1u)) continue;
        lv = (reg16(0x424) >> i & 1u) | (reg16(0x426) >> i & 1u) << 1 | (reg16(0x428) >> i & 1u) << 2;
        if (lv > ipl) ipl = lv;
    }
    m68k_set_ipl((int)ipl);
    if ((g_mcipd & g_mcieb) && !(g_mcipd_seen & g_mcieb)) sat_scu_raise(6); /* SCU: sound request */
    g_mcipd_seen = g_mcipd & g_mcieb;
}

static u32 reg_read16(u32 r)
{
    switch (r) {
    case 0x408: return (reg16(r) & 0xf800u) | sat_mix_call_address(reg16(r) >> 11 & 31u) << 7;
    case 0x418: case 0x41a: case 0x41c: return (reg16(r) & 0x700u) | g_timer[(r - 0x418) / 2];
    case 0x420: return g_scipd;
    case 0x42c: return g_mcipd;
    default: return reg16(r);
    }
}

static void reg_written(u32 r)
{
    u32 v = reg16(r);
    if (r < 0x400) {
        sat_mix_slot_written(r);
        if ((r & 0x1fu) == 0 && (v & 0x1000u)) sat_mix_keyex();
        return;
    }
    switch (r) {
    case 0x418: case 0x41a: case 0x41c: g_timer[(r - 0x418) / 2] = v & 0xffu, g_tpre[(r - 0x418) / 2] = 0; break;
    case 0x41e: g_scieb = v; break;
    case 0x420: if (v & IRQ_CPU) g_scipd |= IRQ_CPU; break;
    case 0x422: g_scipd &= ~v; break;
    case 0x42a: g_mcieb = v; break;
    case 0x42c: if (v & IRQ_CPU) g_mcipd |= IRQ_CPU; break;
    case 0x42e: g_mcipd &= ~v; break;
    default: break;
    }
    update_irq();
}

static u32 scsp_read(u32 o, u32 size)
{
    u32 v = 0;
    for (u32 b = o; b < o + size; b++) {
        u32 w = reg_read16(b & ~1u);
        v = v << 8 | (b & 1u ? w & 0xffu : w >> 8);
    }
    return v;
}

static void scsp_write(u32 o, u32 size, u32 value)
{
    store_be(g_scsp, o, size, value);
    for (u32 r = o & ~1u; r < o + size; r += 2) reg_written(r);
}

u32 sat_snd_rd(u32 a, int size)
{
    if (a < 0x100000u) return load_be(g_sram, a & (SRAM_SIZE - 1u), (u32)size);
    if (a >= 0x100000u && a + (u32)size <= 0x100000u + SCSP_SIZE) return scsp_read(a - 0x100000u, (u32)size);
    return 0;
}

void sat_snd_wr(u32 a, int size, u32 value)
{
    if (a < 0x100000u) store_be(g_sram, a & (SRAM_SIZE - 1u), (u32)size, value);
    else if (a + (u32)size <= 0x100000u + SCSP_SIZE) scsp_write(a - 0x100000u, (u32)size, value);
}

void sat_scsp_cpu(int on)
{
    if (on && !g_cpu_on) {
        m68k_reset();
        port_log("scsp: SNDON, the 68000 starts at %06x with stack %06x\n", m68k.pc, m68k.a[7]);
    }
    g_cpu_on = on;
}

static void timers(void)
{
    for (u32 t = 0; t < 3; t++) {
        u32 shift = reg16(0x418 + 2 * t) >> 8 & 7u;
        if (++g_tpre[t] < (1u << shift)) continue;
        g_tpre[t] = 0;
        if (++g_timer[t] == 0x100u) {
            g_timer[t] = 0;
            g_scipd |= IRQ_TIMER_A << t;
            g_mcipd |= IRQ_TIMER_A << t;
        }
    }
}

/* one video frame of sound: the 68000 and the SCSP in steps of 16 samples (256 clocks a sample at 11.29 MHz) */
void sat_scsp_frame(void)
{
    LARGE_INTEGER t0, t1;
    static s16 out[2048 * 2];
    static u32 carry, frames;
    u32 n = (SAT_SCSP_RATE + carry) / sat_sys_hz(), done = 0;
    carry = (SAT_SCSP_RATE + carry) % sat_sys_hz();
    QueryPerformanceCounter(&t0);
    if (!sat_sys_fast() && !sat_sys_virtual()) {
        /* On the real clock the sound follows the clock, not the game's frames: a frame that took longer than its 20 ms
         * is still followed by all the sound that time held, so a slow picture does not make the sound stutter. */
        static LONGLONG last, rem;
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        if (last) {
            LONGLONG t = (t0.QuadPart - last) * (LONGLONG)SAT_SCSP_RATE + rem;
            n = (u32)(t / f.QuadPart);
            rem = t % f.QuadPart;
        }
        last = t0.QuadPart;
    }
    if (n > 2048) n = 2048;
    while (done < n) {
        u32 step = n - done < 16u ? n - done : 16u;
        if (g_cpu_on) m68k_run(256u * step);
        for (u32 k = 0; k < step; k++, done++) {
            s32 l, r;
            timers();
            sat_mix_sample(&l, &r);
            out[2 * done] = (s16)(l > 32767 ? 32767 : l < -32768 ? -32768 : l);
            out[2 * done + 1] = (s16)(r > 32767 ? 32767 : r < -32768 ? -32768 : r);
        }
        if ((g_scieb | g_mcieb) & IRQ_SAMPLE) g_scipd |= IRQ_SAMPLE, g_mcipd |= IRQ_SAMPLE;
        update_irq();
    }
    sat_audio_push(out, n);
    QueryPerformanceCounter(&t1);
    sat_perf_audio(t1.QuadPart - t0.QuadPart);
    if (++frames % 250u == 0)
        port_log("scsp: frame %u, 68K %s at %06x ipl %u, %u voices on, SCIEB %03x SCIPD %03x MCIEB %03x\n", frames, g_cpu_on ? "on" : "off", m68k.pc,
                 (unsigned)m68k.ipl, sat_mix_active(), g_scieb, g_scipd, g_mcieb);
}
#else
static int g_cpu_on;
u32 sat_snd_rd(u32 a, int size)
{
    (void)a, (void)size;
    return 0;
}
void sat_snd_wr(u32 a, int size, u32 value) { (void)a, (void)size, (void)value; }
#ifdef SAT_SOUND_STREAM
static int g_stream_on;
#endif

static u32 scsp_read(u32 o, u32 size) { return o + size <= SCSP_SIZE ? load_be(g_scsp, o, size) : 0u; }

static void scsp_write(u32 o, u32 size, u32 value)
{
    if (o + size <= SCSP_SIZE) store_be(g_scsp, o, size, value);
}

void sat_scsp_cpu(int on) { g_cpu_on = on; }

/* Each frame a running 68K driver would have taken the commands the SH-2 left in its mailbox bytes (and cleared its
 * own "starting" flags): the game's list of those bytes (game.json saturn.sound_clear) is zeroed. */
void sat_scsp_frame(void)
{
    if (!g_cpu_on) return;
    for (int i = 0; i < SAT_NSOUND_CLEAR; i++) g_sram[sat_sound_clear[i] & (SRAM_SIZE - 1u)] = 0;
#ifdef SAT_SOUND_STREAM
    /* A stream the driver plays reports each block it has played by changing the stream's status byte; the game's own
     * clock of the stream (what it may buffer ahead) runs on those changes. After the game's start command every status byte
     * steps once per SAT_SS_BLOCK bytes of the stream's rate, which is what the driver would have reported. */
    if (g_stream_on) {
        static u32 acc;
        acc += SAT_SS_RATE;
        while (acc >= SAT_SS_BLOCK * sat_sys_hz()) {
            acc -= SAT_SS_BLOCK * sat_sys_hz();
            for (u32 k = 0; k < SAT_SS_STATUS_N; k++) g_sram[(SAT_SS_STATUS_ADDR + k * SAT_SS_STATUS_STEP) & (SRAM_SIZE - 1u)]++;
        }
    }
#endif
}
#endif

static u32 sram_read(u32 p, u32 size)
{
    static u32 reads;
    if ((++reads & 0x3ffu) == 0) sat_busy_poll(); /* a loop on a mailbox byte: the frame (and the 68K's answer) must come */
    return load_be(g_sram, (p - SRAM_LO) & (SRAM_SIZE - 1u), size);
}

static void sram_write(u32 p, u32 size, u32 value)
{
    u32 o = (p - SRAM_LO) & (SRAM_SIZE - 1u);
#if defined(SAT_SOUND_STREAM) && !defined(SAT_SOUND_CPU)
    if (o == SAT_SS_START_ADDR && size == 1u && (value & 0xffu) == SAT_SS_START_CMD) g_stream_on = 1;
#endif
    store_be(g_sram, o, size, value);
}

static u32 scsp_map_read(u32 p, u32 size) { return scsp_read(p - SCSP_LO, size); }
static void scsp_map_write(u32 p, u32 size, u32 value) { scsp_write(p - SCSP_LO, size, value); }

void sat_scsp_init(void)
{
    memset(g_sram, 0, sizeof g_sram);
    memset(g_scsp, 0, sizeof g_scsp);
#ifdef SAT_SOUND_CPU
    sat_mix_reset();
#endif
    sat_map(SRAM_LO, SRAM_LO + SRAM_SIZE, "SoundRAM", sram_read, sram_write);
    sat_map(SCSP_LO, SCSP_LO + 0x10000u, "SCSP", scsp_map_read, scsp_map_write);
}
