/* One line of a texture pack's index.txt read into its parts (pvr_pack.c load_index reads the file): KEY FILE, then the
 * words if=game.NAME, wide=W, rim=RRGGBB, over and the variant (xbox or playstation), as port/TEXTURE_PACKS.md says.
 * Split from pvr_pack.c at its 400-line cap; what it reads is unchanged.
 * Nearest existing: pvr_pack.c (the lookup and its globals; this is the part that needs none of them). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "pvr_int.h"
#include "platform.h"

/* An index line's variant word: "" is every style (0), xbox 1, playstation 2; -1 for any other word. */
static int variant_of(const char *s)
{
    s += strspn(s, " \t");
    if (!*s || *s == '\r' || *s == '\n') return 0;
    if (!strncmp(s, "xbox", 4) && !s[4 + strspn(s + 4, " \t\r\n")]) return 1;
    if (!strncmp(s, "playstation", 11) && !s[11 + strspn(s + 11, " \t\r\n")]) return 2;
    return -1;
}

int pvr_pack_every_line; /* --pack-mips: a chain does not depend on the setting, and its run is a check run (all off) */

/* line (cut in place) into e: 1 an image line; 0 a blank or comment line; -1 a line the renderer can't read; -2 a line
 * off by its if= setting. */
int pvr_pack_line(char *line, PvrPackLine *e)
{
    char *hash = strchr(line, '#'), *end, *rest;
    memset(e, 0, sizeof *e);
    if (hash) *hash = '\0';
    e->key = _strtoui64(line, &end, 16);
    e->file = end + strspn(end, " \t");
    rest = e->file + strcspn(e->file, " \t\r\n");
    if (*rest) {
        char *word = strstr(rest + 1, "if="), *stop, setting[PVR_PACK_NAME];
        if (word) { /* blanked once read, so the other words read as before */
            size_t n = strcspn(word + 3, " \t\r\n");
            snprintf(setting, sizeof setting, "%.*s", (int)n, word + 3);
            memset(word, ' ', n + 3u);
            if (!n || !(pvr_pack_every_line || port_menu_check_on(setting))) return n ? -2 : -1;
        }
        word = strstr(rest + 1, "wide=");
        if (word) {
            e->wide = strtoul(word + 5, &stop, 10);
            if (!e->wide || (*stop && !strchr(" \t\r\n", *stop))) e->variant = -1;
            *word = '\0';
        }
        word = strstr(rest + 1, "rim=");
        if (word) {
            e->rim = PVR_PACK_RIM_SET | strtoul(word + 4, &stop, 16);
            if (stop != word + 10 || (e->rim & ~(PVR_PACK_RIM_SET | 0xffffffu))) e->variant = -1;
            *word = '\0';
        }
        word = strstr(rest + 1, "over");
        if (word && strspn(word + 4, " \t\r\n") == strlen(word + 4)) {
            e->over = 1;
            *word = '\0';
        }
        if (!e->variant) e->variant = variant_of(rest + 1);
    }
    *rest = '\0';
    if (e->variant < 0 || end == line || e->file == end || !*e->file) return strspn(line, " \t\r\n") != strlen(line) ? -1 : 0;
    return 1;
}
