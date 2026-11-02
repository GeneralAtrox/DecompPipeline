#!/usr/bin/env bash
cd $DPWORK/land/tcdone/l
bash jr_six.sh old > six_old.log 2>&1
bash jr_six.sh new > six_new.log 2>&1
for f in old_ps2 new_ps2 old_spawn new_spawn; do echo "== $f"; grep -a "score\|ERROR" jrsix_$f.log | head -4 | cut -c1-160; done
