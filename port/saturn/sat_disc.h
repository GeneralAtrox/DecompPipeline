/* The game disc as the CD block sees it: a cue sheet of 2352-byte raw sectors, and the ISO 9660 directory tree on the
 * data track. Addresses are frame addresses (FAD = logical block + 150), as the CD block's commands use. */
#ifndef SAT_DISC_H
#define SAT_DISC_H

#include "sat.h"

typedef struct {
    u8 ctrl;  /* 0x41 data, 0x01 audio */
    u32 fad;  /* the track's first frame (INDEX 01) */
} SatTrack;

typedef struct {
    u32 fad, size; /* the file's first frame and its length in bytes */
    u8 unit, gap, fn, attr; /* interleave unit and gap, file number, ISO flags (0x02 directory) */
    u32 dir_lba;   /* a directory's own block, to read it in turn */
    char name[40];
} SatFile;

enum { SAT_SECTOR = 2352, SAT_MAX_FILES = 254 };

int sat_disc_open(const char *cue);           /* 0 on success; the disc stays closed (no disc) on failure */
int sat_disc_present(void);
u32 sat_disc_ntracks(void);
const SatTrack *sat_disc_track(u32 tno);       /* 1-based, NULL out of range */
u32 sat_disc_leadout(void);
int sat_disc_read(u32 fad, u8 *raw);           /* 2352 bytes of the frame; 0 when outside the disc */
int sat_disc_root(SatFile *root);              /* the root directory record */
int sat_disc_dir(u32 dir_lba, u32 size, SatFile *out, int max); /* the directory's records, "." and ".." first */

#endif
