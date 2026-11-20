/* The code image from the disc (game.json "saturn" "image_files" and "image_sha256", which gen_saturn.py turns into
 * SAT_IMAGE_FILES and SAT_IMAGE_SHA256). A launcher's play run has no image.bin: it is game code and never ships in a
 * release. So a run without --image lays the disc's code files at their load addresses in a zero-filled buffer from
 * SAT_IMAGE_BASE, as onboarding made image.bin (targets/saturn_sh2/tools/overlay_image.py), and checks the SHA-256 of
 * the whole. The files are read through sat_disc.c's ISO 9660 reader, which must have the disc open. Setup and verify
 * run the same check (console_sat.c), so another revision of the disc is refused there (exit code 5), not at play.
 *
 * Nearest existing: overlay_image.py build() - the same layout, in Python, from the same plan. */
#include <windows.h>
#include <bcrypt.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_disc.h"
#include "sat_game.h"

enum { IMAGE_MAX = 0x00100000 }; /* work RAM high, where the image goes */

/* the SHA-256 of data as 64 lower-case hex digits; 0 when Windows' hash fails */
int sat_image_sha256_hex(const u8 *data, u32 size, char hex[65])
{
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE h = NULL;
    u8 out[32];
    int ok = 0;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0))) return 0;
    if (BCRYPT_SUCCESS(BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0))) {
        ok = BCRYPT_SUCCESS(BCryptHashData(h, (PUCHAR)data, size, 0)) && BCRYPT_SUCCESS(BCryptFinishHash(h, out, 32, 0));
        BCryptDestroyHash(h);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    for (int i = 0; ok && i < 32; i++) sprintf_s(hex + 2 * i, 3, "%02x", out[i]);
    return ok;
}

static int fail(int code, char *why, size_t cap, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(why, cap, fmt, ap);
    va_end(ap);
    return code;
}

#ifdef SAT_IMAGE_FILES
typedef struct {
    const char *path; /* on the disc, from its root */
    u32 base;         /* the load address */
} ImageFile;
static const ImageFile k_files[] = SAT_IMAGE_FILES;
enum { NFILES = sizeof k_files / sizeof k_files[0] };

int sat_image_from_disc(u8 **image, u32 *size, char *why, size_t cap)
{
    u32 fad[NFILES], len[NFILES], end = 0;
    char hex[65] = "";
    u8 *buf;
    *image = NULL, *size = 0;
    if (!sat_disc_present()) return fail(4, why, cap, "no disc to make the code image from");
    for (int i = 0; i < NFILES; i++) {
        const ImageFile *f = &k_files[i];
        if ((fad[i] = sat_disc_find(f->path, NULL, &len[i])) == 0)
            return fail(5, why, cap, "the disc has no %s, a file of this build's code image: another revision of " SAT_GAME_NAME "?", f->path);
        if (f->base < SAT_IMAGE_BASE || f->base - SAT_IMAGE_BASE > IMAGE_MAX - len[i])
            return fail(5, why, cap, "the disc's %s (%u bytes) does not fit in work RAM at %08x", f->path, len[i], f->base);
        if (f->base - SAT_IMAGE_BASE + len[i] > end) end = f->base - SAT_IMAGE_BASE + len[i];
    }
    if ((buf = (u8 *)calloc(1, end ? end : 1u)) == NULL) return fail(1, why, cap, "out of memory for the code image");
    for (int i = 0; i < NFILES; i++)
        if (!sat_disc_file_bytes(fad[i], 0, len[i], buf + (k_files[i].base - SAT_IMAGE_BASE))) {
            free(buf);
            return fail(4, why, cap, "the disc's %s cannot be read", k_files[i].path);
        }
    if (!sat_image_sha256_hex(buf, end, hex) || strcmp(hex, SAT_IMAGE_SHA256)) {
        free(buf);
        return fail(5, why, cap, "the code image made from the disc's %d file(s) has SHA-256 %.16s..., not this build's %.16s...: another "
                    "revision of " SAT_GAME_NAME "?", (int)NFILES, hex, SAT_IMAGE_SHA256);
    }
    *image = buf, *size = end;
    return 0;
}
#else
int sat_image_from_disc(u8 **image, u32 *size, char *why, size_t cap) /* a game.json without image_files: image.bin it is */
{
    *image = NULL, *size = 0;
    return fail(-1, why, cap, "this build has no image_files (game.json)");
}
#endif
