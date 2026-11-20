"""xb_join: catalogue functions cut inside a loop are joined for the port; ones that only sit side by side are not; and a leaf runs on past
a blr its earlier branch jumps over, which gen_xbox.holes now follows (no entry at Too Human's 82dea9ac inside 82dea980)."""
import pathlib
import struct
import sys

X360 = pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc"
sys.path.insert(0, str(X360 / "port" / "tools"))
import xb_join  # noqa: E402
import xb_leaf  # noqa: E402

BASE = 0x82000000
BLR, NOP = 0x4E800020, 0x60000000


def b(at, to, link=False):
    return 0x48000000 | ((to - at) & 0x03FFFFFC) | int(link)


def bne(at, to):
    return 0x40820000 | ((to - at) & 0xFFFC)


def image(words):
    return b"".join(struct.pack(">I", w) for w in words)


def test_cut_loop_is_joined():
    # F 0x00..0x10 falls into G 0x10..0x20, whose bne goes back into F (82cb0098 / 82cb0260)
    w = [NOP, NOP, NOP, NOP, NOP, NOP, bne(BASE + 0x18, BASE + 0x08), BLR]
    got = xb_join.join({BASE: [(BASE, BASE + 0x10)], BASE + 0x10: [(BASE + 0x10, BASE + 0x20)]}, image(w), BASE)
    assert got == {BASE: (BASE, BASE + 0x20)}


def test_back_branch_alone_joins_only_an_uncalled_piece():
    # F ends in blr; G branches back into F. Joined unless something calls G with bl
    w = [NOP, NOP, NOP, BLR, NOP, NOP, bne(BASE + 0x18, BASE + 0x08), BLR, b(BASE + 0x20, BASE + 0x10, link=True), BLR]
    bodies = {BASE: [(BASE, BASE + 0x10)], BASE + 0x10: [(BASE + 0x10, BASE + 0x20)]}
    assert xb_join.join(bodies, image(w[:8]), BASE) == {BASE: (BASE, BASE + 0x20)}
    bodies[BASE + 0x20] = [(BASE + 0x20, BASE + 0x28)]
    assert xb_join.join(bodies, image(w), BASE) == {}


def test_neighbours_are_not_joined():
    # F ends in blr, G ends in a tail branch to F's entry (a call, not a cut), and a bl that does not return is not a fall
    w = [NOP, BLR, NOP, b(BASE + 0x0c, BASE), NOP, b(BASE + 0x14, BASE, link=True), NOP, BLR]
    bodies = {BASE: [(BASE, BASE + 0x08)], BASE + 0x08: [(BASE + 0x08, BASE + 0x10)], BASE + 0x10: [(BASE + 0x10, BASE + 0x18)],
              BASE + 0x18: [(BASE + 0x18, BASE + 0x20)]}
    assert xb_join.join(bodies, image(w), BASE) == {}


def test_leaf_runs_past_a_blr_it_jumps_over():
    # 82dea980: a forward bne past the first blr; the leaf is one body to the last blr, so no start at the second piece
    w = [bne(BASE, BASE + 0x0c), NOP, BLR, NOP, BLR]
    assert xb_leaf.body(image(w), BASE, BASE) == [(BASE, BASE + 0x14)]
