/* The game's XMA waves are decoded by xma_decoder.dll, beside the exe. It is a separate work under its own licence and is not part of this repository; the
 * kit knows only these four functions, so anyone can put their own library in its place. Without it the game runs and its XMA sounds are silent, with one
 * line in the log saying why.
 *
 * Nearest existing: xb_sound.c's PCM path, which reads samples straight from the bank and needs no decoder. */
#include <windows.h>
#include <stdlib.h>
#include "xb.h"
#include "xb_xma_dll.h"

#define XMA_INTERFACE 1u

static uint32_t (*s_size)(void);
static void (*s_open)(void *, const uint8_t *, uint32_t, uint32_t);
static float (*s_at)(void *, uint32_t);

/* Only from beside the exe: the search path could find some other file of the name */
static int load(void)
{
    static int tried;
    char path[MAX_PATH + 32];
    DWORD n;
    HMODULE lib;
    uint32_t (*version)(void);
    if (tried) return s_open != NULL;
    tried = 1;
    n = GetModuleFileNameA(NULL, path, MAX_PATH);
    while (n && path[n - 1] != '\\' && path[n - 1] != '/') n--;
    lstrcpyA(path + n, "xma_decoder.dll");
    lib = LoadLibraryExA(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!lib) {
        port_log("sound: xma_decoder.dll is not beside the exe, so XMA sounds are silent\n");
        return 0;
    }
    version = (uint32_t (*)(void))GetProcAddress(lib, "xma_interface_version");
    if (!version || version() != XMA_INTERFACE) {
        port_log("sound: xma_decoder.dll is a different version, so XMA sounds are silent\n");
        return 0;
    }
    s_size = (uint32_t (*)(void))GetProcAddress(lib, "xma_stream_size");
    s_at = (float (*)(void *, uint32_t))GetProcAddress(lib, "xma_at");
    s_open = (void (*)(void *, const uint8_t *, uint32_t, uint32_t))GetProcAddress(lib, "xma_open");
    if (!s_size || !s_at || !s_open) {
        s_open = NULL;
        port_log("sound: xma_decoder.dll lacks a function, so XMA sounds are silent\n");
        return 0;
    }
    port_log("sound: XMA from xma_decoder.dll\n");
    return 1;
}

void *xb_xma_open(void *stream, const uint8_t *data, uint32_t bytes, uint32_t rate)
{
    if (!load()) return NULL;
    if (!stream) {
        stream = malloc(s_size());
        if (!stream) xb_stop("out_of_memory", "an XMA stream", s_size());
    }
    s_open(stream, data, bytes, rate);
    return stream;
}

float xb_xma_at(void *stream, uint32_t sample)
{
    return stream ? s_at(stream, sample) : 0.0f;
}
