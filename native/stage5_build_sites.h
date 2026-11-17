#ifndef SCAPI_STAGE5_BUILD_SITES_H
#define SCAPI_STAGE5_BUILD_SITES_H
/* Typed placement starts BEFORE entering placement mode. The domain is only
   the target renderer's current viewport, for this real selected builder's
   automatically completed lawful build options. No whole-map search occurs.
   40CE10 is the existing 1.05 placement evaluator: its branches include
   refinery/geyser, creep, psi, actor reachability and resource spacing.
   No current hidden geometry/blocker query is made for an explored fog site. */
typedef struct S5BuildSiteRow {
    DWORD type,width,height,name_offset,name_length;
    BYTE outcomes[296]; /* 21*14 cells, rounded to DWORD; 254=fog,255=unknown */
} S5BuildSiteRow;
typedef struct S5BuildSites {
    DWORD magic,version,bytes,epoch,frame;
    LONG camera_x,camera_y;
    DWORD width_px,height_px,selection_generation,builder_type,count;
    LONG tile_left,tile_top;
    DWORD columns,rows,tick,ready;
    /* S5BuildSiteRow[count], then copied current lawful building labels. */
} S5BuildSites;
static S5BuildSites *stage5_build_sites;
/* Exact comparisons avoid hash collisions becoming stale executable advice.
   Only already lawful scene positions/own building overview enter this stamp.
   No hidden unit list or current fog creep/blocker scan is introduced. */
typedef struct S5BuildSitesStamp {
    DWORD epoch,selection,started,completion,event_sequence,event_generation;
    LONG camera_x,camera_y;
    DWORD width,height,map_width,map_height,actor_position,actor_incarnation,actor_status,own_count,ground_count;
    DWORD tiles[294],rules[228][3],own_buildings[1700][5],ground[1700][5];
} S5BuildSitesStamp;
static S5BuildSitesStamp stage5_sites_stamp,stage5_sites_fresh_stamp;
static int stage5_sites_stamp_valid;
static int stage5_sites_semantic_stamp(BYTE *actor,ScapiAutoPanel *completed,
                                     int left,int top,int right,int bottom) {
    S5BuildSitesStamp *s=&stage5_sites_fresh_stamp;DWORD i,n=0,flags,mask=U32(0x65098c),explored=U32(0x650990);
    ScapiPanelText *texts=(ScapiPanelText*)(completed+1);int x,y;
    if(!SCAPI_State.events_enabled || !event_stream || event_stream->flags || event_stream->lost ||
       event_stream->epoch!=SCAPI_State.epoch || !SCAPI_State.own_serial || (SCAPI_State.own_serial&1) ||
       SCAPI_State.own_count>1700 || scene_epoch!=SCAPI_State.epoch || scene_count>1700)return 0;
    memset(s,0,sizeof(*s));s->epoch=SCAPI_State.epoch;s->selection=SCAPI_State.selection_generation;
    s->started=SCAPI_State.details_started_tick;s->completion=completed->sequence;
    s->event_sequence=event_stream->write_seq;s->event_generation=SCAPI_State.events_generation;
    s->camera_x=SCAPI_State.camera_x;s->camera_y=SCAPI_State.camera_y;
    s->map_width=SCAPI_State.width_tiles;s->map_height=SCAPI_State.height_tiles;
    s->width=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    s->height=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    s->actor_position=*(DWORD*)(*(BYTE**)(actor+12)+20);
    s->actor_incarnation=actor[0xa5];s->actor_status=*(DWORD*)(actor+0xdc)&0x65u;
    for(y=top;y<=bottom;++y)for(x=left;x<=right;++x) {
        s->tiles[n]=0xffffffffu;
        if(s5_footprint_gate(x,y,1,1)==SC_OK && s5_tile_flags(x,y,&flags)==SC_OK)
            s->tiles[n]=(flags&mask)?flags&(mask|explored):flags;
        ++n;
    }
    for(i=0;i<completed->text_count;++i)if(texts[i].role==3 && texts[i].key<228) {
        DWORD kind=texts[i].key;
        s->rules[kind][0]=U32(0x6b0358+4*kind);s->rules[kind][1]=U32(0x6ae968+4*kind);
        s->rules[kind][2]=U8(0x4fefe4+228u*actor[0x4c]+kind);
    }
    for(i=0;i<SCAPI_State.own_count;++i)if(stage5_own_items[i].flags&S5_OWN_BUILDING) {
        ScapiOwnOverviewItem *item=&stage5_own_items[i];DWORD *row=s->own_buildings[s->own_count++];
        row[0]=item->identity;row[1]=item->type;row[2]=item->flags;
        row[3]=item->x;row[4]=item->y;
    }
    for(i=0;i<scene_count;++i)if(!scene_view[i].private_terrain_domain) {
        ScapiViewItem *item=&scene_view[i];DWORD *row=s->ground[s->ground_count++];
        row[0]=item->flags>>5;row[1]=item->private_type;row[2]=item->x;row[3]=item->y;row[4]=item->private_owner;
    }
    return 1;
}
static int stage5_sites_obvious_terrain_block(int x,int y,int w,int h,DWORD kind) {
    int xx,yy;DWORD flags;
    /* Exact target4246C0 refinery classification. Geysers are an exception to
       ordinary buildable ground and must always reach the native evaluator. */
    if(kind==0x6e || kind==0x9d || kind==0x95)return 0;
    for(yy=y;yy<y+h;++yy)for(xx=x;xx<x+w;++xx)
        if(s5_tile_flags(xx,yy,&flags)!=SC_OK || (flags&0x20800000u))return 1;
    return 0;
}

static DWORD stage5_build_sites_request(void) {
    BYTE *actor,*text,backup[96],kinds[228]={0};
    ScapiAutoPanel *completed=stage5_auto_panel;ScapiPanelText *texts;
    S5BuildSites *fresh;S5BuildSiteRow *rows;DWORD r,n=0,total,i,j,q,serial;
    int width,height,left,top,right,bottom,x,y,w,h,cacheable=0;
    r=s5_terrain_ready();if(r)return r;
    r=stage5_owned_selection(1,&actor);if(r)return r;
    if(actor[0x4c]>=12 || *(WORD*)(actor+0x64)>=228)return SC_INCOMPATIBLE_STATE;
    width=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    height=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    if(width<1 || width>640 || height<1 || height>400)return SC_INCOMPATIBLE_STATE;
    left=SCAPI_State.camera_x/32;top=SCAPI_State.camera_y/32;
    right=(SCAPI_State.camera_x+width-1)/32;bottom=(SCAPI_State.camera_y+height-1)/32;
    if(right-left+1>21 || bottom-top+1>14)return SC_INCOMPATIBLE_STATE;
    /* Readiness is established by the ordinary selected-panel clock, never an
       extra Agent flag, menu traversal or action. The captured graph belongs
       to that exact interval and selection, including normal native reads. */
    r=SCAPI_State.details_ready && s5_terrain_dwelled() && completed &&
      completed->panel.epoch==SCAPI_State.epoch &&
      completed->panel.selection_generation==SCAPI_State.selection_generation &&
      completed->panel.details_started_tick==SCAPI_State.details_started_tick &&
      completed->panel.panel_type==*(WORD*)(actor+0x64) &&
      completed->panel.panel_owned && !SCAPI_State.auto_panel_fault;
    texts=r?(ScapiPanelText*)(completed+1):NULL;
    if(r && completed->text_count>240)return SC_INCOMPATIBLE_STATE;
    total=sizeof(S5BuildSites);
    if(r && completed->profiles[5]==1)for(i=0;i<completed->text_count;++i)if(texts[i].role==3) {
        ScapiPanelText *item=&texts[i];
        if(item->key>=228 || kinds[item->key] || !item->length ||
           item->offset>completed->bytes || item->length>completed->bytes-item->offset ||
           ((BYTE*)completed)[item->offset+item->length-1])return SC_INCOMPATIBLE_STATE;
        kinds[item->key]=1;++n;
        if(total>0xffffffffu-sizeof(S5BuildSiteRow)-item->length)return SC_INCOMPATIBLE_STATE;
        total+=sizeof(S5BuildSiteRow)+item->length;
    }
    if(r && completed->profiles[5]==1) {
        cacheable=stage5_sites_semantic_stamp(actor,completed,left,top,right,bottom);
        if(cacheable && stage5_sites_stamp_valid && stage5_build_sites &&
           !memcmp(&stage5_sites_stamp,&stage5_sites_fresh_stamp,sizeof(stage5_sites_stamp)))return SC_OK;
    }
    stage5_sites_stamp_valid=0;
    fresh=(S5BuildSites*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,total);
    if(!fresh)return SC_UNINITIALIZED;
    fresh->magic=0x31534253u;fresh->version=1;fresh->bytes=total;
    fresh->epoch=SCAPI_State.epoch;fresh->frame=SCAPI_State.raw_counter;
    fresh->camera_x=SCAPI_State.camera_x;fresh->camera_y=SCAPI_State.camera_y;
    fresh->width_px=width;fresh->height_px=height;
    fresh->selection_generation=SCAPI_State.selection_generation;
    fresh->builder_type=*(WORD*)(actor+0x64);fresh->count=n;
    fresh->tile_left=left;fresh->tile_top=top;
    fresh->columns=right-left+1;fresh->rows=bottom-top+1;fresh->tick=real_tick();
    fresh->ready=!r?0:completed->profiles[5]==1?1:2;
    rows=(S5BuildSiteRow*)(fresh+1);text=(BYTE*)(rows+n);j=0;
    q=U32(0x650ce0);memcpy(backup,AT(0x653950),96);
    if(r && completed->profiles[5]==1)for(i=0;i<completed->text_count;++i)if(texts[i].role==3) {
        ScapiPanelText *item=&texts[i];S5BuildSiteRow *row=&rows[j++];DWORD kind=item->key;
        int px=(short)U16(0x6ae968+4*kind),py=(short)U16(0x6ae96a+4*kind);
        if(px<=0 || py<=0 || px%32 || py%32 || px>6*32 || py>8*32)goto invalid;
        w=px/32;h=py/32;row->type=kind;row->width=px;row->height=py;
        row->name_offset=(DWORD)(text-(BYTE*)fresh);row->name_length=item->length;
        memcpy(text,(BYTE*)completed+item->offset,item->length);text+=item->length;
        memset(row->outcomes,255,sizeof(row->outcomes));
        for(y=top;y<=bottom;++y)for(x=left;x<=right;++x) {
            DWORD index=(y-top)*fresh->columns+x-left,reason;
            if(s5_footprint_gate(x,y,w,h)!=SC_OK)continue;
            if(s5_footprint_visible_gate(x,y,w,h)!=SC_OK){row->outcomes[index]=254;continue;}
            if(stage5_sites_obvious_terrain_block(x,y,w,h,kind)){row->outcomes[index]=4;continue;}
            reason=((S5PlacementFn)AT(0x40ce10))(actor,actor[0x4c],x,y,kind,1,1,0);
            if(reason>10 || U32(0x650ce0)!=q)goto invalid;
            row->outcomes[index]=(BYTE)reason;
        }
    }
    memcpy(AT(0x653950),backup,96);
    if(U32(0x650ce0)!=q){HeapFree(GetProcessHeap(),0,fresh);return SC_QUEUE_UNCONFIRMED;}
    serial=SCAPI_State.build_sites_serial;
    if(serial>=0xfffffffeu){HeapFree(GetProcessHeap(),0,fresh);return SC_INCOMPATIBLE_STATE;}
    InterlockedIncrement((LONG*)&SCAPI_State.build_sites_serial);
    if(stage5_build_sites)HeapFree(GetProcessHeap(),0,stage5_build_sites);
    stage5_build_sites=fresh;
    SCAPI_State.build_sites_address=(DWORD)(uintptr_t)fresh;
    SCAPI_State.build_sites_bytes=total;
    InterlockedIncrement((LONG*)&SCAPI_State.build_sites_serial);
    stage5_sites_stamp_valid=cacheable;
    if(cacheable)memcpy(&stage5_sites_stamp,&stage5_sites_fresh_stamp,sizeof(stage5_sites_stamp));
    return SC_OK;
invalid:
    memcpy(AT(0x653950),backup,96);HeapFree(GetProcessHeap(),0,fresh);
    return U32(0x650ce0)==q?SC_INCOMPATIBLE_STATE:SC_QUEUE_UNCONFIRMED;
}
#endif
