/* The boundary of the real clock the next tick is due at (pace.c's pace_next_tick). The caller keeps
 * *due between blanks. Each blank is due one tick after the last one's due time, so a late one is repaid by not
 * waiting; further behind than CATCH_UP_TICKS (a load, a stall) the lost time is dropped, so the game never races.
 * catch_up 0 (--catch-up off): the boundary after now.
 *
 * A dropped blank is due now (the boundary just passed), not at the boundary after now: the old rule (catch_up 2, PACE_DROP_NOW=0)
 * waited for the next boundary there, half a blank on average, 8.2 ms of every 123 ms gameplay frame in Too Human on build box A (2026-11-14),
 * while a tick 1 to 5 blanks late waited for nothing. Only a tick more than CATCH_UP_TICKS late is affected; one on time is unchanged */
#ifndef PACE_H
#define PACE_H
#define CATCH_UP_TICKS 6u
static unsigned long long pace_next(unsigned long long *due, unsigned long long now, int catch_up)
{
    if (!catch_up) *due = now;
    else if (now >= *due + CATCH_UP_TICKS) *due = catch_up == 2 || !now ? now : now - 1u;
    return ++*due;
}
#endif
