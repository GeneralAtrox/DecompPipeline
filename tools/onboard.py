"""AutoOnboard's old entry point, kept so running jobs, handovers, Start Game.bat files and remote runs that call
`py -3.12 tools/onboard.py ARGS` go on working: it runs tools/AutoOnboard/autoonboard.py with the same arguments and exit code.

Nearest existing: tools/AutoOnboard/autoonboard.py - this is only its forwarder; the tool itself moved there.
"""
import pathlib
import runpy
import sys

TARGET = pathlib.Path(__file__).resolve().parent / "AutoOnboard" / "autoonboard.py"
if __name__ == "__main__":
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")  # its sys.exit(main()) carries the exit code out
