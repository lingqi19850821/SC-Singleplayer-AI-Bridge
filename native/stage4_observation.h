/* Target 1.05 candidates. Addresses/formulas are supported by the stage-4
   static evidence; actual UI values and geometry require separate validation.
   This file runs only at the existing UI-thread mailbox site. */
static DWORD detail_epoch,detail_selection;
static DWORD detail_values[14];
static int panel_visible(BYTE *u);
static int panel_hud_visible(BYTE *u);
static void panel_detail_stamp(BYTE *u,DWORD *stamp);

/* Target 1.05's 0x424660 tests this exact per-player byte for 3 or 4.
   Separate from alliance and unit owner; never export the player table. */
static int stage4_rescuable_owner(DWORD owner) {
    BYTE controller;
    if(owner>=12)return 0;
    controller=U8(0x6509e0+36u*owner);
    return controller==3 || controller==4;
}

static void stage4_interrupt(void) {
    SCAPI_State.details_active=0; SCAPI_State.details_ready=0;
    SCAPI_State.details_started_tick=0;
}
static void stage4_attention(BYTE *u,DWORD *values,DWORD now) {
    if(!SCAPI_State.details_active || detail_epoch!=SCAPI_State.epoch ||
       detail_selection!=SCAPI_State.selection_generation ||
       memcmp(values,detail_values,sizeof(detail_values))) {
        memcpy(detail_values,values,sizeof(detail_values));
        SCAPI_State.details_started_tick=now;SCAPI_State.details_active=1;SCAPI_State.details_ready=0;
        detail_epoch=SCAPI_State.epoch;detail_selection=SCAPI_State.selection_generation;
    }
    if((DWORD)(now-SCAPI_State.details_started_tick)>=SCAPI_State.details_wait_ms)
        SCAPI_State.details_ready=1;
    (void)u;
}
static void stage4_sample(void) {
    BYTE *dialog=*(BYTE**)AT(0x650340),*u;DWORD values[14],now=real_tick();
    SCAPI_State.control_locked=U8(0x6b0cf8)!=0;
    SCAPI_State.minimap_width=0;SCAPI_State.minimap_height=0;
    if(SCAPI_State.active && readable(dialog,12)) {
        SCAPI_State.minimap_left=*(short*)(dialog+4);SCAPI_State.minimap_top=315+*(short*)(dialog+6);
        SCAPI_State.minimap_width=U16(0x650330);SCAPI_State.minimap_height=U16(0x650332);
        SCAPI_State.minimap_scale=U16(0x650868);
    }
    if(SCAPI_State.details_active &&
       (detail_epoch!=SCAPI_State.epoch || detail_selection!=SCAPI_State.selection_generation))
        stage4_interrupt();
    u=((BYTE**)AT(0x684940))[0];
    if(SCAPI_State.pause || !SCAPI_State.active || SCAPI_State.selection_count!=1 ||
       !SCAPI_State.details_wait_ms || SCAPI_State.details_wait_ms>=0x80000000u ||
       !valid_unit(u) || *(WORD*)(u+0x64)>=228 || u[0x4c]>=12 || !panel_hud_visible(u))stage4_interrupt();
    else {
        panel_detail_stamp(u,values);stage4_attention(u,values,now);
#ifdef SCAPI_STAGE5
        if(SCAPI_State.details_ready)stage5_auto_capture(u,values,now);
#endif
    }
    stage4_view_sample();
#ifdef SCAPI_STAGE5
    stage5_nuclear_sample();
#endif
}
static DWORD stage4_minimap_point(int *x,int *y) {
    DWORD px,py;
    DWORD w=SCAPI_State.minimap_width,h=SCAPI_State.minimap_height,s=SCAPI_State.minimap_scale;
    /* Valid map image, not the surrounding padding. Input is a screen pixel
       inside the real minimap; no extra world-coordinate parameter exists. */
    if(!w || !h || w>128 || h>128 || !s || s>64 ||
       w*s!=SCAPI_State.width_tiles*32u || h*s!=SCAPI_State.height_tiles*32u)
        return SC_UNINITIALIZED;
    if(*x<SCAPI_State.minimap_left || *y<SCAPI_State.minimap_top) return SC_NOT_ON_SCREEN;
    px=(DWORD)*x-(DWORD)SCAPI_State.minimap_left;py=(DWORD)*y-(DWORD)SCAPI_State.minimap_top;
    if(px>=w || py>=h) return SC_NOT_ON_SCREEN;
    *x=px*s;*y=py*s;
    return point_on_map(*x,*y);
}
static DWORD panel_upgrade(DWORD owner,DWORD upgrade) {
    if(owner>=12 || upgrade>=61) return 0;
    return upgrade<46 ? U8(0x50d018+46*owner+upgrade) : U8(0x50f066+15*owner+upgrade);
}
static int panel_visible(BYTE *u) {
    BYTE *sprite=*(BYTE**)(u+12);
    DWORD mask=U32(0x65098c),status=*(DWORD*)(u+0xdc);
    if(!(sprite[12]&mask) || ((status&0x300) && !(*(DWORD*)(u+0xe4)&mask))) return 0;
    return operable_part(u);
}
/* HUD presence without the camera viewport requirement: the selected unit may
   keep its detail dwell and learned card while the camera looks elsewhere. */
static int panel_hud_visible(BYTE *u) {
    BYTE *sprite=*(BYTE**)(u+12);
    DWORD mask=U32(0x65098c),status=*(DWORD*)(u+0xdc);
    return (sprite[12]&mask) && !((status&0x300) && !(*(DWORD*)(u+0xe4)&mask));
}
static int panel_health_visible(BYTE *u) {
    DWORD type=*(WORD*)(u+0x64),maximum=U32(0x6abd38+type*4)/256u;
    if(!maximum) {maximum=(*(DWORD*)(u+8)+255u)/256u;if(!maximum)maximum=1;}
    /* Native stat-number renderer 4A4C7F..4A4CA4; no invented value cap. */
    return maximum<=9999 && U16(0x6adb78+type*2)<=9999 &&
        !(*(DWORD*)(u+0xdc)&0x04000000u);
}
static DWORD panel_weapon_kind(BYTE *u,int air) {
    DWORD type=*(WORD*)(u+0x64),w;
    /* Actual loaded tables, including map modifications. UI damage factors
       follow 418F00/418F60, not the upstream default type database. */
    w=air?U8(0x6ada90+type):U8(0x6ae418+type);
    if(!air && type==103 && !(*(DWORD*)(u+0xdc)&0x10)) w=130;
    if(w==130 && valid_unit(*(void**)(u+0x70))) {
        DWORD sub=*(WORD*)(*(BYTE**)(u+0x70)+0x64);
        if(sub<228) w=air?U8(0x6ada90+sub):U8(0x6ae418+sub);
    }
    return w;
}
static void panel_weapon(BYTE *u,int air,DWORD *weapon,DWORD *base,DWORD *bonus,DWORD *level) {
    DWORD type=*(WORD*)(u+0x64),owner=u[0x4c],w=panel_weapon_kind(u,air),factor,up;
    *weapon=w;*base=0;*bonus=0;*level=0;
    if(w>=130) return;
    factor=U8(0x6b0e28+w);
    if(type==32 || type==10 || type==65 || type==77) factor=2;
    if(type==58) factor=1;
    up=U8(0x6b1988+w);
    *level=panel_upgrade(owner,up);
    *base=U16(0x6b1e30+w*2)*factor;
    *bonus=U16(0x6b16e8+w*2)*factor*(*level);
}
static DWORD panel_visible_cost_stamp(DWORD type,BYTE *record,DWORD flags) {
    BYTE *table=*(BYTE**)AT(0x68d344),*label;
    WORD id=*(WORD*)(record+16),tech=*(WORD*)(record+14),offset;
    /* getTooltipInfo 4A1DBA..1E9B clears every cost for a disabled button.
       Only a displayed category-three cost contributes to this stamp. */
    if((flags&2) || tech>=44 || !(U8(0x6af338+type)&7) ||
       !readable(table,2) || !id || id>*(WORD*)table || !readable(table+id*2,2))return 0;
    offset=*(WORD*)(table+id*2);label=table+offset;
    if(!readable(label,2) || label[1]!=3)return 0;
    /* The native renderer skips zero cost fields at 4A22BC..4A22BF. */
    return U16(0x69f548+tech*2);
}
static void panel_detail_stamp(BYTE *u,DWORD *stamp) {
    DWORD type=*(WORD*)(u+0x64),hash=0,n=0;BYTE *root,*node;
    stamp[0]=type;stamp[1]=u[0x4c];stamp[2]=U8(0x6ae500+type);
    stamp[3]=((UnitValueFn)AT(0x43b220))(u)&255u;
    stamp[4]=panel_upgrade(u[0x4c],U8(0x6b00a0+type));
    panel_weapon(u,0,&stamp[5],&stamp[6],&stamp[7],&stamp[8]);
    panel_weapon(u,1,&stamp[9],&stamp[10],&stamp[11],&stamp[12]);
    /* A changed current own skill or newly visible cost restarts learning.
       Current energy and ordinary-command readiness do not restart it. */
#ifdef SCAPI_STAGE5
    hash=stage5_detail_card_stamp(u);
    if(u[0x4c]==U32(0x4f30c0))
        for(n=0;n<228;++n)hash=hash*33u+U8(0x4fefe4+228u*u[0x4c]+n);
#else
    if(u[0x4c]==U32(0x4f30c0) && (root=*(BYTE**)AT(0x68ca50))!=0 && readable(root,0x46)) {
        node=*(BYTE**)(root+0x42);
        while(node && n++<9 && readable(node,0x2a)) {
            BYTE *record=*(BYTE**)(node+0x26);
            if((*(DWORD*)(node+0x18)&8) && readable(record,20)) {
                hash=hash*33u+(DWORD)record+*(WORD*)(record+16);
                hash=hash*33u+panel_visible_cost_stamp(type,record,*(DWORD*)(node+0x18));
            }
            node=*(BYTE**)node;
        }
        /* Mission permission changes affect the integrated building list even
           when a submenu is not open. They require a fresh legal dwell. */
        for(n=0;n<228;++n)hash=hash*33u+U8(0x4fefe4+228u*u[0x4c]+n);
    }
#endif
    /* An unfinished zero-button building changes no command card on
       completion, so fold its lifecycle bit into the dwell stamp. */
    hash=hash*33u+(((U32(0x6b0358+type*4u)&1u) && !(*(DWORD*)(u+0xdc)&1u))?1u:0u);
    stamp[13]=hash;
}
static void stage4_fill_panel(BYTE *u,DWORD *values,ScapiState *out) {
    DWORD type=*(WORD*)(u+0x64),owner=u[0x4c],hp;
    memset(&out->panel_available,0,(BYTE*)(&out->panel_name_address+1)-(BYTE*)&out->panel_available);
    out->panel_owner=out->panel_visual=out->panel_has_health=0;
    out->panel_armor_bonus=out->panel_max_energy=out->panel_rescuable=0;
    out->panel_ground_upgrade_id=out->panel_air_upgrade_id=out->panel_armor_upgrade_id=61;
    out->panel_available=1;out->panel_owned=owner==U32(0x4f30c0);
    out->panel_rescuable=!out->panel_owned && stage4_rescuable_owner(owner);
    out->panel_type=type;
    out->panel_owner=owner;
    out->panel_visual=*(WORD*)(*(BYTE**)(u+12)+8);
    out->panel_has_health=panel_health_visible(u);
    hp=*(DWORD*)(u+8);out->panel_hp=(hp+255u)/256u;
    out->panel_max_hp=U32(0x6abd38+type*4)/256u;
    out->panel_has_shields=U8(0x6abb68+type)!=0;
    if(out->panel_has_shields) {
        out->panel_shields=*(DWORD*)(u+0x60)/256u;
        out->panel_max_shields=U16(0x6adb78+type*2);
        out->panel_shield_level=panel_upgrade(owner,15);
    }
    out->panel_has_energy=out->panel_owned && (U32(0x6b0358+type*4)&0x200000)!=0;
    if(out->panel_has_energy) {
        out->panel_energy=*(WORD*)(u+0xa2)/256u;
        out->panel_max_energy=((UnitValueFn)AT(0x433fe0))(u)/256u;
    }
    out->panel_name_address=(DWORD)((UnitNameFn)AT(0x425390))(type);
    out->panel_armor_level=panel_upgrade(owner,U8(0x6b00a0+type));
    if(!out->panel_max_hp) out->panel_max_hp=out->panel_hp?out->panel_hp:1;
    /* Private readout for comparing already-learned records. Public output
       still gates new values on completed learning; no raw mailbox endpoint. */
    out->panel_armor_base=values[2];out->panel_armor_bonus=values[3];
    out->panel_ground_weapon=values[5];out->panel_ground_base=values[6];
    out->panel_ground_bonus=values[7];out->panel_ground_level=values[8];
    out->panel_air_weapon=values[9];out->panel_air_base=values[10];
    out->panel_air_bonus=values[11];out->panel_air_level=values[12];
    out->panel_armor_upgrade_id=U8(0x6b00a0+type);
    if(values[5]<130)out->panel_ground_upgrade_id=U8(0x6b1988+values[5]);
    if(values[9]<130)out->panel_air_upgrade_id=U8(0x6b1988+values[9]);
 }
static DWORD stage4_observe(int learn) {
    BYTE *u;DWORD values[14],now=real_tick();
    (void)learn; /* Compatibility argument; observation never restarts attention. */
    SCAPI_State.panel_available=0;
    if(SCAPI_State.expected_selection!=SCAPI_State.selection_generation)return SC_STALE_SELECTION;
    if(SCAPI_State.selection_count!=1){stage4_interrupt();return SC_NO_SELECTION;}
    u=((BYTE**)AT(0x684940))[0];
    if(!valid_unit(u) || !panel_hud_visible(u)){stage4_interrupt();return SC_NO_SELECTABLE_UNIT;}
    if(*(WORD*)(u+0x64)>=228 || u[0x4c]>=12)return SC_UNINITIALIZED;
    if(!SCAPI_State.details_wait_ms || SCAPI_State.details_wait_ms>=0x80000000u)return SC_BAD_ARGUMENT;
    panel_detail_stamp(u,values);stage4_attention(u,values,now);
    stage4_fill_panel(u,values,&SCAPI_State);
#ifdef SCAPI_STAGE5
    if(SCAPI_State.details_ready)stage5_auto_capture(u,values,now);
#endif
    return SC_OK;
}
