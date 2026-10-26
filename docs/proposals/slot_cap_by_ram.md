# Proposal: set the Ghidra slot cap from measured RAM

Status: **built on `waste-audit-2`** (the user's yes, 2026-10-23): `core/ghidra_broker.py` `by_ram`, on in
`config.json`. See "As built" at the end for where it differs from this proposal. Proof K in
`docs/proofs/2026-10-23/PROOFS.md`.

## What the cap is today

`config.json` `"ghidra_broker": {"slots": 2}` and `core/ghidra_broker.py` DEFAULTS:

- 2 JVMs at any RAM level under 88%;
- a 3rd while RAM is under 82% (`third_below`);
- none at 88% or more (`stop_at`).

The numbers 2, 82 and 88 were chosen by hand. Nothing measured them.

## What was measured (2026-10-23, one hour of the live queue, a sample every 2 s)

| Figure | Value |
|--------|-------|
| Samples | 1,779 over 60 min |
| Share of samples with tickets waiting | 100% |
| Share at the cap with tickets waiting | 73% |
| ... of those, with RAM under 75% | 67% |
| JVMs running (median) | 3 |
| Tickets waiting (median) | 4 |
| Peak private memory of one prepare JVM | 952 MB (3% of the machine's 31.5 GB) |
| Machine RAM in use (median / max) | 72% / 83.9% |
| Cores | 24 |

So for about half of the hour, work waited for a slot while RAM had room for 3 or more JVMs.

## The proposed rule

Admit one more JVM while **RAM now + one JVM's measured peak (3%) stays under 85%**. 85% is the point where the
user's own rule resumes paused work after a 95% stop, so no admitted JVM can push the machine into the zone where
work pauses. `stop_at` (88%) and the 95% stop stay as they are.

- A ceiling of `cores / 4` (6 here), so CPU is never oversubscribed by Ghidra alone.
- The peak per JVM is measured, not configured: the broker already lists every JVM's PID. It keeps the largest
  private memory it has seen, with 1 GB as the default until a first sample exists.
- At today's 72% median that admits about 4 JVMs instead of 2–3. That would have cleared the waiting queue in most
  samples above.

## What it would save

When waiting tickets are admitted sooner, nothing is computed twice. Only wall time is saved. In the sampled hour
the queue held a median of 4 waiters at about 9.7 s per cold prepare. One more JVM for half the hour serves about
185 more prepares (about 30 min of queued Ghidra work moved earlier). Once slot servers are on (the lease commit,
`prepare_list --serve`), a served prepare is about 1.1 s, so the cap matters less. The two changes add up.

## Why it is not landed

- It is a policy change on a shared machine. Other sessions' JVMs and the user's own apps share the RAM.
- It needs a live check: a day with the rule on, watching that RAM never passes 85% because of an admitted JVM.

## As built (2026-10-23)

`"ghidra_broker": {"slots": 2, "by_ram": true}`. At each grant the broker adds up what the machine would hold:

- RAM now;
- plus, for every Ghidra JVM running, what it may still grow: the measured peak less its memory now;
- plus a full peak for every granted token whose JVM has not started yet.

It then admits one more JVM while that total plus one more peak stays:

- under 95% (the stop line) for the first 2 (`slots`);
- under 85% (`fill_below`, the resume line) for any beyond them, up to `cores / 4` (6 here; `max_jvms` sets it).

None start at 88% or more (`stop_at`), as before. The peak is measured: the largest of the broker's JVMs
(private or working-set memory), kept in `jvm_peak.json` beside the queue, 1 GB until a first sample. Today it is
952 to 975 MB.

Where this differs from the proposal:

- Running JVMs' growth and tokens not yet started both count. So a burst of starts cannot pass the line between two
  samples, and a JVM that will grow is not counted as small.
- The first 2 are held to the 95% line with growth counted. The old rule let 2 run at any RAM under 88% whatever
  they would grow to.

The broker cannot hold back other programs: RAM driven past 95% by the user's own apps is stopped by the 95% rule,
not by the cap. The cap only never starts a JVM that its own count puts past the line.
