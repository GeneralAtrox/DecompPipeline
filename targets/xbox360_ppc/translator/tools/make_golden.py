"""Write tests/golden_words.json: for every op the decoder finds in an executable's code, the first word with that op
(and its fields and text). The decoder matched a reference disassembler on every code word of Too Human
(tools/check_decoder.py) when this file was made, so it pins that agreement as a regression test.

    python tools/make_golden.py [--workspace DIR]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from xenontranslate.decode import decode, disasm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    a = ap.parse_args()
    img = open(os.path.join(a.workspace, "image.bin"), "rb").read()
    seen = {}
    for line in open(os.path.join(a.workspace, "code_ranges.txt")):
        lo, hi = (int(x, 16) for x in line.split())
        for ad in range(lo, hi, 4):
            w = int.from_bytes(img[ad - 0x82000000:ad - 0x82000000 + 4], "big")
            i = decode(w, ad)
            if i.op not in seen:
                seen[i.op] = {"addr": f"{ad:08x}", "word": f"{w:08x}", "f": i.f, "text": disasm(i)}
    out = os.path.join(os.path.dirname(__file__), "..", "tests", "golden_words.json")
    with open(out, "w") as f:
        json.dump(dict(sorted(seen.items())), f, indent=0)
    print(len(seen), "ops")


if __name__ == "__main__":
    main()
