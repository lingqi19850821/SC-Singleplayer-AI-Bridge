#ifndef SCAPI_STAGE5_RATE_H
#define SCAPI_STAGE5_RATE_H
/* End-to-start cadence uses the high-resolution monotonic clock. GetTickCount
   is retained for the old diagnostic fields, never the new maximum-rate gate.
   334 ms is the conservative integer-millisecond representation of <= 3/s.
   The clock is shared by every player action and is not reset by re-arming. */
#define SCAPI_STAGE5_MIN_INTERVAL_MS 334u
typedef BOOL (WINAPI *Stage5CounterFn)(LARGE_INTEGER*);
static Stage5CounterFn stage5_counter=QueryPerformanceCounter;
static LARGE_INTEGER stage5_frequency,stage5_last_finish;
static int stage5_rate_started;
static int stage5_rate_valid(void) {
    return SCAPI_State.action_interval_ms>=SCAPI_STAGE5_MIN_INTERVAL_MS &&
           SCAPI_State.action_interval_ms<0x80000000u && stage5_frequency.QuadPart>0;
}
static int stage5_rate_due(void) {
    LARGE_INTEGER now;LONGLONG required;
    if(!stage5_rate_valid() || !stage5_counter(&now))return 0;
    if(!stage5_rate_started)return 1;
    required=(stage5_frequency.QuadPart*SCAPI_State.action_interval_ms+999)/1000;
    return now.QuadPart>=stage5_last_finish.QuadPart &&
           now.QuadPart-stage5_last_finish.QuadPart>=required;
}
static void stage5_rate_before(void) {
    LARGE_INTEGER now;
    if(stage5_counter(&now)) {
        SCAPI_State.action_qpc_low=now.LowPart;
        SCAPI_State.action_qpc_high=(DWORD)now.HighPart;
    }
}
static void stage5_rate_after(void) {
    if(stage5_counter(&stage5_last_finish)) {
        SCAPI_State.finished_qpc_low=stage5_last_finish.LowPart;
        SCAPI_State.finished_qpc_high=(DWORD)stage5_last_finish.HighPart;
        stage5_rate_started=1;
    }
}
#endif
