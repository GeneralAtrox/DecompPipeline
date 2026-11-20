"""gen_port.py: a game.json hardware function at an address the registry leaves unnamed, which a readable file calls
by its raw name fn_ADDR (Power Stone 2's Game_Main calls fn_8c17db30, the game's hw.c defines Irq_InitBase): the name
check lets it through and a generated fn_ADDR calls the game's definition by argument words; a registry name that differs from
game.json is still refused. Run: python -W error tests/test_gen_hw_alias.py.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "port" / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
import gen_port  # noqa: E402


def split(a):
    t, n = a.strip().rsplit(" ", 1)
    return (t + "*" * n.count("*"), n.lstrip("*"))


class Readable:  # readable.parse_proto's shape: (return type, name, [(type, name)])
    @staticmethod
    def parse_proto(p):
        head, args = p.strip().rstrip(";").rstrip(")").split("(", 1)
        ret, name = head.rsplit(" ", 1)
        params = [] if args.strip() in ("", "void") else [split(a) for a in args.split(",")]
        return ret, name, params

CFG = {"entry": {"addr": "8c02d2a8", "name": "Game_Main"}, "hardware": {"8c17db30": "Irq_InitBase",
                                                                        "8c178680": "Timer_Init"}}
PROTOS = {"8c02d2a8": "void Game_Main(void)", "8c17db30": "void fn_8c17db30(void)", "8c178680": "void Timer_Init(void)"}


class HwAlias(unittest.TestCase):
    def names(self, **over):
        n = {"8c02d2a8": "Game_Main", "8c17db30": "fn_8c17db30", "8c178680": "Timer_Init"}
        n.update(over)
        return n

    def test_raw_name_passes_and_maps(self):
        gen_port.check_names(CFG, self.names(), PROTOS)
        fw = gen_port.hw_forwarders(Readable, CFG["hardware"], self.names(), PROTOS)
        self.assertEqual(fw, [["u32 Irq_InitBase(void); /* the game's definition, called by words */", "void fn_8c17db30(void)", "{",
                               "    ((u32 (*)(void))Irq_InitBase)();", "}"]])

    def test_forwarder_passes_words_and_returns(self):
        hw = {"8c2b54fc": "ADXF_Open"}
        fw = gen_port.hw_forwarders(Readable, hw, {"8c2b54fc": "fn_8c2b54fc"}, {"8c2b54fc": "s32 fn_8c2b54fc(u32 a, u8 *b)"})
        self.assertEqual(fw[0][3], "    return (s32)(uintptr_t)((u32 (*)(u32, u32))ADXF_Open)((u32)(uintptr_t)a, "
                                   "(u32)(uintptr_t)b);")
        with self.assertRaises(SystemExit):
            gen_port.hw_forwarders(Readable, hw, {"8c2b54fc": "fn_8c2b54fc"}, {"8c2b54fc": "u64 fn_8c2b54fc(u32 a)"})

    def test_named_and_absent_need_no_alias(self):
        n = self.names(**{"8c17db30": "Irq_InitBase"})
        gen_port.check_names(CFG, n, PROTOS)
        self.assertEqual(gen_port.hw_forwarders(Readable, CFG["hardware"], n, PROTOS), [])
        n.pop("8c17db30")
        self.assertEqual(gen_port.hw_forwarders(Readable, CFG["hardware"], n, PROTOS), [])

    def test_other_registry_name_refused(self):
        with self.assertRaises(SystemExit):
            gen_port.check_names(CFG, self.names(**{"8c17db30": "Irq_Setup"}), PROTOS)


if __name__ == "__main__":
    unittest.main()
