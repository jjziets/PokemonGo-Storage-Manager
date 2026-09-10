/* TRACEWEAVER: file-role=macos-stream-activity; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
#import <Foundation/Foundation.h>

#include "pokemgr_activity.h"

#ifdef SC_PK_ACTIVITY_TEST
# include <stdatomic.h>
static _Atomic(unsigned) active_count;

unsigned
sc_pk_activity_test_active_count(void) {
    return atomic_load(&active_count);
}
#endif

/* TRACEWEAVER: entrypoint=sc_pk_activity_begin; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
void *
sc_pk_activity_begin(void) {
    @autoreleasepool {
        id activity = [[NSProcessInfo processInfo]
            beginActivityWithOptions:NSActivityUserInitiatedAllowingIdleSystemSleep
            reason:@"Pokemon storage frame export"];
        /* The decoder outlives this autorelease pool. Keep the exact token,
         * without changing process defaults or requesting display wakefulness. */
        [activity retain];
#ifdef SC_PK_ACTIVITY_TEST
        if (activity) {
            atomic_fetch_add(&active_count, 1);
        }
#endif
        return (void *) activity;
    }
}

/* TRACEWEAVER: entrypoint=sc_pk_activity_end; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
void
sc_pk_activity_end(void *token) {
    if (!token) {
        return;
    }
    @autoreleasepool {
        id activity = (id) token;
        [[NSProcessInfo processInfo] endActivity:activity];
        [activity release];
#ifdef SC_PK_ACTIVITY_TEST
        atomic_fetch_sub(&active_count, 1);
#endif
    }
}
