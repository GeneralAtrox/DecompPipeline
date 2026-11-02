#!/usr/bin/env bash
# jr_six.sh SIDE: the junk round's six caught wrong reads (generated mutants m1/m6 of PS2 8c171288, m1/m9 of Spawn 8c04e620, PS2 8c03eee4) on the old/new judge
# Nearest existing: l/jr_ab.sh (same tool on a TC list) - this is for the PS2 and Spawn workspaces
V=${V}_j; SIDE=$1; L=$DPWORK/land/tcdone/l
if [ "$SIDE" = new ]; then K="$DPWORK/land/tcdone/kitjr"; else K="$DPWORK/land/tcdone/kit"; fi
for P in "ps2 $DPWORK/recheck/ws/ps2/decomp/main 8c171288 8c03eee4" "spawn $DPWORK/recheck/ws/spawn-main/decomp/main 8c04e620"; do
  set -- $P; N=$1; W=$2; shift 2
  CHG="jrsix_${SIDE}_${N}${V}" $L/j.sh 1500 "junk-compare rule: the six known wrong reads, $N, side $SIDE" py -3.12 -B $K/tools/mutation_check.py --project $W "$@" --report --workers 4 --time-cap 1200 --json $DPWORK/land/tcdone/l/jrsix_${SIDE}_${N}.json > $L/jrsix_${SIDE}_${N}.log 2>&1
done
