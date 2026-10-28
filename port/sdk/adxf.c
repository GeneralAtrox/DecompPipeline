/* Platform layer: CRI's ADX file system (ADXF, cri_adxf.h) over the kit's gdFs (sdk/gdfs.c), for a game that opens
 * and reads its disc files through ADXF rather than gdFs. Reads are synchronous, as gdFs's are: a no-wait read is
 * done when the call returns, so the status goes straight from "stop" to "read end".
 *
 * Needs from the game's headers: ADXF (a pointer to struct AdxFs, defined here), GDFS, and the function names below
 * as the game's registry has them; and the kit's gdfs module in the game's "sdk" list. */
#include "game.h"
#include "platform.h"

/* cri_adxf.h's values (the game compares against these numbers) */
#define ADXF_STAT_STOP 1
#define ADXF_STAT_READING 2
#define ADXF_STAT_READEND 3
#define ADXF_STAT_ERROR 4
#define ADXF_SEEK_SET 0
#define ADXF_SEEK_CUR 1
#define ADXF_SEEK_END 2
#define ADXF_ERR_PRM (-3) /* a bad parameter or handle */
#define ADXF_MAX_OPEN 16
#define GD_SECTOR 2048

struct AdxFs {
    int used;
    GDFS fs;
    s32 stat;
    s32 size;      /* bytes */
    s32 req_sct;   /* the last read's sectors asked for */
    s32 read_sct;  /* and read */
};

static struct AdxFs g_adxf[ADXF_MAX_OPEN];

static int valid(ADXF f) { return f && f >= g_adxf && f < g_adxf + ADXF_MAX_OPEN && f->used; }

ADXF ADXF_Open(const char *fileName, void *atr)
{
    GDFS fs;
    s32 size = 0;
    (void)atr;
    fs = gdFsOpen(fileName, NULL);
    if (!fs) {
        port_log("adxf: can't find '%s' (ADXF_Open)\n", fileName ? fileName : "(null)");
        return NULL;
    }
    gdFsGetFileSize(fs, &size);
    for (int i = 0; i < ADXF_MAX_OPEN; i++) {
        ADXF f = &g_adxf[i];
        if (f->used) continue;
        f->used = 1;
        f->fs = fs;
        f->stat = ADXF_STAT_STOP;
        f->size = size;
        f->req_sct = f->read_sct = 0;
        return f;
    }
    gdFsClose(fs);
    port_log("adxf: no free handle for %s\n", fileName);
    return NULL;
}

void ADXF_Close(ADXF adxf)
{
    if (!valid(adxf)) return;
    gdFsClose(adxf->fs);
    adxf->used = 0;
}

/* Reads up to nsct sectors from the position into buf; returns the sectors taken (clipped at the end of the file). */
s32 ADXF_ReadNw32(ADXF adxf, s32 nsct, void *buf)
{
    s32 pos, left;
    if (!valid(adxf) || nsct < 0 || !buf) return ADXF_ERR_PRM;
    pos = gdFsTell(adxf->fs);
    left = (adxf->size + GD_SECTOR - 1) / GD_SECTOR - pos;
    adxf->req_sct = nsct < left ? nsct : left;
    adxf->read_sct = 0;
    if (adxf->req_sct > 0 && gdFsRead(adxf->fs, adxf->req_sct, buf) != 0) {
        adxf->stat = ADXF_STAT_ERROR;
        return ADXF_ERR_PRM;
    }
    adxf->read_sct = adxf->req_sct;
    adxf->stat = ADXF_STAT_READEND;
    return adxf->req_sct;
}

s32 ADXF_Stop(ADXF adxf)
{
    if (!valid(adxf)) return ADXF_ERR_PRM;
    adxf->stat = ADXF_STAT_STOP;
    return 0;
}

s32 ADXF_Seek(ADXF adxf, s32 pos, s32 type)
{
    s32 origin = type == ADXF_SEEK_CUR ? GDD_SEEK_CUR : type == ADXF_SEEK_END ? GDD_SEEK_END : GDD_SEEK_SET;
    if (!valid(adxf) || gdFsSeek(adxf->fs, pos, origin) != 0) return ADXF_ERR_PRM;
    return gdFsTell(adxf->fs);
}

s32 ADXF_Tell(ADXF adxf) { return valid(adxf) ? gdFsTell(adxf->fs) : ADXF_ERR_PRM; }

s32 ADXF_GetFsizeSct(ADXF adxf) { return valid(adxf) ? (adxf->size + GD_SECTOR - 1) / GD_SECTOR : ADXF_ERR_PRM; }

s32 ADXF_GetFsizeByte(ADXF adxf) { return valid(adxf) ? adxf->size : ADXF_ERR_PRM; }

s32 ADXF_GetNumReqSct(ADXF adxf, s32 *seekpos)
{
    if (!valid(adxf)) return ADXF_ERR_PRM;
    if (seekpos) *seekpos = gdFsTell(adxf->fs) - adxf->read_sct;
    return adxf->req_sct;
}

s32 ADXF_GetNumReadSct(ADXF adxf) { return valid(adxf) ? adxf->read_sct : ADXF_ERR_PRM; }

s32 ADXF_GetStat(ADXF adxf) { return valid(adxf) ? adxf->stat : ADXF_STAT_ERROR; }
