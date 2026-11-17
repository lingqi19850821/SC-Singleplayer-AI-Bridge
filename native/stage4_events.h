#ifndef SCAPI_STAGE4_EVENTS_H
#define SCAPI_STAGE4_EVENTS_H
#include "stage4_hooks.h"
#include "stage4_event_transport.h"

/* Private capture configuration. Capacity must be supplied by the developer;
   no default queue, retention limit, disk I/O or model wait in this thread. */
static ScapiCodeHook text_hook,attack_hook,research_hook,upgrade_hook;
#ifdef SCAPI_STAGE5
static ScapiCodeHook notification_hook,weapon_damage_hook,weapon_melee_hook,weapon_bullet_hook,nuclear_warning_hook,minimap_ping_hook;
#endif
static ScapiEventHeader *event_stream;

#ifdef SCAPI_STAGE5
static DWORD stage5_notification_caller(ScapiHookRegisters *r) {
    DWORD sp=r->saved_esp+4;
    /* Existing hook 4A96DD: 4A9600 has only pushed ESI and EDI, so its
       native return address is original ESP+8. No caller stack is exported.
       Ordinary unit-ready4480F0 passes the completed entity. Building-ready
       448550 may instead pass the worker or parent building: see the
       target-specific saved-register decoding below. */
    if(sp<r->saved_esp || !readable((void*)(uintptr_t)sp,12))return 0;
    return *(DWORD*)(uintptr_t)(sp+8);
}
static DWORD stage5_completion_caller(DWORD caller) {
    return caller==(DWORD)(uintptr_t)AT(0x448170) ||
           caller==(DWORD)(uintptr_t)AT(0x4485db) ||
           caller==(DWORD)(uintptr_t)AT(0x448630);
}
static int stage5_completion_abi_valid(void) {
    static const BYTE entry[]={0x56,0x57,0x8b,0xf9};
    static const BYTE scv[]={0x8b,0xce,0xc7,0x87,0x80,0,0,0,0,0,0,0,0xe8,0x9d,0x20,3,0};
    static const BYTE addon[]={0xf6,0x86,0xdc,0,0,0,1,0x74,0x5c,0x8b,0xcf,0xe8,0x86,0x3f,1,0};
    static const BYTE zerg[]={0x8b,0xce,0xc6,0x46,0x4e,6,0xe8,0xcc,0xc7,0,0};
    return readable(AT(0x448550),sizeof(entry)) && !memcmp(AT(0x448550),entry,sizeof(entry)) &&
           readable(AT(0x4164a2),sizeof(scv)) && !memcmp(AT(0x4164a2),scv,sizeof(scv)) &&
           readable(AT(0x4345ba),sizeof(addon)) && !memcmp(AT(0x4345ba),addon,sizeof(addon)) &&
           readable(AT(0x43bd79),sizeof(zerg)) && !memcmp(AT(0x43bd79),zerg,sizeof(zerg));
}
static DWORD stage5_completed_entity(ScapiHookRegisters *r,DWORD caller,BYTE **entity) {
    DWORD sp=r->saved_esp+4,outer;int zerg_finishing=0;BYTE *u=(BYTE*)(uintptr_t)r->esi;
    if(caller!=(DWORD)(uintptr_t)AT(0x448170)) {
        /* 448550 pushes ESI then EDI. At4A96DD, original ESP contains
           saved EDI, saved ESI, return4A9600, three arguments, saved
           outer EDI(+24), outer ESI(+28), return448550(+32).
           4164A4 already clears completed-building+80 before notification;
           its saved EDI still identifies the building. 434540 holds the
           completed addon in ESI but notifies with parent EDI. 43BD79
           notifies with the finishing Zerg building itself. No scan or
           fallback to the actor can safely identify an unknown caller. */
        if(sp<r->saved_esp || !readable((void*)(uintptr_t)sp,36))return SCAPI_EVENT_E_ARGUMENT;
        outer=*(DWORD*)(uintptr_t)(sp+32);
        if(outer==(DWORD)(uintptr_t)AT(0x4164b3))u=*(BYTE**)(uintptr_t)(sp+24);
        else if(outer==(DWORD)(uintptr_t)AT(0x4345ca))u=*(BYTE**)(uintptr_t)(sp+28);
        else if(outer==(DWORD)(uintptr_t)AT(0x43bd84))zerg_finishing=1;
        else return SCAPI_EVENT_E_STATE;
        if(!valid_unit(u) || !(u[0xdc]&2))return SCAPI_EVENT_E_ARGUMENT;
        if(zerg_finishing) {
            /* Target43BD7B sets order-state6 before this native advisor
               notification. Completed bit0 is only set later, after the
               state6 animation signal, via43BE20->4221B0->421F34/49.
               Require the proven call/state, not a future completed bit. */
            if(u[0x4e]!=6)return SCAPI_EVENT_E_STATE;
        } else if(!(u[0xdc]&1))return SCAPI_EVENT_E_ARGUMENT;
    }
    if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0))return SCAPI_EVENT_E_ARGUMENT;
    *entity=u;return SCAPI_EVENT_OK;
}
static void stage5_notification_fault(DWORD code) {
    SCAPI_State.events_fault=code;scapi_event_mark_invalid(event_stream,code);
}
static DWORD stage5_completion_name_type(DWORD kind,const BYTE **name,DWORD *bytes) {
    DWORD n=0;const BYTE *p;
    if(kind>=228)return SCAPI_EVENT_E_ARGUMENT;
    p=(const BYTE*)((UnitNameFn)AT(0x425390))(kind);
    /* Reuse the event ring's existing218-byte capacity. A terminator must
       exist within capacity+1; never publish a truncated or previous name. */
    for(n=0;n<=SCAPI_EVENT_TEXT_BYTES;++n) {
        if(!readable(p,n+1))return SCAPI_EVENT_E_ARGUMENT;
        if(!p[n]) {
            if(!n)return SCAPI_EVENT_E_ARGUMENT;
            *name=p;*bytes=n;return SCAPI_EVENT_OK;
        }
    }
    return SCAPI_EVENT_E_OVERSIZE;
}
static DWORD stage5_completion_name(BYTE *u,const BYTE **name,DWORD *bytes) {
    return stage5_completion_name_type(*(WORD*)(u+0x64),name,bytes);
}
static DWORD stage5_zerg_completion_name(BYTE *u,const BYTE **name,DWORD *bytes) {
    DWORD index=u[0xa4],kind;
    /* Only called after the exact43BD84/state6 native notification gate.
       43BC09..43BC41 chooses these five morph targets from the active queue;
       43BDED applies that target to unit+64 AFTER the advisor notification.
       Ordinary building construction uses the current type, as native does.
       No other caller derives a name from an unfinished production queue. */
    if(index>=5)return SCAPI_EVENT_E_ARGUMENT;
    kind=*(WORD*)(u+0x98+index*2u);
    if(kind!=132 && kind!=133 && kind!=137 && kind!=144 && kind!=146)
        return stage5_completion_name(u,name,bytes);
    return stage5_completion_name_type(kind,name,bytes);
}
#endif

static void stage4_events_flags(void) {
    SCAPI_State.event_hooks=(text_hook.installed?1u:0u)|(attack_hook.installed?2u:0u)|
                           (research_hook.installed?4u:0u)|(upgrade_hook.installed?8u:0u)
#ifdef SCAPI_STAGE5
                           |(notification_hook.installed?16u:0u)|(weapon_damage_hook.installed?32u:0u)|
                           (weapon_melee_hook.installed?64u:0u)|(weapon_bullet_hook.installed?128u:0u)|
                           (nuclear_warning_hook.installed?256u:0u)|(minimap_ping_hook.installed?512u:0u)
#endif
                           ;
}
static void stage4_events_lifecycle(void) {
    if(event_stream && SCAPI_State.events_enabled &&
       (!SCAPI_State.active || SCAPI_State.epoch!=event_stream->epoch)) {
        SCAPI_State.events_enabled=0;
        SCAPI_State.events_fault=SCAPI_EVENT_E_EPOCH;
        scapi_event_mark_invalid(event_stream,SCAPI_EVENT_E_EPOCH);
    }
}
static int stage4_event_ready(void) {
    if(GetCurrentThreadId()!=SCAPI_State.ui_thread || !SCAPI_State.events_enabled)return 0;
    sample_lifecycle();
    stage4_events_lifecycle();
    return SCAPI_State.events_enabled!=0;
}
static DWORD stage4_event_append(DWORD kind,int x,int y,DWORD color,const BYTE *text,DWORD count) {
    ScapiEventInput in;DWORD result;
    memset(&in,0,sizeof(in));in.epoch=event_stream->epoch;
    in.tick=real_tick();in.type=kind;in.x=x;in.y=y;in.color=color;
    in.text=text;in.text_bytes=count;
    result=scapi_event_append(event_stream,&in,0);
    if(result)SCAPI_State.events_fault=result;
    return result;
}
static DWORD stage4_publish(DWORD kind,int x,int y,DWORD token) {
    if(!stage4_event_ready())return SCAPI_EVENT_E_EPOCH;
    return stage4_event_append(kind,x,y,token,0,0);
}
#ifdef SCAPI_STAGE5
#include "stage5_weapon_feedback.h"
#include "stage5_minimap_ping.h"
#endif
static void __cdecl stage4_event_callback(DWORD kind,ScapiHookRegisters *r) {
    if(!stage4_event_ready())return;
#ifdef SCAPI_STAGE5
    if(kind==S5_EVENT_MINIMAP_PING) {stage5_minimap_ping_capture(r);return;}
    if(kind==S5_EVENT_NUCLEAR_WARNING) {
        /* 46966B is after the real local-player/UI-context display gates.
           The only permitted caller is NukeLaunch's actual warning display;
           this function has pushed nothing before the hook. No missile,
           target or owner coordinates are copied into a global warning. */
        DWORD sp=r->saved_esp+4;
        if(sp<r->saved_esp || !readable((void*)(uintptr_t)sp,4)) {
            stage5_notification_fault(SCAPI_EVENT_E_ARGUMENT);return;
        }
        if(*(DWORD*)(uintptr_t)sp==(DWORD)(uintptr_t)AT(0x435250))
            stage4_event_append(S5_EVENT_NUCLEAR_WARNING,0,0,0,0,0);
        return;
    }
    if(kind==S5_HOOK_WEAPON_DAMAGE || kind==S5_HOOK_WEAPON_MELEE || kind==S5_HOOK_WEAPON_BULLET) {
        stage5_weapon_impact(kind,r);return;
    }
    if(kind==S5_EVENT_NOTIFICATION) {
        /* Native 4A9600 has written BOTH Space target coordinates at 4A96DD.
           Capture now; later notifications overwrite the same globals.
           No unit handle, caller stack or hidden project data is exported. */
        int x=I32(0x4ffa9c),y=I32(0x4ffaa0);
        DWORD caller=stage5_notification_caller(r),type=kind,color=0,bytes=0,result;
        const BYTE *name=NULL;BYTE *u=(BYTE*)(uintptr_t)r->esi;
        if(stage5_completion_caller(caller)) {
            if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0))return;
            result=stage5_completed_entity(r,caller,&u);
            if(result){stage5_notification_fault(result);return;}
            if(caller!=(DWORD)(uintptr_t)AT(0x448170) &&
               *(DWORD*)(uintptr_t)(r->saved_esp+4+32)==(DWORD)(uintptr_t)AT(0x43bd84))
                result=stage5_zerg_completion_name(u,&name,&bytes);
            else result=stage5_completion_name(u,&name,&bytes);
            if(result){stage5_notification_fault(result);return;}
            color=caller==(DWORD)(uintptr_t)AT(0x448170)?1:2;
        } else if(caller==(DWORD)(uintptr_t)AT(0x448886) ||
                  caller==(DWORD)(uintptr_t)AT(0x4488f8)) {
            if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0))return;
            type=caller==(DWORD)(uintptr_t)AT(0x448886)?S4_EVENT_RESEARCH:S4_EVENT_UPGRADE;
            color=1; /* for type6/7 this marks a real native camera target */
        }
        if(x<0 || y<0 || SCAPI_State.camera_max_x<0 || SCAPI_State.camera_max_y<0) {
            stage5_notification_fault(SCAPI_EVENT_E_STATE);return;
        }
        if(x>SCAPI_State.camera_max_x)x=SCAPI_State.camera_max_x;
        if(y>SCAPI_State.camera_max_y)y=SCAPI_State.camera_max_y;
        stage4_event_append(type,x&~7,y&~7,color,name,bytes);return;
    }
#endif
    if(kind==S4_EVENT_TEXT) {
        /* At 469480 the actual display-copy count is EBX and line start EBP.
           The pushed source/count are already on the original stack. Never
           read other trigger strings, future messages or the whole STR table. */
        DWORD n=0,copy=r->ebx;const BYTE *p=(const BYTE*)(uintptr_t)r->ebp;
        DWORD original_esp=r->saved_esp+4;
        BYTE color;
        if(!copy || copy>218 || !readable(p,copy) ||
           !readable((void*)(uintptr_t)(original_esp+0x1b),1)) {
            SCAPI_State.events_fault=SCAPI_EVENT_E_ARGUMENT;
            scapi_event_mark_invalid(event_stream,SCAPI_EVENT_E_ARGUMENT);return;
        }
        while(n+1<copy && p[n])++n;
        color=*(BYTE*)(uintptr_t)(original_esp+0x1b);
        stage4_event_append(kind,0,0,color,p,n);
    } else if(kind==S4_EVENT_ATTACK) {
        /* This site is after native own-unit/global/per-unit alert gates.
           Convert the alerted own unit to actual integer minimap precision.
           No attacker, offscreen attributes or exact world position exported. */
        BYTE *u=(BYTE*)(uintptr_t)r->edi,*sp;int x,y;
        DWORD s=SCAPI_State.minimap_scale,w=SCAPI_State.minimap_width,h=SCAPI_State.minimap_height;
        int left=SCAPI_State.minimap_left,top=SCAPI_State.minimap_top;
        if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0) || !s || !w || !h || w>128 || h>128 ||
           w*s!=SCAPI_State.width_tiles*32u || h*s!=SCAPI_State.height_tiles*32u)return;
        sp=*(BYTE**)(u+12);x=*(short*)(sp+20);y=*(short*)(sp+22);
        x=left+x/(int)s;y=top+y/(int)s;
        if(x<left || y<top || x>=left+(int)w || y>=top+(int)h)return;
        stage4_event_append(kind,x,y,0,0,0);
    } else if(kind==S4_EVENT_RESEARCH || kind==S4_EVENT_UPGRADE) {
        /* Actual research/upgrade advisor notifications at 44883A/4488AA,
           after their local-owner gate; EDI is the completing building.
           No project id, skill effect or offscreen unit data is published. */
        BYTE *u=(BYTE*)(uintptr_t)r->edi;
        if(!valid_unit(u) || u[0x4c]!=U32(0x4f30c0))return;
#ifdef SCAPI_STAGE5
        /* Stage5 records exactly one typed event at4A96DD after both native
           Space coordinates are written. These existing early hooks detect
           the4A9600 global suppression precondition instead of emitting a
           duplicate zero-position event. Native source pushes targetflag1
           and passes this verified completing building directly in ECX. */
        if(U32(0x68d1e0))stage5_notification_fault(SCAPI_EVENT_E_STATE);
        return;
#else
        stage4_event_append(kind,0,0,0,0,0);
#endif
    }
}
static DWORD stage4_events_stop(void) {
    DWORD result=SC_OK;
    SCAPI_State.events_enabled=0;
    if(text_hook.installed && scapi_hook_remove(&text_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(attack_hook.installed && scapi_hook_remove(&attack_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(research_hook.installed && scapi_hook_remove(&research_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(upgrade_hook.installed && scapi_hook_remove(&upgrade_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
#ifdef SCAPI_STAGE5
    if(minimap_ping_hook.installed && scapi_hook_remove(&minimap_ping_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(nuclear_warning_hook.installed && scapi_hook_remove(&nuclear_warning_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    stage5_nuclear_reset();
    if(notification_hook.installed && scapi_hook_remove(&notification_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(weapon_damage_hook.installed && scapi_hook_remove(&weapon_damage_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(weapon_melee_hook.installed && scapi_hook_remove(&weapon_melee_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
    if(weapon_bullet_hook.installed && scapi_hook_remove(&weapon_bullet_hook)!=SC_HOOK_OK)result=SC_UNINITIALIZED;
#endif
    stage4_events_flags();
    return result;
}
static DWORD stage4_events_request(void) {
    DWORD result,capacity;size_t bytes;
    static const BYTE text_prefix[]={0x8d,0x04,0x55,0x00,0xe6,0x64,0x00};
    static const BYTE attack_prefix[]={0xc7,0x05,0xa0,0xca,0x63,0x00,0x64,0x00,0x00,0x00};
    static const BYTE research_prefix[]={0x25,0xff,0x00,0x00,0x00};
#ifdef SCAPI_STAGE5
    static const BYTE notification_prefix[]={0xc6,0x86,0x8a,0x00,0x00,0x00,0x08};
    static const BYTE nuclear_warning_prefix[]={0x68,0xda,0x00,0x00,0x00};
#endif
    if(SCAPI_State.operation==SC_CAPTURE_STOP)return stage4_events_stop();
    if(SCAPI_State.operation==SC_CAPTURE_ACK) {
        if(!event_stream || (DWORD)SCAPI_State.x<1u)return SC_BAD_ARGUMENT;
        result=scapi_event_ack(event_stream,(DWORD)SCAPI_State.x);
        return result?SC_BAD_ARGUMENT:SC_OK;
    }
    if(SCAPI_State.operation!=SC_CAPTURE_START)return SC_BAD_OP;
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer)return SC_NO_GAME;
    if(SCAPI_State.expected_epoch!=SCAPI_State.epoch)return SC_STALE_EPOCH;
    if(SCAPI_State.events_enabled || SCAPI_State.event_hooks)return SC_BAD_ARGUMENT;
    if(SCAPI_State.x<1)return SC_BAD_ARGUMENT;
    capacity=(DWORD)SCAPI_State.x;bytes=scapi_event_required_bytes(capacity);
    if(!bytes || bytes>0x7fffffffu)return SC_BAD_ARGUMENT;
    if(event_stream && (event_stream->capacity!=capacity ||
                       event_stream->write_seq!=event_stream->ack_seq))return SC_BAD_ARGUMENT;
    /* All sites verified before modifying either one; this point is the
       established UI-loop rendezvous outside these routines. */
    if(!readable(AT(0x469480),sizeof(text_prefix)) ||
       memcmp(AT(0x469480),text_prefix,sizeof(text_prefix)) ||
       !readable(AT(0x4486f1),sizeof(attack_prefix)) ||
       memcmp(AT(0x4486f1),attack_prefix,sizeof(attack_prefix)))return SC_UNINITIALIZED;
#ifdef SCAPI_STAGE5
    if(!readable(AT(0x4a96dd),sizeof(notification_prefix)) ||
       memcmp(AT(0x4a96dd),notification_prefix,sizeof(notification_prefix)) ||
       !stage5_completion_abi_valid() || !stage5_weapon_abi_valid() ||
       !stage5_nuclear_abi_valid() || !stage5_minimap_ping_abi_valid() ||
       !readable(AT(0x46bcf6),sizeof(stage5_minimap_ping_prefix)) ||
       memcmp(AT(0x46bcf6),stage5_minimap_ping_prefix,sizeof(stage5_minimap_ping_prefix)) ||
       !readable(AT(0x46966b),sizeof(nuclear_warning_prefix)) ||
       memcmp(AT(0x46966b),nuclear_warning_prefix,sizeof(nuclear_warning_prefix)))return SC_UNINITIALIZED;
#endif
    if(!event_stream) {
        event_stream=VirtualAlloc(NULL,bytes,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
        if(!event_stream)return SC_UNINITIALIZED;
        SCAPI_State.events_address=(DWORD)(uintptr_t)event_stream;
        SCAPI_State.events_bytes=bytes;SCAPI_State.events_capacity=capacity;
    }
    if(scapi_event_init(event_stream,bytes,capacity,SCAPI_State.epoch)!=SCAPI_EVENT_OK)return SC_BAD_ARGUMENT;
    SCAPI_State.events_epoch=SCAPI_State.epoch;SCAPI_State.events_fault=0;
    ++SCAPI_State.events_generation;
    result=scapi_hook_install(&text_hook,AT(0x469480),text_prefix,sizeof(text_prefix),S4_EVENT_TEXT,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&attack_hook,AT(0x4486f1),attack_prefix,sizeof(attack_prefix),S4_EVENT_ATTACK,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&research_hook,AT(0x44883a),research_prefix,sizeof(research_prefix),S4_EVENT_RESEARCH,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&upgrade_hook,AT(0x4488aa),research_prefix,sizeof(research_prefix),S4_EVENT_UPGRADE,stage4_event_callback);
#ifdef SCAPI_STAGE5
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&notification_hook,AT(0x4a96dd),notification_prefix,sizeof(notification_prefix),S5_EVENT_NOTIFICATION,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&weapon_damage_hook,AT(0x41ab52),stage5_weapon_damage_prefix,sizeof(stage5_weapon_damage_prefix),S5_HOOK_WEAPON_DAMAGE,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&weapon_melee_hook,AT(0x41a0e0),stage5_weapon_halluc_prefix,sizeof(stage5_weapon_halluc_prefix),S5_HOOK_WEAPON_MELEE,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&weapon_bullet_hook,AT(0x41a7c3),stage5_weapon_halluc_prefix,sizeof(stage5_weapon_halluc_prefix),S5_HOOK_WEAPON_BULLET,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&nuclear_warning_hook,AT(0x46966b),nuclear_warning_prefix,sizeof(nuclear_warning_prefix),S5_EVENT_NUCLEAR_WARNING,stage4_event_callback);
    if(result==SC_HOOK_OK)
        result=scapi_hook_install(&minimap_ping_hook,AT(0x46bcf6),stage5_minimap_ping_prefix,sizeof(stage5_minimap_ping_prefix),S5_EVENT_MINIMAP_PING,stage4_event_callback);
#endif
    stage4_events_flags();
    if(result!=SC_HOOK_OK) {stage4_events_stop();SCAPI_State.events_fault=0x100u+result;return SC_UNINITIALIZED;}
    stage4_contacts_restart();SCAPI_State.events_enabled=1;return SC_OK;
}
#endif
