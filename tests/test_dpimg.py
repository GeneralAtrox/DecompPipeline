"""dpimg (port/tools/dpimg.py, docs/IMGTOOL.md) without Pillow: mode "1" packing always, and where the library is
built, a PNG round trip, crop, compare and a Dreamcast texture. Pillow equality is proven by
docs/proofs/2026-11-07/dpimg_vs_pillow.py, not here."""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"))
import dpimg  # noqa: E402


def need_lib():
    if not os.environ.get("DPIMG_DLL") and not (dpimg.OUT / "dpimg.dll").exists():
        pytest.skip("dpimg is not built on this machine (py -3.12 port/tools/dpimg.py build)")
    dpimg.lib()


def test_mode1_packs_as_pillow_does():
    grey = bytes([0, 255, 255, 0, 0, 0, 0, 0, 255, 7] * 3)  # 10 wide: each row pads to two bytes
    packed = dpimg._pack1(grey, 10, 3)
    assert len(packed) == 6 and packed[:2] == bytes([0b01100000, 0b11000000])
    assert dpimg._unpack1(packed, 10, 3) == bytes(255 if v else 0 for v in grey)


def test_png_round_trip_crop_compare(tmp_path):
    need_lib()
    im = dpimg.new("RGBA", (5, 4), (10, 20, 30, 255))
    im.paste((200, 0, 0, 255), (1, 1, 3, 3))
    im.save(tmp_path / "a.png")
    back = dpimg.open(tmp_path / "a.png")
    assert back.tobytes() == im.tobytes()
    assert back.crop((1, 1, 3, 3)).tobytes() == bytes([200, 0, 0, 255]) * 4
    other = back.copy()
    other.paste((201, 0, 0, 255), (1, 1, 2, 2))  # one red pixel, one level brighter
    assert dpimg.compare(back, other)["differing"] == 1
    assert dpimg.compare(back, other, 1)["differing"] == 0


def test_pvr_565_non_twiddled():
    need_lib()
    data = (0xF800).to_bytes(2, "little") * 64  # pure red, 8x8
    px = dpimg.decode_pvr(1 << 27 | 1 << 26, (8, 8), data).getpixel((3, 5))
    assert px[0] >= 248 and px[1:] == (0, 0, 255)
