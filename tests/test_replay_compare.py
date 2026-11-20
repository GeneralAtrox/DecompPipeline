"""port/tools/replay_compare.py reads a session recording as port/base/session.c writes it, and inputs_only() refuses one that
carries save files or trailing bytes, so a recording committed to a game's repository as a check's input holds only inputs."""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"))
import replay_compare as rc  # noqa: E402


def recording(pad=b"\x01\x02", saves=b"", extra=b"", vblanks=900):
    """The layout of session_write(): magic, header, four sections (pad, timer, rtc, saves), pad reads, vertical blanks."""
    header = b"v1 --fixed-clock 0"
    out = b"PORTREC1" + struct.pack("<I", len(header)) + header
    for sec in (pad, b"\x05", b"", saves):
        out += struct.pack("<I", len(sec)) + sec
    return out + struct.pack("<II", 7, vblanks) + extra


class InputsOnly(unittest.TestCase):
    def test_sections_and_end(self):
        data = recording()
        s = rc.sections(data)
        self.assertEqual((s["pad"], s["timer"], s["rtc"], s["saves"], s["reads"], s["vblanks"], s["extra"]),
                         (b"\x01\x02", b"\x05", b"", b"", 7, 900, b""))
        self.assertEqual(rc.pad_bytes(data), b"\x01\x02")
        self.assertEqual(rc.last_vblank(data), 900)

    def test_inputs_only_passes_a_plain_recording(self):
        self.assertEqual(rc.inputs_only(recording()), "")

    def test_planted_saves_and_trailing_bytes_are_caught(self):
        self.assertIn("5 bytes of save files", rc.inputs_only(recording(saves=b"SAVE!")))
        self.assertIn("2 bytes past the end", rc.inputs_only(recording(extra=b"xx")))
        self.assertEqual(rc.inputs_only(b"NOTAREC0" + recording()[8:]), "not a session recording")

    def test_players_two_to_eight_follow_the_counts(self):
        others = struct.pack("<I", 14) + b"	" * 14
        data = recording(extra=others, vblanks=12990)
        self.assertEqual(rc.last_vblank(data), 12990)
        self.assertEqual(rc.pad_bytes(data), b"")
        self.assertEqual((rc.sections(data)["others"], rc.sections(data)["extra"]), (b"	" * 14, b""))
        self.assertEqual(rc.inputs_only(data), "")  # other players' pads are inputs

    def test_planted_bytes_after_the_other_players_are_caught(self):
        self.assertIn("3 bytes past the end", rc.inputs_only(recording(extra=struct.pack("<I", 2) + b"		" + b"xyz")))


def recording_at(vblank, others=b""):
    header = b"v1\nframe 1 vblank 2\n"
    data = b"PORTREC1" + struct.pack("<I", len(header)) + header
    for section in (b"\x01\x02", b"", b"\x03" * 5, b""):  # pad, timer, rtc, saves (base/session.c)
        data += struct.pack("<I", len(section)) + section
    data += struct.pack("<II", 77, vblank)  # pad reads, then the last vertical blank
    if others:
        data += struct.pack("<I", len(others)) + others
    return data


def row(job, pair, differ=0, frames=(10, 10), wav=True, exits=(0, 0)):
    return dict(job=job, pair=pair, differ=differ, frames_old=frames[0], frames_new=frames[1], wav_same=wav,
                old={"exit": exits[0]}, new={"exit": exits[1]})


class FromTheToyCommanderLine(unittest.TestCase):
    """last_vblank with players 2..8 after the counts, and the --out resume rows (done_rows, differs)."""

    def test_one_player(self):
        assert rc.last_vblank(recording_at(12990)) == 12990

    def test_players_two_to_eight_follow_the_vblank(self):
        assert rc.last_vblank(recording_at(12990, b"\x09" * 14)) == 12990
        assert rc.pad_bytes(recording_at(5, b"\x09" * 3)) == b"\x01\x02"

    def test_resume_counts_only_the_same_pair(self):  # --out reused for other builds once said ALL SAME without running them
        rows = [row("frontend", "aa", differ=5), row("mission", "bb")]
        assert set(rc.done_rows(rows, "bb")) == {"mission"}
        assert rc.done_rows(rows, "cc") == {}
        assert rc.done_rows([dict(row("x", "aa"), pair=None)], "aa") == {}  # a row from before pairs: run again

    def test_a_resumed_difference_is_still_one(self):
        assert not rc.differs(row("a", "p"), 0)
        for bad in (row("a", "p", differ=1), row("a", "p", frames=(10, 12)), row("a", "p", wav=False),
                    row("a", "p", exits=(0, 3)), row("a", "p", frames=(0, 0))):
            assert rc.differs(bad, 1)
        assert not rc.differs(row("a", "p", frames=(10, 11)), 1)


if __name__ == "__main__":
    unittest.main()
