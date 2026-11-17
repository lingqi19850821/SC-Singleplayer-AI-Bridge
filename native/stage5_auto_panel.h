#ifndef SCAPI_STAGE5_AUTO_PANEL_H
#define SCAPI_STAGE5_AUTO_PANEL_H
/* One completed legal attention interval, retained until the sole host worker
   commits it. Commands cannot pass an unacknowledged completion. Strings and
   numeric rule profiles are copied now, never reconstructed after selection
   changes. No game callback or menu mutation is used to discover build sets. */
typedef struct ScapiPanelText {
    DWORD role,key,enabled,handler,category,cost,label,mapping,offset,length;
} ScapiPanelText;
typedef struct ScapiAutoPanel {
    DWORD magic,version,bytes,sequence,tick,text_count,progress_valid,progress_percent;
    ScapiState panel;
    DWORD profiles[18];
    /* ScapiPanelText[text_count], then exactly-sized NUL-terminated strings. */
} ScapiAutoPanel;
static ScapiAutoPanel *stage5_auto_panel;
static DWORD stage5_auto_last_epoch,stage5_auto_last_selection,stage5_auto_last_started;
static DWORD stage5_auto_last_card_valid;
static ScapiButton stage5_detail_seen_card[9];
static DWORD stage5_detail_seen_epoch,stage5_detail_seen_selection,stage5_detail_seen_menu;
static DWORD stage5_detail_seen_hash,stage5_detail_seen_valid;

static DWORD stage5_detail_card_stamp(BYTE *u) {
    ScapiButton fresh[9];DWORD i,hash=0,flags;
    if(u[0x4c]!=U32(0x4f30c0))return 0;
    memset(fresh,0,sizeof(fresh));stage5_collect_card(fresh);
    if(stage5_card_collect_valid) {
        for(i=0;i<9;++i)if(fresh[i].slot) {
            flags=*(DWORD*)((BYTE*)(uintptr_t)fresh[i].node+0x18);
            hash=hash*33u+fresh[i].record+*(WORD*)((BYTE*)(uintptr_t)fresh[i].record+16);
            hash=hash*33u+panel_visible_cost_stamp(*(WORD*)(u+0x64),(BYTE*)(uintptr_t)fresh[i].record,flags);
        }
        memcpy(stage5_detail_seen_card,fresh,sizeof(fresh));stage5_detail_seen_hash=hash;
        stage5_detail_seen_epoch=SCAPI_State.epoch;stage5_detail_seen_selection=SCAPI_State.selection_generation;
        stage5_detail_seen_menu=U16(0x68c9bc);stage5_detail_seen_valid=1;return hash;
    }
    /* Preserve continuity only for v23's proven temporary redraw suppression
       and an unchanged previously real visible card. Cached rows never supply
       new strings/costs to a completion or actionable current-card tickets. */
    if(U8(0x68c9dc) && stage5_detail_seen_valid &&
       stage5_detail_seen_epoch==SCAPI_State.epoch &&
       stage5_detail_seen_selection==SCAPI_State.selection_generation &&
       stage5_detail_seen_menu==U16(0x68c9bc) &&
       stage5_card_epoch==SCAPI_State.epoch && stage5_card_selection==SCAPI_State.selection_generation &&
       stage5_card_menu==stage5_detail_seen_menu &&
       !memcmp(stage5_detail_seen_card,stage5_card_identity,sizeof(stage5_detail_seen_card)))
        return stage5_detail_seen_hash;
    return 0;
}

/* Target file loader445D60 allocates exactly its SFile byte count through
   Storm ordinal401. Existing Storm ordinal404 (stdcall3) returns the source
   allocation's usable byte extent, excluding its own headers/padding
   (target Storm16E50->16A30). Never substitute a VM region for this object.
   Map STR pointer63CA94 additionally has its declared chunk bytes63CA8C
   (46711D..467149). No arbitrary character cap or truncation is applied. */
#ifndef S5_AUTO_SOURCE_BYTES
static DWORD stage5_auto_source_bytes(BYTE *base) {
    typedef DWORD (WINAPI *SourceSizeFn)(void*,const char*,DWORD);
    HMODULE storm;SourceSizeFn size;
    if((uintptr_t)base<8 || !readable(base-8,8) ||
       *(WORD*)(base-2)!=0x6f6d || (base[-5]&2))return 0;
    storm=GetModuleHandleA("storm.dll");if(!storm)return 0;
    size=(SourceSizeFn)GetProcAddress(storm,(LPCSTR)404);if(!size)return 0;
    return size(base,"SCAPI loaded string object",0);
}
#define S5_AUTO_SOURCE_BYTES(base) stage5_auto_source_bytes(base)
#endif
static int stage5_auto_table(BYTE *table,DWORD *bytes) {
    DWORD extent,header;
    if(!table || !readable(table,2))return 0;
    extent=S5_AUTO_SOURCE_BYTES(table);
    if(!extent || extent==0xffffffffu)return 0;
    if(table==*(BYTE**)AT(0x63ca94)) {
        DWORD declared=U32(0x63ca8c);
        if(!declared || declared>extent)return 0;
        extent=declared;
    }
    if((uintptr_t)table>0xffffffffu-extent || !readable(table,extent))return 0;
    header=2u+2u*(*(WORD*)table);
    if(header>extent)return 0;
    *bytes=extent;return 1;
}
static int stage5_auto_string(BYTE *p,DWORD *length) {
    BYTE *sources[2],*table,*zero;DWORD i,bytes,offset,header;
    sources[0]=*(BYTE**)AT(0x68d344);sources[1]=*(BYTE**)AT(0x63ca94);
    if(!p)return 0;
    for(i=0;i<2;++i) {
        table=sources[i];if(!stage5_auto_table(table,&bytes))continue;
        if((uintptr_t)p<(uintptr_t)table || (uintptr_t)p-(uintptr_t)table>=bytes)continue;
        offset=(DWORD)(p-table);header=2u+2u*(*(WORD*)table);
        if(offset<header)return 0;
        zero=memchr(p,0,bytes-offset);
        if(!zero)return 0;
        *length=(DWORD)(zero-p)+1;return 1;
    }
    return 0;
}
static BYTE *stage5_auto_label(DWORD id,int header) {
    BYTE *table=*(BYTE**)AT(0x68d344);DWORD count,offset,bytes;
    if(!stage5_auto_table(table,&bytes))goto invalid;count=*(WORD*)table;
    if(!id || id>count)goto invalid;
    offset=*(WORD*)(table+2*id);
    if(offset<2+2*count || offset>=bytes || (header && bytes-offset<3))goto invalid;
    return table+offset+(header?2:0);
invalid:SCAPI_State.auto_panel_fault=2;return NULL;
}
static int stage5_auto_add(ScapiPanelText *rows,BYTE **strings,DWORD *count,
                          ScapiPanelText row,BYTE *text) {
    if(*count>=240 || !stage5_auto_string(text,&row.length)){
        SCAPI_State.auto_panel_fault=2;return 0;
    }
    rows[*count]=row;strings[*count]=text;++*count;return 1;
}
static int stage5_auto_builds(BYTE *u,ScapiPanelText *rows,BYTE **strings,DWORD *count) {
    /* The domain is the engine's WORD button-set id, not an arbitrary cap.
       Building kinds have exactly 228 entries in this target's loaded tables. */
    BYTE visited[8192]={0},kinds[228]={0};WORD pending[65536];
    DWORD head=0,tail=0,owner=u[0x4c],i,set,n;BYTE *entry,*record,*label;
    ScapiPanelText row;
    pending[tail++]=*(WORD*)(u+0x94);
    visited[pending[0]>>3]|=(BYTE)(1u<<(pending[0]&7));
    while(head<tail) {
        set=pending[head++];entry=AT(0x4f6e08+12*set);
        if(!readable(entry,12))return 0;
        n=*(DWORD*)entry;record=*(BYTE**)(entry+4);
        if(n>0xffffffffu/20 || (n && !readable(record,n*20)))return 0;
        for(i=0;i<n;++i) {
            BYTE *r=record+20*i;DWORD action=*(DWORD*)(r+8)-(DWORD)(uintptr_t)game+0x400000u;
            DWORD arg=*(WORD*)(r+14),id=*(WORD*)(r+16);
            if(!stage5_handler_allowed(action) && action!=0x47a770u)return 0;
            if(action==0x4a17f0u) {
                if(!(visited[arg>>3]&(1u<<(arg&7)))) {
                    visited[arg>>3]|=(BYTE)(1u<<(arg&7));pending[tail++]=(WORD)arg;
                }
            } else if(action==0x47ad80u || action==0x47adc0u) {
                if(arg>=228)return 0;
                if(kinds[arg] || !U8(0x4fefe4+228*owner+arg))continue;
                kinds[arg]=1;label=stage5_auto_label(id,1);if(!label)return 0;
                memset(&row,0,sizeof(row));row.role=3;row.key=arg;
                row.handler=action;row.label=id;row.category=label[-1];
                /* Mission permission, not current prerequisites/resources. */
                if(!stage5_auto_add(rows,strings,count,row,label))return 0;
            } else if(arg<228 && (U32(0x6b0358+4*arg)&1u)) {
                /* A building-producing callback outside the supported native
                   placement pair is an unsupported graph, not an empty list. */
                label=stage5_auto_label(id,1);
                if(label && label[-1]==1)return 0;
            }
        }
    }
    return 1;
}
static void stage5_auto_progress(ScapiAutoPanel *out) {
    BYTE *root=*(BYTE**)AT(0x68ca68),*node,*slow,*fast;DWORD flags;
    /* The UI writer 4A2E30 stores the rendered percentage at control+24.
       Statdata controls have WORD ids; reject cycles, do not derive ETA. */
    if(!out->panel.panel_owned || *(BYTE**)AT(0x68d2f8)!=*(BYTE**)AT(0x684940) ||
       U8(0x68c9dc) || !readable(root,0x46))return;
    if(*(WORD*)(root+0x22)) {
        root=*(BYTE**)(root+0x32);if(!readable(root,0x46))return;
    }
    node=*(BYTE**)(root+0x42);slow=fast=node;
    while(node) {
        if(!readable(node,0x46))return;
        flags=*(DWORD*)(node+0x18);
        if(*(WORD*)(node+0x20)==14 && (flags&8)) {
            DWORD percent=*(WORD*)(node+0x24);
            if(percent>100){SCAPI_State.auto_panel_fault=5;return;}
            out->progress_valid=1;out->progress_percent=percent;return;
        }
        node=*(BYTE**)node;
        if(fast){if(!readable(fast,4))return;fast=*(BYTE**)fast;}
        if(fast){if(!readable(fast,4))return;fast=*(BYTE**)fast;}
        if(slow){if(!readable(slow,4))return;slow=*(BYTE**)slow;}
        if(fast && fast==slow){SCAPI_State.auto_panel_fault=5;return;}
    }
}
static void stage5_auto_capture(BYTE *u,DWORD *values,DWORD now) {
    ScapiPanelText rows[240],row;BYTE *strings[240],*label;ScapiButton buttons[9];
    DWORD i,n=0,total,sequence,type=*(WORD*)(u+0x64),w,factor,*p,build_status=0,build_start,card_valid=0;
    ScapiAutoPanel *fresh;
    if(SCAPI_State.auto_panel_fault)return;
    if(SCAPI_State.auto_panel_ack>SCAPI_State.auto_panel_sequence){SCAPI_State.auto_panel_fault=1;return;}
    if(SCAPI_State.auto_panel_ack!=SCAPI_State.auto_panel_sequence) {
        if(stage5_auto_last_epoch!=SCAPI_State.epoch ||
           stage5_auto_last_selection!=SCAPI_State.selection_generation ||
           stage5_auto_last_started!=SCAPI_State.details_started_tick)
            SCAPI_State.auto_panel_fault=6; /* explicit backpressure, no silent overwrite */
        return;
    }
    if(stage5_auto_last_epoch==SCAPI_State.epoch &&
       stage5_auto_last_selection==SCAPI_State.selection_generation &&
       stage5_auto_last_started==SCAPI_State.details_started_tick &&
       !(u[0x4c]==U32(0x4f30c0) && !stage5_auto_last_card_valid && !U8(0x68c9dc)))return;
    memset(&row,0,sizeof(row));
    label=(BYTE*)((UnitNameFn)AT(0x425390))(type);
    if(!stage5_auto_add(rows,strings,&n,row,label))goto invalid;
    if(panel_health_visible(u))for(i=0;i<2;++i) {
        w=values[i?9:5];if(w>=130)continue;
        memset(&row,0,sizeof(row));row.role=1;row.key=w;row.mapping=U8((i?0x6ada90:0x6ae418)+type);
        row.label=U16(0x6b1ca0+2*w);row.category=i;
        label=stage5_auto_label(row.label,0);if(!label || !stage5_auto_add(rows,strings,&n,row,label))goto invalid;
    }
    if(u[0x4c]==U32(0x4f30c0)) {
        memset(buttons,0,sizeof(buttons));
        stage5_collect_card(buttons);
        card_valid=stage5_card_collect_valid;
        if(!card_valid && stage5_auto_last_epoch==SCAPI_State.epoch &&
           stage5_auto_last_selection==SCAPI_State.selection_generation &&
           stage5_auto_last_started==SCAPI_State.details_started_tick)return;
        /* A temporarily hidden/unsupported card cannot withhold otherwise
           legal completed numerical learning or fabricate empty skills. */
        for(i=0;i<9;++i)if(card_valid && buttons[i].slot) {
            memset(&row,0,sizeof(row));row.role=2;row.key=buttons[i].slot;
            row.enabled=buttons[i].enabled;row.handler=buttons[i].handler;row.category=buttons[i].category;
            row.cost=panel_visible_cost_stamp(type,(BYTE*)(uintptr_t)buttons[i].record,buttons[i].enabled?0:2);
            if(!stage5_auto_add(rows,strings,&n,row,(BYTE*)(uintptr_t)buttons[i].label_address))goto invalid;
        }
        build_start=n;build_status=1;
        if(!stage5_auto_builds(u,rows,strings,&n)){n=build_start;build_status=2;}
        if(SCAPI_State.auto_panel_fault)return;
    }
    total=sizeof(ScapiAutoPanel)+n*sizeof(ScapiPanelText);
    for(i=0;i<n;++i){if(total>0xffffffffu-rows[i].length)goto invalid;rows[i].offset=total;total+=rows[i].length;}
    fresh=(ScapiAutoPanel*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,total);
    if(!fresh){SCAPI_State.auto_panel_fault=3;return;}
    sequence=SCAPI_State.auto_panel_sequence+1;
    if(!sequence){HeapFree(GetProcessHeap(),0,fresh);SCAPI_State.auto_panel_fault=4;return;}
    fresh->magic=0x31504153u;fresh->version=1;fresh->bytes=total;fresh->sequence=sequence;fresh->tick=now;fresh->text_count=n;
    memcpy(&fresh->panel,&SCAPI_State,sizeof(ScapiState));stage4_fill_panel(u,values,&fresh->panel);
    p=fresh->profiles;p[0]=1;p[1]=U8(0x6ae500+type);p[2]=U8(0x6b00a0+type);p[3]=1;p[4]=values[3]-values[4];p[5]=build_status;
    for(i=0;i<2;++i){w=values[i?9:5];p=fresh->profiles+6*(i+1);if(w>=130)continue;
        factor=U8(0x6b0e28+w);if(type==32 || type==10 || type==65 || type==77)factor=2;if(type==58)factor=1;
        p[0]=1;p[1]=w;p[2]=U8(0x6b1988+w);p[3]=U16(0x6b1e30+2*w)*factor;p[4]=U16(0x6b16e8+2*w)*factor;
    }
    fresh->profiles[11]=card_valid;
    stage5_auto_progress(fresh);
    memcpy(fresh+1,rows,n*sizeof(ScapiPanelText));
    for(i=0;i<n;++i)memcpy((BYTE*)fresh+rows[i].offset,strings[i],rows[i].length);
    /* Producer retains the old allocation until its dedicated ack is seen. */
    if(stage5_auto_panel)HeapFree(GetProcessHeap(),0,stage5_auto_panel);
    stage5_auto_panel=fresh;SCAPI_State.auto_panel_address=(DWORD)(uintptr_t)fresh;SCAPI_State.auto_panel_bytes=total;
    stage5_auto_last_epoch=SCAPI_State.epoch;stage5_auto_last_selection=SCAPI_State.selection_generation;
    stage5_auto_last_started=SCAPI_State.details_started_tick;
    stage5_auto_last_card_valid=card_valid;
    InterlockedExchange((LONG*)&SCAPI_State.auto_panel_sequence,sequence);return;
invalid:SCAPI_State.auto_panel_fault=2;
}
#endif
