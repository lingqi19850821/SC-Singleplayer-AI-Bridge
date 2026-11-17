#ifndef SCAPI_STAGE5_BUILDING_MEMORY_H
#define SCAPI_STAGE5_BUILDING_MEMORY_H
/* Authorized enemy-building observations only. The caller must invoke seen()
   only where the existing on-screen attention gate produces a lawful scene
   item, and only for an enemy whose actual DAT flags include building bit 0.
   No game-pool reads occur in begin/end/stop. Hidden/dead objects never refresh
   an old record. This is a transient acknowledged queue, not history retention.

   One UI-thread producer and one external durable consumer. All wire fields
   are 32-bit little-endian. Initial write_seq=0, next_ack_seq=1. The consumer
   writes only SCAPI_State.building_ack, AFTER durably persisting every sequence
   below it. The producer alone validates and publishes header.next_ack_seq.
   A committed slot may be reused only after acknowledgement. Storage is never
   freed while the DLL is resident. A new epoch may reset the same allocation
   only after the old stream is disabled and completely durably acknowledged;
   capacity must match and only the lifecycle fault may be reset. Other faults
   cannot be cleared by start. Same-epoch start cannot erase pending records.
   Overflow disables capture explicitly: the rejected observation is NOT
   durable. Already committed records remain drainable. No implicit eviction.

   identity is stream-local, assigned only on legal observation. It is not an
   actionable game handle. Persist with the complete stream key and epoch.
   Pool unique+sprite distinguish observed native generations; 256 entirely
   hidden pool reuses returning both values cannot be identified from legal
   observations alone. currently_observed means this observer currently has a
   lawful scene item; it never asserts alive/dead or global game visibility.
*/
typedef struct ScapiBuildingHeader {
    DWORD magic,version,epoch,capacity;
    volatile DWORD write_seq,next_ack_seq,enabled,fault;
} ScapiBuildingHeader;
typedef struct ScapiBuildingSlot {
    volatile DWORD commit_seq;
    DWORD identity,kind,owner,visual;
    LONG x,y;
    DWORD last_seen_tick,currently_observed,reserved0;
} ScapiBuildingSlot;
typedef struct ScapiBuildingTrack {
    DWORD live,unique,sprite,identity,kind,owner,visual;
    LONG x,y;
    DWORD last_seen_tick,last_emit_tick,observed,seen;
} ScapiBuildingTrack;
enum {
    S5_BUILDING_MAGIC=0x314d4253u,S5_BUILDING_VERSION=1u,
    S5_BUILDING_E_FULL=1u,S5_BUILDING_E_ACK=2u,
    S5_BUILDING_E_SEQUENCE=3u,S5_BUILDING_E_EPOCH=4u,
    S5_BUILDING_E_IDENTITY=5u,S5_BUILDING_E_ALLOC=6u,
    S5_BUILDING_E_ARGUMENT=7u,S5_BUILDING_E_FRAME=8u
};
static ScapiBuildingHeader *building_stream;
static ScapiBuildingTrack building_tracks[1700];
static DWORD building_refresh_ms,building_identity_serial,building_frame_open;

static void stage5_buildings_fault(DWORD fault) {
    if(!SCAPI_State.building_fault ||
       (SCAPI_State.building_fault==S5_BUILDING_E_EPOCH && fault!=S5_BUILDING_E_EPOCH))
        SCAPI_State.building_fault=fault;
    SCAPI_State.building_enabled=0;
    if(building_stream) {
        if(!building_stream->fault ||
           (building_stream->fault==S5_BUILDING_E_EPOCH && fault!=S5_BUILDING_E_EPOCH))
            building_stream->fault=fault;
        InterlockedExchange((volatile LONG*)&building_stream->enabled,0);
    }
    building_frame_open=0;
}
static ScapiBuildingSlot *stage5_buildings_slot(DWORD seq) {
    return (ScapiBuildingSlot*)((BYTE*)building_stream+sizeof(*building_stream))+
        ((seq-1u)%building_stream->capacity);
}
static int stage5_buildings_sync(void) {
    DWORD ack,i;
    if(!building_stream)return 0;
    if(SCAPI_State.epoch!=building_stream->epoch || !SCAPI_State.active)
        stage5_buildings_fault(S5_BUILDING_E_EPOCH);
    ack=SCAPI_State.building_ack;
    if(ack<building_stream->next_ack_seq || ack>building_stream->write_seq+1u) {
        stage5_buildings_fault(S5_BUILDING_E_ACK);return 0;
    }
    for(i=building_stream->next_ack_seq;i<ack;++i) {
        if(stage5_buildings_slot(i)->commit_seq!=i) {
            stage5_buildings_fault(S5_BUILDING_E_ACK);return 0;
        }
    }
    InterlockedExchange((volatile LONG*)&building_stream->next_ack_seq,(LONG)ack);
    return SCAPI_State.building_enabled && building_stream->enabled &&
        !SCAPI_State.building_fault && !building_stream->fault;
}
static int stage5_buildings_emit(ScapiBuildingTrack *t,DWORD observed,DWORD now) {
    DWORD seq;ScapiBuildingSlot *s;
    if(!stage5_buildings_sync())return 0;
    if(building_stream->write_seq>=0xfffffffeu) {
        stage5_buildings_fault(S5_BUILDING_E_SEQUENCE);return 0;
    }
    seq=building_stream->write_seq+1u;
    if(seq-building_stream->next_ack_seq>=building_stream->capacity) {
        stage5_buildings_fault(S5_BUILDING_E_FULL);return 0;
    }
    s=stage5_buildings_slot(seq);
    InterlockedExchange((volatile LONG*)&s->commit_seq,0);
    s->identity=t->identity;s->kind=t->kind;s->owner=t->owner;s->visual=t->visual;
    s->x=t->x;s->y=t->y;s->last_seen_tick=t->last_seen_tick;
    s->currently_observed=observed;s->reserved0=0;
    InterlockedExchange((volatile LONG*)&s->commit_seq,(LONG)seq);
    InterlockedExchange((volatile LONG*)&building_stream->write_seq,(LONG)seq);
    t->observed=observed;t->last_emit_tick=now;return 1;
}
static int stage5_buildings_hide(ScapiBuildingTrack *t,DWORD now) {
    /* Coalesced legal frames may have advanced last_seen beyond the durable
       true record. Publish that final authorized timestamp first; the false
       record then freezes an EXACT tuple that the consumer already persisted.
       Both records are committed independently; a full queue remains explicit. */
    if(t->last_seen_tick!=t->last_emit_tick &&
       !stage5_buildings_emit(t,1,t->last_seen_tick))return 0;
    return stage5_buildings_emit(t,0,now);
}
static DWORD stage5_buildings_start(DWORD capacity,DWORD refresh_ms) {
    size_t bytes;DWORD rollover=0;
    if(!capacity || capacity>0x7fffffffu/sizeof(ScapiBuildingSlot) ||
       !refresh_ms || refresh_ms>=0x80000000u)return SC_BAD_ARGUMENT;
    bytes=sizeof(ScapiBuildingHeader)+(size_t)capacity*sizeof(ScapiBuildingSlot);
    if(bytes>0x7fffffffu)return SC_BAD_ARGUMENT;
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer)return SC_NO_GAME;
    if(SCAPI_State.expected_epoch!=SCAPI_State.epoch)return SC_STALE_EPOCH;
    if(building_stream) {
        if(capacity!=building_stream->capacity)return SC_BAD_ARGUMENT;
        stage5_buildings_sync();
        if(SCAPI_State.epoch==building_stream->epoch) {
            if(refresh_ms!=building_refresh_ms)return SC_BAD_ARGUMENT;
            if(SCAPI_State.building_fault || building_stream->fault)return SC_UNINITIALIZED;
            SCAPI_State.building_enabled=1;
            InterlockedExchange((volatile LONG*)&building_stream->enabled,1);
            return SC_OK;
        }
        if(building_stream->enabled || SCAPI_State.building_enabled ||
           building_stream->next_ack_seq!=building_stream->write_seq+1u ||
           (building_stream->fault && building_stream->fault!=S5_BUILDING_E_EPOCH) ||
           (SCAPI_State.building_fault && SCAPI_State.building_fault!=S5_BUILDING_E_EPOCH))
            return SC_UNINITIALIZED;
        rollover=1;
    }
    if(!rollover) {
        if(SCAPI_State.building_fault)return SC_UNINITIALIZED;
        building_stream=VirtualAlloc(NULL,bytes,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
        if(!building_stream) {
            stage5_buildings_fault(S5_BUILDING_E_ALLOC);return SC_UNINITIALIZED;
        }
    }
    memset(building_stream,0,bytes);memset(building_tracks,0,sizeof(building_tracks));
    building_identity_serial=building_frame_open=0;SCAPI_State.building_fault=0;
    building_stream->magic=S5_BUILDING_MAGIC;building_stream->version=S5_BUILDING_VERSION;
    building_stream->epoch=SCAPI_State.epoch;building_stream->capacity=capacity;
    building_stream->next_ack_seq=1;
    building_refresh_ms=refresh_ms;
    SCAPI_State.building_address=(DWORD)(uintptr_t)building_stream;
    SCAPI_State.building_bytes=(DWORD)bytes;SCAPI_State.building_capacity=capacity;
    SCAPI_State.building_epoch=SCAPI_State.epoch;SCAPI_State.building_ack=1;
    SCAPI_State.building_enabled=1;
    InterlockedExchange((volatile LONG*)&building_stream->enabled,1);
    return SC_OK;
}
static void stage5_buildings_begin(DWORD now) {
    DWORD i;(void)now;
    if(!stage5_buildings_sync())return;
    if(building_frame_open) {stage5_buildings_fault(S5_BUILDING_E_FRAME);return;}
    for(i=0;i<1700;++i)building_tracks[i].seen=0;
    building_frame_open=1;
}
static void stage5_buildings_seen(BYTE *u,DWORD kind,DWORD owner,DWORD visual,
                                  LONG x,LONG y,DWORD now) {
    uintptr_t first=(uintptr_t)AT(0x5b2e68),p=(uintptr_t)u;
    DWORD slot,unique,sprite,changed;ScapiBuildingTrack *t;
    if(!SCAPI_State.building_enabled || !building_frame_open)return;
    if(p<first || p-first>=1700u*0x148u || (p-first)%0x148u ||
       kind>=228 || owner>=12) {
        stage5_buildings_fault(S5_BUILDING_E_ARGUMENT);return;
    }
    /* These are the ONLY object reads: caller already established the same
       lawful screen observation. Never inspect an unseen pool slot here. */
    unique=u[0xa5];sprite=(DWORD)(uintptr_t)*(BYTE**)(u+12);
    slot=(DWORD)((p-first)/0x148u);t=&building_tracks[slot];
    if(!t->live || t->unique!=unique || t->sprite!=sprite) {
        if(t->live && t->observed && !stage5_buildings_hide(t,now))return;
        if(building_identity_serial==0xffffffffu) {
            stage5_buildings_fault(S5_BUILDING_E_IDENTITY);return;
        }
        memset(t,0,sizeof(*t));t->live=1;t->unique=unique;t->sprite=sprite;
        t->identity=++building_identity_serial;
    }
    changed=!t->observed || t->kind!=kind || t->owner!=owner ||
        t->visual!=visual || t->x!=x || t->y!=y;
    t->seen=1;t->kind=kind;t->owner=owner;t->visual=visual;t->x=x;t->y=y;
    t->last_seen_tick=now;
    if(changed || now-t->last_emit_tick>=building_refresh_ms)
        stage5_buildings_emit(t,1,now);
}
static void stage5_buildings_end(DWORD now) {
    DWORD i;
    if(!building_frame_open || !stage5_buildings_sync())return;
    for(i=0;i<1700;++i) {
        ScapiBuildingTrack *t=&building_tracks[i];
        if(t->live && t->observed && !t->seen && !stage5_buildings_hide(t,now))break;
    }
    building_frame_open=0;
}
static DWORD stage5_buildings_stop(void) {
    DWORD now=real_tick();
    /* The conservative end marker changes availability only. last_seen and
       all observed facts remain frozen; no game memory is read. */
    if(stage5_buildings_sync()) {
        if(building_frame_open)building_frame_open=0;
        stage5_buildings_begin(now);stage5_buildings_end(now);
    }
    SCAPI_State.building_enabled=0;building_frame_open=0;
    if(building_stream)InterlockedExchange((volatile LONG*)&building_stream->enabled,0);
    return SCAPI_State.building_fault?SC_UNINITIALIZED:SC_OK;
}
#endif
