"""The port base's network link (port/base/net_link.c) through a planted bad network, in behaviour.
A fixed-seed simulated carrier loses, repeats, delays, reorders and corrupts packets, and strangers send it garbage and
well-formed packets without the session's token. Reliable messages must arrive once, in order, whole; unreliable ones at
most once, whole; two different game builds must refuse each other.
Then the proof the checks work: each planted fault in a copy of the source must make the matching scenario fail.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_net_link.py
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
#include "net_link.h"

typedef struct { NetLink *to; uint32_t due; int len; uint8_t data[NET_MAX_PACKET]; } Flight;
#define FLIGHTS 6000
static Flight g_air[FLIGHTS];
static int g_air_n;
static uint32_t g_now, g_rng = 0x1234567u;
static int g_loss, g_dup, g_corrupt, g_latency, g_jitter;   /* percent, percent, percent, ms, ms */
static NetLink A, B, Stranger;

static uint32_t rnd(void) { g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5; return g_rng; }
static int chance(int pct) { return (int)(rnd() % 100) < pct; }

static void launch(NetLink *to, const uint8_t *p, int len)
{
    Flight *f;
    if (g_air_n == FLIGHTS) return;
    f = &g_air[g_air_n++];
    f->to = to;
    f->due = g_now + (uint32_t)g_latency + (g_jitter ? rnd() % (uint32_t)g_jitter : 0);
    f->len = len;
    memcpy(f->data, p, (size_t)len);
    if (chance(g_corrupt)) f->data[rnd() % (uint32_t)len] ^= (uint8_t)(1u << (rnd() % 8));
}
static void carry(void *ctx, const uint8_t *p, int len)
{
    NetLink *to = ctx;
    if (chance(g_loss)) return;
    launch(to, p, len);
    if (chance(g_dup)) launch(to, p, len);
}
static void deliver(void)
{
    int i = 0;
    while (i < g_air_n) {
        if (g_air[i].due <= g_now) {
            Flight f = g_air[i];
            g_air[i] = g_air[--g_air_n];
            net_link_on_packet(f.to, f.data, f.len, g_now);
        } else {
            i++;
        }
    }
}
static void fail(const char *why, int n)
{
    printf("FAIL %s %d (t=%u, A %u bad, B %u bad)\n", why, n, g_now, net_link_stats(&A)->bad, net_link_stats(&B)->bad);
    exit(1);
}
static int msg_len(int i) { return 1 + (i * 37) % 900; }
static void msg_fill(uint8_t *m, int i, int n)
{
    int k;
    for (k = 0; k < n; k++) m[k] = (uint8_t)(i * 31 + k);
    m[0] = (uint8_t)i;
    if (n > 1) m[1] = (uint8_t)(i >> 8);
}
static void setup(void)
{
    static const uint8_t d[NET_DIGEST] = { 1, 2, 3 };
    net_link_init(&A, carry, &B, d, 0x11111111u);
    net_link_init(&B, carry, &A, d, 0x22222222u);
    net_link_listen(&A, g_now);
    net_link_connect(&B, g_now);
}
static void step(void)
{
    g_now++;
    deliver();
    net_link_tick(&A, g_now);
    net_link_tick(&B, g_now);
}
static void connect_up(void)
{
    uint32_t end = g_now + 7000;
    while (g_now < end && (net_link_state(&A) != NET_ESTABLISHED || net_link_state(&B) != NET_ESTABLISHED)) step();
    if (net_link_state(&A) != NET_ESTABLISHED || net_link_state(&B) != NET_ESTABLISHED) fail("no connection", net_link_state(&B));
}

/* Sends total reliable messages from B and total unreliable; checks everything A hands over */
static void exchange(int total, uint32_t limit_ms, int strangers)
{
    static uint8_t seen[20000];
    static uint8_t m[NET_MAX_MSG], got[NET_MAX_MSG];
    int sent_r = 0, sent_u = 0, read_r = 0, read_u = 0;
    uint32_t end = g_now + limit_ms;
    memset(seen, 0, sizeof seen);
    while (g_now < end && (read_r < total || sent_u < total)) {
        int rel, n;
        if (sent_r < total) {
            int len = msg_len(sent_r);
            msg_fill(m, sent_r, len);
            if (net_link_send(&B, 1, m, len, g_now) == 0) sent_r++;
        }
        if (sent_u < total) {
            int len = msg_len(sent_u) + 2;
            msg_fill(m, sent_u, len);
            if (net_link_send(&B, 0, m, len, g_now) == 0) sent_u++;
        }
        if (strangers && g_now % 7 == 0) {
            uint8_t junk[64];
            int k, jl = 1 + (int)(rnd() % 64);
            for (k = 0; k < jl; k++) junk[k] = (uint8_t)rnd();
            net_link_on_packet(&A, junk, jl, g_now);
            net_link_send(&Stranger, 1, "EVIL", 4, g_now);
            net_link_send(&Stranger, 0, "EVIL", 4, g_now);
        }
        step();
        while ((n = net_link_recv(&A, &rel, got, sizeof got)) > 0) {
            int i = got[0] | got[1] << 8;
            if (n >= 4 && !memcmp(got, "EVIL", 4)) fail("a stranger's message came through", n);
            msg_fill(m, i, n);
            if (memcmp(m, got, (size_t)n)) fail("message damaged", i);
            if (rel) {
                if (i != (read_r & 0xFFFF) || n != msg_len(read_r)) fail("reliable out of order", i);
                read_r++;
            } else {
                if (i >= 20000) fail("unreliable number", i);
                if (seen[i]) fail("unreliable twice", i);
                if (n != msg_len(i) + 2) fail("unreliable length", i);
                seen[i] = 1;
                read_u++;
            }
        }
        if (n < 0) fail("recv", n);
    }
    if (read_r < total) fail("reliable messages missing", total - read_r);
    if (!g_loss && !g_corrupt && read_u + (int)net_link_stats(&A)->dropped_dgrams < total) fail("unreliable lost on a clean link", read_u);
    printf("ok t=%u rel %d dgram %d/%d retrans %u dup %u bad %u rtt %u\n", g_now, read_r, read_u, total,
           net_link_stats(&B)->retransmits, net_link_stats(&A)->duplicates, net_link_stats(&A)->bad, net_link_stats(&B)->rtt_ms);
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    static const uint8_t other[NET_DIGEST] = { 9 };
    if (!strcmp(s, "clean")) { g_latency = 5; setup(); connect_up(); exchange(2000, 60000, 0); }
    else if (!strcmp(s, "bad")) { g_latency = 40; g_jitter = 60; g_loss = 20; g_dup = 10; setup(); connect_up(); exchange(1500, 60000, 0); }
    else if (!strcmp(s, "dup")) { g_latency = 5; g_jitter = 20; g_dup = 60; setup(); connect_up(); exchange(800, 120000, 0); }
    else if (!strcmp(s, "corrupt")) { g_latency = 5; g_corrupt = 30; setup(); connect_up(); exchange(800, 120000, 0); }
    else if (!strcmp(s, "strangers")) {
        g_latency = 5;
        setup();
        connect_up();
        net_link_init(&Stranger, carry, &A, A.digest, 5);
        Stranger.state = NET_ESTABLISHED;
        Stranger.token = A.token ^ 0x5A5A5A5Au;
        Stranger.next_dgram = 30000;   /* ahead of the real numbers, so only the token can keep it out */
        exchange(500, 60000, 1);
    } else if (!strcmp(s, "wrap")) {   /* the numbers roll over past 65535 in the middle of the run */
        g_latency = 3;
        g_jitter = 20;
        g_loss = 5;
        setup();
        connect_up();
        B.next_rel = B.next_dgram = 65000;
        A.read_rel = 65000;
        { int i; for (i = 0; i < NET_WINDOW; i++) B.out[i].used = 0; }
        {
            static uint8_t m[8], got[NET_MAX_MSG];
            int sent = 0, read = 0, rel, n;
            uint32_t end = g_now + 120000;
            while (g_now < end && read < 1500) {
                if (sent < 1500) {
                    m[0] = (uint8_t)sent; m[1] = (uint8_t)(sent >> 8);
                    if (net_link_send(&B, 1, m, 8, g_now) == 0) sent++;
                }
                step();
                while ((n = net_link_recv(&A, &rel, got, sizeof got)) > 0)
                    if (rel) {
                        if ((got[0] | got[1] << 8) != read) fail("wrap out of order", read);
                        read++;
                    }
            }
            if (read < 1500) fail("wrap messages missing", 1500 - read);
            printf("ok wrap read %d\n", read);
        }
    } else if (!strcmp(s, "refuse")) {   /* a different game build: refused on the joiner, the host keeps waiting */
        static const uint8_t ours[NET_DIGEST] = { 1, 2, 3 };
        g_latency = 5;
        net_link_init(&A, carry, &B, ours, 0x11111111u);
        net_link_listen(&A, g_now);
        net_link_init(&B, carry, &A, other, 0x33333333u);
        net_link_connect(&B, g_now);
        { int i; for (i = 0; i < 1000; i++) step(); }
        if (net_link_state(&B) != NET_REFUSED || net_link_status(&B) != NET_REFUSED_CONTENT) fail("different data not refused", net_link_state(&B));
        if (net_link_state(&A) != NET_LISTENING) fail("the host stopped waiting", net_link_state(&A));
        printf("ok refused\n");
    } else if (!strcmp(s, "gone")) {   /* one player silently gone, and one who says goodbye */
        g_latency = 5;
        setup();
        connect_up();
        g_loss = 100;
        while (g_now < 9500) step();
        if (net_link_state(&A) != NET_TIMED_OUT) fail("no time-out", net_link_state(&A));
        g_loss = 0;
        g_air_n = 0;
        setup();
        connect_up();
        net_link_close(&B);
        { int i; for (i = 0; i < 100; i++) step(); }
        if (net_link_state(&A) != NET_CLOSED) fail("goodbye not heard", net_link_state(&A));
        printf("ok gone\n");
    } else if (!strcmp(s, "restart")) {   /* the joiner starts again without a word: the host's link ends at once, not 8 seconds on */
        static const uint8_t d[NET_DIGEST] = { 1, 2, 3 };
        uint32_t end;
        g_latency = 5;
        setup();
        connect_up();
        net_link_init(&B, carry, &A, d, 0x44444444u);
        net_link_connect(&B, g_now);
        end = g_now + 1000;
        while (g_now < end && net_link_state(&A) == NET_ESTABLISHED) step();
        if (net_link_state(&A) != NET_CLOSED) fail("a new start not heard", net_link_state(&A));
        net_link_init(&A, carry, &B, d, 0x11111112u);
        net_link_listen(&A, g_now);
        connect_up();
        printf("ok restart\n");
    } else if (!strcmp(s, "bye")) {   /* half of all packets lost: a goodbye still gets there nearly always */
        int heard = 0, i, k;
        g_latency = 5;
        for (i = 0; i < 100; i++) {
            g_loss = 0;
            g_air_n = 0;
            setup();
            connect_up();
            g_loss = 50;
            net_link_close(&B);
            for (k = 0; k < 20; k++) step();
            if (net_link_state(&A) == NET_CLOSED) heard++;
        }
        if (heard < 75) fail("goodbyes heard", heard);
        printf("ok bye %d\n", heard);
    } else if (!strcmp(s, "window")) {   /* nobody reading: the sender is told to wait, nothing lost */
        int sent = 0, i, rel, read = 0;
        uint8_t m[4] = { 1, 2, 3, 4 }, got[8];
        g_latency = 5;
        setup();
        connect_up();
        for (i = 0; i < 4000; i++) {
            m[0] = (uint8_t)sent;
            if (net_link_send(&B, 1, m, 4, g_now) == 0) sent++;
            step();
        }
        if (sent != 2 * NET_WINDOW) fail("window not honoured", sent);   /* the reader's held ones and the sender's own */
        for (i = 0; i < 4000; i++) {
            step();
            while (net_link_recv(&A, &rel, got, sizeof got) > 0) {
                if (got[0] != (uint8_t)read) fail("window out of order", read);
                read++;
            }
        }
        if (read != sent) fail("window lost messages", sent - read);
        printf("ok window %d\n", sent);
    } else {
        fail("no such scenario", 0);
    }
    return 0;
}
"""

SCENARIOS = ("clean", "bad", "dup", "corrupt", "strangers", "wrap", "refuse", "gone", "window", "restart", "bye")

# (the code to replace, what replaces it, the scenario that must then fail)
PLANTED = (
    ("if (s->used && now - s->sent_ms >= s->rto) resend(l, s, now);", "(void)s;", "bad"),
    ("if (~crc_add(crc_add(crc_add(0xFFFFFFFFu, p, 8), zero, 4), p + 12, len - 12) != r32(p + 8)) { l->stats.bad++; return; }", "(void)zero;", "corrupt"),
    ("if (l->dgram_seen >> -ahead & 1) { l->stats.duplicates++; return; }", ";", "dup"),
    ("|| r32(p + 4) != l->token", "", "strangers"),
    ("else if (memcmp(m + 8, l->digest, NET_DIGEST)) st = NET_REFUSED_CONTENT;", "", "refuse"),
    ("after = (uint16_t)(s->seq - next);", "after = s->seq < next ? 0x8000u : (uint16_t)(s->seq - next);", "wrap"),
    ("if (now - l->last_rx >= l->timeout_ms) { l->state = NET_TIMED_OUT; return; }", "", "gone"),
    ("if (s->used) return -1;   /* the window is full: the oldest in flight sits in this slot */", "", "window"),
    ("if (!in_window(seq, l->read_rel)) {", "if (0) {", "bad"),
    ("} else if (l->state == NET_ESTABLISHED && st == NET_OK) {", "} else if (0) {", "restart"),
    ("#define BYE_SENDS 3", "#define BYE_SENDS 1", "bye"),
)


def build(d, source, name):
    vc = str(game_config.vcvars32()).replace("/", "\\")
    (d / f"{name}.c").write_text(source)
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /TC /std:c11 /W4 /WX /O1 /I"{BASE}" /I"{d}" '
                       f'{name}.c check.c /Fe:{name}.exe"', cwd=d, capture_output=True, text=True, timeout=300)
    return r


@unittest.skipUnless(HAVE_VC, NO_VC)
class NetLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pathlib.Path(tempfile.mkdtemp(prefix="net_link_"))
        (cls.d / "check.c").write_text(CHECK_C)
        cls.source = (BASE / "net_link.c").read_text()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def run_scenario(self, exe, s):
        return subprocess.run([str(self.d / exe), s], capture_output=True, text=True, timeout=120)

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
