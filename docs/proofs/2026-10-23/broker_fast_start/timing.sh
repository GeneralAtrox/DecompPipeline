#!/bin/bash
# broker-fast-start proof: each picked function prepared cold by the old tree (dp_old, 836b497) into ws_old and by the
# fixed tree (DecompPipeline-bfs) into ws_new, alternating which goes first; one broker slot, only when no other ticket
# waits. Prints "addr tree seconds rc" per prepare_list call (wall, from launch to exit).
B="${BFS_DIR:?}"; PR="${PROOF_DIR:?}"; NEW="${NEW_TREE:?}"
i=0
for A in $(tr -d '\r' < "$B/ws_old/picked.txt"); do
  if [ $((i % 2)) = 0 ]; then order="old new"; else order="new old"; fi
  for T in $order; do
    if [ $T = old ]; then tree="$B/dp_old"; else tree="$NEW"; fi
    timeout 3700 python "$B/queue_free.py" 3600 || { echo "queue busy, stop"; exit 2; }
    t=$(date +%s.%N)
    (cd "$tree" && timeout 1300 python "$PR/ramguard.py" 1200 -- python "$PR/withpass.py" \
      python -B tools/prepare_list.py --project "$B/ws_$T" "$A" --slots 1 >> "$B/run_$T.log" 2>&1)
    rc=$?
    echo "$A $T $(python -c "import time;print(round(time.time()-$t,2))") $rc"
  done
  i=$((i + 1))
done
