#ifndef SCAPI_STAGE5_ENTITY_IDENTITY_H
#define SCAPI_STAGE5_ENTITY_IDENTITY_H
/* Public observation identities are independent of the finite native packet
   incarnation. Only own-roster or already-qualified view callers enter here.
   A break in lawful continuity starts a new identity, never an unseen scan. */
typedef struct S5EntityIdentity {
    DWORD identity,unique,sprite,continuous,seen,owned,own_seen;
} S5EntityIdentity;
static S5EntityIdentity stage5_entities[1700];
static DWORD stage5_entity_epoch,stage5_entity_serial,stage5_entity_fault;
static void stage5_entity_scope(void) {
    if(stage5_entity_epoch!=SCAPI_State.epoch) {
        memset(stage5_entities,0,sizeof(stage5_entities));
        stage5_entity_epoch=SCAPI_State.epoch;stage5_entity_serial=stage5_entity_fault=0;
    }
}
static DWORD stage5_entity_observe(BYTE *u,int owned) {
    DWORD slot,sprite;S5EntityIdentity *entry;
    stage5_entity_scope();
    if(!valid_unit(u)){stage5_entity_fault=1;return 0;}
    slot=(DWORD)((u-AT(0x5b2e68))/0x148u);
    entry=&stage5_entities[slot];sprite=(DWORD)(uintptr_t)*(BYTE**)(u+12);
    if(!entry->continuous || entry->unique!=u[0xa5] || entry->sprite!=sprite) {
        /* Fits flags bits5..31. Exhaustion is explicit and never wraps. */
        if(stage5_entity_serial>=0x07ffffffu) {
            stage5_entity_fault=1;SCAPI_State.enable_actions=0;return 0;
        }
        entry->identity=++stage5_entity_serial;
        entry->unique=u[0xa5];entry->sprite=sprite;
    }
    entry->continuous=entry->seen=1;entry->owned=owned?1u:0u;
    if(owned)entry->own_seen=1;
    return entry->identity;
}
static void stage5_entity_frame_begin(void) {
    DWORD i;stage5_entity_scope();
    for(i=0;i<1700;++i)stage5_entities[i].seen=0;
}
static void stage5_entity_frame_end(void) {
    DWORD i;
    for(i=0;i<1700;++i)if(!stage5_entities[i].seen)stage5_entities[i].continuous=0;
}
static void stage5_entity_own_begin(void) {
    DWORD i;stage5_entity_scope();
    for(i=0;i<1700;++i)stage5_entities[i].own_seen=0;
}
static void stage5_entity_own_end(void) {
    DWORD i;
    for(i=0;i<1700;++i)if(stage5_entities[i].owned && !stage5_entities[i].own_seen) {
        stage5_entities[i].continuous=stage5_entities[i].owned=0;
    }
}
#endif
