#ifndef SCAPI_STAGE4_ATTENTION_CLOCK_H
#define SCAPI_STAGE4_ATTENTION_CLOCK_H
typedef struct ScapiAttentionClock {DWORD active,since;} ScapiAttentionClock;
static void scapi_attention_reset(ScapiAttentionClock *clock) {clock->active=0;clock->since=0;}
static DWORD scapi_attention_elapsed(ScapiAttentionClock *clock,DWORD now,int present) {
    if(!present){scapi_attention_reset(clock);return 0;}
    if(!clock->active){clock->active=1;clock->since=now;}
    return now-clock->since;
}
#endif
