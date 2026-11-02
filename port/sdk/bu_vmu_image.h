/* The file images (sg_bup.h), behind bu_vmu.c's buMakeBackupFileImage and buAnalyzeBackupFileImage. Those stay in the
 * .c file: gen_port reads an SDK module's defined names from it, not from what it includes. */
#define IMAGE_HEADER 0x80u
#define IMAGE_PALETTE 0x60u
#define IMAGE_ICON 512u
#define IMAGE_BLOCK 512u
#define ERR_BAD_IMAGE (-1) /* the CRC does not match (nonzero: the game only tests for 0) */

static void wr32(u8 *p, u32 v)
{
    vmfs_wr16(p, v & 0xffffu);
    vmfs_wr16(p + 2, v >> 16);
}

static u32 rd32(const u8 *p) { return vmfs_rd16(p) | vmfs_rd16(p + 2) << 16; }

static u32 eyecatch_size(u32 type) { return type == 1u ? 8064u : type == 2u ? 4544u : type == 3u ? 2048u : 0u; }

static u16 image_crc(const u8 *p, u32 n)
{
    u32 crc = 0;
    for (u32 i = 0; i < n; i++) {
        crc ^= (u32)p[i] << 8;
        for (int b = 0; b < 8; b++) crc = crc & 0x8000u ? (crc << 1) ^ 0x1021u : crc << 1;
    }
    return (u16)crc;
}

/* The file image of header's data in buf; returns its size in blocks. */
static s32 image_make(void *buf, const BUS_BACKUPFILEHEADER *header)
{
    u8 *b = (u8 *)buf;
    u32 icons = header->iconCount * IMAGE_ICON, eye = eyecatch_size(header->visualType);
    u32 size = IMAGE_HEADER + icons + eye + header->saveSize;
    u32 total = (size + IMAGE_BLOCK - 1u) / IMAGE_BLOCK * IMAGE_BLOCK;
    memset(b, 0, total);
    memcpy(b, header->vmsComment, 16);
    memcpy(b + 0x10, header->btrComment, 32);
    memcpy(b + 0x30, header->gameName, 16);
    vmfs_wr16(b + 0x40, header->iconCount);
    vmfs_wr16(b + 0x42, header->iconSpeed);
    vmfs_wr16(b + 0x44, header->visualType);
    wr32(b + 0x48, header->saveSize);
    if (header->iconPalette) memcpy(b + IMAGE_PALETTE, header->iconPalette, 32);
    if (icons && header->iconData) memcpy(b + IMAGE_HEADER, header->iconData, icons);
    if (eye && header->visualData) memcpy(b + IMAGE_HEADER + icons, header->visualData, eye);
    if (header->saveSize) memcpy(b + IMAGE_HEADER + icons + eye, header->saveData, header->saveSize);
    vmfs_wr16(b + 0x46, image_crc(b, size));
    return (s32)(total / IMAGE_BLOCK);
}

/* The header of the file image in buf, its pointers into buf; ERR_BAD_IMAGE if the CRC does not match. */
static s32 image_analyze(BUS_BACKUPFILEHEADER *header, void *buf)
{
    u8 *b = (u8 *)buf;
    u16 crc = (u16)vmfs_rd16(b + 0x46);
    u32 icons, eye, size;
    memset(header, 0, sizeof *header);
    memcpy(header->vmsComment, b, 16);
    memcpy(header->btrComment, b + 0x10, 32);
    memcpy(header->gameName, b + 0x30, 16);
    header->iconCount = (u16)vmfs_rd16(b + 0x40);
    header->iconSpeed = (u16)vmfs_rd16(b + 0x42);
    header->visualType = (u16)vmfs_rd16(b + 0x44);
    header->saveSize = rd32(b + 0x48);
    icons = header->iconCount * IMAGE_ICON;
    eye = eyecatch_size(header->visualType);
    size = IMAGE_HEADER + icons + eye + header->saveSize;
    header->iconPalette = b + IMAGE_PALETTE;
    header->iconData = b + IMAGE_HEADER;
    header->visualData = eye ? b + IMAGE_HEADER + icons : NULL;
    header->saveData = b + IMAGE_HEADER + icons + eye;
    vmfs_wr16(b + 0x46, 0);
    u16 got = image_crc(b, size);
    vmfs_wr16(b + 0x46, crc);
    return got == crc ? ERR_OK : ERR_BAD_IMAGE;
}
