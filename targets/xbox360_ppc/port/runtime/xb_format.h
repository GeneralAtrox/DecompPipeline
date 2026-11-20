/* xb_imports.c, the kernel's printf family (one translation unit with it; here for the 400-line cap). A guest format in guest memory, wide (UTF-16,
 * big-endian) or narrow, and a va_list: on the console a pointer to the arguments' save area, 8 bytes each, an integer in the low word (all 8 for
 * ll and I64), a float as a double. Microsoft's meaning of %s in each width: in a wide format %s is a wide string and %S a narrow one, %hs narrow,
 * %ls wide; the narrow format the other way round. Numbers go through the host's snprintf with the same flags, width and precision. */

static u32 fmt_ch(u32 p, int wide) { return wide ? rd16(p) : rd8(p); }

/* Appends one character to out[room] (UTF-16 code units), counting past the end */
static void fmt_put(u16 *out, unsigned room, unsigned *n, u32 c)
{
    if (*n + 1 < room) out[*n] = (u16)c;
    (*n)++;
}

/* The formatted text of fmt (wide or narrow) with the arguments at va, into out (room units, zero-ended); the length it has */
static unsigned guest_format(u32 fmt, int wide, u32 va, u16 *out, unsigned room)
{
    unsigned n = 0, step = wide ? 2u : 1u;
    u32 p = fmt, c;
    while ((c = fmt_ch(p, wide)) != 0) {
        char spec[32], num[512];
        unsigned k = 0, len, i;
        int width = -1, prec = -1, left = 0, longs = 0, shorts = 0, wide_arg;
        u64 v;
        p += step;
        if (c != '%') { fmt_put(out, room, &n, c); continue; }
        spec[k++] = '%';
        while ((c = fmt_ch(p, wide)) == '-' || c == '+' || c == ' ' || c == '#' || c == '0') {
            if (c == '-') left = 1;
            if (k < 8) spec[k++] = (char)c;
            p += step;
        }
        if (c == '*') { width = (int)rd32(va + 4); va += 8; p += step; c = fmt_ch(p, wide); }
        else for (width = 0; c >= '0' && c <= '9'; p += step, c = fmt_ch(p, wide)) width = width * 10 + (int)(c - '0');
        if (c == '.') {
            p += step;
            c = fmt_ch(p, wide);
            if (c == '*') { prec = (int)rd32(va + 4); va += 8; p += step; c = fmt_ch(p, wide); }
            else for (prec = 0; c >= '0' && c <= '9'; p += step, c = fmt_ch(p, wide)) prec = prec * 10 + (int)(c - '0');
        }
        for (;; p += step, c = fmt_ch(p, wide)) {
            if (c == 'l') longs++;
            else if (c == 'h') shorts++;
            else if (c == 'I' && fmt_ch(p + step, wide) == '6' && fmt_ch(p + 2 * step, wide) == '4') { longs = 2; p += 2 * step; }
            else if (c == 'I' && fmt_ch(p + step, wide) == '3' && fmt_ch(p + 2 * step, wide) == '2') { longs = 0; p += 2 * step; }
            else break;
        }
        p += step;
        if (width > 0) k += (unsigned)sprintf_s(spec + k, sizeof spec - k, "%d", width);
        if (prec >= 0) k += (unsigned)sprintf_s(spec + k, sizeof spec - k, ".%d", prec);
        switch (c) {
        case '%':
            fmt_put(out, room, &n, '%');
            continue;
        case 'd': case 'i': case 'u': case 'x': case 'X': case 'o':
            v = longs >= 2 ? rd64(va) : rd32(va + 4);
            va += 8;
            if (longs >= 2) { spec[k++] = 'l'; spec[k++] = 'l'; }
            spec[k++] = (char)c;
            spec[k] = 0;
            if (longs >= 2) sprintf_s(num, sizeof num, spec, v);
            else if (c == 'd' || c == 'i') sprintf_s(num, sizeof num, spec, shorts ? (int)(short)v : (int)(u32)v);
            else sprintf_s(num, sizeof num, spec, shorts ? (unsigned)(u16)v : (unsigned)v);
            break;
        case 'p':
            sprintf_s(num, sizeof num, "%08X", rd32(va + 4));
            va += 8;
            break;
        case 'f': case 'F': case 'e': case 'E': case 'g': case 'G': {
            union { u64 b; double d; } u;
            u.b = rd64(va);
            va += 8;
            spec[k++] = (char)c;
            spec[k] = 0;
            sprintf_s(num, sizeof num, spec, u.d);
            break;
        }
        case 'c': case 'C': case 's': case 'S': {
            u32 at = rd32(va + 4), one = (c == 'c' || c == 'C');
            unsigned pad;
            va += 8;
            wide_arg = shorts ? 0 : longs ? 1 : (c == 's' || c == 'c') ? wide : !wide;
            if (one) len = 1;
            else if (!at) { at = 0; len = 6; }
            else for (len = 0; (prec < 0 || (int)len < prec) && (wide_arg ? rd16(at + 2 * len) : rd8(at + len)); len++) {}
            pad = width > (int)len ? (unsigned)width - len : 0;
            if (!left) for (i = 0; i < pad; i++) fmt_put(out, room, &n, ' ');
            for (i = 0; i < len; i++) {
                u32 ch = one ? (wide_arg ? (at & 0xffffu) : (at & 0xffu)) : !at ? (u32)"(null)"[i] : wide_arg ? rd16(at + 2 * i) : rd8(at + i);
                fmt_put(out, room, &n, ch);
            }
            if (left) for (i = 0; i < pad; i++) fmt_put(out, room, &n, ' ');
            continue;
        }
        default:
            xb_stop("import_format", "a printf conversion the kit does not know", c);
        }
        for (i = 0; num[i]; i++) fmt_put(out, room, &n, (u8)num[i]);
    }
    out[n < room ? n : room - 1] = 0;
    return n;
}
