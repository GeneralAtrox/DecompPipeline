/* What a mocked call leaves besides r3 and f1 (TcCallFx, the oracle's side), included by tc_rt.c once.
 *   Q k word ...     per vector: call k's r4, scripted because its callee returns r3:r4 (TcFlow.returnsR4). tc_lastr4
 *                    answers it for that call; any other call's r4 stays the junk (mock_junk) or 0, as before.
 *   K k addr bytes   per vector: call k stores the bytes (big endian, 1..8) at addr once it returns (fx_call from
 *                    tc_call and tc_callf), as tcs_write does: in MEM1, never over an instruction, logged as a write.
 *                    Only an oracle last resort makes one (TcVectors.lastResort): a vector without K runs as before.
 * tcrt_reset forgets the lines (fx_reset); runner.c hands each one to tcrt_fx_line after the vector's V line. */
#define FX_K 64

static u32 g_fx_r4[TCRT_RETS];
static u8 g_fx_has_r4[TCRT_RETS];
static u32 g_fx_k[FX_K][3]; /* call, addr, size */
static u8 g_fx_kb[FX_K][8];
static u32 g_fx_nk;

static void fx_reset(void)
{
    memset(g_fx_has_r4, 0, sizeof g_fx_has_r4);
    g_fx_nk = 0;
}

static int fx_hex(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static void fx_k_line(char *p)
{
    char *q;
    u32 n = 0, k = (u32)strtoul(p, &q, 10), a;
    if (q == p || g_fx_nk >= FX_K) return;
    a = (u32)strtoul(q, &p, 16);
    if (p == q) return;
    while (*p == ' ') p++;
    for (; n < 8 && fx_hex(p[0]) >= 0 && fx_hex(p[1]) >= 0; n++, p += 2)
        g_fx_kb[g_fx_nk][n] = (u8)(fx_hex(p[0]) << 4 | fx_hex(p[1]));
    if (n == 0) return;
    g_fx_k[g_fx_nk][0] = k, g_fx_k[g_fx_nk][1] = a, g_fx_k[g_fx_nk][2] = n;
    g_fx_nk++;
}

void tcrt_fx_line(const char *line)
{
    char *p = (char *)line + 1;
    if (line[0] == 'K') fx_k_line(p);
    if (line[0] != 'Q') return;
    for (;;) {
        char *q;
        unsigned long k = strtoul(p, &q, 10);
        if (q == p) break;
        p = q;
        u32 w = (u32)strtoul(p, &q, 16);
        if (q == p) break;
        p = q;
        if (k < TCRT_RETS) {
            g_fx_r4[k] = w;
            g_fx_has_r4[k] = 1;
        }
    }
}

/* Call k's scripted r4: 1 and *out when Q has it. */
static int fx_r4(u32 k, u32 *out)
{
    if (k >= TCRT_RETS || !g_fx_has_r4[k]) return 0;
    *out = g_fx_r4[k];
    return 1;
}

/* Call k returns: its K writes, in the order the lines came (TcOracle writes them in the same order). */
static void fx_call(u32 k)
{
    for (u32 i = 0; i < g_fx_nk; i++)
        if (g_fx_k[i][0] == k) tcs_write(g_fx_k[i][1], g_fx_k[i][2], g_fx_kb[i]);
}
