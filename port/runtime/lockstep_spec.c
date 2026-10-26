/* The lockstep watch list (--lockstep-spec): one item per line, "name words address [flags]", as lockstep.c's header
 * describes. Parsed once; the words are read from guest RAM as the check needs them. */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"
#include "lockstep_parts.h"

#define LINE_CAP 4096u
#define STEP_DEREF 0x7fffffff

static int guest_word(u32 a, u32 *out)
{
    u32 p = a & 0x1fffffffu;
    if ((a & 3u) || p < 0x0c000000u || p >= 0x0c000000u + GUEST_RAM_SIZE) return 0;
    *out = *(const volatile u32 *)(uintptr_t)(GUEST_RAM_BASE + (p - 0x0c000000u));
    return 1;
}

/* Where an item's words start; 0 if a step leaves guest RAM. */
static int item_at(const LsItem *it, u32 *at)
{
    u32 a = it->base;
    for (u32 s = 0; s < it->steps; s++) {
        if (it->step[s] == STEP_DEREF) {
            if (!guest_word(a, &a)) return 0;
        } else {
            a += (u32)it->step[s];
        }
    }
    *at = a;
    return 1;
}

static int parse_item(char *line, LsSpec *spec, LsItem *it)
{
    char *ctx = NULL, *name = strtok_s(line, " \t\r\n", &ctx), *words, *addr, *p, *flag;
    if (!name || name[0] == '#') return 0;
    words = strtok_s(NULL, " \t\r\n", &ctx);
    addr = strtok_s(NULL, " \t\r\n", &ctx);
    if (!words || !addr) return -1;
    memset(it, 0, sizeof *it);
    while ((flag = strtok_s(NULL, " \t\r\n", &ctx)) != NULL) {
        if (!strcmp(flag, "key")) it->key = 1;
        if (!strcmp(flag, "frames")) it->frames = 1;
        if (!strcmp(flag, "paused")) it->paused = 1;
        if (!strcmp(flag, "counter")) it->counter = 1;
        if (!strcmp(flag, "emu")) return 0; /* the emulator's only */
    }
    it->words = (u32)strtoul(words, NULL, 0);
    if ((p = strchr(addr, '|')) != NULL) addr = p + 1;
    if (!strcmp(addr, "@rand")) {
        it->rand = 1;
        return it->counter ? -1 : 1;
    }
    it->base = (u32)strtoul(addr, &p, 16);
    while (*p) {
        if (it->steps == LS_MAX_STEPS) return -1;
        if (*p == '*') {
            it->step[it->steps++] = STEP_DEREF;
            p++;
        } else if (*p == '+' || *p == '-') {
            s32 sign = *p == '-' ? -1 : 1;
            it->step[it->steps++] = sign * (s32)strtoul(p + 1, &p, 16);
        } else {
            return -1;
        }
    }
    if (it->key && spec->nkey + it->words > MAX_KEY) return -1;
    if (it->key) spec->nkey += it->words;
    if (it->frames) spec->frames = it;
    if (it->paused) spec->paused = it;
    if (it->counter) spec->counter = it;
    return 1;
}

int lockstep_spec_load(const char *path, LsSpec *spec)
{
    FILE *f = NULL;
    char line[LINE_CAP];
    u32 n = 0;
    if (fopen_s(&f, path, "r") || !f) {
        port_log("lockstep: cannot open the spec %s\n", path);
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        n++;
        if (spec->nitems == LS_MAX_ITEMS) break;
        int r = parse_item(line, spec, &spec->items[spec->nitems]);
        if (r < 0) {
            port_log("lockstep: %s line %u is not \"name words address [flags]\"\n", path, n);
            fclose(f);
            return 0;
        }
        spec->nitems += (u32)r;
    }
    fclose(f);
    port_log("lockstep: %u watched items, %u key words%s\n", spec->nitems, spec->nkey,
             spec->counter ? ", frames end where the counter label changes" : "");
    return 1;
}

int lockstep_item_word(const LsItem *it, u32 j, u32 *w)
{
    u32 at;
    if (it->rand) {
        *w = port_rand_state();
        return j == 0;
    }
    return item_at(it, &at) && guest_word(at + 4u * j, w);
}

volatile u32 *lockstep_word_host(const LsSpec *spec, u32 n)
{
    u32 at, w;
    for (u32 i = 0; i < spec->nitems; n -= spec->items[i++].words) {
        const LsItem *it = &spec->items[i];
        if (n >= it->words) continue;
        if (it->rand || !item_at(it, &at) || !guest_word(at += 4u * n, &w)) return NULL;
        return (volatile u32 *)(uintptr_t)(GUEST_RAM_BASE + ((at & 0x1fffffffu) - 0x0c000000u));
    }
    return NULL;
}
