"""Batch 33: a capped lean context (core/lean_cap).

Over the cap, context.md's "Types and constants" keeps what the function reaches most directly (the declarations'
struct, the struct one pointer away, a constant group holding a distinctive value raw.c writes) and cuts the rest (a
group holding only a small value raw.c writes, a group only a comment points at); a struct that does not fit is only
declared; the summary names the cut. Control: under the cap (or with the cap off) nothing changes.
Synthetic headers. Run: python -W error tests/test_lean_cap.py (also from test_tooling.py).
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import lean_cap, lean_select, readable_headers, readable_lean  # noqa: E402


def group(prefix, values, note):
    return f"/* {note} */\n" + "".join(f"#define {prefix}_{i:02d} 0x{v:x}u  /* {prefix.lower()} value {i} */\n"
                                       for i, v in enumerate(values)) + "\n"


HEADER = ("#ifndef T_H\n#define T_H\n\n#include \"rd_rt.h\"\n\n"
          "/* One pointer away from Big. */\n"
          "typedef struct Near { u32 a; u32 b; u32 c; u32 d; } Near;\n\n"
          "typedef struct Big {\n"
          "    u32 mode;      /* 0x00: MODE_* */\n"
          "    u32 kind;      /* 0x04: KIND_* */\n"
          "    u32 flags;     /* 0x08: FLAG_* */\n"
          "    Near *near;    /* 0x0c */\n"
          "} Big;\n\n"
          + group("MODE", [0x100 + i for i in range(30)], "Modes: raw.c writes 0x105.")
          + group("KIND", [2] + [0x200 + i for i in range(29)], "Kinds: raw.c writes only 2.")
          + group("FLAG", [0x300 + i for i in range(30)], "Flags: raw.c writes none.")
          + "#endif\n")
DECL = "void Tick(Big *b);"
EVIDENCE = {0x105, 2}


class Cap(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        (d / "types.h").write_text(HEADER, encoding="utf-8")
        self.hs = readable_headers.Headers(d).finish()

    def select(self, limit):
        with mock.patch.object(lean_cap, "budget", lambda: limit):
            return lean_select.select(self.hs, DECL, {"Tick"}, EVIDENCE)

    def names(self, items):
        return {n for it in items for n in it.names}

    def body(self, v, items):
        return readable_lean.types_body(self.hs, items, None, v)

    def test_cut_by_reach(self):
        full_v, full = self.select(0)
        self.assertTrue({"MODE_00", "KIND_00", "FLAG_00", "Near"} <= self.names(full))  # control: the cap off
        need = lean_cap.size(full_v, {it for it in full if not it.names & {"KIND_00", "FLAG_00"}})
        v, items = self.select(need + 40)
        self.assertLessEqual(len(self.body(v, items)), need + 40)
        self.assertLess(need + 40, len(self.body(full_v, full)))
        self.assertTrue({"Big", "Near", "MODE_00"} <= self.names(items), self.names(items))
        self.assertFalse({"KIND_00", "FLAG_00"} & self.names(items))
        self.assertEqual(sorted(v.cut["dropped"]), ["FLAG_*", "KIND_*"])
        text = lean_cap.summary(v.cut, 60000)
        self.assertIn("left out: FLAG_*, KIND_*", text)
        self.assertIn("index.txt", text)

    def test_struct_only_declared(self):
        full_v, full = self.select(0)
        big = lean_cap.size(full_v, {it for it in full if "Big" in it.names})
        v, items = self.select(big + 40)  # room for Big alone: Near is declared, the groups left out
        self.assertIn("typedef struct Near Near;", v.shown(self.hs.by_name["Near"]))
        self.assertIn("members not shown", v.shown(self.hs.by_name["Near"]))
        self.assertEqual(v.cut["declared"], ["Near"])
        self.assertIn("u32 d;", full_v.shown(self.hs.by_name["Near"]))  # control: uncapped, shown whole

    def test_under_cap_unchanged(self):
        full_v, full = self.select(0)
        v, items = self.select(10 ** 6)
        self.assertEqual(self.body(v, items), self.body(full_v, full))
        self.assertEqual(v.cut, {"declared": [], "dropped": []})
        self.assertEqual(lean_cap.summary(v.cut, 10 ** 6), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
