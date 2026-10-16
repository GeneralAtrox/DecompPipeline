"""Stand-in for `claude -p` so the orchestrator can be tested end to end without spending tokens.

Enable with config.json:  "agent_command": ["{python}", "{root}/tools/fake_agent.py"]  ({root} = DecompPipeline/)
Runs in the sandbox (cwd = <project>/sandbox/<addr>), reads the prompt on stdin, prints stream-json events.

TC_FAKE_MODE (environment):
  reference  (default) copy <project>/reference/<addr>.c to work.c if it exists, else write a stub
  stub       write a wrong stub (exercises the FAIL / stall path)
  tamper     also try to modify a harness file in the sandbox (exercises tamper detection)
TC_FAKE_DELAY (seconds of simulated thinking), TC_FAKE_COST (reported $ per attempt),
TC_FAKE_RATELIMIT=1 (the first session per function fails with a 429, exercising the backoff).
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(os.environ["DECOMP_PROJECT"])  # the project workspace (set by decomp.py, inherited)


def emit(ev):
    print(json.dumps(ev), flush=True)


def tool(name, **inp):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})


def main():
    sys.stdin.read()
    box = pathlib.Path.cwd()
    addr = box.name
    mode = os.environ.get("TC_FAKE_MODE", "reference")
    marker = ROOT / "state" / f"fake_rl_{addr}"
    if os.environ.get("TC_FAKE_RATELIMIT") and not marker.exists():
        marker.write_text("1")
        emit({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
              "result": "API Error: 429 rate_limit_error", "total_cost_usd": 0.0, "num_turns": 0})
        sys.exit(1)
    time.sleep(float(os.environ.get("TC_FAKE_DELAY", "0")))
    meta = json.loads((ROOT / "harness" / addr / "meta.json").read_text(encoding="utf-8"))
    ref = ROOT / "reference" / f"{addr}.c"
    if mode == "reference" and ref.exists():
        code = ref.read_text(encoding="utf-8")
    else:
        ret = {"void": "", "f32": "return 0.0f;"}.get(meta["ret"], "return 0;")
        code = f'#include "tc_rt.h"\n\n{meta["signature"]}\n{{\n    {ret}\n}}\n'
    tool("Write", file_path=str(box / "work.c"))
    (box / "work.c").write_text(code, encoding="utf-8")
    if mode == "tamper":
        tool("Edit", file_path=str(box / "tc_rt.h"))
        try:
            p = box / "tc_rt.h"
            os.chmod(p, 0o666)
            p.write_text(p.read_text(encoding="utf-8") + "\n/* tampered */\n", encoding="utf-8")
        except OSError:
            pass
    tool("Bash", command="python check.py")
    r = subprocess.run([sys.executable, "check.py"], cwd=box, capture_output=True, text=True, timeout=300)
    out = r.stdout.strip()
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": out, "is_error": False}]}})
    verdict = "RESULT: PASS" if r.returncode == 0 else "RESULT: FAIL fake agent gave up"
    emit({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": float(os.environ.get("TC_FAKE_COST", "0")), "num_turns": 2,
          "result": verdict, "usage": {"input_tokens": 0, "output_tokens": 0}, "permission_denials": []})


if __name__ == "__main__":
    main()
