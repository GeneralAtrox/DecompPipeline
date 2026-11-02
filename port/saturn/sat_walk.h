#ifndef SAT_WALK_H
#define SAT_WALK_H

/* start + span * i / den for i = 0, 1, 2.. without a divide per step: the same truncated values as that expression */
typedef struct {
    int at, sign, whole, part, left, den;
} Walk;

static __inline void walk_start(Walk *w, int start, int span, int den)
{
    int a = span < 0 ? -span : span;
    w->at = start;
    w->sign = span < 0 ? -1 : 1;
    w->whole = a / den;
    w->part = a % den;
    w->left = 0;
    w->den = den;
}

static __inline void walk_next(Walk *w)
{
    int up = w->whole;
    w->left += w->part;
    if (w->left >= w->den) {
        w->left -= w->den;
        up++;
    }
    w->at += w->sign * up;
}

#endif
