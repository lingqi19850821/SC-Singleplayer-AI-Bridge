#ifndef SCAPI_STAGE5_OWN_OVERVIEW_H
#define SCAPI_STAGE5_OWN_OVERVIEW_H
/* Snapshot of real local-owned entities, independent of the current camera.
   Private identities/name pointers are transport fields, not public handles.
   Protocol25 layout: <IIii13I>, 68 bytes. Reserved queue fields stay zero.
   Only research/upgrade work names are sampled; no production queue or timer.
   Unknown positions have flags bit 1 clear.
   Shared observation identities join the lawful own and view sources. */
typedef struct ScapiOwnOverviewItem {
    DWORD identity,type;
    LONG x,y;
    DWORD flags,name_address,carrier_identity;
    DWORD work_valid,queue_count,queue_name0,queue_name1,queue_name2,queue_name3,queue_name4;
    DWORD research_name,upgrade_name;
    DWORD command_identity; /* private finite native encoding, never public */
} ScapiOwnOverviewItem;
enum { S5_OWN_BUILDING=1,S5_OWN_POSITION=2,S5_OWN_CARRIER_POSITION=4 };
static ScapiOwnOverviewItem stage5_own_items[1700];

/* The active-work icon tooltip at 4A3D0E..4A3D76 reads exactly the active
   tech/upgrade's loaded name: tech label 69F658, upgrade label 6B0B28.
   4AA3D0 -> 4D5AB0 resolves a one-based index from the loaded 68D344 table.
   These names have no two-byte command-button header. Bound the loaded table
   and offset before returning a private pointer; no whole-table export. */
static DWORD stage5_work_name(DWORD label) {
    BYTE *table=*(BYTE**)AT(0x68d344);DWORD count,offset;
    if(!label || !readable(table,2))return 0;
    count=*(WORD*)table;
    if(label>count || !readable(table,2+2*count))return 0;
    offset=*(WORD*)(table+2*label);
    if(offset<2+2*count || !readable(table+offset,1))return 0;
    return (DWORD)(uintptr_t)(table+offset);
}
static void stage5_own_work(BYTE *u,ScapiOwnOverviewItem *item) {
    DWORD research=u[0xc8],upgrade=u[0xc9];
    DWORD research_name=0,upgrade_name=0;
    /* 421966..421989 initializes these building-only union fields to 44/61;
       43A689/43A862 start a project; native cancellation restores sentinels.
       Reject malformed or simultaneous projects instead of inventing idle. */
    if(research>44 || upgrade>61 || (research<44 && upgrade<61))return;
    if(research<44) {
        research_name=stage5_work_name(U16(0x69f658+2*research));
        if(!research_name)return;
    }
    if(upgrade<61) {
        upgrade_name=stage5_work_name(U16(0x6b0b28+2*upgrade));
        if(!upgrade_name)return;
    }
    item->research_name=research_name;item->upgrade_name=upgrade_name;item->work_valid=1;
}

static DWORD stage5_own_identity(BYTE *u) {
    /* Exact native 424B80..424BB2 encoding; caller already validated pool. */
    return (DWORD)((u-AT(0x5b2e68))/0x148u+1u)|((DWORD)u[0xa5]<<11);
}
static int stage5_own_entity(BYTE *u,DWORD local) {
    DWORD type;
    if(!valid_unit(u) || u[0x4c]!=local)return 0;
    type=*(WORD*)(u+0x64);
    if(type>=228 || (U32(0x6b0358+type*4u)&0x10u))return 0;
    /* Actual target name-index: scanner/map-revealer/disruption/dark-swarm
       are spell or internal entities, not an additional owned actor. */
    return type!=33 && type!=101 && type!=105 && type!=202;
}
static int stage5_own_position(BYTE *u,LONG *x,LONG *y) {
    BYTE *sprite=*(BYTE**)(u+12);
    LONG px=*(short*)(sprite+20),py=*(short*)(sprite+22);
    if(!SCAPI_State.width_tiles || !SCAPI_State.height_tiles || px<0 || py<0 ||
       (DWORD)px>=SCAPI_State.width_tiles*32u ||
       (DWORD)py>=SCAPI_State.height_tiles*32u)return 0;
    *x=px;*y=py;return 1;
}
static BYTE *stage5_own_carrier(BYTE *u,DWORD local) {
    BYTE *carrier=*(BYTE**)(u+0x80);
    DWORD type,cap,i,encoded;
    if(carrier==u || !stage5_own_entity(carrier,local) ||
       (*(DWORD*)(carrier+0xdc)&0x60u))return NULL;
    type=*(WORD*)(carrier+0x64);cap=U8(0x6ace30+type);
    if(!cap || cap>8)return NULL;
    encoded=stage5_own_identity(u);
    /* Same reciprocal cargo identity used by stage5_collect_cargo. Do not
       truncate a corrupt/nonrepresentable identity to a coincidental WORD. */
    if(encoded>0xffffu)return NULL;
    for(i=0;i<cap;++i)if(*(WORD*)(carrier+0xb0+2*i)==encoded)return carrier;
    return NULL;
}
static DWORD stage5_own_overview(void) {
    DWORD i,count=0,local=U32(0x4f30c0);
    if(SCAPI_State.expected_epoch!=SCAPI_State.epoch)return SC_STALE_EPOCH;
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer || local>=12)return SC_UNINITIALIZED;
    if(SCAPI_State.own_serial>=0xfffffffeu)return SC_INCOMPATIBLE_STATE;
    InterlockedIncrement((LONG*)&SCAPI_State.own_serial);
    stage5_entity_own_begin();
    SCAPI_State.own_address=(DWORD)(uintptr_t)stage5_own_items;
    SCAPI_State.own_count=0;
    for(i=0;i<1700;++i) {
        BYTE *u=AT(0x5b2e68)+i*0x148u,*source=u;
        ScapiOwnOverviewItem *item;
        DWORD type,status;
        if(!stage5_own_entity(u,local))continue;
        item=&stage5_own_items[count++];memset(item,0,sizeof(*item));
        type=*(WORD*)(u+0x64);status=*(DWORD*)(u+0xdc);
        item->identity=stage5_entity_observe(u,1);item->type=type;
        item->command_identity=stage5_own_identity(u);
        if(U32(0x6b0358+type*4u)&1u)item->flags|=S5_OWN_BUILDING;
        item->name_address=(DWORD)(uintptr_t)((UnitNameFn)AT(0x425390))(type);
        if(item->flags&S5_OWN_BUILDING)stage5_own_work(u,item);
        if(status&0x40u) {
            source=stage5_own_carrier(u,local);
            if(source) {
                item->carrier_identity=stage5_own_identity(source);
                if(stage5_own_position(source,&item->x,&item->y))
                    item->flags|=S5_OWN_POSITION|S5_OWN_CARRIER_POSITION;
            }
        } else if(!(status&0x20u)) {
            /* Carrier fighters in a hangar have a special parent chain not
               yet verified for 1.05. Preserve their count without claiming
               a stale hidden-sprite position. Burrowed actors are retained. */
            if(!((type==73 || type==85) && ((*(BYTE**)(u+12))[14]&0x20u)) &&
               stage5_own_position(u,&item->x,&item->y))item->flags|=S5_OWN_POSITION;
        }
    }
    stage5_entity_own_end();
    SCAPI_State.own_count=stage5_entity_fault?0:count;
    InterlockedIncrement((LONG*)&SCAPI_State.own_serial);
    return stage5_entity_fault?SC_INCOMPATIBLE_STATE:SC_OK;
}
#endif
