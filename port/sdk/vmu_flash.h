/* The VMU flash file system on a 128 KB card image (sdk/vmu_flash.c), for sdk/bu_vmu.c: 256 blocks of 512 bytes,
 * user blocks 0..199, the directory at 253 down to 241 (13 blocks of 16 entries), the FAT at 254 (a u16 per block:
 * the next block, 0xfffa at a chain's end, 0xfffc free), the root at 255; words are little-endian. It is the layout
 * the console's BIOS writes, so a card image is the file an emulator keeps.
 *
 * Root block: 0x00 sixteen 0x55 (formatted), 0x10 the 32 volume bytes (the custom colour), 0x30 the format time (BCD),
 * 0x46 the FAT's block, 0x4a the directory's, 0x4e the icon, 0x50 the user block count.
 * Directory entry (32 bytes): 0x00 type (0: free, 0x33 data, 0xcc a game), 0x01 copy flag, 0x02 first block, 0x04 the
 * name (12 bytes, padded with NULs or spaces), 0x10 the time (BCD: century, year, month, day, hour, minute, second,
 * day of week), 0x18 size in blocks, 0x1a header offset in blocks. */
#ifndef VMU_FLASH_H
#define VMU_FLASH_H

#include "platform.h"

#define VMFS_BLOCK 512u
#define VMFS_BLOCKS 256u
#define VMFS_SIZE (VMFS_BLOCKS * VMFS_BLOCK)
#define VMFS_USER_BLOCKS 200u
#define VMFS_ENTRIES 208u
#define VMFS_NAME_LEN 12u
#define VMFS_TYPE_GAME 0xccu

#define VMFS_OK 0
#define VMFS_FULL 1 /* not enough free blocks or directory entries */
#define VMFS_GAME 2 /* the name is a game's file, which a save never replaces */

u32 vmfs_rd16(const u8 *p);
void vmfs_wr16(u8 *p, u32 v);
void vmfs_copy(u8 *dst, const u8 *src, u32 n);

void vmfs_format(u8 *img);              /* a blank formatted card, dated now */
int vmfs_formatted(const u8 *img);
const u8 *vmfs_root(const u8 *img);
u32 vmfs_free(const u8 *img);           /* free user blocks */
u32 vmfs_free_from_start(const u8 *img); /* free user blocks from block 0 on (room for a game) */
u8 *vmfs_entry(u8 *img, u32 i);         /* directory entry i, 0..VMFS_ENTRIES-1 */
int vmfs_find(u8 *img, const char *name); /* the entry index, -1 if absent */
void vmfs_name(const u8 *entry, char *out); /* the entry's name as a C string (13 bytes) */

/* Saves (or replaces) a data file of blocks blocks; time is a BUS_TIME (NULL: now). VMFS_OK, _FULL or _GAME. */
int vmfs_save(u8 *img, const char *name, const u8 *data, u32 blocks, const u8 *time, u8 copyflag);
u32 vmfs_load(u8 *img, u32 entry, u8 *buf, u32 blocks); /* blocks 0: the whole file; the blocks moved */
void vmfs_delete(u8 *img, u32 entry);

/* BUS_TIME {u16 year; u8 month, day, hour, minute, second, day_of_week} from the card's BCD time. */
void vmfs_bus_time(u8 *out, const u8 *bcd8);

#endif
