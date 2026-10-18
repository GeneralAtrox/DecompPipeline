/* Platform layer: Audio64's AM start-up (mid-1999's, 1.x): the driver choice, the game's file and memory callbacks,
 * and amInit, which loads the driver file through the file callback and installs it at the start of the sound RAM, as
 * the console does. Nothing runs it: the kit's AC layer (ac_digi.c) plays the ports itself. The image is kept so the
 * words a game reads from the driver's header are there. TRUE (1) or FALSE (0) as the library returns them. */
#include "game.h"
#include "platform.h"
#include "aica.h"

#define AM_FILE_SIZE 3 /* the file callback's operations (the game's callback switches on them) */
#define AM_FILE_LOAD 4
#define DRIVER_AREA 0x10000u /* the sound RAM's first 64 KB, which the audio heap (am_heap.c) leaves out */

typedef u32 (*AmFileFn)(const char *path, void *file, void *buf, u32 *size, s32 op);

static AmFileFn g_file;
static char g_driver[16];

void Am_SetFileCallback(SdkProc func) { g_file = (AmFileFn)func; }

void Am_SetMemoryCallbacks(SdkProc allocFunc, SdkProc freeFunc) { (void)allocFunc, (void)freeFunc; }

s32 amSelectDriver(s32 driverType)
{
    static const char *const names[] = {"audio64.drv", "MidiDa.drv"};
    if (driverType < 1 || driverType > 2) return 0;
    for (u32 i = 0; (g_driver[i] = names[driverType - 1][i]) != 0; i++) {
    }
    return 1;
}

const char *Am_GetDriverFileName(void) { return g_driver; }

s32 amFileGetSize(const char *fileName, u32 *size)
{
    return g_file && size && g_file(fileName, NULL, NULL, size, AM_FILE_SIZE) != 0;
}

s32 amInit(void *driverBuffer, u32 driverSize)
{
    u8 *ram = aica_ram();
    u32 size = driverSize;
    if (!g_file || !g_driver[0] || !driverBuffer || !ram) return 0;
    if (!g_file(g_driver, NULL, driverBuffer, &size, AM_FILE_LOAD)) {
        port_log("sound: cannot load %s\n", g_driver);
        return 0;
    }
    size = driverSize < DRIVER_AREA ? driverSize : DRIVER_AREA;
    for (u32 i = 0; i < size; i++) ram[i] = ((const u8 *)driverBuffer)[i];
    port_log("sound: %s (%u bytes) installed; the kit's AC layer plays its ports\n", g_driver, driverSize);
    audio_start();
    return 1;
}

void amShutdown(void)
{
    for (u32 v = 0; v < AICA_VOICES; v++) aica_voice_stop(v);
}
