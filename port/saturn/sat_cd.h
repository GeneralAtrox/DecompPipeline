/* The CD block's sector buffers, selectors (filter + buffer partition) and host data transfer; sat_cd.c has the
 * register interface and commands. Internal to the CD block files. */
#ifndef SAT_CD_H
#define SAT_CD_H

#include "sat.h"
#include "sat_disc.h"

enum { CD_BLOCKS = 200, CD_SELECTORS = 24, CD_NONE = 0xff };

/* host-visible bits of HIRQ */
enum { H_CMOK = 1, H_DRDY = 2, H_CSCT = 4, H_BFUL = 8, H_PEND = 16, H_DCHG = 32, H_ESEL = 64, H_EHST = 128, H_ECPY = 256,
       H_EFLS = 512, H_SCDQ = 1024, H_MPED = 2048 };

typedef struct {
    u8 mode, chan, fid, smmask, smval, cimask, cival, ctrue, cfalse;
    u32 fad, count;
} CdFilter;

void cdb_reset(int selectors_too);
void cdb_set_lengths(int get_code, int put_code); /* 0 2048, 1 2336, 2 2340, 3 2352, else unchanged */
CdFilter *cdb_filter(u32 n);                      /* NULL when n is not a selector */
void cdb_connect_cd(u32 selector);                /* CD device -> selector (CD_NONE disconnects) */
u32 cdb_cd_selector(void);
u32 cdb_last_part(void);
int cdb_store(const u8 *raw, u32 fad);            /* a sector from the drive through the filters; 0 when the buffer is full */
u32 cdb_free(void);
u32 cdb_count(u32 part);
int cdb_info(u32 part, u32 pos, u32 *fad, u8 *fn, u8 *cn, u8 *sm, u8 *ci);
void cdb_delete(u32 part, u32 pos, u32 num);      /* num 0xffff: to the end */
u32 cdb_actual_size(u32 part, u32 pos, u32 num);  /* bytes */

/* sat_cdda.c: the audio tracks, played as the drive turns */
void sat_cdda_enable(int on);
void sat_cdda_flush(void);
void sat_cdda_push(const u8 *raw);                /* one audio sector (2352 bytes) */

/* the data port (0x05818000) */
void xfer_blocks(u32 part, u32 pos, u32 num, int delete_after);
void xfer_bytes(const void *data, u32 len);
u32 xfer_read(u32 size);
u32 xfer_end(void);                               /* bytes moved; deletes sectors after a get-then-delete */
int xfer_active(void);

extern u32 sat_cd_data_rate; /* sat_cd.c */

#endif
