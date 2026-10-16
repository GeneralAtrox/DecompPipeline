/* Platform layer: Shinobi's system services on the host. Hardware set-up is a no-op. The system configuration is
 * the console game.json "console" describes (country, language, broadcast; stereo until the game sets mono) with a
 * VGA cable. The RTC is the host's local time, and the timer is the host's performance counter at the SH-4 TMU2
 * rate the game expects (Pphi/64 = 781250 Hz).
 *
 * Needs from the game's headers: SYE_CBL_VGA, SYS_RTC_DATE {year, month, day, hour, minute, second, dayOfWeek,
 * ageOfMoon} and the function names below as the game's registry has them (the Sy_* ones are registry names for
 * library functions with no SDK name). */
#include "game.h"
#include "platform.h"

#if !defined(PORT_CONSOLE_COUNTRY) || !defined(PORT_CONSOLE_LANGUAGE) || !defined(PORT_CONSOLE_BROADCAST)
#error "sdk/sy_sys.c needs game.json \"console\": {\"country\", \"language\", \"broadcast\"}"
#endif

#define CFG_OK 0
#define TIMER_HZ 781250u

static s32 g_sound_mode; /* 0 stereo, 1 mono */
static u64 g_timer_base;

/* The port is a VGA box: 640x480 progressive at 60 Hz, so the game reads no broadcast standard from the cable. */
s32 syCblCheck(void) { return SYE_CBL_VGA; }

void syHwInit(void) {}

void syHwInit2(void) {}

void syHwFinish(void) {}

s32 Sy_CfgInit(void *work)
{
    (void)work;
    return CFG_OK;
}

s32 Sy_CfgExit(void) { return CFG_OK; }

s32 Sy_CfgSetSoundMode(s32 mode)
{
    g_sound_mode = mode;
    return CFG_OK;
}

s32 Sy_CfgGetSoundMode(s32 *mode)
{
    *mode = g_sound_mode;
    return CFG_OK;
}

s32 Sy_CfgGetCountry(s32 *country)
{
    *country = PORT_CONSOLE_COUNTRY;
    return CFG_OK;
}

s32 Sy_CfgGetLanguage(s32 *language)
{
    *language = PORT_CONSOLE_LANGUAGE;
    return CFG_OK;
}

s32 Sy_CfgGetBroadcast(s32 *broadcast)
{
    *broadcast = PORT_CONSOLE_BROADCAST;
    return CFG_OK;
}

s32 syRtcInit(void) { return 0; }

void syRtcFinish(void) {}

void Sy_RtcUpdate(void) {}

s32 syRtcGetDate(SYS_RTC_DATE *date)
{
    HostTime t;
    host_local_time(&t);
    date->year = t.year;
    date->month = (u8)t.month;
    date->day = (u8)t.day;
    date->hour = (u8)t.hour;
    date->minute = (u8)t.minute;
    date->second = (u8)t.second;
    date->dayOfWeek = (u8)t.day_of_week;
    date->ageOfMoon = 0;
    return 0;
}

void Sy_TimerStart(void) { g_timer_base = host_ticks(TIMER_HZ); }

s32 Sy_TimerGetCount(void) { return (s32)(u32)(host_ticks(TIMER_HZ) - g_timer_base); }
