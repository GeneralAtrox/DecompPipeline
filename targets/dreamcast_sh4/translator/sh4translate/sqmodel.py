"""The SH-4 store-queue model the translator's C carries (SQ_HELPERS), moved from translate.py unchanged."""
import re


# The SH-4 store queues as DP's judge models them (DP's TcSq; tc_rt.h maps the window onto RAM for these functions): a store in the window
# 0xe0000000..0xe3ffffff lands in RAM at once and fills its bytes of queue SQ[address bit 5] (at the address' bits 0..4: a block 0x40 on
# shares them); `pref @a` sends a's queue to a's 32-byte RAM block: the bytes stored since, and the rest from the block that queue last
# held (when that is a's own block, those bytes stay as they are, unread: it may be machine code, which the C must not read, R8; a store
# hands its bytes to the queue for the same reason). At entry the stream before the pointer is taken as sent (the queues hold the blocks
# 0x40 and 0x20 before it), and a callee that moves the pointer up through the window sends each block it passes.
SQ_WRITE = re.compile(r"\bwr(8|16|32|f32)\(")
SQ_HELPERS = [
    "/* The SH-4 store queues: a store in the window 0xe0000000..0xe3ffffff lands in RAM and fills its bytes of queue",
    " * (address bit 5); pref @a sends a's queue to a's RAM block: the bytes stored since, the rest from the block the",
    " * queue last held. */",
    "typedef struct { u32 from[2]; u32 marked[2]; u8 bytes[2][32]; } SqQueues;",
    "static u32 sq_in(u32 a) { return a - 0xe0000000u < 0x04000000u; }",
    "static void sq_held(SqQueues *q, u32 b)",
    "{",
    "    if (sq_in(b)) { q->from[(b >> 5) & 1u] = 0x8c000000u | (b & 0x00ffffe0u); q->marked[(b >> 5) & 1u] = 0; }",
    "}",
    "static void sq_start(SqQueues *q, u32 p)",
    "{",
    "    q->from[0] = 0; q->from[1] = 0; q->marked[0] = 0; q->marked[1] = 0;",
    "    p &= 0xffffffe0u;",
    "    if (sq_in(p)) { sq_held(q, p - 0x40u); sq_held(q, p - 0x20u); }",
    "}",
    "static void sq_put(SqQueues *q, u32 a, u32 n, u32 v)",
    "{",
    "    u32 i;",
    "    for (i = 0; i < n; i++)",
    "        if (sq_in(a + i)) {",
    "            q->marked[((a + i) >> 5) & 1u] |= 1u << ((a + i) & 31u);",
    "            q->bytes[((a + i) >> 5) & 1u][(a + i) & 31u] = (u8)(v >> (8u * i));",
    "        }",
    "}",
    "/* A store that also fills its bytes of queue. */",
    "static void sq_wr8(SqQueues *q, u32 a, u8 v) { wr8(a, v); sq_put(q, a, 1u, v); }",
    "static void sq_wr16(SqQueues *q, u32 a, u16 v) { wr16(a, v); sq_put(q, a, 2u, v); }",
    "static void sq_wr32(SqQueues *q, u32 a, u32 v) { wr32(a, v); sq_put(q, a, 4u, v); }",
    "static void sq_wrf32(SqQueues *q, u32 a, f32 v) { union { u32 u; f32 f; } b; b.f = v; wrf32(a, v); "
    "sq_put(q, a, 4u, b.u); }",
    "static void sq_pref(SqQueues *q, u32 a)",
    "{",
    "    u8 b[32];",
    "    u32 k, to, from, j;",
    "    if (!sq_in(a)) return;",
    "    k = (a >> 5) & 1u;",
    "    to = 0x8c000000u | (a & 0x00ffffe0u);",
    "    from = q->from[k] ? q->from[k] : to;",
    "    for (j = 0; j < 32u; j++)",
    "        b[j] = ((q->marked[k] >> j) & 1u) ? q->bytes[k][j] : from != to ? rd8(from + j) : 0;",
    "    for (j = 0; j < 32u; j++)",
    "        if (((q->marked[k] >> j) & 1u) || from != to) wr8(to + j, b[j]);   /* a's own bytes stay as they are */",
    "    q->from[k] = to;",
    "    q->marked[k] = 0;",
    "}",
    "static void sq_moved(SqQueues *q, u32 p, u32 r)",
    "{",
    "    u32 b;",
    "    if (!sq_in(p) || r <= p || r - p > 0x1000u) return;",
    "    for (b = p & 0xffffffe0u; b < r; b += 0x20u) sq_held(q, b);",
    "}",
]


def sq_callee_bytes(meta):
    """meta "sq_callee" {"8c074f18": {"r4": "10-17,20-2f"}} as {0x8c074f18: {"r4": (bits 0..63, bits 64..127)}};
    a loose register ("?": the callee's stores depend on data) stores nothing in DP's model."""
    out = {}
    for c, regs in (meta or {}).items():
        for r, spec in regs.items():
            if spec == "?":
                continue
            bits = 0
            for part in spec.split(","):
                lo, _, hi = part.partition("-")
                for j in range(int(lo, 16), int(hi or lo, 16) + 1):
                    bits |= 1 << j
            out.setdefault(int(c, 16), {})[r] = (bits & (1 << 64) - 1, bits >> 64)
    return out or None


def sq_callee_helpers():
    """SQ_HELPERS with the bytes a callee stored (sq_mark): a pref sends each from its RAM byte, unread when that is
    the byte it sends to (the block's own byte stays: the callee wrote it there; it may be machine code, R8)."""
    h = list(SQ_HELPERS)
    swap = {
        "typedef struct { u32 from[2]; u32 marked[2]; u8 bytes[2][32]; } SqQueues;":
            ["typedef struct { u32 from[2]; u32 marked[2]; u8 bytes[2][32]; u32 src[2][32]; } SqQueues;",
             "static void sq_clr(u32 *s, u32 n) { while (n) s[--n] = 0; }"],
        "    if (sq_in(b)) { q->from[(b >> 5) & 1u] = 0x8c000000u | (b & 0x00ffffe0u); q->marked[(b >> 5) & 1u] = 0; }":
            ["    if (sq_in(b)) { q->from[(b >> 5) & 1u] = 0x8c000000u | (b & 0x00ffffe0u); q->marked[(b >> 5) & 1u] = 0;",
             "                    sq_clr(q->src[(b >> 5) & 1u], 32u); }"],
        "    q->from[0] = 0; q->from[1] = 0; q->marked[0] = 0; q->marked[1] = 0;":
            ["    q->from[0] = 0; q->from[1] = 0; q->marked[0] = 0; q->marked[1] = 0; sq_clr(q->src[0], 64u);"],
        "            q->bytes[((a + i) >> 5) & 1u][(a + i) & 31u] = (u8)(v >> (8u * i));":
            ["            q->bytes[((a + i) >> 5) & 1u][(a + i) & 31u] = (u8)(v >> (8u * i));",
             "            q->src[((a + i) >> 5) & 1u][(a + i) & 31u] = 0;"],
        "        b[j] = ((q->marked[k] >> j) & 1u) ? q->bytes[k][j] : from != to ? rd8(from + j) : 0;":
            ["        b[j] = ((q->marked[k] >> j) & 1u) ? q->bytes[k][j] : q->src[k][j] ? (q->src[k][j] != to + j ? "
             "rd8(q->src[k][j]) : 0)",
             "             : from != to ? rd8(from + j) : 0;"],
        "        if (((q->marked[k] >> j) & 1u) || from != to) wr8(to + j, b[j]);   /* a's own bytes stay as they are */":
            ["        if (((q->marked[k] >> j) & 1u) || (q->src[k][j] ? q->src[k][j] != to + j : from != to))",
             "            wr8(to + j, b[j]);   /* a's own bytes stay as they are */"],
        "    q->marked[k] = 0;":
            ["    q->marked[k] = 0;", "    sq_clr(q->src[k], 32u);"],
    }
    out = []
    for ln in h:
        out += swap.pop(ln, [ln])
    if swap:
        raise AssertionError(f"SQ_HELPERS lines moved: {list(swap)}")
    return out + [
        "/* A mocked callee stored the bytes lo/hi mark (offsets 0..127) through a: those in the window are its own. */",
        "static void sq_mark(SqQueues *q, u32 a, unsigned long long lo, unsigned long long hi)",
        "{",
        "    u32 j, b;",
        "    for (j = 0; j < 128u; j++) {",
        "        b = a + j;",
        "        if (!(((j < 64u ? lo >> j : hi >> (j - 64u)) & 1u) && sq_in(b))) continue;",
        "        q->marked[(b >> 5) & 1u] &= ~(1u << (b & 31u));",
        "        q->src[(b >> 5) & 1u][b & 31u] = 0x8c000000u | (b & 0x00ffffffu);",
        "    }",
        "}",
    ]
