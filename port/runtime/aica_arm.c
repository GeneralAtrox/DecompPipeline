/* The AICA's ARM7 and its bus (aica_arm.h): the register file's reads and writes from both sides, the timers, the
 * interrupt controller and the ARM's time, run in the vertical blank's mix (aica.c's slice hook) and while the SH-4
 * polls the driver. */
#include <string.h>
#include <time.h>
#include "aica_arm.h"
#include "arm7.h"

#define ARM_RAM_WINDOW 0x00800000u /* below: the sound RAM, mirrored */
#define ARM_CYCLES_PER_SAMPLE 512  /* 22,579,200 Hz / 44,100 Hz */
#define SLICE_FRAMES 16u           /* the mix's slice: 0.36 ms */
#define AHEAD_MAX AICA_VBLANK_MAX_FRAMES
#define REG_MSLC 0x280cu
#define REG_MONITOR_EG 0x2810u
#define REG_MONITOR_CA 0x2814u
#define REG_DMA 0x2880u
#define REG_TIMER_A 0x2890u
#define REG_SCIEB 0x289cu
#define REG_SCIPD 0x28a0u
#define REG_SCIRE 0x28a4u
#define REG_SCILV0 0x28a8u
#define REG_MCIEB 0x28b4u
#define REG_MCIPD 0x28b8u
#define REG_MCIRE 0x28bcu
#define REG_L 0x2d00u
#define REG_M 0x2d04u
#define INT_SCPU 0x20u
#define INT_TIMER_A 0x40u
#define INT_SAMPLE 0x400u
#define INT_MASK 0x7ffu

static Arm7 g_arm;
static int g_running;           /* the ARM is out of reset */
static u32 g_sci_pending, g_mci_pending;
static u32 g_timer_div[3];      /* samples since the timer's last count */
static int g_int_out;           /* L holds an interrupt for the ARM */
static u32 g_l;
static u32 g_ahead;             /* samples the ARM has run ahead of the mix (while the SH-4 polled) */
static u64 g_samples;           /* samples the ARM has run */
static int g_timed;             /* with the key log (--audio-keys): the ARM's host time logged every 10 s */
static u64 g_time_ns, g_time_from;

int aica_arm_running(void) { return g_running; }

/* The interrupt controller: the lowest pending enabled interrupt's level (SCILV0..2's bits for it; bits 8..10 use
 * bit 7's), latched into L while none is held. */
static void update_interrupts(void)
{
    u32 pending = g_sci_pending & aica_reg16(REG_SCIEB) & INT_MASK;
    if (!g_int_out && pending) {
        u32 i = 0;
        while (!(pending >> i & 1u)) i++;
        u32 bit = 1u << (i > 7u ? 7u : i), level = 0;
        for (u32 n = 0; n < 3; n++)
            if (aica_reg16(REG_SCILV0 + 4u * n) & bit) level |= 1u << n;
        g_int_out = 1;
        g_l = level;
    }
    g_arm.fiq = g_int_out;
}

static void int_raise(u32 bits)
{
    g_sci_pending |= bits;
    g_mci_pending |= bits;
}

/* One sample of the chip's time: the ARM's cycles, then the timers and the sample interrupt. */
static void sample_step(void)
{
    arm7_run(&g_arm, ARM_CYCLES_PER_SAMPLE);
    for (u32 t = 0; t < 3; t++) {
        u32 reg = REG_TIMER_A + 4u * t, v = aica_reg16(reg);
        if (++g_timer_div[t] < 1u << (v >> 8 & 7u)) continue;
        g_timer_div[t] = 0;
        v = (v & 0x0700u) | ((v + 1u) & 0xffu);
        g_aica_regs[reg] = (u8)v;
        if ((v & 0xffu) == 0) int_raise(INT_TIMER_A << t);
    }
    int_raise(INT_SAMPLE);
    update_interrupts();
    g_samples++;
}

static u64 now_ns(void)
{
    struct timespec t;
    timespec_get(&t, TIME_UTC);
    return (u64)t.tv_sec * 1000000000u + (u64)t.tv_nsec;
}

/* Runs n samples, timed when asked: 10 s of samples log the host time they took (a measure, off by default). */
static void run_samples(u32 n)
{
    u64 t0 = g_timed ? now_ns() : 0;
    for (u32 i = 0; i < n; i++) sample_step();
    if (!g_timed) return;
    g_time_ns += now_ns() - t0;
    if (g_samples - g_time_from >= (u64)AICA_RATE * 10u) {
        double vblanks = (double)(g_samples - g_time_from) * 60000.0 / (AICA_RATE * 1001.0);
        port_log("aica: the ARM's host time: %.1f ms for %.0f blanks' samples, %.1f us a blank\n", g_time_ns / 1e6,
                 vblanks, g_time_ns / 1e3 / vblanks);
        g_time_ns = 0;
        g_time_from = g_samples;
    }
}

static void slice(u32 frames)
{
    u32 behind = frames;
    if (!g_running) return;
    if (g_ahead >= frames) {
        g_ahead -= frames;
        return;
    }
    behind -= g_ahead;
    g_ahead = 0;
    run_samples(behind);
}

void aica_arm_poll(void)
{
    if (!g_running) return;
    if (g_ahead + SLICE_FRAMES > AHEAD_MAX) {
        port_wait_vblank();
        return;
    }
    run_samples(SLICE_FRAMES);
    g_ahead += SLICE_FRAMES;
}

static u32 reg_read(u32 offset, int arm)
{
    u32 reg = offset & (AICA_REG_SIZE - 4u);
    u32 value;
    if (arm && reg == REG_L) value = g_l;
    else if (reg == REG_MONITOR_EG || reg == REG_MONITOR_CA)
        value = aica_chan_monitor(aica_reg16(REG_MSLC) >> 8 & 63u, reg);
    else if (reg == REG_SCIPD) value = g_sci_pending;
    else if (reg == REG_MCIPD) value = g_mci_pending;
    else if (reg == REG_SCIRE || reg == REG_MCIRE || reg == REG_M) value = 0;
    else value = aica_reg16(reg);
    return offset & 2u ? 0u : value;
}

static void start_arm(void)
{
    arm7_reset(&g_arm);
    g_arm.ram = aica_ram();
    g_arm.ram_mask = AICA_RAM_SIZE - 1u;
    g_arm.ram_size = g_arm.ram ? ARM_RAM_WINDOW : 0u;
    g_running = g_arm.ram != NULL;
    g_ahead = 0;
    audio_start(); /* the sound device, the dump and the key log */
    g_timed = aica_key_logging();
    aica_slice_hook(slice, SLICE_FRAMES);
    port_log("aica: the ARM7 runs the sound driver (%u cycles a sample)%s\n", ARM_CYCLES_PER_SAMPLE,
             g_running ? "" : ": no sound RAM");
}

/* A 16-bit register written (the byte or halfword already merged into the register file) from either side. */
static void reg_written(u32 reg, u32 value, int arm)
{
    if (reg < AICA_VOICES * AICA_CHAN_SIZE) {
        aica_chan_write(reg / AICA_CHAN_SIZE, reg % AICA_CHAN_SIZE);
        return;
    }
    switch (reg) {
    case REG_TIMER_A:
    case REG_TIMER_A + 4u:
    case REG_TIMER_A + 8u: g_timer_div[(reg - REG_TIMER_A) / 4u] = 0; break;
    case REG_SCIEB: g_aica_regs[reg + 1u] &= (u8)(INT_MASK >> 8); break;
    case REG_SCIPD:
        if (value & INT_SCPU) g_sci_pending |= INT_SCPU;
        break;
    case REG_SCIRE: g_sci_pending &= ~value; break;
    case REG_MCIPD:
        if (value & INT_SCPU) g_mci_pending |= INT_SCPU;
        break;
    case REG_MCIRE: g_mci_pending &= ~value; break;
    case REG_M:
        if (arm && (value & 1u)) g_int_out = 0; /* the interrupt taken: L may take the next */
        break;
    case AICA_ARM_RESET:
        if (arm) break;
        if (value & 1u) g_running = 0;
        else if (!g_running) {
#ifdef PORT_AICA_ARM
            start_arm();
#else
            port_log("aica: the ARM's reset released, but game.json does not run the ARM (aica_arm)\n");
#endif
        }
        break;
    default:
        if (reg >= REG_DMA && reg < REG_TIMER_A && (aica_reg16(REG_DMA + 0x8u) & 1u))
            port_log("aica: DMA (%04x) is not run\n", aica_reg16(REG_DMA + 0x8u));
        break;
    }
    update_interrupts();
}

static void reg_write(u32 offset, u32 value, u32 size, int arm)
{
    u32 reg = offset & (AICA_REG_SIZE - 4u);
    if (size == 1) {
        if (offset & 2u) return;
        g_aica_regs[reg + (offset & 1u)] = (u8)value;
    } else {
        if (offset & 2u) return; /* the upper half of a register's 4-byte step holds nothing */
        g_aica_regs[reg] = (u8)value;
        g_aica_regs[reg + 1u] = (u8)(value >> 8);
    }
    reg_written(reg, aica_reg16(reg), arm);
}

static u32 arm_read(Arm7 *a, u32 addr, u32 size)
{
    (void)a;
    u32 v = reg_read(addr & (AICA_REG_SIZE - 1u), 1);
    return size == 1 ? v >> ((addr & 1u) * 8u) & 0xffu : v;
}

static void arm_write(Arm7 *a, u32 addr, u32 value, u32 size)
{
    (void)a;
    reg_write(addr & (AICA_REG_SIZE - 1u), value, size, 1);
}

u32 aica_arm_sh4_read(u32 offset) { return reg_read(offset & (AICA_REG_SIZE - 1u), 0); }

void aica_arm_sh4_write(u32 offset, u32 value)
{
    static int init;
    if (!init) {
        init = 1;
        g_arm.rd = arm_read;
        g_arm.wr = arm_write;
        aica_chan_reset();
    }
    if ((offset & (AICA_REG_SIZE - 1u)) == AICA_ARM_RESET) {
        g_aica_regs[AICA_ARM_RESET] = (u8)value;
        reg_written(AICA_ARM_RESET, value & 0xffffu, 0);
        return;
    }
    reg_write(offset & (AICA_REG_SIZE - 1u), value, 4, 0);
}
