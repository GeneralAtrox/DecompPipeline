#!/usr/bin/env bash
# jr_drv.sh LIST...: the old/new A/B (jr_ab.sh) for each list in turn
# Nearest existing: l/jr_ab.sh - this only loops it
cd $DPWORK/land/tcdone/l
for L in "$@"; do for S in old new; do V=${V} bash jr_ab.sh $S $L 3000; done; done
echo DRIVER-DONE
