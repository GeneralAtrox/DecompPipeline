"""The hand-over check's noise cuts, on made-up records (core/handover.py), each with a real fault planted beside it that the
check must still report: a call to a function that never returns leaves nothing live after it (a returning callee's ret-set
stays); a cycle with no return in it never returns while a pointer call does; a tail call to code that returns returns (a read
after it stays a ret-set); a kernel import that never returns (RtlRaiseException under _CxxThrowException) is a seed; a blr to
an LR loaded from outside the frame (longjmp) is no return; and the Xbox 360 exporter takes a register stored as it stands into
the function's own frame as a save, while a non-volatile stored anywhere else (setjmp's jmp_buf) stays a read."""
import tempfile
import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
X360 = REPO / "targets" / "xbox360_ppc"
sys.path[:0] = [str(REPO), str(X360 / "translator"), str(X360 / "port" / "tools")]
from core import handover as H  # noqa: E402
import handover as XH  # noqa: E402

ABI = H.Abi(lambda n: re.fullmatch(r"[rf]\d+", n) is not None, XH.channel, {"r0", "r4", "r5", "r11"}, set(), {"r3", "f1"})
FATAL, LOOP_A, LOOP_B, CALLER, PTR, OK, TAIL = 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700
TAILER, THROW, THROWER, SAVER, ONESELF, BUF = 0xa00, 0xb00, 0xc00, 0xd00, 0xe00, 0xf00
RAISE = 0x8312e7fc      # an import thunk: not in the records


def rec(blocks, succ, calls):
    return json.dumps({"b": blocks, "s": succ, "c": calls})


LINES = {
    FATAL: rec([[["a", "r4", []]], [["a", "r4", []]]], [[1], [1]], []),                                  # writes r4, loops for ever
    LOOP_A: rec([[["c", 0, []]]], [[]], [{"at": LOOP_A, "to": LOOP_B, "tail": True}]),                    # calls B ...
    LOOP_B: rec([[["c", 0, []]]], [[]], [{"at": LOOP_B, "to": LOOP_A, "tail": True}]),                    # ... which calls A
    PTR: rec([[["c", 0, ["r3"]], ["r", ["r3"]]]], [[]], [{"at": PTR, "to": None, "tail": False}]),         # a pointer call, then returns
    OK: rec([[["a", "r4", []], ["r", ["r3"]]]], [[]], []),                                                # writes r4 and returns
    # one path calls FATAL then reads r4 (code that is never reached); the other calls OK and reads r4 (a real ret-set)
    CALLER: rec([[], [["c", 0, []], ["a", "r3", ["r4"]]], [["c", 1, []], ["a", "r3", ["r4"]], ["r", ["r3"]]]], [[1, 2], [], []],
                [{"at": CALLER + 4, "to": FATAL, "tail": False}, {"at": CALLER + 8, "to": OK, "tail": False}]),
    TAIL: rec([[["c", 0, []]]], [[]], [{"at": TAIL, "to": OK, "tail": True}]),           # b OK: returns as OK does
    # bl TAIL, then reads r4: OK (TAIL's tail call) wrote it, a real ret-set on TAIL
    TAILER: rec([[["c", 0, []], ["a", "r3", ["r4"]], ["r", ["r3"]]]], [[]], [{"at": TAILER, "to": TAIL, "tail": False}]),
}
SEEDED = {
    **LINES,
    THROW: rec([[["c", 0, []], ["r", ["r3"]]]], [[]], [{"at": THROW, "to": RAISE, "tail": False}]),   # bl RtlRaiseException; blr
    # one path throws, then reads r4 (never reached); the other calls OK and reads r4 (a real ret-set)
    THROWER: rec([[], [["c", 0, []], ["a", "r3", ["r4"]]], [["c", 1, []], ["a", "r3", ["r4"]], ["r", ["r3"]]]], [[1, 2], [], []],
                 [{"at": THROWER + 4, "to": THROW, "tail": False}, {"at": THROWER + 8, "to": OK, "tail": False}]),
}


def ld(base):
    from xenontranslate.expr import Call, Var
    return Call("rd32", [Var(base, "u32")], "u32", load=True)


def lifted(*stmts):
    from xenontranslate.translate import Block, Ret
    b = Block("e", 0)
    b.stmts = list(stmts)
    b.term = Ret(None)
    return XH.record([b])


class Ev(H.Evidence):
    def __init__(self):
        pass

    def stores(self, caller, callee):
        return ()

    def loads(self, fn):
        return set()

    def vstores(self, fn):
        return set()

    def nv(self, fn):
        return False

    def keeps(self, fn):
        return set()

    def kind(self, fn):
        return "r"

    def pointed(self):
        return set()


class NoiseCuts(unittest.TestCase):
    def test_never_return(self):
        img = H.Image(LINES, ABI)
        self.assertEqual(img.noret, {FATAL, LOOP_A, LOOP_B})
        rows = H.check(img, Ev())[0]
        ret = [(r["kind"], r["callee"], r["reg"]) for r in rows if r["caller"] == CALLER and r["kind"].startswith("ret")]
        self.assertEqual(ret, [("ret-set", OK, "r4")])

    def test_tail_call_to_returning_code_keeps_its_reads(self):
        """The cut "a tail call to code that returns returns" with a real fault beside it: a read after a call to TAIL."""
        img = H.Image(LINES, ABI)
        self.assertNotIn(TAIL, img.noret)
        rows = H.check(img, Ev())[0]
        self.assertIn(("ret-set", TAIL, "r4"), [(r["kind"], r["callee"], r["reg"]) for r in rows if r["caller"] == TAILER])

    def test_raise_import_never_returns(self):
        """82a4fcc0 -> 82a6eef0 -> RtlRaiseException: a seed import makes the function that calls it on every path never
        return; the real ret-set on the path through a returning callee stays."""
        img = H.Image(SEEDED, ABI, {RAISE})
        self.assertEqual(img.noret, {FATAL, LOOP_A, LOOP_B, THROW, RAISE})
        rows = H.check(img, Ev())[0]
        ret = [(r["kind"], r["callee"], r["reg"]) for r in rows if r["caller"] == THROWER and r["kind"].startswith("ret")]
        self.assertEqual(ret, [("ret-set", OK, "r4")])

    def test_noreturn_thunks_from_the_import_table(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="x360_noret_"))
        try:
            (d / "image.json").write_text(json.dumps({"imports": [
                {"library": "xboxkrnl.exe", "ordinal": 310, "thunk": 0x8312e7fc},     # RtlRaiseException
                {"library": "xboxkrnl.exe", "ordinal": 327, "thunk": 0x8312ec5c},     # RtlUnwind: comes back to the CRT's unwinder
                {"library": "xboxkrnl.exe", "ordinal": 77, "thunk": 0x8312e89c},      # KeAcquireSpinLockAtRaisedIrql
                {"library": "xam.xex", "ordinal": 425, "thunk": 0x8312e50c},          # XamLoaderTerminateTitle
                {"library": "xboxkrnl.exe", "ordinal": 82, "kind": "variable", "slot": 4}]}), encoding="utf-8")
            self.assertEqual(XH.noreturn_thunks(d), {0x8312e7fc, 0x8312e50c})
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_blr_to_an_lr_loaded_outside_the_frame_is_no_return(self):
        """longjmp 82a566a0: lwz r5,0x134(r7); mtlr r5; blr goes to setjmp's return point. An epilogue's lwz r12,-8(r1);
        mtlr r12; blr, and mtlr of the entry LR, return."""
        from xenontranslate.expr import Var
        from xenontranslate.ir import Assign
        kinds = lambda r: [st[0] for st in r["b"][0]]
        self.assertNotIn("r", kinds(lifted(Assign("r5", ld("r7")), Assign("lr", Var("r5", "u64")), Assign("r3", Var("r6", "u64")))))
        self.assertNotIn("r", kinds(lifted(Assign("lr", ld("r7")))))
        self.assertIn("r", kinds(lifted(Assign("r12", ld("r1")), Assign("lr", Var("r12", "u64")))))
        self.assertIn("r", kinds(lifted(Assign("r12", Var("lr", "u64")), Assign("lr", Var("r12", "u64")))))
        img = H.Image({OK: rec(*[lifted(Assign("r5", ld("r7")), Assign("lr", Var("r5", "u64")))[k] for k in ("b", "s", "c")])}, ABI)
        self.assertEqual(img.noret, {OK})

    def test_store_into_the_frame_is_a_save_elsewhere_a_read(self):
        from xenontranslate import expr as X
        from xenontranslate.expr import Const, Var
        from xenontranslate.ir import Assign, Store
        reads = lambda r: set().union(*(set(st[1]) for st in r["b"][0] if st[0] in ("u", "r")))
        got = reads(lifted(Store("wr64", Var("r1", "u32"), Var("r14", "u64")),                    # __savegprlr: std r14,-0x98(r1)
                           Assign("r10", X.add(Var("r1", "u64"), Const(0x88, "u64"))),            # addi r10,r1,0x88
                           Store("wr64", Var("r10", "u32"), Var("r15", "u64")),                   # std r15,0(r10): own frame
                           Store("wr32", Var("r3", "u32"), Var("r16", "u64")),                    # setjmp: stw r16,..(r3)
                           Store("wr32", Var("r3", "u32"), Var("r5", "u64"))))
        self.assertEqual(got & {"r14", "r15", "r16", "r5"}, {"r16", "r5"})

    def test_setjmp_declared_is_a_save_another_buffer_store_still_a_read(self):
        """jumps.json's setjmp: its stores of r14..r31 / f14..f31 into the caller's jmp_buf are saves (the port runs a host setjmp
        in its place), so SAVER's call of it reports nothing. The planted fault beside it: the same stores in a function the table
        does not name (BUF) stay a read of the caller's r14, and a declared setjmp's store of a volatile register stays a read."""
        from xenontranslate.expr import Var
        from xenontranslate.ir import Store
        from xenontranslate.translate import Block, Ret
        caller = lambda to: rec([[["a", "r14", []], ["c", 0, []], ["r", ["r3"]]]], [[]], [{"at": SAVER, "to": to, "tail": False}])

        def body(saves, reg):
            b = Block("e", 0)
            b.stmts, b.term = [Store("wr64", Var("r3", "u32"), Var(reg, "u64"))], Ret(None)
            return json.dumps(XH.record([b], saves))
        for saves, reg, want in ((True, "r14", False), (False, "r14", True), (True, "r5", True)):
            img = H.Image({SAVER: caller(BUF), BUF: body(saves, reg)}, ABI)
            rows = {(r["kind"], r["caller"], r["callee"], r["reg"]) for r in H.check(img, Ev())[0]}
            self.assertEqual(("fwd-unstored", SAVER, BUF, "r14") in rows, want and reg == "r14", (saves, reg))
            if reg == "r5":
                self.assertIn("r5", set().union(*(set(st[1]) for st in json.loads(body(saves, reg))["b"][0] if st[0] == "u")))

    def test_planted_nonvolatile_handed_through_memory(self):
        """A real fault the narrowed save cut must report: SAVER sets r14 and calls BUF, whose C stores the caller's r14 into
        the buffer r3 points at (setjmp's way); SAVER's C stores nothing. A store into BUF's own frame would be no read."""
        from xenontranslate.expr import Var
        from xenontranslate.ir import Store
        caller = lambda to: rec([[["a", "r14", []], ["c", 0, []], ["r", ["r3"]]]], [[]], [{"at": SAVER, "to": to, "tail": False}])
        into_buf = lifted(Store("wr64", Var("r3", "u32"), Var("r14", "u64")))
        into_frame = lifted(Store("wr64", Var("r1", "u32"), Var("r14", "u64")))
        for callee, want in ((into_buf, True), (into_frame, False)):
            img = H.Image({SAVER: caller(BUF), BUF: json.dumps(callee)}, ABI)
            rows = {(r["kind"], r["caller"], r["callee"], r["reg"]) for r in H.check(img, Ev())[0]}
            self.assertEqual(("fwd-unstored", SAVER, BUF, "r14") in rows, want)


if __name__ == "__main__":
    unittest.main()
