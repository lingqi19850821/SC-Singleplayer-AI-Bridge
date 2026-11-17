#ifndef SCAPI_STAGE5_CARGO_H
#define SCAPI_STAGE5_CARGO_H
/* Observe only the current native statdata cargo icons. 4A6C7C puts the
   authoritative passenger in control+26; 4A36F1 passes it to 472390. */
typedef void *(FAST *S5FindControlFn)(void*,unsigned);
typedef void (FAST *S5UnloadOneFn)(void*);
typedef struct ScapiCargo {
    DWORD slot,visual,type,enabled,unit,unique,node,sort_position;
} ScapiCargo;
static ScapiCargo stage5_cargo[8];
static DWORD stage5_cargo_selection,stage5_cargo_epoch;

static DWORD stage5_collect_cargo(ScapiCargo *rows) {
    BYTE *carrier,*root,*node,*p;DWORD local=U32(0x4f30c0),kind,cap,count=0,id,i,j,encoded;
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer || SCAPI_State.selection_count!=1 ||
       U8(0x68c9dc) || U8(0x68ca6c)!=11 || local>=12)return 0;
    carrier=*(BYTE**)AT(0x68d2f8);
    if(!valid_unit(carrier) || carrier!=*(BYTE**)AT(0x684940) || carrier[0x4c]!=local)return 0;
    kind=*(WORD*)(carrier+0x64);if(kind>=228)return 0;
    cap=U8(0x6ace30+kind);if(!cap || cap>8)return 0;
    root=*(BYTE**)AT(0x68ca68);if(!readable(root,0x46))return 0;
    for(id=0x12;id<=0x20;++id) {
        ScapiCargo item;DWORD flags;
        node=((S5FindControlFn)AT(0x4d69f0))(root,id);
        if(!node)continue;
        if(!readable(node,0x46) || *(WORD*)(node+0x20)!=id)return 0;
        flags=*(DWORD*)(node+0x18);p=*(BYTE**)(node+0x26);
        if(!(flags&8) || !p)continue;
        if(!valid_unit(p) || *(WORD*)(p+0x64)>=228 || p[0x4c]!=local ||
           *(BYTE**)(p+0x80)!=carrier || !(*(DWORD*)(p+0xdc)&0x40))return 0;
        encoded=((EncodeFn)AT(0x424b80))(p);
        for(i=0;i<cap;++i)if(*(WORD*)(carrier+0xb0+2*i)==encoded)break;
        if(i==cap || count>=8)return 0;
        for(i=0;i<count;++i)if(rows[i].unit==(DWORD)(uintptr_t)p)return 0;
        memset(&item,0,sizeof(item));item.visual=*(WORD*)(*(BYTE**)(p+12)+8);
        item.type=*(WORD*)(p+0x64);item.enabled=!(flags&2);
        item.unit=(DWORD)(uintptr_t)p;item.unique=p[0xa5];item.node=(DWORD)(uintptr_t)node;
        item.sort_position=(*(WORD*)(node+6)<<16)|*(WORD*)(node+4);
        for(j=count;j && rows[j-1].sort_position>item.sort_position;--j)rows[j]=rows[j-1];
        rows[j]=item;++count;
    }
    for(i=0;i<count;++i)rows[i].slot=i;
    return count;
}
static void stage5_sample_cargo(void) {
    ScapiCargo fresh[8];DWORD count,changed,next;
    memset(fresh,0,sizeof(fresh));count=stage5_collect_cargo(fresh);
    if(!count)memset(fresh,0,sizeof(fresh));
    changed=memcmp(fresh,stage5_cargo,sizeof(fresh)) ||
        stage5_cargo_selection!=SCAPI_State.selection_generation || stage5_cargo_epoch!=SCAPI_State.epoch;
    memcpy(stage5_cargo,fresh,sizeof(fresh));stage5_cargo_selection=SCAPI_State.selection_generation;
    stage5_cargo_epoch=SCAPI_State.epoch;SCAPI_State.cargo_address=(DWORD)(uintptr_t)stage5_cargo;
    SCAPI_State.cargo_count=count;
    if(changed) {
        next=SCAPI_State.cargo_serial+1;if(!next)SCAPI_State.enable_actions=0;
        InterlockedExchange((LONG*)&SCAPI_State.cargo_serial,next);
    }
}
#endif
