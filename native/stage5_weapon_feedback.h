#ifndef SCAPI_STAGE5_WEAPON_FEEDBACK_H
#define SCAPI_STAGE5_WEAPON_FEEDBACK_H
/* Exact 1.05 native impact sites. No bullet polling, source-unit reads, audio,
   default weapon database, or model-facing identity is used here. */
enum { S5_HOOK_WEAPON_DAMAGE=9,S5_HOOK_WEAPON_MELEE=10,S5_HOOK_WEAPON_BULLET=11 };
static const BYTE stage5_weapon_damage_prefix[]={0x8a,0x54,0x24,0x18,0x33,0xc9};
static const BYTE stage5_weapon_halluc_prefix[]={0x33,0xc9,0x80,0xfb,0x22,0x0f,0x95,0xc1};

static int stage5_weapon_abi_valid(void) {
    return readable(AT(0x41ab52),sizeof(stage5_weapon_damage_prefix)) &&
        !memcmp(AT(0x41ab52),stage5_weapon_damage_prefix,sizeof(stage5_weapon_damage_prefix)) &&
        readable(AT(0x41a0e0),sizeof(stage5_weapon_halluc_prefix)) &&
        !memcmp(AT(0x41a0e0),stage5_weapon_halluc_prefix,sizeof(stage5_weapon_halluc_prefix)) &&
        readable(AT(0x41a7c3),sizeof(stage5_weapon_halluc_prefix)) &&
        !memcmp(AT(0x41a7c3),stage5_weapon_halluc_prefix,sizeof(stage5_weapon_halluc_prefix));
}
static void stage5_weapon_fault(DWORD error) {
    SCAPI_State.events_fault=error;scapi_event_mark_invalid(event_stream,error);
}
static int stage5_weapon_visible(BYTE *u,int *x,int *y) {
    BYTE *sp;int ax,ay,wx,wy;
    DWORD scale=SCAPI_State.minimap_scale,w=SCAPI_State.minimap_width,h=SCAPI_State.minimap_height;
    int left=SCAPI_State.minimap_left,top=SCAPI_State.minimap_top;
    /* Sampling and impacts both run on the engine thread. Refuse a previous
       camera snapshot rather than applying old geometry to the new picture. */
    if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0) || U32(0x4f30c0)>=12 ||
       SCAPI_State.width_tiles!=U16(0x4fef3c) || SCAPI_State.height_tiles!=U16(0x4fef3e) ||
       SCAPI_State.camera_x!=I32(0x51bce4) || SCAPI_State.camera_y!=I32(0x51bce0) ||
       SCAPI_State.camera_max_x!=I32(0x51bcd0) || SCAPI_State.camera_max_y!=I32(0x51bcac) ||
       !scale || !w || !h || w>128 || h>128 ||
       w*scale!=SCAPI_State.width_tiles*32u || h*scale!=SCAPI_State.height_tiles*32u ||
       !stage4_view_anchor(u,&ax,&ay))return 0;
    sp=*(BYTE**)(u+12);wx=*(short*)(sp+20);wy=*(short*)(sp+22);
    if(wx<0 || wy<0 || (DWORD)wx>=w*scale || (DWORD)wy>=h*scale)return 0;
    *x=left+wx/(int)scale;*y=top+wy/(int)scale;
    return 1;
}
static DWORD stage5_weapon_label(DWORD weapon,BYTE *output,DWORD *bytes) {
    BYTE *table,*p;DWORD i,n=0,count,offset;WORD label;
    if(weapon>=130)return SCAPI_EVENT_E_ARGUMENT;
    label=U16(0x6b1ca0+2u*weapon);table=*(BYTE**)AT(0x68d344);
    if(!readable(table,2))return SCAPI_EVENT_E_ARGUMENT;
    count=*(WORD*)table;
    if(!label || label>count || !readable(table,2u+2u*count))return SCAPI_EVENT_E_ARGUMENT;
    offset=*(WORD*)(table+2u*label);
    if(offset<2u+2u*count || (uintptr_t)table+offset<(uintptr_t)table)return SCAPI_EVENT_E_ARGUMENT;
    p=table+offset;
    /* Native weapon tooltip 4A3F1E indexes this direct stat_txt string, not
       the command-card category header. Match PanelText's control filtering.
       Bound the source as well as the output; a terminator is mandatory. */
    for(i=0;i<=SCAPI_EVENT_TEXT_BYTES;++i) {
        BYTE c;if(!readable(p,i+1u))return SCAPI_EVENT_E_ARGUMENT;c=p[i];
        if(!c){if(!n)return SCAPI_EVENT_E_ARGUMENT;*bytes=n;return SCAPI_EVENT_OK;}
        if(i==SCAPI_EVENT_TEXT_BYTES)return SCAPI_EVENT_E_OVERSIZE;
        if(c>=32 || c==9 || c==10)output[n++]=c;
    }
    return SCAPI_EVENT_E_OVERSIZE;
}
static void stage5_weapon_impact(DWORD kind,ScapiHookRegisters *r) {
    BYTE *u,*p;BYTE text[SCAPI_EVENT_TEXT_BYTES];DWORD weapon,bytes=0,error,sp;int x,y;
    if(kind==S5_HOOK_WEAPON_DAMAGE || kind==S5_HOOK_WEAPON_MELEE)u=(BYTE*)(uintptr_t)r->edi;
    else if(kind==S5_HOOK_WEAPON_BULLET)u=(BYTE*)(uintptr_t)r->esi;
    else return;
    if(!stage5_weapon_visible(u,&x,&y))return;
    if(kind==S5_HOOK_WEAPON_DAMAGE) {
        /* 41AB52: four saved registers, second incoming argument. */
        sp=r->saved_esp+4u;
        if(sp<r->saved_esp || sp>0xffffffffu-0x18u) {stage5_weapon_fault(SCAPI_EVENT_E_ARGUMENT);return;}
        p=(BYTE*)(uintptr_t)(sp+0x18u);
        if(!readable(p,1)){stage5_weapon_fault(SCAPI_EVENT_E_ARGUMENT);return;}
        weapon=*p;
    } else weapon=r->ebx&255u;
    error=stage5_weapon_label(weapon,text,&bytes);
    if(error){stage5_weapon_fault(error);return;}
    stage4_event_append(S5_EVENT_WEAPON,x,y,weapon,text,bytes);
}
#endif
