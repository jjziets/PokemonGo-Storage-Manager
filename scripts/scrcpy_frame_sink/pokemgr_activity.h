/* TRACEWEAVER: file-role=macos-stream-activity-api; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
#ifndef SC_POKEMGR_ACTIVITY_H
#define SC_POKEMGR_ACTIVITY_H

/* The returned activity is retained until sc_pk_activity_end(). It prevents
 * App Nap for active frame export while allowing idle system/display sleep. */
void *sc_pk_activity_begin(void);
void sc_pk_activity_end(void *activity);

#ifdef SC_PK_ACTIVITY_TEST
unsigned sc_pk_activity_test_active_count(void);
#endif

#endif
