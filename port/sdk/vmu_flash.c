/* Platform layer: the VMU flash file system on a card image (vmu_flash.h has the layout). Data files take free user
 * blocks from the top down, as the console's own file system does; games (none are saved here) start at block 0. */
#include "vmu_flash.h"

#define ROOT_BLOCK 255u
#define FAT_BLOCK 254u
#define DIR_BLOCK 253u
#define DIR_BLOCKS 13u
#define DIR_ENTRY 32u
#define FAT_FREE 0xfffcu
#define FAT_END 0xfffau
#define TYPE_DATA 0x33u
#define ROOT_X52 31u    /* root words 0x52 and 0x56 as the BIOS formats a card (their meaning is not known) */
#define ROOT_X56 0x80u

u32 vmfs_rd16(const u8 *p) { return (u32)p[0] | (u32)p[1] << 8; }

void vmfs_wr16(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
}

void vmfs_copy(u8 *dst, const u8 *src, u32 n)
{
    while (n--) *dst++ = *src++;
}

static void fill(u8 *p, u8 v, u32 n)
{
    while (n--) *p++ = v;
}

static u32 fat(const u8 *img, u32 b) { return vmfs_rd16(img + FAT_BLOCK * VMFS_BLOCK + 2u * b); }
static void fat_set(u8 *img, u32 b, u32 v) { vmfs_wr16(img + FAT_BLOCK * VMFS_BLOCK + 2u * b, v); }

const u8 *vmfs_root(const u8 *img) { return img + ROOT_BLOCK * VMFS_BLOCK; }

u8 *vmfs_entry(u8 *img, u32 i) { return img + (DIR_BLOCK - i / 16u) * VMFS_BLOCK + i % 16u * DIR_ENTRY; }

static u8 bcd(u32 v) { return (u8)((v / 10u % 10u) << 4 | v % 10u); }
static u32 unbcd(u8 v) { return (v >> 4) * 10u + (v & 15u); }

/* The card's BCD time from a BUS_TIME, or from the host's clock when time is NULL. */
static void bcd_time(u8 *out, const u8 *time)
{
    HostTime t;
    u32 v[8];
    if (time) {
        v[0] = vmfs_rd16(time) / 100u, v[1] = vmfs_rd16(time) % 100u;
        for (u32 i = 2; i < 8; i++) v[i] = time[i];
    } else {
        host_local_time(&t);
        v[0] = t.year / 100u, v[1] = t.year % 100u, v[2] = t.month, v[3] = t.day;
        v[4] = t.hour, v[5] = t.minute, v[6] = t.second, v[7] = t.day_of_week;
    }
    for (u32 i = 0; i < 8; i++) out[i] = bcd(v[i]);
}

void vmfs_bus_time(u8 *out, const u8 *bcd8)
{
    vmfs_wr16(out, unbcd(bcd8[0]) * 100u + unbcd(bcd8[1]));
    for (u32 i = 2; i < 8; i++) out[i] = (u8)unbcd(bcd8[i]);
}

void vmfs_format(u8 *img)
{
    u8 *root = img + ROOT_BLOCK * VMFS_BLOCK;
    fill(img, 0, VMFS_SIZE);
    fill(root, 0x55, 16);
    bcd_time(root + 0x30, NULL);
    vmfs_wr16(root + 0x40, VMFS_BLOCKS - 1u);
    vmfs_wr16(root + 0x44, ROOT_BLOCK);
    vmfs_wr16(root + 0x46, FAT_BLOCK);
    vmfs_wr16(root + 0x48, 1);
    vmfs_wr16(root + 0x4a, DIR_BLOCK);
    vmfs_wr16(root + 0x4c, DIR_BLOCKS);
    vmfs_wr16(root + 0x50, VMFS_USER_BLOCKS);
    vmfs_wr16(root + 0x52, ROOT_X52);
    vmfs_wr16(root + 0x56, ROOT_X56);
    for (u32 b = 0; b < VMFS_BLOCKS; b++) fat_set(img, b, FAT_FREE);
    fat_set(img, ROOT_BLOCK, FAT_END);
    fat_set(img, FAT_BLOCK, FAT_END);
    for (u32 b = DIR_BLOCK; b > DIR_BLOCK - DIR_BLOCKS + 1u; b--) fat_set(img, b, b - 1u);
    fat_set(img, DIR_BLOCK - DIR_BLOCKS + 1u, FAT_END);
}

int vmfs_fix_root(u8 *img)
{
    u8 *root = img + ROOT_BLOCK * VMFS_BLOCK;
    if (vmfs_rd16(root + 0x42) != ROOT_BLOCK || vmfs_rd16(root + 0x44) != 0) return 0;
    vmfs_wr16(root + 0x42, 0);
    vmfs_wr16(root + 0x44, ROOT_BLOCK);
    vmfs_wr16(root + 0x52, ROOT_X52);
    vmfs_wr16(root + 0x56, ROOT_X56);
    return 1;
}

int vmfs_formatted(const u8 *img)
{
    const u8 *root = vmfs_root(img);
    for (u32 i = 0; i < 16; i++)
        if (root[i] != 0x55) return 0;
    return vmfs_rd16(root + 0x46) == FAT_BLOCK && vmfs_rd16(root + 0x4a) == DIR_BLOCK;
}

u32 vmfs_free(const u8 *img)
{
    u32 n = 0;
    for (u32 b = 0; b < VMFS_USER_BLOCKS; b++) n += fat(img, b) == FAT_FREE;
    return n;
}

u32 vmfs_free_from_start(const u8 *img)
{
    u32 n = 0;
    while (n < VMFS_USER_BLOCKS && fat(img, n) == FAT_FREE) n++;
    return n;
}

int vmfs_find(u8 *img, const char *name)
{
    for (u32 i = 0; i < VMFS_ENTRIES; i++) {
        const u8 *e = vmfs_entry(img, i);
        u32 k = 0;
        if (!e[0]) continue;
        while (k < VMFS_NAME_LEN && name[k] && (u8)name[k] == e[4 + k]) k++;
        if (k < VMFS_NAME_LEN && name[k]) continue;
        while (k < VMFS_NAME_LEN && (e[4 + k] == 0 || e[4 + k] == ' ')) k++;
        if (k == VMFS_NAME_LEN) return (int)i;
    }
    return -1;
}

void vmfs_name(const u8 *entry, char *out)
{
    u32 k = 0;
    for (; k < VMFS_NAME_LEN && entry[4 + k] && entry[4 + k] != ' '; k++) out[k] = (char)entry[4 + k];
    out[k] = '\0';
}

static void free_chain(u8 *img, u32 b)
{
    for (u32 n = 0; b < VMFS_BLOCKS && n < VMFS_BLOCKS; n++) {
        u32 next = fat(img, b);
        fat_set(img, b, FAT_FREE);
        b = next;
    }
}

int vmfs_save(u8 *img, const char *name, const u8 *data, u32 blocks, const u8 *time, u8 copyflag)
{
    int i = vmfs_find(img, name);
    u8 *e = i >= 0 ? vmfs_entry(img, (u32)i) : NULL;
    u32 b = VMFS_USER_BLOCKS, prev = FAT_END;
    if (e && e[0] == VMFS_TYPE_GAME) return VMFS_GAME;
    if (blocks > vmfs_free(img) + (e ? vmfs_rd16(e + 0x18) : 0)) return VMFS_FULL;
    for (u32 k = 0; !e && k < VMFS_ENTRIES; k++)
        if (!vmfs_entry(img, k)[0]) e = vmfs_entry(img, k);
    if (!e) return VMFS_FULL;
    if (i >= 0) free_chain(img, vmfs_rd16(e + 2));
    fill(e, 0, DIR_ENTRY);
    vmfs_wr16(e + 2, FAT_END);
    for (u32 k = 0; k < blocks; k++) {
        do b--;
        while (fat(img, b) != FAT_FREE);
        vmfs_copy(img + b * VMFS_BLOCK, data + k * VMFS_BLOCK, VMFS_BLOCK);
        if (prev == FAT_END) vmfs_wr16(e + 2, b);
        else fat_set(img, prev, b);
        prev = b;
    }
    if (prev != FAT_END) fat_set(img, prev, FAT_END);
    e[0] = TYPE_DATA;
    e[1] = copyflag;
    for (u32 k = 0; k < VMFS_NAME_LEN && name[k]; k++) e[4 + k] = (u8)name[k];
    bcd_time(e + 0x10, time);
    vmfs_wr16(e + 0x18, blocks);
    return VMFS_OK;
}

u32 vmfs_load(u8 *img, u32 entry, u8 *buf, u32 blocks)
{
    const u8 *e = vmfs_entry(img, entry);
    u32 b = vmfs_rd16(e + 2), moved = 0;
    if (!blocks || blocks > vmfs_rd16(e + 0x18)) blocks = vmfs_rd16(e + 0x18);
    for (; moved < blocks && b < VMFS_BLOCKS; moved++, b = fat(img, b))
        vmfs_copy(buf + moved * VMFS_BLOCK, img + b * VMFS_BLOCK, VMFS_BLOCK);
    return moved;
}

void vmfs_delete(u8 *img, u32 entry)
{
    free_chain(img, vmfs_rd16(vmfs_entry(img, entry) + 2));
    fill(vmfs_entry(img, entry), 0, DIR_ENTRY);
}
