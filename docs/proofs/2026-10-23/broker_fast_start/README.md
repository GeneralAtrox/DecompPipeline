# broker-fast-start: two fixed costs every fresh tool process paid before its first prepare

## The fixes
1. `core/ghidra_broker.processes()` now takes every parent from one whole-machine `ppid_map()`. It no longer reads
   psutil's per-process ppid (which rebuilt that map once per process) or create_time (which no caller used). The rows
   are (pid, ppid, name); `jvms()` reads only those three fields. Measured on this machine (about 430 processes): the
   first call in a fresh process took 8.4–9.4 s and now takes 0.02 s. Later calls in the same process took 0.04–0.1 s
   and still do. Every broker decision (holds, queue, `in_use`, reaping by start time and boot time, served slots) reads
   the same pids, parents and names as before.
2. `tools/common.server_state` asks the workspace's port only when some TCP socket on this machine has that local port
   (`port_bound`). With no socket, Windows retried the refused connect for 2.04 s before the answer None, and that
   answer now comes at once. With a socket, or when psutil cannot list sockets, the call is unchanged.

Tests: `tests/test_broker_fast_start.py` checks both fixes. For the scan: the same rows as psutil's own per-process
ppid and name, under 1.5 s in a fresh process (8.4 s on the old code), the slow path when the map is missing, and
`jvms()` taking 3-field rows. For server_state: None at once without asking the port, and the old answers for a
server of this project, a server of another project, a listener that is not a server, and sockets that cannot be
listed. On the old tree, 3 of these tests fail and 3 error; on this branch all 8 pass.

## The proof: prepare_list wall time before and after, same harnesses
- Two identical private copies of Dragon Force 2 BTL (`build_ws.py`, seed 13): 12 functions picked at random among
  those whose recorded script phase was under 30 s, because the deadline-bound ones depend on wall time. Each pick
  lost its oracle stamp.
- `timing.sh`: each function was prepared cold by the old tree (836b497) into one copy and by this branch into the
  other, alternating which went first. One broker slot, each call started only when no other ticket waited and RAM was
  under 85%, a 95% RAM brake, and a hard time limit. The wall time is the whole prepare_list call (`timing.out`); the
  Ghidra phases are in `prepare_timing_{old,new}.jsonl`.

| 12 calls each | wall median | wall mean | outside Ghidra, median | inside Ghidra, median |
|---|---|---|---|---|
| old (836b497) | 20.77 s | 30.29 s | 11.38 s | 8.38 s |
| broker-fast-start | **12.27 s** | 15.94 s | **2.44 s** | 8.28 s |

The means include waits for other sessions' JVMs: two old calls took 80 s and one new call took 57 s.
- Harnesses: all 48 files (meta.json, vectors.txt, expected.txt and oracle_summary.json for each of the 12 functions)
  are byte-equal between the two copies.
- Full suite (runsuite.py, every test file, 6 jobs, deselecting `test_fsca_rm.py::PrepareHeals::test_cause_named`,
  which takes a real broker token): 1,803 passed, 0 failed, 0 skipped.
