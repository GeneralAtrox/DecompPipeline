/* The lockstep check's emulator frames (the --lockstep file tools/lockstep.py writes) and the RTC they carry. */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "lockstep_parts.h"

#define LINE_CAP 4096u

/* One frame per line, "label on r l x y rtc jump key.." (hex). */
u32 lockstep_load_emu(const char *path, EmuFrame **frames)
{
    FILE *f = NULL;
    char line[LINE_CAP];
    u32 cap = 0, n = 0;
    if (fopen_s(&f, path, "r") || !f) {
        port_log("lockstep: cannot open the emulator frames %s\n", path);
        return 0;
    }
    *frames = NULL;
    while (fgets(line, sizeof line, f)) {
        EmuFrame e;
        char *p = line;
        unsigned long v[8];
        if (line[0] == '#') continue;
        for (int i = 0; i < 8; i++) v[i] = strtoul(p, &p, 16);
        memset(&e, 0, sizeof e);
        e.label = (u32)v[0];
        e.on = (u32)v[1];
        e.r = (u8)v[2];
        e.l = (u8)v[3];
        e.x = (s8)(s32)v[4];
        e.y = (s8)(s32)v[5];
        e.rtc = (u32)v[6];
        e.jump = (u32)v[7];
        while (e.nkey < MAX_KEY) {
            char *q = p;
            u32 k = (u32)strtoul(p, &p, 16);
            if (p == q) break;
            e.key[e.nkey++] = k;
        }
        if (n == cap) {
            cap = cap ? cap * 2u : 4096u;
            EmuFrame *grown = (EmuFrame *)realloc(*frames, cap * sizeof **frames);
            if (!grown) break;
            *frames = grown;
        }
        (*frames)[n++] = e;
    }
    fclose(f);
    port_log("lockstep: driving from %s: %u emulator frames\n", path, n);
    return n;
}

/* The AICA's RTC (seconds from 1950-01-01, a Sunday) as a date: days to the civil calendar (H. Hinnant's method). */
void lockstep_rtc_date(u32 seconds, HostTime *t)
{
    u32 days = seconds / 86400u, rem = seconds % 86400u;
    s32 z = (s32)days - 7305 + 719468, era = z / 146097, doe = z - era * 146097;
    s32 yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    s32 doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
    s32 m = mp < 10 ? mp + 3 : mp - 9;
    t->year = (u16)(yoe + era * 400 + (m <= 2));
    t->month = (u16)m;
    t->day = (u16)(doy - (153 * mp + 2) / 5 + 1);
    t->hour = (u16)(rem / 3600u);
    t->minute = (u16)(rem / 60u % 60u);
    t->second = (u16)(rem % 60u);
    t->day_of_week = (u16)(days % 7u);
}
