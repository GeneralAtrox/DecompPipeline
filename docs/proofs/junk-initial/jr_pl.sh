#!/usr/bin/env bash
# jr_pl.sh SIDE KEY: the junk round's planted wrong reads (land/junk/out/planted_KEY.json) through mutation_check --report on the old (kit) or new (kitjr) judge
# Nearest existing: l/jr_ab.sh (generated mutants of a function list) - this judges the hand-planted ones of the junk round
SIDE=$1; KEY=$2
L=$DPWORK/land/tcdone/l
if [ "$SIDE" = new ]; then K="$DPWORK/land/tcdone/kitjr"; else K="$DPWORK/land/tcdone/kit"; fi
case $KEY in tc) W=$DPWORK/recheck/ws/tc/decomp;; ps2) W=$DPWORK/recheck/ws/ps2/decomp/main;; spawn-main) W=$DPWORK/recheck/ws/spawn-main/decomp/main;; esac
export CHG="jrpl_${SIDE}_${KEY}${V}"
exec $L/j.sh 3300 "junk-compare rule A/B on the junk round's planted wrong reads, $KEY, side $SIDE: lost kills must be 0" py -3.12 -B $K/tools/mutation_check.py --project $W --planted $DPWORK/land/junk/out/planted_$KEY.json --report --workers 6 --time-cap 3000 --json $DPWORK/land/tcdone/l/jrpl_${SIDE}_${KEY}.json > $L/jrpl_${SIDE}_${KEY}.log 2>&1
