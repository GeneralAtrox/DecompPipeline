/* Platform layer: the flash manager (fmMng*, the system library's block store in a block-allocated partition of the
 * console's 128 KB flash ROM), written from its disassembly, over a flash image the port holds. The image is --flash
 * FILE when given (host_flash_read: e.g. the dc_nvmem.bin an emulator run used, so the game reads what it read
 * there); otherwise a stock console's: the user partition (2) formatted and holding no blocks, the rest erased.
 * Writes change the image in memory only (flash bits clear, as the BIOS writes them); nothing is written back.
 *
 * The library's own behaviour is kept, including what it leaves in the caller's work buffer, which is game memory:
 *   fmMngInit(partition, work)  partitions 1..4 (else -7). The partition's first 64 bytes must be its header,
 *                               "KATANA_FLASH" then the partition number at 16 and the mode (0 or 1) at 17, else -8:
 *                               unformatted, and the caller formats. Then the work buffer (the partition's size) is
 *                               an image of it: 0xff, the header at 0, each valid block (its logical number below
 *                               the block count, its CRC right) at (number + 1) * 64, later copies winning, up to
 *                               the first free block, and the allocation bitmap after the last block. Mode 1 gives
 *                               -6.
 *   fmMngFormat(mode)           erases the partition and writes a header (mode 0 or 1, else -6; -11 with no Init).
 *   fmMngRead(block, buffer)    queues a read, fmMngWrite(block, buffer) a write (-11 no Init, -9 one is queued,
 *                               -5 block out of range); fmMngExecServer runs it: 2 busy, 1 done, 3 failed. A read is
 *                               one step and copies the block's 60 data bytes from the work buffer: a block never
 *                               written reads as 0xff bytes and succeeds; a CRC mismatch fails (-4). A write appends
 *                               the block; when the partition is full it takes two more steps, erasing it and writing
 *                               back the newest copy of each block (dropping blocks of all 0xff data).
 *   Fm_Finish()                 ends the session (fmMngExecServer then returns 0, the others -11).
 * Needs from the game's headers: the registry names of these six. */
#include "game.h"
#include "platform.h"

#define FLASH_SIZE 0x20000u
#define BLOCK 64u
#define BLOCK_DATA 60u
#define FREE_ID 0xffffu
#define FM_ERR_NO_INIT (-11)
#define FM_ERR_BUSY (-9)
#define FM_ERR_UNFORMATTED (-8)
#define FM_ERR_PARTITION (-7)
#define FM_ERR_MODE (-6)
#define FM_ERR_BLOCK (-5)
#define FM_ERR_CRC (-4)
#define FM_ERR_ERASE (-3)
#define FM_ERR_WRITE (-2)
#define FM_ERR_FULL (-10)
#define FM_BUSY 2
#define FM_DONE 1
#define FM_FAILED 3

static const char k_header[16] = {'K', 'A', 'T', 'A', 'N', 'A', '_', 'F', 'L', 'A', 'S', 'H', '_', '_', '_', '_'};
/* The BIOS's partitions: offset and size (factory, reserved, user, game, the unnamed 64 KB one). */
static const u32 k_parts[5][2] = {{0x1a000u, 0x2000u}, {0x18000u, 0x2000u}, {0x1c000u, 0x4000u},
                                  {0x10000u, 0x8000u}, {0x00000u, 0x10000u}};

static u8 g_flash[FLASH_SIZE];
static int g_loaded;

static struct {
    int open;
    u16 part, mode;
    u32 offset, size;
    s32 used;   /* blocks allocated, in physical order from block 1 */
    s32 blocks; /* logical blocks: the partition's blocks less the header and the bitmap */
    s32 maps;   /* bitmap blocks */
    u8 *work;
    s32 state;  /* queued request: 0 none, 1 read, 2 write */
    s32 status; /* fmMngExecServer's result */
    s32 step;   /* a write's step: 0 append, 1 erase, 2 write back */
    s32 block;
    s32 error;
    void *buffer;
    u8 tmp[BLOCK];
} g_fm;

static void copy(u8 *dst, const u8 *src, u32 n)
{
    for (u32 i = 0; i < n; i++) dst[i] = src[i];
}

static void fill(u8 *dst, u8 v, u32 n)
{
    for (u32 i = 0; i < n; i++) dst[i] = v;
}

static u16 get16(const u8 *p) { return (u16)(p[0] | (p[1] << 8)); }

static void put16(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
}

/* The image, loaded at the first Init. The stock one: erased, the user partition's header written. */
static void load(void)
{
    if (g_loaded) return;
    g_loaded = 1;
    int got = host_flash_read(g_flash, FLASH_SIZE);
    if (got == 1) {
        port_log("fm: flash image from --flash\n");
        return;
    }
    if (got < 0) port_log("fm: --flash is not a readable %u-byte image: a stock console's flash instead\n", FLASH_SIZE);
    fill(g_flash, 0xffu, FLASH_SIZE);
    copy(g_flash + k_parts[2][0], (const u8 *)k_header, sizeof k_header);
    g_flash[k_parts[2][0] + 16u] = 2;
    g_flash[k_parts[2][0] + 17u] = 0;
}

/* The BIOS's flash calls, over the image (an address past its end wraps, as the chip's address lines do). */
static void flash_read(u32 offset, u8 *dst, u32 n)
{
    for (u32 i = 0; i < n; i++) dst[i] = g_flash[(offset + i) & (FLASH_SIZE - 1u)];
}

static s32 flash_write(u32 offset, const u8 *src, u32 n)
{
    for (u32 i = 0; i < n; i++) g_flash[(offset + i) & (FLASH_SIZE - 1u)] &= src[i];
    return (s32)n;
}

static s32 flash_erase(u32 offset)
{
    s32 found = -1;
    for (u32 p = 0; p < 5u; p++)
        if (k_parts[p][0] == offset) {
            fill(g_flash + offset, 0xffu, k_parts[p][1]);
            found = 0;
        }
    return found;
}

/* The block CRC: CRC-16/CCITT over the id and data (62 bytes), from 0xffff, inverted. */
static u32 crc(const u8 *p)
{
    u32 n = 0xffffu;
    for (u32 i = 0; i < BLOCK - 2u; i++) {
        n ^= (u32)p[i] << 8;
        for (u32 b = 0; b < 8u; b++) n = (n & 0x8000u) ? ((n << 1) ^ 0x1021u) : (n << 1);
    }
    return ~n & 0xffffu;
}

static u8 *bitmap(void) { return g_fm.work + (u32)(g_fm.blocks + 1) * BLOCK; }

/* Allocated blocks: the bitmap's leading 0 bits, most significant first. */
static s32 count_used(const u8 *map)
{
    s32 n = 0;
    for (u32 i = 0; i < (u32)g_fm.maps * BLOCK; i++)
        for (u32 b = 0; b < 8u; b++, n++)
            if (((u32)map[i] << b) & 0x80u) return n;
    return n;
}

static void make_bitmap(s32 used, u8 *map)
{
    for (u32 i = 0; i < (u32)g_fm.maps * BLOCK; i++) {
        if (used >= 8) {
            map[i] = 0;
            used -= 8;
        } else if (used > 0) {
            u32 v = 0;
            for (u32 b = 0; b < 8u; b++) {
                v <<= 1;
                if (used > 0) used--;
                else v |= 1u;
            }
            map[i] = (u8)v;
        } else {
            map[i] = 0xffu;
        }
    }
}

/* The work buffer from the partition: its header, its bitmap, then each valid block in physical order, stopping at
 * the first free one. */
static s32 scan(void)
{
    flash_read(g_fm.offset, g_fm.work, BLOCK);
    flash_read(g_fm.offset + (u32)(g_fm.blocks + 1) * BLOCK, bitmap(), (u32)g_fm.maps * BLOCK);
    g_fm.used = count_used(bitmap());
    if (g_fm.mode != 0) return FM_ERR_MODE;
    for (s32 i = 0; i < g_fm.used; i++) {
        flash_read(g_fm.offset + (u32)(i + 1) * BLOCK, g_fm.tmp, BLOCK);
        u32 id = get16(g_fm.tmp);
        if (id == FREE_ID) break;
        if (crc(g_fm.tmp) == get16(g_fm.tmp + 62) && (s32)id < g_fm.blocks)
            copy(g_fm.work + (id + 1u) * BLOCK, g_fm.tmp, BLOCK);
    }
    return 0;
}

s32 fmMngInit(s32 partition, void *work)
{
    u8 *h = g_fm.tmp;
    if (partition < 1 || partition >= 5) return FM_ERR_PARTITION;
    load();
    g_fm.open = 1;
    g_fm.part = (u16)partition;
    g_fm.offset = k_parts[partition][0];
    g_fm.size = k_parts[partition][1];
    g_fm.maps = (s32)((g_fm.size + 0x7fffu) >> 15);
    g_fm.blocks = (s32)(g_fm.size >> 6) - g_fm.maps - 1;
    g_fm.work = (u8 *)work;
    g_fm.state = g_fm.status = g_fm.error = 0;
    flash_read(g_fm.offset, h, BLOCK);
    for (u32 i = 0; i < 12u; i++)
        if (h[i] != (u8)k_header[i]) return FM_ERR_UNFORMATTED;
    if ((s8)h[16] != partition) return FM_ERR_UNFORMATTED;
    g_fm.mode = (u16)(s16)(s8)h[17];
    if (g_fm.mode != 0 && g_fm.mode != 1) return FM_ERR_UNFORMATTED;
    fill(g_fm.work, 0xffu, g_fm.size);
    return scan();
}

s32 fmMngFormat(s32 mode)
{
    if (!g_fm.open) return FM_ERR_NO_INIT;
    if (mode != 0 && mode != 1) return FM_ERR_MODE;
    if (flash_erase(g_fm.offset) != 0) return FM_ERR_ERASE;
    copy(g_fm.tmp, (const u8 *)k_header, sizeof k_header);
    g_fm.mode = (u16)mode;
    g_fm.tmp[17] = (u8)mode;
    g_fm.tmp[16] = (u8)g_fm.part;
    if (flash_write(g_fm.offset, g_fm.tmp, 18) != 18) return FM_ERR_WRITE;
    fill(g_fm.work, 0xffu, g_fm.size);
    return scan();
}

static s32 queue(s32 state, s32 block, void *buffer)
{
    if (!g_fm.open) return FM_ERR_NO_INIT;
    if (g_fm.state != 0) return FM_ERR_BUSY;
    if (block < 0 || block >= g_fm.blocks) return FM_ERR_BLOCK;
    g_fm.state = state;
    g_fm.status = FM_BUSY;
    g_fm.block = block;
    g_fm.buffer = buffer;
    g_fm.step = 0;
    return 0;
}

s32 fmMngRead(s32 block, void *buffer) { return queue(1, block, buffer); }

s32 fmMngWrite(s32 block, const void *buffer) { return queue(2, block, (void *)buffer); }

static s32 read_step(void)
{
    const u8 *e = g_fm.work + (u32)(g_fm.block + 1) * BLOCK;
    if (g_fm.mode != 0) return FM_ERR_MODE;
    copy((u8 *)g_fm.buffer, e + 2, BLOCK_DATA);
    if (get16(e) == FREE_ID || crc(e) == get16(e + 62)) return 0;
    return FM_ERR_CRC;
}

/* The block at the end of the allocated ones (its copy in the work buffer first), then the bitmap. */
static s32 append(void)
{
    u8 *t = g_fm.tmp;
    u32 map = (u32)(g_fm.blocks + 1) * BLOCK, maps = (u32)g_fm.maps * BLOCK;
    if (g_fm.mode != 0) return FM_ERR_MODE;
    put16(t, (u32)g_fm.block);
    copy(t + 2, (const u8 *)g_fm.buffer, BLOCK_DATA);
    put16(t + 62, crc(t));
    copy(g_fm.work + (u32)(g_fm.block + 1) * BLOCK, t, BLOCK);
    if (g_fm.used >= g_fm.blocks) return FM_ERR_FULL;
    make_bitmap(g_fm.used + 1, bitmap());
    if (flash_write(g_fm.offset + map, bitmap(), maps) != (s32)maps) return FM_ERR_WRITE;
    g_fm.used++;
    if (flash_write(g_fm.offset + (u32)g_fm.used * BLOCK, t, BLOCK) != (s32)BLOCK) return FM_ERR_WRITE;
    return 0;
}

/* A block whose data is all 0xff with its CRC right is dropped at a write back (its work copy erased). */
static int drop_blank(u8 *e)
{
    for (u32 i = 2; i < 2u + BLOCK_DATA; i++)
        if (e[i] != 0xffu) return 0;
    if (crc(e) != get16(e + 62)) return 0;
    fill(e, 0xffu, BLOCK);
    return 1;
}

/* The erased partition from the work buffer: the header, each block in logical order, the bitmap. */
static s32 write_back(void)
{
    u32 at = g_fm.offset + BLOCK, maps = (u32)g_fm.maps * BLOCK;
    s32 n = 0;
    if (g_fm.mode != 0) return FM_ERR_MODE;
    if (flash_write(g_fm.offset, g_fm.work, BLOCK) != (s32)BLOCK) return FM_ERR_WRITE;
    for (s32 i = 0; i < g_fm.blocks; i++) {
        u8 *e = g_fm.work + (u32)(i + 1) * BLOCK;
        if (get16(e) == FREE_ID || drop_blank(e)) continue;
        if (flash_write(at, e, BLOCK) != (s32)BLOCK) return FM_ERR_WRITE;
        n++;
        at += BLOCK;
    }
    make_bitmap(n, bitmap());
    if (flash_write(g_fm.offset + (u32)(g_fm.blocks + 1) * BLOCK, bitmap(), maps) != (s32)maps) return FM_ERR_WRITE;
    g_fm.used = n;
    return 0;
}

/* One step of a write: FM_DONE (1) while it has more steps, else its result. */
static s32 write_step(void)
{
    switch (g_fm.step) {
    case 0: {
        s32 r = append();
        if (r != FM_ERR_FULL) return r;
        g_fm.step = 1;
        return 1;
    }
    case 1:
        if (flash_erase(g_fm.offset) == -1) return FM_ERR_ERASE;
        g_fm.step = 2;
        return 1;
    case 2:
        return write_back();
    default:
        return FM_ERR_WRITE;
    }
}

s32 fmMngExecServer(void)
{
    s32 r;
    if (!g_fm.open) return 0;
    switch (g_fm.state) {
    case 0:
        return g_fm.status;
    case 1:
        r = read_step();
        break;
    case 2:
        r = write_step();
        if (r == 1) return g_fm.status;
        break;
    default:
        g_fm.state = g_fm.status = 0;
        return 0;
    }
    g_fm.state = 0;
    g_fm.status = r ? FM_FAILED : FM_DONE;
    g_fm.error = r;
    return g_fm.status;
}

void Fm_Finish(void) { g_fm.open = 0; }
