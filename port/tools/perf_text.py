"""The readable report of port/tools/perf.py: one Markdown page from the numbers perf_report.py worked out.

Nearest existing: none writes a performance report; tools/scorecard.py prints gate rows, not a page per run.
"""


def _ms(v):
    return f"{v:.2f}"


def _table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _band(a):
    """The frames just over the bar, first: they decide a p99 that sits near the bar."""
    b = a["band"]
    if not b["frames"]:
        return []
    L = [f"## The frames just over the bar ({_ms(b['from_ms'])} to {_ms(b['to_ms'])} ms)", "",
         f"{b['frames']} frames ({b['of_over_frames']:.0%} of all frames over the bar) were made in {_ms(b['from_ms'])} to {_ms(b['to_ms'])} ms; "
         f"{b['ticks']} such ticks were sampled. " + ("**The p99 frame is in this band: what is extra here is what fails the bar.**"
                                                     if b["p99_in_band"] else "The p99 frame is not in this band."), ""]
    if b["functions_extra"]:
        L += ["What runs longer in these frames than in frames within the bar (ms per tick):", ""]
        L += _table(["Function", "What", "Thread", "Within the bar", "In the band", "Extra", "Samples", "Which rule applies"],
                    [[f"`{n[:60]}`", tier, th, _ms(norm), _ms(band), f"{extra:+.2f}", s, fix] for n, tier, th, norm, band, extra, s, fix in b["functions_extra"]])
        extra = sum(r[5] for r in b["functions_extra"])
        L += ["", f"These add up to {_ms(extra)} ms a tick. " + ("Fewer than 80 samples lie behind a row marked low: more profile runs make it firmer." if b["ticks"] < 200 else "")]
    else:
        L.append("No function ran measurably longer in these frames: the extra time is spread thin, or is waiting (see the tables below).")
    return L + [""]


def _once(b):
    """One-off work in the measured window (shader compiles, first-use loads, file opens the kit logs)."""
    L, fault = [], []
    for side, s in b["sides"].items():
        if not s.get("once"):
            continue
        what = ", ".join(f"{v['count']:.0f} x {k} ({v['ms']:.0f} ms)" for k, v in s["once"].items())
        line = f"- Build {side}, per run: {what}; {s['once_frames']:.0f} frames carried it"
        if s.get("once_unplaced"):
            line += f" ({s['once_unplaced']:.0f} were logged without a vertical blank, so their frames are not marked)"
        w = s.get("without_once")
        if w:
            line += (f". With those frames: p99 {_ms(s['p99']['mean'])} ms, worst {_ms(s['worst']['max'])} ms, late {s['late_per_1000']['mean']:.2f} per 1,000. "
                     f"Without them: median {_ms(w['median'])} ms, p99 {_ms(w['p99'])} ms, worst {_ms(w['worst'])} ms, late {w['late_per_1000']:.2f} per 1,000")
        L.append(line + ".")
        fault += [f"build {side} compiled {v['count']:.0f} shaders during play ({v['ms']:.0f} ms)" for k, v in s["once"].items() if "shader" in k]
    return L, fault


def _bench(rep, b, meta):
    L = ["## 1. Benchmark", ""]
    rows = []
    for side, s in b["sides"].items():
        rows.append([f"{side}: {meta['builds'].get(side, '')}", s["runs"], _ms(s["median"]["mean"]),
                     f"{_ms(s['p99']['mean'])} ({_ms(s['p99']['min'])} to {_ms(s['p99']['max'])})", _ms(s["worst"]["max"]),
                     f"{s['late_per_1000']['mean']:.2f}", _ms(s["p99"]["spread"])])
    L += _table(["Build", "Runs", "Make median ms", "Make p99 ms (range)", "Worst ms", "Late per 1,000", "p99 run-to-run spread ms"], rows)
    L += ["", "Run order " + " ".join(r["side"] for r in b["runs"]) + "; processor load before each run "
          + ", ".join(f"{r.get('cpu', 0):.0f}%" for r in b["runs"]) + "."]
    for key, d in b.get("difference", {}).items():
        L.append(f"- {key}: B minus A = {d['b_minus_a_ms']:+.2f} ms against a run-to-run spread of {_ms(d['noise_ms'])} ms: "
                 + ("**a real difference**" if d["real"] else "**inside the noise, not a difference**") + ".")
    if len(b["sides"]) == 1:
        L.append("- One build only: the spread above is this machine's noise for this replay. A change smaller than it cannot be told from nothing.")
    once, _ = _once(b)
    if once:
        L += ["", "One-off work inside the measured window (it belongs before play; the frames that carried it are in the table above):", ""] + once
    r = rep.get("resolution")
    if r:
        L += ["", f"Output size check: at {r['small_size']} make median {_ms(r['small']['median'])} ms and p99 {_ms(r['small']['p99'])} ms; "
              f"at {r['full_size']} median {_ms(r['full']['median'])} ms and p99 {_ms(r['full']['p99'])} ms. {r['verdict']}"]
    g = rep.get("gpu")
    if g:
        L += ["", f"Graphics card time per vertical blank, from a separate pass with timer queries (the pass itself is slower, so only this column is read from it): "
              f"median {_ms(g['median_ms'])} ms, p99 {_ms(g['p99_ms'])} ms, worst {_ms(g['worst_ms'])} ms over {g['blanks']} blanks."]
    return L + [""]


def _pair(rows, nkey):
    """rows from perf_report's table(): key columns, then normal, band, far, over."""
    return [[*r[:nkey], _ms(r[nkey]), _ms(r[nkey + 1]), _ms(r[nkey + 2]), f"{r[nkey + 3] - r[nkey]:+.2f}"] for r in rows]


def render(rep):
    a, b, meta = rep.get("analysis"), rep.get("bench"), rep["meta"]
    L = [f"# Performance report: {meta['name']}", "",
         f"Machine {meta['machine']}, {meta['when']}, output {meta['size']}, replay `{meta['replay']}`"
         f" to vertical blank {meta['stop_at']}" + (f", timed from {meta['from']}" if meta.get("from") else "") + "."]
    ms = meta.get("machine_state") or {}
    if ms.get("processor"):
        clock = ms.get("run_mhz") or (ms.get("mhz") or [None])[:1]
        L.append(f"{ms['processor']}; power plan {ms.get('plan') or 'not known'}; "
                 + {True: "on mains", False: "**on battery**", None: "power source not known"}[ms.get("mains")]
                 + (f"; clock at the end of the runs {min(clock)}-{max(clock)} MHz" if clock and clock[0] else "")
                 + (f" of {ms['mhz'][1]} MHz" if ms.get("mhz") else "") + ". Compare runs only where these match.")
    if meta.get("scenario"):
        L.append(f"Scenario: **{meta['scenario']}**" + (f" ({meta['scenario_shows']})" if meta.get("scenario_shows") else "") + ".")
    L.append(f"Bar: 99% of frames made within {rep['bar_ms']:.2f} ms and at most 1 late frame in 1,000.")
    if meta["machine"].upper() != meta.get("verdict_machine", "").upper():
        L.append(f"**These numbers rank costs and compare builds. The verdict comes only from {meta.get('verdict_machine') or 'the target laptop'}.**")
    if meta.get("plant_us"):
        L.append(f"**Test run: a planted slow function of {meta['plant_us']} us a tick is in this build's runs.**")
    if meta.get("plant_sys_us"):
        L.append(f"**Test run: a planted spin inside system calls of {meta['plant_sys_us']} us a tick is in this build's runs.**")
    fault = _once(b)[1] if b else []
    if fault:
        L.append("**Fault of the build, not noise: " + "; ".join(fault) + ". Shaders are compiled before the first frame; "
                 "until they are, read the numbers \"without them\" in section 1 as the build's steady cost.**")
    L.append("")
    if a:
        L += _band(a)
    if b:
        L += _bench(rep, b, meta)
    if not a:
        return "\n".join(L) + "\n"
    t = a["ticks"]
    used = "frames over the bar" if a["class_used"] == "over" else "all frames (none was over the bar)"
    L += ["## 2. Where the time goes", "",
          f"Profile runs: make median {_ms(a['make_ms']['median'])} ms, p99 {_ms(a['make_ms']['p99'])} ms, worst {_ms(a['make_ms']['worst'])} ms. "
          f"Sampled ticks: {t['normal']} within the bar, {t['band']} just over it, {t['far']} far over; one sample stands for about {a['sample_ms']:.2f} ms.",
          f"To pass, the p99 frame has to lose **{_ms(a['need']['p99_cut_ms'])} ms**; the frames over the bar are over by {_ms(a['need']['mean_over_cut_ms'])} ms on average.", ""]
    if rep.get("overhead"):
        o = rep["overhead"]
        L += [f"Cost of measuring: with the sampler on, make median {o['median_ms']:+.2f} ms and p99 {o['p99_ms']:+.2f} ms against the benchmark runs "
              f"(their spread: {_ms(o['spread_ms'])} ms); the threads were held {o['held_ms']} ms in all, {o['held_share']:.2%} of the run.", ""]
    head = ["Within the bar", "Just over", "Far over", "Extra in frames over"]
    L += ["### Processor, graphics or waiting (ms per tick, both threads)", ""] + _table(["Bound by", *head], _pair(a["bound"], 1))
    L += ["", "Graphics here is time the threads spend inside the graphics driver; a frame held up by the card itself shows as waiting on present. The output size check in section 1 settles which.", "",
          "### By thread and layer (ms per tick)", ""] + _table(["Thread", "Layer", *head], _pair(a["layers"], 2))
    L += ["", "### By kit section, game thread (ms per tick)", ""] + _table(["Section", *head], _pair(a["sections"], 1))
    L += ["", "### What the waiting waits on (ms per tick)", "", "Time inside system calls, by the function of the port that made the call.", ""]
    L += _table(["Thread", "Waits on", "Called from", *head], [[r[0], r[1], f"`{r[2][:50]}`", *_pair([r[3:]], 0)[0]] for r in a["waits"][:14]])
    L += ["", f"### Functions, by cost in {used}", "",
          "Self: time in the function's own code. Inclusive: with everything it calls by original address (calls between readable functions are direct and show as self time of each only). Calls: exact, from a fixed-clock pass.", ""]
    rows = []
    for f in a["functions"][:25]:
        incl = f"{_ms(f['inclusive_ms_normal'])} / {_ms(f['inclusive_ms_over'])}" if "inclusive_ms_over" in f else "-"
        calls = f"{f['calls_per_tick']:.1f}" if "calls_per_tick" in f else "-"
        note = f" ({f['blocked_on'] or 'in a system call'})" if f["in_system_call"] or f["blocked_on"] else ""
        rows.append([f"`{f['name'][:70]}`" + note, f["address"] or "-", f["tier"], f["thread"],
                     f"{_ms(f['self_ms_normal'])} / {_ms(f['self_ms_over'])}", incl, calls, f"{f['samples_normal']} / {f['samples_over']}"])
    L += _table(["Function", "Original address", "What", "Thread", "Self ms: within / over", "Inclusive ms: within / over", "Calls a tick", "Samples"], rows)
    L += ["", "### The worst frames", ""]
    for w in a["worst_frames"]:
        run = ", ".join(f"{n[:50]} {s:.0%}" for n, s in w["running"]) or "no sample fell in it"
        sec = ", ".join(f"{n} {s:.0%}" for n, s in w["sections"])
        bound = ", ".join(f"{k} {v:.0%}" for k, v in sorted(w["bound"].items(), key=lambda kv: -kv[1]))
        L.append(f"- vertical blank {w['vblank']}: made in {_ms(w['make_ms'])} ms (tick {_ms(w['tick_ms'])})."
                 + (f" Bound by: {bound}." if bound else "")
                 + (f" Graphics card, timer-query pass: {_ms(w['gpu_ms'])} ms." if "gpu_ms" in w else "") + f" Running: {run}."
                 + (f" Sections: {sec}." if sec else "") + (f" Game log before it: {'; '.join(w['game_was'])}." if w["game_was"] else ""))
    L += ["", "### Where in the replay the frames over the bar are", ""]
    for g in a["over_frames"][:12]:
        L.append(f"- vertical blanks {g['from_vblank']} to {g['to_vblank']}: {g['frames']} frames over, the worst {_ms(g['worst_ms'])} ms"
                 + (f". Game log: {'; '.join(g['game_was'])}" if g["game_was"] else "") + ".")
    if not a["over_frames"]:
        L.append("- None.")
    L += ["", "## 3. What to swap, in order", "",
          f"Costs are per frame in {used}. Gain is an estimate (a range from past swaps), to be confirmed by an A/B benchmark of the swap.", ""]
    rows = []
    for s in a["swaps"]:
        share = f"{s['share_of_needed_cut']:.0%}" if s["share_of_needed_cut"] is not None else "-"
        rows.append([s["rank"], f"`{s['name'][:60]}`", s["address"] or "-", f"{s['what']}: {s['kind']}", _ms(s["cost_ms_per_frame"]),
                     f"{s['share_of_frame_work']:.0%}", share, f"{_ms(s['gain_ms'][0])} to {_ms(s['gain_ms'][1])}",
                     f"{s['confidence']} ({s['samples']} samples)", s["fix"], "yes" if s["reaches_bar_here"] else ""])
    L += _table(["#", "Function", "Original address", "What it is", "Cost ms", "Share of the frame's work", "Cost against the cut needed",
                 "Estimated gain ms", "How sure", "Which rule applies", "Bar reached by here (mid estimates)"], rows)
    L += ["", "Gain ranges: " + "; ".join(sorted({f"{s['what']}: {s['gain_basis']}" for s in a["swaps"]})) + "."]
    if rep.get("notes"):
        L += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"
