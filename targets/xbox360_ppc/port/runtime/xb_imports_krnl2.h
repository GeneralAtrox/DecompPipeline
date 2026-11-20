/* xb_imports.c, the kernel's imports continued (one translation unit with it, for its helpers; here for the 400-line cap) */
/* XeCrypt's SHA-1, on the console's own record in guest memory: { u32 count, u32 state[5], u8 buffer[64] } */
#define ROL32(x, n) (((x) << (n)) | ((x) >> (32 - (n))))
static void sha_block(u32 st, u32 bytes)
{
    u32 w[80], v[5], i;
    for (i = 0; i < 16; i++) w[i] = rd32(bytes + 4 * i);
    for (; i < 80; i++) w[i] = ROL32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    for (i = 0; i < 5; i++) v[i] = rd32(st + 4 + 4 * i);
    for (i = 0; i < 80; i++) {
        u32 f = i < 20 ? ((v[1] & v[2]) | (~v[1] & v[3])) + 0x5A827999u : i < 40 ? (v[1] ^ v[2] ^ v[3]) + 0x6ED9EBA1u
              : i < 60 ? ((v[1] & v[2]) | (v[1] & v[3]) | (v[2] & v[3])) + 0x8F1BBCDCu : (v[1] ^ v[2] ^ v[3]) + 0xCA62C1D6u;
        u32 t = ROL32(v[0], 5) + f + v[4] + w[i];
        v[4] = v[3]; v[3] = v[2]; v[2] = ROL32(v[1], 30); v[1] = v[0]; v[0] = t;
    }
    for (i = 0; i < 5; i++) wr32(st + 4 + 4 * i, rd32(st + 4 + 4 * i) + v[i]);
}

static void sha_update(u32 st, u32 in, u32 len)
{
    u32 count = rd32(st), i;
    wr32(st, count + len);
    for (i = 0; i < len; i++) {
        u32 at = (count + i) & 63;
        wr8(st + 24 + at, rd8(in + i));
        if (at == 63) sha_block(st, st + 24);
    }
}

static void sha_init(u32 st)
{
    static const u32 start[5] = {0x67452301u, 0xEFCDAB89u, 0x98BADCFEu, 0x10325476u, 0xC3D2E1F0u};
    u32 i;
    wr32(st, 0);
    for (i = 0; i < 5; i++) wr32(st + 4 + 4 * i, start[i]);
}

static void sha_final(u32 st, u32 out, u32 room)
{
    u32 bits = rd32(st) << 3, high = rd32(st) >> 29, at = rd32(st) & 63, i;
    wr8(st + 24 + at++, 0x80);
    if (at > 56) {
        while (at < 64) wr8(st + 24 + at++, 0);
        sha_block(st, st + 24);
        at = 0;
    }
    while (at < 56) wr8(st + 24 + at++, 0);
    wr32(st + 24 + 56, high);
    wr32(st + 24 + 60, bits);
    sha_block(st, st + 24);
    for (i = 0; i < room && i < 20; i++) wr8(out + i, rd8(st + 4 + i));
}

static int krnl2_call(const XbImp *imp, const TcArgs *a, u64 *r3)
{
    switch (imp->ord) {
    case 44:      /* InterlockedPopEntrySList(header): first stop in Too Human, 82769ad0 under 82768370. The header is { u32 first, u16 depth, u16 sequence } */
    case 45:      /* InterlockedPushEntrySList(header, entry): an entry starts with the address of the next */
    case 43: {    /* InterlockedFlushSList(header) */
        volatile LONG64 *h = (volatile LONG64 *)(tc_base + (u32)a->r[0]);
        for (;;) {
            u64 was = _byteswap_uint64((u64)*h), to;
            u32 first = (u32)(was >> 32), depth = (u32)(was >> 16) & 0xFFFFu, seq = (u32)was & 0xFFFFu;
            if (imp->ord == 44) {
                if (!first) { *r3 = 0; return 1; }
                to = ((u64)rd32(first) << 32) | ((depth - 1) & 0xFFFFu) << 16 | ((seq + 1) & 0xFFFFu);
            } else if (imp->ord == 45) {
                wr32((u32)a->r[1], first);
                to = ((u64)(u32)a->r[1] << 32) | ((depth + 1) & 0xFFFFu) << 16 | ((seq + 1) & 0xFFFFu);
            } else {
                to = (u64)((seq + 1) & 0xFFFFu);
            }
            if (InterlockedCompareExchange64(h, (LONG64)_byteswap_uint64(to), (LONG64)_byteswap_uint64(was)) == (LONG64)_byteswap_uint64(was)) {
                *r3 = first;
                return 1;
            }
        }
    }
    case 399:     /* XeCryptShaInit(state): first stop in Too Human, 82a6ee98 under 82769978, hashing what it loads. The set: 400 and 401 follow it */
        sha_init((u32)a->r[0]);
        *r3 = 0;
        return 1;
    case 400:     /* XeCryptShaUpdate(state, bytes, length) */
        sha_update((u32)a->r[0], (u32)a->r[1], (u32)a->r[2]);
        *r3 = 0;
        return 1;
    case 401:     /* XeCryptShaFinal(state, out, room) */
        sha_final((u32)a->r[0], (u32)a->r[1], (u32)a->r[2]);
        *r3 = 0;
        return 1;
    case 406: {   /* XexGetModuleSection(module, name, void **data, u32 *size): first stop in Too Human, 82a484f8 under 82770830, "MyDigest". Sections are the XEX's resources, listed in games/<game>/xb_game.h */
        static const struct { const char *name; u32 at, size; } sections[] = {XB_SECTIONS};
        size_t i;
        for (i = 0; sections[i].name; i++)
            if (!strcmp((const char *)tc_base + (u32)a->r[1], sections[i].name)) {
                wr32((u32)a->r[2], sections[i].at);
                wr32((u32)a->r[3], sections[i].size);
                *r3 = 0;
                return 1;
            }
        *r3 = XB_STATUS(0xC0000225u);   /* STATUS_NOT_FOUND */
        return 1;
    }
    case 218: {   /* NtDuplicateObject(handle, HANDLE *out, options): first stop 82a70c08 on its own thread, -2. Options 1 closes the source */
        u32 from = (u32)a->r[0];
        HANDLE to = NULL;
        HANDLE source = from == 0xFFFFFFFEu ? GetCurrentThread() : from == 0xFFFFFFFFu ? GetCurrentProcess() : host_handle(from);
        if (!DuplicateHandle(GetCurrentProcess(), source, GetCurrentProcess(), &to, 0, FALSE,
                             DUPLICATE_SAME_ACCESS | (((u32)a->r[2] & 1u) ? DUPLICATE_CLOSE_SOURCE : 0u)))
            xb_stop("import_failed", "NtDuplicateObject", from);
        wr32((u32)a->r[1], handle_new(to));
        *r3 = 0;
        return 1;
    }
    case 650: {   /* NtAllocateEncryptedMemory(0, size, u32 *base): Too Human, first stop 8312ecfc on B out of the main menu's Options (build box A mn14a), size 0x14.
                   * The console maps memory its hardware encrypts: 64 KB aligned, the size rounded up to 64 KB, none over 64 KB. Guest memory is not
                   * encrypted here, which the game cannot see: a 64 KB block it can give back (651) */
        u32 size = (u32)a->r[1], at;
        if (!size) { *r3 = XB_STATUS(0xC000000Du); return 1; }      /* STATUS_INVALID_PARAMETER */
        if (size > 0x10000u) { *r3 = XB_STATUS(0xC0000017u); return 1; }   /* STATUS_NO_MEMORY */
        at = xb_alloc_block(0x10000u, 0x10000u);
        wr32((u32)a->r[2], at);
        *r3 = 0;
        return 1;
    }
    case 651:     /* NtFreeEncryptedMemory(0, base): the pair of 650 */
        *r3 = free_block_told((u32)a->r[1]) ? 0 : XB_STATUS(0xC000000Du);
        return 1;
    case 199:     /* MmSetAddressProtect(address, size, protect): first stop in Too Human, 82a48260 under 8281eda0, 2 MB with 0x404 (read-write, write combined). Guest
                   * memory is one read-write block here, so caching and write-combining have no meaning; no result */
        *r3 = 0;
        return 1;
    case 116:     /* KeInitializeSemaphore(semaphore, count, limit): the structure kevent_of reads */
        wr8((u32)a->r[0], 5);
        wr32((u32)a->r[0] + 4, (u32)a->r[1]);
        wr32((u32)a->r[0] + 16, (u32)a->r[2]);
        return 1;
    case 136: {   /* KeReleaseSemaphore(semaphore, increment, count, wait): the previous count */
        LONG before = 0;
        WS_SIGNAL(kevent_of((u32)a->r[0]));
        ReleaseSemaphore(kevent_of((u32)a->r[0]), (LONG)(u32)a->r[2], &before);
        *r3 = (u32)before;
        return 1;
    }
    case 213:     /* NtCreateSemaphore(HANDLE *out, attributes, count, limit) */
        wr32((u32)a->r[0], handle_new(CreateSemaphoreA(NULL, (LONG)(u32)a->r[2], (LONG)(u32)a->r[3], NULL)));
        *r3 = 0;
        return 1;
    case 243: {   /* NtReleaseSemaphore(handle, count, u32 *previous) */
        LONG before = 0;
        WS_SIGNAL(host_handle((u32)a->r[0]));
        ReleaseSemaphore(host_handle((u32)a->r[0]), (LONG)(u32)a->r[1], &before);
        if ((u32)a->r[2]) wr32((u32)a->r[2], (u32)before);
        *r3 = 0;
        return 1;
    }
    case 321:     /* RtlTryEnterCriticalSection(CRITICAL_SECTION *): the pair of Enter */
        *r3 = TryEnterCriticalSection(cs_of((u32)a->r[0])) ? 1 : 0;
        return 1;
    case 206:     /* NtClearEvent(handle): the pair of NtSetEvent */
        ResetEvent(host_handle((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 337: {   /* vswprintf(u16 *out, const u16 *format, va_list): Too Human, first stop 82a45e08 under 82810af8 on the main thread, the format "Game:\\".
                   * xb_format.h; the console's has no room argument, so a text longer than the kit's buffer stops */
        static __declspec(thread) u16 text[4096];   /* per thread: any guest thread may format */
        unsigned len = guest_format((u32)a->r[1], 1, (u32)a->r[2], text, 4096), i;
        if (len >= 4096) xb_stop("import_format", "a formatted text longer than 4095 characters", len);
        for (i = 0; i <= len; i++) wr16((u32)a->r[0] + 2 * i, text[i]);
        *r3 = len;
        return 1;
    }
    case 441: {   /* VdGetCurrentDisplayGamma(u32 *type, float *power): Too Human, first stop 82753dc0 under SetGammaRamp (8274ef90), which compares the type with
                   * 1 and 2 and builds its curve from the power. The console's default display: type 2 (sRGB piecewise curve), power 2.2222 (Xenia answers the same) */
        float power = 2.22222233f;
        u32 bits;
        memcpy(&bits, &power, 4);
        wr32((u32)a->r[0], 2);
        wr32((u32)a->r[1], bits);
        *r3 = 0;
        return 1;
    }
    case 254: {   /* NtWaitForMultipleObjectsEx(count, HANDLE *handles, wait type, mode, alertable, s64 *timeout): Too Human, first stop 82ec66d8 under 82ec6788 on the
                   * thread 82dae020, three handles, 1 (any), alertable. As 175, on handles: the index of the one that ended the wait */
        HANDLE h[64];
        u32 i, n = (u32)a->r[0], tp = (u32)a->r[5];
        DWORD ms = INFINITE, got;
        if (n > 64) xb_stop("wait_object", "a wait on more than 64 objects", n);
        if ((u32)a->r[4] && xb_apc_run(a)) { *r3 = STATUS_USER_APC_; return 1; }
        for (i = 0; i < n; i++) h[i] = host_handle(rd32((u32)a->r[1] + 4 * i));
        if (tp) { s64 t = (s64)rd64(tp); ms = t >= 0 ? 0 : (DWORD)((-t + 9999) / 10000); }
        got = WaitForMultipleObjects(n, h, (u32)a->r[2] == 0, ms);
        if (got == WAIT_FAILED) {
            for (i = 0; i < n; i++) port_log("wait handle %u: %08x, host %p\n", i, rd32((u32)a->r[1] + 4 * i), (void *)h[i]);
            xb_stop("wait_object", "the host's wait failed: a handle with no host object", GetLastError());
        }
        *r3 = got == WAIT_TIMEOUT ? 0x102u : got - WAIT_OBJECT_0;
        return 1;
    }
    }
    return 0;
}
