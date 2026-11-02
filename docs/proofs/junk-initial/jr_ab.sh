#!/usr/bin/env bash
# jr_ab.sh SIDE LIST SECONDS: mutation_check --report with the junk pass on, SIDE old (kit) or new (kitjr), over l/LIST.txt
# Nearest existing: l/rd_mut_ab2.py (readable A/B) / tools/mutation_audit.py - this runs the raw judge's official mutation_check on two kits
SIDE=$1; LIST=$2; SECS=${3:-3000}
L=$DPWORK/land/tcdone/l
if [ "$SIDE" = new ]; then K="$DPWORK/land/tcdone/kitjr"; else K="$DPWORK/land/tcdone/kit"; fi
export CHG="jrab_${SIDE}_${LIST}${V}"
exec $L/j.sh $SECS "raw mutation A/B for the junk-compare initial-memory rule, side $SIDE, list $LIST: lost kills must be 0" py -3.12 -B $K/tools/mutation_check.py --project $DPWORK/recheck/ws/tc/decomp @$DPWORK/land/tcdone/l/$LIST.txt --report --workers 6 --time-cap 3000 --json $DPWORK/land/tcdone/l/jrab_${SIDE}_${LIST}.json > $L/jrab_${SIDE}_${LIST}.log 2>&1
