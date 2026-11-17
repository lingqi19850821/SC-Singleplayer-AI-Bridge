#ifndef SCAPI_STAGE5_MINIMAP_PING_H
#define SCAPI_STAGE5_MINIMAP_PING_H
/* Target1.05 successful visible ping registration. Static evidence lives in
   evidence/minimap-ping-repair-20261114/static/. Never inspect a trigger
   instance or the undisplayed world-coordinate arguments to46BC80. */
static const BYTE stage5_minimap_ping_prefix[]={0x66,0xc7,0x86,0x54,0x03,0x65,0x00,0x00,0x00};
static int stage5_minimap_ping_abi_valid(void) {
    static const BYTE entry[]={0x53,0x8b,0xda,0x56,0x57,0x8b,0xc1,0x33,0xf6,0xba,0x48,0x03,0x65,0x00};
    static const BYTE active[]={0xc6,0x86,0x48,0x03,0x65,0x00,0x01};
    static const BYTE gate[]={0x56,0x8b,0xf1,0x80,0x3e,0x00,0x74,0x58,0xe8,0xe3,0x8e,0x00,0x00,
        0x3b,0x05,0xc0,0x30,0x4f,0x00,0x75,0x4b,0xe8,0xd6,0x8f,0x00,0x00,0x85,0xc0,0x75,0x42};
    static const BYTE trigger[]={0xe8,0xf0,0x25,0xfb,0xff};
    static const BYTE transmission[]={0xe8,0xa4,0x2f,0xfb,0xff};
    return readable(AT(0x46bc80),sizeof(entry)) && !memcmp(AT(0x46bc80),entry,sizeof(entry)) &&
        readable(AT(0x46bcc8),sizeof(active)) && !memcmp(AT(0x46bcc8),active,sizeof(active)) &&
        readable(AT(0x4b9630),sizeof(gate)) && !memcmp(AT(0x4b9630),gate,sizeof(gate)) &&
        readable(AT(0x4b968b),sizeof(trigger)) && !memcmp(AT(0x4b968b),trigger,sizeof(trigger)) &&
        readable(AT(0x4b8cd7),sizeof(transmission)) && !memcmp(AT(0x4b8cd7),transmission,sizeof(transmission));
}
static int stage5_minimap_ping_project(int origin_x,int origin_y,DWORD w,DWORD h,DWORD scale,
        DWORD tiles_x,DWORD tiles_y,int max_x,int max_y,int *target_x,int *target_y) {
    int view_w,view_h,x,y;
    if(!w || !h || w>128 || h>128 || !scale || scale>64 ||
       !tiles_x || !tiles_y || w*scale!=tiles_x*32u || h*scale!=tiles_y*32u ||
       max_x<0 || max_y<0 || (DWORD)max_x>=w*scale || (DWORD)max_y>=h*scale ||
       origin_x< -64 || origin_y< -64 || origin_x>=(int)w-64 || origin_y>=(int)h-64)return 0;
    view_w=(int)(w*scale)-max_x;view_h=(int)(h*scale)-max_y;
    /* Actual display center is origin+64, not originalWorld/scale. Small
       maps quantize the original point at tile precision before enlarging. */
    x=(origin_x+64)*(int)scale-view_w/2;y=(origin_y+64)*(int)scale-view_h/2;
    if(x<0)x=0;if(y<0)y=0;if(x>max_x)x=max_x;if(y>max_y)y=max_y;
    *target_x=x&~7;*target_y=y&~7;return 1;
}
static void stage5_minimap_ping_capture(ScapiHookRegisters *r) {
    DWORD sp=r->saved_esp+16,caller,w,h,scale;BYTE *slot;int x,y;
    /* Three pushed registers put the native return at hookESP+12. These
       verified script returns create local display slots; notification
       already has its existing typed event. No multiplayer-ping claim. */
    if(sp<r->saved_esp || !readable((void*)(uintptr_t)sp,4)) {
        stage5_notification_fault(SCAPI_EVENT_E_ARGUMENT);return;
    }
    caller=*(DWORD*)(uintptr_t)sp;
    if(caller!=(DWORD)(uintptr_t)AT(0x4b9690) && caller!=(DWORD)(uintptr_t)AT(0x4b8cdc))return;
    if(r->esi>240 || (r->esi&15)) {stage5_notification_fault(SCAPI_EVENT_E_ARGUMENT);return;}
    slot=AT(0x650348)+r->esi;
    if(!readable(slot,16) || slot[0]!=1) {stage5_notification_fault(SCAPI_EVENT_E_STATE);return;}
    w=U16(0x650330);h=U16(0x650332);scale=U16(0x650868);
    if(!stage5_minimap_ping_project(*(LONG*)(slot+4),*(LONG*)(slot+8),w,h,scale,
            U16(0x4fef3c),U16(0x4fef3e),I32(0x51bcd0),I32(0x51bcac),&x,&y)) {
        stage5_notification_fault(SCAPI_EVENT_E_STATE);return;
    }
    /* Once per successful creation. Full native slots skip the hook;
       render frames do not publish. No raw world/owner/location/caller data. */
    stage4_event_append(S5_EVENT_MINIMAP_PING,x,y,0,0,0);
}
#endif
