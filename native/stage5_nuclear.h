#ifndef SCAPI_STAGE5_NUCLEAR_H
#define SCAPI_STAGE5_NUCLEAR_H
#include "stage4_attention_clock.h"
/* Target-1.05 marker producer, not an order-target inference. 435470..4354B0
   creates spriteE7 via412BE0 and stores its actual thingy at Ghost+D0.
   The actor may be hidden: foreign legality depends on the red marker itself.
   All private identities and unqualified coordinates stay in this process. */
typedef struct ScapiNuclearTrack {
    DWORD live,unique,owner,actor_sprite,marker,marker_sprite,announced,own;
    LONG x,y;
    ScapiAttentionClock screen;
} ScapiNuclearTrack;
static ScapiNuclearTrack nuclear_tracks[1700];
static DWORD nuclear_epoch,nuclear_generation,nuclear_local,nuclear_serial;
static DWORD nuclear_clock_started,nuclear_last_tick;

static void stage5_nuclear_reset(void) {
    memset(nuclear_tracks,0,sizeof(nuclear_tracks));
    nuclear_clock_started=0;
}
static void stage5_nuclear_stale(ScapiNuclearTrack *t) {
    if(t->announced)stage4_publish(S5_EVENT_NUCLEAR_STALE,0,0,t->announced);
    memset(t,0,sizeof(*t));
}
static int stage5_nuclear_abi_valid(void) {
    static const BYTE create[]={0xb9,0xe7,0,0,0,0x0f,0xbf,0x50,0x5a,0x52,0x0f,0xbf,0x50,0x58};
    static const BYTE store[]={0x89,0x86,0xd0,0,0,0};
    static const BYTE ready[]={0xc6,0x46,0x4e,6};
    static const BYTE warning[]={0x8b,0x3d,0xc0,0x30,0x4f,0,0xb9,0x17,4,0,0,
        0xe8,0x89,0x51,7,0,0x8b,0xc8,0x8b,0xd7,0xe8,0,0x44,3,0};
    static const BYTE sprite[]={0x88,0x56,0x0a,0x66,0x89,0x7e,8};
    return readable(AT(0x43547a),sizeof(create)) && !memcmp(AT(0x43547a),create,sizeof(create)) &&
        readable(AT(0x43548f),sizeof(store)) && !memcmp(AT(0x43548f),store,sizeof(store)) &&
        readable(AT(0x4354b0),sizeof(ready)) && !memcmp(AT(0x4354b0),ready,sizeof(ready)) &&
        readable(AT(0x435237),sizeof(warning)) && !memcmp(AT(0x435237),warning,sizeof(warning)) &&
        readable(AT(0x410669),sizeof(sprite)) && !memcmp(AT(0x410669),sprite,sizeof(sprite));
}
static void stage5_nuclear_sample(void) {
    DWORD i,now=real_tick(),local=U32(0x4f30c0),mask=U32(0x65098c);
    int paused=SCAPI_State.pause && !U8(0x6b0cf8);
    int camera_current=SCAPI_State.width_tiles==U16(0x4fef3c) &&
        SCAPI_State.height_tiles==U16(0x4fef3e) &&
        SCAPI_State.camera_x==I32(0x51bce4) && SCAPI_State.camera_y==I32(0x51bce0) &&
        SCAPI_State.camera_max_x==I32(0x51bcd0) && SCAPI_State.camera_max_y==I32(0x51bcac);
    if(!SCAPI_State.events_enabled || !SCAPI_State.active || local>=12 || !SCAPI_State.tile_pointer) {
        stage5_nuclear_reset();return;
    }
    if(nuclear_epoch!=SCAPI_State.epoch || nuclear_generation!=SCAPI_State.events_generation ||
       nuclear_local!=local || (nuclear_clock_started && now-nuclear_last_tick>=0x80000000u)) {
        stage5_nuclear_reset();nuclear_epoch=SCAPI_State.epoch;
        nuclear_generation=SCAPI_State.events_generation;nuclear_local=local;
    }
    nuclear_clock_started=1;nuclear_last_tick=now;
    for(i=0;i<1700;++i) {
        BYTE *u=AT(0x5b2e68)+i*0x148u,*actor_sprite=*(BYTE**)(u+12),*marker,*sp;
        ScapiNuclearTrack *t=&nuclear_tracks[i];
        DWORD owner=u[0x4c],own=owner==local;LONG x,y;int in_view;
        if(!actor_sprite || !readable(actor_sprite,0x24) || u[0x4d]!=0x81 || u[0x4e]!=6 || owner>=12 ||
           !(marker=*(BYTE**)(u+0xd0))) {
            stage5_nuclear_stale(t);continue;
        }
        if(!readable(marker,0x10) || !(sp=*(BYTE**)(marker+12)) || !readable(sp,0x24) ||
           *(WORD*)(sp+8)!=0xe7 || sp[10]!=owner || !*(DWORD*)(sp+0x1c)) {
            /* An invalid expected marker is a capture failure, not proof of
               cancellation. Do not turn malformed data into a partial success. */
            stage5_notification_fault(SCAPI_EVENT_E_STATE);stage5_nuclear_reset();return;
        }
        x=*(short*)(sp+20);y=*(short*)(sp+22);
        if(x<0 || y<0 || (DWORD)x>=SCAPI_State.width_tiles*32u || (DWORD)y>=SCAPI_State.height_tiles*32u) {
            stage5_notification_fault(SCAPI_EVENT_E_STATE);stage5_nuclear_reset();return;
        }
        if(t->live && (t->unique!=u[0xa5] || t->owner!=owner || t->actor_sprite!=(DWORD)(uintptr_t)actor_sprite ||
           t->marker!=(DWORD)(uintptr_t)marker || t->marker_sprite!=(DWORD)(uintptr_t)sp || t->x!=x || t->y!=y))
            stage5_nuclear_stale(t);
        if(!t->live) {
            t->live=1;t->unique=u[0xa5];t->owner=owner;t->own=own;
            t->actor_sprite=(DWORD)(uintptr_t)actor_sprite;t->marker=(DWORD)(uintptr_t)marker;
            t->marker_sprite=(DWORD)(uintptr_t)sp;t->x=x;t->y=y;
        }
        in_view=camera_current && !paused && (sp[12]&mask) && !(sp[14]&0x20) && point_on_screen(x,y)==SC_OK;
        if(!own && !in_view) {
            /* A previously known foreign marker becomes non-current on loss
               of lawful screen observation, never 'cancelled' or 'detonated'. */
            stage5_nuclear_stale(t);continue;
        }
        if(!own && scapi_attention_elapsed(&t->screen,now,in_view)<3000u)continue;
        if(!t->announced) {
            if(nuclear_serial>=0x7fffffffu) {
                stage5_notification_fault(SCAPI_EVENT_E_EXHAUSTED);stage5_nuclear_reset();return;
            }
            t->announced=(++nuclear_serial<<1)|own;
            if(stage4_publish(S5_EVENT_NUCLEAR_MARKER,x,y,t->announced)!=SCAPI_EVENT_OK)return;
        }
    }
}
#endif
