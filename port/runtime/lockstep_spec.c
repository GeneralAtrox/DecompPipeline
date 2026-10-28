/* The lockstep watch list (--lockstep-spec): one item per line, "name words address [flags]", as lockstep.c's header
 * describes. Parsed once; the words are read from guest RAM as the check needs them. Flag if=ITEM:VALUE (or
 * if=ITEM[J]:VALUE, word J; VALUE hex): the item is read only while that earlier item's word equals VALUE, and is
 * otherwise "-" (not in guest RAM), as lockstep_emu.lua writes it: state that lives only in some scenes (a level's
 * tables, whose memory a movie reuses) is not read once its scene has gone. */
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

/* The gate of flag if=ITEM[J]:VALUE: ITEM an earlier item of the spec; 0 if it is not one. */
static int parse_gate(const char *text, const LsSpec *spec, LsItem *it)
{
    const char *colon = strchr(text, ':'), *br = strchr(text, '[');
    size_t len = (size_t)((br && br < colon ? br : colon) - text);
    char *end = NULL;
    if (!colon || !colon[1] || len == 0 || len >= sizeof it->name) return 0;
    it->gate_word = br && br < colon ? (u32)strtoul(br + 1, NULL, 0) : 0u;
    it->gate_value = (u32)strtoul(colon + 1, &end, 16);
    if (*end) return 0;
    for (u32 i = 0; i < spec->nitems; i++) {
        const LsItem *g = &spec->items[i];
        if (!strncmp(g->name, text, len) && !g->name[len] && it->gate_word < g->words) it->gate = g;
    }
    return it->gate != NULL;
}

static int parse_item(char *line, LsSpec *spec, LsItem *it)
{
    char *ctx = NULL, *name = strtok_s(line, " \t\r\n", &ctx), *words, *addr, *p, *flag;
    if (!name || name[0] == '#') return 0;
    words = strtok_s(NULL, " \t\r\n", &ctx);
    addr = strtok_s(NULL, " \t\r\n", &ctx);
    if (!words || !addr) return -1;
    memset(it, 0, sizeof *it);
    strncpy_s(it->name, sizeof it->name, name, _TRUNCATE);
    while ((flag = strtok_s(NULL, " \t\r\n", &ctx)) != NULL) {
        if (!strncmp(flag, "if=", 3) && !parse_gate(flag + 3, spec, it)) return -1;
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

/* 1 if the item is read now: no gate, or its gate's word holds the value. */
static int gate_open(const LsItem *it)
{
    u32 w;
    return !it->gate || (lockstep_item_word(it->gate, it->gate_word, &w) && w == it->gate_value);
}

int lockstep_item_word(const LsItem *it, u32 j, u32 *w)
{
    u32 at;
    if (!gate_open(it)) return 0;
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
        if (!gate_open(it)) return NULL;
        if (it->rand) return port_rand_word(); /* host memory: the watch logs each rand call's stack */
        if (!item_at(it, &at) || !guest_word(at += 4u * n, &w)) return NULL;
        return (volatile u32 *)(uintptr_t)(GUEST_RAM_BASE + ((at & 0x1fffffffu) - 0x0c000000u));
    }
    return NULL;
}
