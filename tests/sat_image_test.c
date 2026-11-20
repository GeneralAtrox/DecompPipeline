/* The code image made from the disc (port/saturn/sat_image.c, with sat_disc.c, both included whole). Run by
 * tests/test_sat_launcher.py as: sat_image_test CUE|- OUT. It prints "code N why" (sat_image_from_disc's result) and, on
 * 0, writes the image to OUT; then opens the disc again (setup checks the image, then its copy) and builds it once more.
 * The game header (sat_game.h: SAT_IMAGE_BASE, SAT_IMAGE_FILES, SAT_IMAGE_SHA256) is the test's, written beside it. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_disc.c"
#include "sat_image.c"

int g_work_on; /* work_count.c's, which sat_disc.c's read counts use */
u64 g_work_now[WC_N];

int main(int argc, char **argv)
{
    u8 *img = NULL, *again = NULL;
    u32 n = 0, n2 = 0;
    char why[400] = "";
    int code;
    FILE *f;
    if (argc < 3) return 2;
    if (strcmp(argv[1], "-")) sat_disc_open(argv[1]);
    code = sat_image_from_disc(&img, &n, why, sizeof why);
    printf("code %d %s\n", code, code ? why : "");
    if (code) return 0;
    if ((f = fopen(argv[2], "wb")) != NULL) fwrite(img, 1, n, f), fclose(f);
    sat_disc_open(argv[1]);
    code = sat_image_from_disc(&again, &n2, why, sizeof why);
    printf("again %d %s\n", code, code == 0 && n2 == n && !memcmp(img, again, n) ? "same" : "differs");
    free(img);
    free(again);
    return 0;
}
