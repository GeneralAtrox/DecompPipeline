"""Host stack locals in readable C (core/stack_overread.py, docs/PROOF_GATES.md section 6). One fail and one pass
control each:
1. R1 overread: a lone Vec3 passed down a call chain to a callee that reads `center + 1` (TC 8c017382's hit_pos,
   read by Explosion_HitEntity) is listed; the two vectors kept adjacent are not. A direct constant index past a
   local's end is listed. A cursor stepped in a loop (a word-at-a-time strcpy, a chunked read) reaches an unknown
   extent and is not; a body-less copy helper reaches its constant size argument.
2. R2 copied out half written: a local filled in part by a callee and then block-copied out (TC 8c06fc14's
   LibAnimPoseBuf frame) is listed; zeroed by its initializer, or filled in full by the callee, it is not. A callee
   that writes its out parameter in every case of a switch with a default fills it; one with a case that returns
   first does not. A member copy guarded by another field (`if (q.hit) *out = q.hit_pos;`) is a field read, not
   checked; padding is not required.
Synthetic C, parsed by pycparser (no cl). Run: python -W error tests/test_stack_overread.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import stack_overread as so  # noqa: E402

HEADER = """
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef int s32; typedef float f32;
typedef unsigned int uintptr_t;
typedef struct Vec3 { f32 x, y, z; } Vec3;
typedef struct Query { u32 hit; Vec3 hit_pos; Vec3 dir; } Query;
typedef struct Frame { u32 index; u32 count; } Frame;
typedef struct PoseBuf { u32 index; u32 count; u32 poses[16]; } PoseBuf;
typedef struct Padded { u8 tag; u32 value; } Padded;
void Shc_BlockCopy(u32 size, void *dst, const void *src);
void Mem_Zero(void *dst, u32 size);
s32 Lib_LowRead(s32 file, void *buf, u32 size);
void Sink(const void *p);
void Sink_Vec(Vec3 v);
void Sink_Padded(Padded p);
"""

EXPLOSION = """
void Vec3_MulScalar(Vec3 *out, const Vec3 *v, f32 s) { out->x = v->x * s; out->y = v->y * s; out->z = v->z * s; }
static void HitEntity(const Vec3 *center, f32 s)
{
    Vec3 impulse;
    if (s > 1.0f) {
        Vec3_MulScalar(&impulse, center + 1, s);
        Sink(&impulse);
    }
}
void ApplyDesc(const Vec3 *center, f32 s) { HitEntity(center, s); }
void Projectile_Hit(void *p, const Vec3 *pos) { (void)p; ApplyDesc(pos, 2.0f); }
"""


def hits(files, header=HEADER):
    return so.check_text(header, files)["hits"]


def rules(files, fn=None):
    return sorted((h["rule"], h["local"]) for h in hits(files) if fn is None or h["fn"].split(":")[-1] == fn)


class Overread(unittest.TestCase):
    def test_lone_vector_down_a_chain(self):
        lone = "void Sweep(Query *q) { Vec3 hit_pos; hit_pos = q->hit_pos; Projectile_Hit(q, &hit_pos); }"
        got = [h for h in hits({"a": EXPLOSION, "b": lone}) if h["fn"] == "Sweep"]
        self.assertEqual([(h["rule"], h["local"], h["line"]) for h in got], [("R1", "hit_pos", 1)])
        self.assertIn("reaches 24 bytes", got[0]["why"])

    def test_adjacent_vectors_pass(self):
        pair = ("void Sweep(Query *q) { Vec3 hit_pos[2]; hit_pos[0] = q->hit_pos; hit_pos[1] = q->dir;"
                " Projectile_Hit(q, &hit_pos[0]); }")
        self.assertEqual(rules({"a": EXPLOSION, "b": pair}, "Sweep"), [])

    def test_offset_into_a_pair(self):
        late = ("void Sweep(Query *q) { Vec3 hit_pos[2]; hit_pos[0] = q->hit_pos; hit_pos[1] = q->dir;"
                " Projectile_Hit(q, &hit_pos[1]); }")
        self.assertEqual(rules({"a": EXPLOSION, "b": late}, "Sweep"), [("R1", "hit_pos")])

    def test_direct_index(self):
        self.assertEqual(rules({"a": "u32 F(void) { u32 w[2]; w[0] = 1; w[1] = 2; return w[2]; }"}), [("R1", "w")])
        self.assertEqual(rules({"a": "u32 F(void) { u32 w[3]; w[0] = 1; w[1] = 2; w[2] = 3; return w[2]; }"}), [])

    def test_cursor_loops(self):
        strcpy = """
void StrCpy(char *dst, const char *src)
{
    u32 *dw; const u32 *sw; char *d = dst; const char *s = src;
    if ((((uintptr_t)dst | (uintptr_t)src) & 3u) == 0u) {
        dw = (u32 *)dst; sw = (const u32 *)src;
        while (*sw != 0u) { *dw = *sw; dw++; sw++; }
        d = (char *)dw; s = (const char *)sw;
    }
    while (*s != 0) { *d = *s; d++; s++; }
    *d = 0;
}
void Draw(const char *digits) { char text[4]; StrCpy(text + 1, digits); text[0] = '0'; Sink(text); }
"""
        read = """
s32 File_Read(s32 file, void *buf, u32 size)
{
    u8 *cursor = (u8 *)buf; u32 remaining = size; s32 total = 0;
    while (remaining > 0x8000u) { total += Lib_LowRead(file, cursor, 0x8000u); remaining -= 0x8000u; cursor += 0x8000u; }
    if (remaining != 0u) { total += Lib_LowRead(file, cursor, remaining); }
    return total;
}
void Mount(s32 f) { u32 word; File_Read(f, &word, 4u); Sink(&word); }
"""
        self.assertEqual(rules({"a": strcpy}), [])
        self.assertEqual(rules({"a": read}), [])

    def test_copy_helper_size(self):
        self.assertEqual(rules({"a": "void F(void *d) { u32 w[2]; w[0] = 0; w[1] = 0; Shc_BlockCopy(12u, d, w); }"}),
                         [("R1", "w")])
        self.assertEqual(rules({"a": "void F(void *d) { u32 w[3] = {0}; Shc_BlockCopy(12u, d, w); }"}), [])


READ_FRAME = """
void Anim_ReadFrame(u32 index, Frame *out)
{
    s32 alias = (s32)(uintptr_t)out;
    u32 *words = (u32 *)(uintptr_t)(u32)alias;
    u32 i;
    words[1] = 0u;
    for (i = 0u; i < index; i++) { words[2 + i] = i; }
}
"""


class HalfWritten(unittest.TestCase):
    def test_pose_frame(self):
        old = """
void Init(u32 *dst, u32 n)
{
    u32 i;
    for (i = 0u; i < n; i++) {
        PoseBuf frame;
        Anim_ReadFrame(i, (Frame *)&frame);
        Shc_BlockCopy((u32)sizeof(frame.poses), dst + 16u * i, (u8 *)&frame + sizeof(Frame));
    }
}
"""
        got = [h for h in hits({"a": READ_FRAME, "b": old}) if h["fn"] == "Init"]
        self.assertEqual([(h["rule"], h["local"], h["line"]) for h in got], [("R2", "frame", 8)])
        self.assertIn("bytes 8..72", got[0]["why"])

    def test_pose_frame_zeroed(self):
        new = """
void Init(u32 *dst, u32 n)
{
    u32 i;
    for (i = 0u; i < n; i++) {
        PoseBuf frame = {0};
        Anim_ReadFrame(i, (Frame *)&frame);
        Shc_BlockCopy((u32)sizeof(frame.poses), dst + 16u * i, frame.poses);
    }
}
"""
        zero = """
void Init(u32 *dst)
{
    PoseBuf frame;
    Mem_Zero(&frame, sizeof(frame));
    Anim_ReadFrame(3u, (Frame *)&frame);
    Shc_BlockCopy((u32)sizeof(frame.poses), dst, frame.poses);
}
"""
        self.assertEqual(rules({"a": READ_FRAME, "b": new}, "Init"), [])
        self.assertEqual(rules({"a": READ_FRAME, "b": zero}, "Init"), [])

    def test_switch_writes(self):
        fill = """
s32 Get(Vec3 *out, s32 mode)
{
    switch (mode) {
    case 1: out->x = 1.0f; out->y = 0.0f; out->z = 0.0f; break;
    case 2: out->x = 2.0f;
    case 9:
    case 3: out->y = 2.0f; out->x = 0.0f; out->z = 0.0f; break;
    default: *out = *out; out->x = 0.0f; out->y = 0.0f; out->z = 0.0f; break;
    }
    return 1;
}
"""
        early = fill.replace("case 2: out->x = 2.0f;", "case 2: return 0;")
        use = "void Use(s32 m) { Vec3 v; Get(&v, m); Sink_Vec(v); }"
        self.assertEqual(rules({"a": fill.replace("*out = *out; ", ""), "b": use}, "Use"), [])
        self.assertEqual(rules({"a": early, "b": use}, "Use"), [("R2", "v")])
        no_default = fill.replace("default: *out = *out; out->x = 0.0f; out->y = 0.0f; out->z = 0.0f; break;", "")
        self.assertEqual(rules({"a": no_default, "b": use}, "Use"), [("R2", "v")])

    def test_guarded_member_copy(self):
        ray = """
void RayQuery(Query *q, s32 hit) { q->hit = 0u; if (hit) { q->hit = 1u; q->hit_pos.x = 1.0f; q->hit_pos.y = 1.0f;
    q->hit_pos.z = 1.0f; } }
s32 Raycast(Vec3 *out, s32 hit) { Query query; RayQuery(&query, hit); if (query.hit != 0u) { *out = query.hit_pos;
    return 1; } return 0; }
"""
        self.assertEqual(rules({"a": ray}), [])

    def test_padding(self):
        self.assertEqual(rules({"a": "void F(void) { Padded p; p.tag = 1; p.value = 2u; Sink_Padded(p); }"}), [])
        self.assertEqual(rules({"a": "void F(void) { Padded p; p.tag = 1; Sink_Padded(p); }"}), [("R2", "p")])


if __name__ == "__main__":
    unittest.main()
