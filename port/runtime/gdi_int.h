/* gdi.c and gdi_loose.c (the loose-file folder, game.json loose_dir): what they share. Kit-private. */
#pragma once
#include "port.h"
#include "platform.h"

#define GDI_DATA 2048u        /* a sector's data bytes */
#define LOOSE_LBA 0x01000000u /* loose files' sectors start here, past any GD-ROM's last */

typedef int (*DirVisit)(void *ctx, const HostDirEnt *e);

/* gdi.c */
void gdi_dir_of(const char *path, char *out, size_t cap);
int gdi_root_dir(PortDisc *d, u32 *lba, u32 *size);
int gdi_find_entry(PortDisc *d, u32 lba, u32 size, const char *name, size_t len, HostDirEnt *out);
PortDisc *gdi_run_disc(void);

/* disc_fill.c */
void disc_lock(int on); /* 1 takes the image's files, 0 lets them go (read_sectors) */

/* gdi_loose.c */
int loose_read(u32 lba, u8 *out);
int loose_hidden(u32 dir_lba, const char *name);
int loose_visit(u32 dir_lba, DirVisit visit, void *ctx);
