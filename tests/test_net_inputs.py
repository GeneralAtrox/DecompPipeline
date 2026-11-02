"""The port base's input stream (port/base/net_inputs.c) through planted loss, in behaviour. Every message must come out once, in
order, byte for byte, with nothing skipped: a lost packet that a game reads as "everything older arrived" is a silent desync.
Then the proof the checks work: each planted fault in a copy of the source must make the matching scenario fail.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_net_inputs.py
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
BASE = TOOL / "port" / "base"
sys.path.insert(0, str(TOOL / "port" / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

CHECK_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "net_inputs.h"

#define CAP 1400
#define TOTAL 5000
static NetInputs tx, rx;
static uint32_t seed = 12345;
static uint32_t got;
static unsigned rnd(unsigned n) { seed = seed * 1664525u + 1013904223u; return (seed >> 8) % n; }

static int msg_len(uint32_t seq) { return 8 + (int)(seq * 7u % 90u); }
static void msg_fill(uint32_t seq, uint8_t *m, int len) { int i; for (i = 0; i < len; i++) m[i] = (uint8_t)(seq * 31u + (unsigned)i); }

/* Everything that has arrived must be exactly the next messages */
static void take(void)
{
    static uint8_t b[NET_INPUT_MSG], want[NET_INPUT_MSG];
    int n;
    while ((n = net_inputs_next(&rx, b, sizeof b)) > 0) {
        int len = msg_len(got);
        msg_fill(got, want, len);
        if (n != len || memcmp(b, want, (size_t)len)) { printf("message %u is wrong\n", got); exit(1); }
        got++;
    }
}

static void all_arrived(void)
{
    if (got != TOTAL) { printf("%u of %u messages\n", got, TOTAL); exit(1); }
}

typedef struct { int len; uint8_t d[CAP]; } Pkt;
static Pkt fast, sure;

static void pack(uint32_t seq)
{
    uint8_t m[NET_INPUT_MSG];
    int len = msg_len(seq);
    msg_fill(seq, m, len);
    if (net_inputs_pack(&tx, m, len, fast.d, CAP, &fast.len, sure.d, CAP, &sure.len)) { printf("pack refused\n"); exit(1); }
}

static void clean(void)
{
    uint32_t i;
    for (i = 0; i < TOTAL; i++) { pack(i); net_inputs_on_packet(&rx, fast.d, fast.len); take(); }
    all_arrived();
    if (rx.repeats == 0) { printf("the repeats were not seen\n"); exit(1); }
}

/* Half the fast packets go, and never three in a row: the repeats alone must cover it, with no reliable channel */
static void light(void)
{
    uint32_t i, lost = 0;
    for (i = 0; i < TOTAL; i++) {
        pack(i);
        if (lost < 3 && rnd(2) && i + 1 < TOTAL) { lost++; continue; }
        lost = 0;
        net_inputs_on_packet(&rx, fast.d, fast.len);
        take();
    }
    all_arrived();
}

/* Long bursts of fast packets go. The reliable copies come late but in order: nothing may come out until they fill the gap */
static void burst(void)
{
    static Pkt late[256];
    uint32_t i, head = 0, tail = 0, burst_left = 0;
    for (i = 0; i < TOTAL; i++) {
        pack(i);
        late[tail++ % 256] = sure;
        if (burst_left == 0 && rnd(40) == 0) burst_left = 20 + rnd(100);
        if (burst_left) burst_left--;
        else net_inputs_on_packet(&rx, fast.d, fast.len);
        take();
        if (tail - head > 30) { net_inputs_on_packet(&rx, late[head % 256].d, late[head % 256].len); head++; take(); }
    }
    while (head != tail) { net_inputs_on_packet(&rx, late[head % 256].d, late[head % 256].len); head++; take(); }
    all_arrived();
}

/* Packets in any order, some twice, inside a small window; the reliable copies then close what is left */
static void shuffled(void)
{
    static Pkt win[16], rel[TOTAL / 8];
    uint32_t i, r = 0, head = 0;
    int used[16] = { 0 };
    for (i = 0; i < TOTAL; i++) {
        unsigned s = rnd(16);
        pack(i);
        if (used[s]) {
            net_inputs_on_packet(&rx, win[s].d, win[s].len);
            if (rnd(4) == 0) net_inputs_on_packet(&rx, win[s].d, win[s].len);
            take();
        }
        win[s] = fast;
        used[s] = 1;
        rel[r++ % (TOTAL / 8)] = sure;
        if (r - head > 40) { net_inputs_on_packet(&rx, rel[head % (TOTAL / 8)].d, rel[head % (TOTAL / 8)].len); head++; take(); }
    }
    while (head != r) { net_inputs_on_packet(&rx, rel[head % (TOTAL / 8)].d, rel[head % (TOTAL / 8)].len); head++; take(); }
    all_arrived();
}

static void expect_bad(const uint8_t *p, int len)
{
    uint32_t before = rx.bad;
    net_inputs_on_packet(&rx, p, len);
    if (rx.bad != before + 1) { printf("a bad packet of %d bytes was believed\n", len); exit(1); }
    if (net_inputs_next(&rx, sure.d, CAP) != 0) { printf("a bad packet gave a message\n"); exit(1); }
}

static void bad(void)
{
    static uint8_t p[CAP * 2];
    memset(p, 0, sizeof p);
    expect_bad(p, 0);
    expect_bad(p, 4);
    p[3] = 9; expect_bad(p, 5); p[3] = 0;  /* no messages at all */
    p[4] = 5; expect_bad(p, 200);          /* more than a packet can carry */
    p[4] = 2; p[3] = 0; p[6] = 4; p[12] = 0; p[13] = 4; expect_bad(p, 5 + 6 + 6);   /* the second one reaches before the first */
    p[3] = 9; p[4] = 1; p[5] = 0; p[6] = 200; expect_bad(p, 100);                     /* a length past the end */
    p[5] = 0; p[6] = 0; expect_bad(p, 7);                                             /* an empty message */
    p[5] = 0x05; p[6] = 0x14; expect_bad(p, 7 + 0x514);                               /* framed well, longer than a held message */
    p[5] = 0; p[6] = 4; expect_bad(p, 12);                                            /* bytes after the last message */
    p[3] = 0; p[4] = 2; p[5] = 0; p[6] = 1; p[8] = 0; p[9] = 1; expect_bad(p, 11);    /* more messages than sequence numbers below it */
    clean();                               /* and it still works */
}

/* A fast packet from far ahead may not take the slot of a message that is still to come */
static void far(void)
{
    uint8_t p[32];
    uint32_t i;
    memset(p, 0x77, sizeof p);
    p[0] = 0; p[1] = 0; p[2] = 0; p[3] = NET_INPUT_HELD; p[4] = 1; p[5] = 0; p[6] = 8;
    net_inputs_on_packet(&rx, p, 7 + 8);
    if (net_inputs_next(&rx, sure.d, CAP) != 0) { printf("a message from far ahead came out\n"); exit(1); }
    for (i = 0; i < TOTAL; i++) { pack(i); net_inputs_on_packet(&rx, fast.d, fast.len); take(); }
    all_arrived();
}

/* The reader stops for long stretches while the sender goes on. The reliable copies wait outside until they fit: none may be lost */
static void slow(void)
{
    static Pkt wait[TOTAL];
    uint32_t i, head = 0, tail = 0;
    for (i = 0; i < TOTAL; i++) {
        pack(i);
        wait[tail++] = sure;
        while (head != tail && net_inputs_fits(&rx, wait[head].d, wait[head].len)) { net_inputs_on_packet(&rx, wait[head].d, wait[head].len); head++; }
        if (i % 500 > 300) take();
    }
    while (head != tail) {
        if (!net_inputs_fits(&rx, wait[head].d, wait[head].len)) take();
        if (!net_inputs_fits(&rx, wait[head].d, wait[head].len)) { printf("stuck at %u\n", head); exit(1); }
        net_inputs_on_packet(&rx, wait[head].d, wait[head].len);
        head++;
    }
    take();
    all_arrived();
}

/* A big message: the fast packet carries what fits and never writes past its buffer */
static void room(void)
{
    static uint8_t m[NET_INPUT_MSG], f[1400 + 64], s[NET_INPUT_MSG + 16];
    int i, fl, sl, r;
    memset(f + 1400, 0xAB, 64);
    for (i = 0; i < 8; i++) {
        memset(m, i, sizeof m);
        if (net_inputs_pack(&tx, m, NET_INPUT_MSG, f, 1400, &fl, s, sizeof s, &sl)) { printf("pack refused\n"); exit(1); }
        if (fl > 1400) { printf("a fast packet of %d\n", fl); exit(1); }
        for (r = 0; r < 64; r++) if (f[1400 + r] != 0xAB) { printf("past the buffer\n"); exit(1); }
        net_inputs_on_packet(&rx, f, fl);
    }
    if (rx.bad) { printf("its own packets were bad\n"); exit(1); }
    if (net_inputs_pack(&tx, m, NET_INPUT_MSG + 1, f, 1400, &fl, s, sizeof s, &sl) != -1) { printf("too long was taken\n"); exit(1); }
    if (net_inputs_pack(&tx, m, 0, f, 1400, &fl, s, sizeof s, &sl) != -1) { printf("empty was taken\n"); exit(1); }
    if (tx.next_send != 8) { printf("a refused message took a number\n"); exit(1); }
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    net_inputs_init(&tx);
    net_inputs_init(&rx);
    if (!strcmp(s, "clean")) clean();
    else if (!strcmp(s, "light")) light();
    else if (!strcmp(s, "burst")) burst();
    else if (!strcmp(s, "shuffled")) shuffled();
    else if (!strcmp(s, "bad")) bad();
    else if (!strcmp(s, "far")) far();
    else if (!strcmp(s, "room")) room();
    else if (!strcmp(s, "slow")) slow();
    else return 2;
    printf("ok %s\n", s);
    return 0;
}
"""

SCENARIOS = ("clean", "light", "burst", "shuffled", "bad", "far", "room", "slow")

# Each one breaks what its scenario proves: (old text, new text, scenario)
PLANTED = (
    ("n->next_recv++;", "n->next_recv += 2;", "clean"),
    ("if (!n->held_used[slot]) return 0;", "if (!n->held_used[slot]) { n->next_recv++; return 0; }", "burst"),
    ("i < NET_INPUT_REPEAT && (uint32_t)i <= seq", "i < 1 && (uint32_t)i <= seq", "light"),
    ("seq - n->next_recv < NET_INPUT_HELD", "seq - n->next_recv < 2 * NET_INPUT_HELD", "far"),
    ("if (seq < n->next_recv) {", "if (0) {", "clean"),
    ("if (at + 2 + m->len > fast_cap) break;", "", "room"),
    ("m > NET_INPUT_MSG || ", "", "bad"),
    ("if (m == 0 || ", "if (", "bad"),
    ("if (at != len) { n->bad++; return; }", "", "bad"),
    (" || seq < (uint32_t)count - 1", "", "bad"),
    ("len <= 0 || len > NET_INPUT_MSG || ", "", "room"),
    ("return seq < n->next_recv || seq - n->next_recv < NET_INPUT_HELD;", "return (void)n, (void)seq, 1;", "slow"),
)


def build(d, source, name):
    vc = str(game_config.vcvars32()).replace("/", "\\")
    (d / f"{name}.c").write_text(source)
    return subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{BASE}" /I"{d}" '
                          f'{name}.c check.c /Fe:{name}.exe"', cwd=d, capture_output=True, text=True, timeout=300)


@unittest.skipUnless(HAVE_VC, NO_VC)
class NetInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pathlib.Path(tempfile.mkdtemp(prefix="net_inputs_"))
        (cls.d / "check.c").write_text(CHECK_C)
        cls.source = (BASE / "net_inputs.c").read_text()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def run_scenario(self, exe, s):
        return subprocess.run([str(self.d / exe), s], capture_output=True, text=True, timeout=60)

    def test_scenarios(self):
        r = build(self.d, self.source, "good")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for s in SCENARIOS:
            with self.subTest(s):
                got = self.run_scenario("good.exe", s)
                self.assertEqual(got.returncode, 0, got.stdout)

    def test_planted_faults_are_caught(self):
        for i, (old, new, s) in enumerate(PLANTED):
            with self.subTest(old[:50]):
                self.assertIn(old, self.source)
                r = build(self.d, self.source.replace(old, new), f"m{i}")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                got = self.run_scenario(f"m{i}.exe", s)
                self.assertNotEqual(got.returncode, 0, f"the {s} scenario passed with a planted fault: {got.stdout}")


if __name__ == "__main__":
    unittest.main()
