/* The boundary of the real clock the next tick is due at (pace.c's pace_next_tick). The caller keeps
 * *due between blanks. Each blank is due one tick after the last one's due time, so a late one is repaid by not
 * waiting; further behind than CATCH_UP_TICKS (a load, a stall) the lost time is dropped, so the game never races.
 * catch_up 0 (--catch-up off): the boundary after now. */
#ifndef PACE_H
#define PACE_H
#define CATCH_UP_TICKS 6u
static unsigned long long pace_next(unsigned long long *due, unsigned long long now, int catch_up)
{
    if (!catch_up || now >= *due + CATCH_UP_TICKS) *due = now;
    return ++*due;
}
#endif
