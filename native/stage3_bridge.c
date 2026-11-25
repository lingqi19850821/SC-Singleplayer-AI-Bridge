/* Target-specific development adapter. No disk game changes, no worker-thread
   game calls, no global units exported. Every engine action runs at a measured
   UI-thread frame-loop call site. Static addresses are independently mapped. */
#include "stage3_protocol.h"
#include <stdint.h>
#include <string.h>

#define FAST __attribute__((fastcall))
typedef DWORD (WINAPI *TickFn)(void);
typedef void (FAST *CameraFn)(int,int);
typedef int (FAST *PointFn)(int,int);
typedef void *(FAST *HitFn)(int,int);
typedef void (FAST *SelectFn)(int,void **,int,int);
typedef void (FAST *TargetFn)(int,int,void*,unsigned,int,int);
typedef unsigned (FAST *EncodeFn)(void*);
typedef void (FAST *QueueFn)(const void*,unsigned);
typedef void (FAST *SendSelectionFn)(unsigned,void**);
typedef void **(FAST *RectQueryFn)(const short*);
typedef int (FAST *FilterSelectionFn)(void**,void**,void*);
typedef int (FAST *MergeSelectionFn)(void**,void**,int,void*);
typedef int (FAST *SelectableFn)(void*);
typedef void (FAST *PopQueryFn)(void**);
typedef DWORD (FAST *UnitValueFn)(void*);
typedef void *(FAST *UnitNameFn)(unsigned);

__declspec(dllexport) ScapiState SCAPI_State = {
    SCAPI_MAGIC, SCAPI_VERSION, sizeof(ScapiState)
};
static TickFn real_tick;
static volatile LONG in_site;
static BYTE *game;
static DWORD previous_active, previous_pointer, previous_counter;
static void *previous_selection[12];
static BYTE previous_unique[12];
static DWORD previous_count;
static DWORD original_iat_protection;
static int action_clock_started;
static DWORD WINAPI hooked_tick(void);
#ifdef SCAPI_STAGE5
static void stage5_sample(void);
static void stage5_menu_sample(void);
static void stage5_terrain_sample(void);
static DWORD stage5_request(void);
static void stage5_auto_capture(BYTE*,DWORD*,DWORD);
static DWORD stage5_detail_card_stamp(BYTE*);
static void stage5_nuclear_sample(void);
static void stage5_nuclear_reset(void);
static int stage5_nuclear_abi_valid(void);
#include "stage5_rate.h"
#endif
#ifdef SCAPI_STAGE4
static void stage4_sample(void);
static void stage4_interrupt(void);
static DWORD stage4_observe(int learn);
static DWORD stage4_minimap_point(int *x,int *y);
static void stage4_events_lifecycle(void);
static DWORD stage4_events_stop(void);
static DWORD stage4_events_request(void);
static void stage4_view_sample(void);
static void stage4_view_reset(void);
static DWORD stage4_view(void);
static DWORD stage4_publish(DWORD kind,int x,int y,DWORD token);
static void stage4_contacts_restart(void);
#endif

#define AT(va) (game + ((DWORD)(va) - 0x400000u))
#define U8(va) (*(volatile BYTE*)AT(va))
#define U16(va) (*(volatile WORD*)AT(va))
#define U32(va) (*(volatile DWORD*)AT(va))
#define I32(va) (*(volatile LONG*)AT(va))

#ifdef SCAPI_STAGE4
enum { S4_EVENT_TEXT=1,S4_EVENT_ATTACK=2,S4_EVENT_CONTACT=3,
       S4_EVENT_STALE=4,S4_EVENT_IN_VIEW=5,S4_EVENT_RESEARCH=6,S4_EVENT_UPGRADE=7,S5_EVENT_NOTIFICATION=8,
       S5_EVENT_WEAPON=9,S5_EVENT_NUCLEAR_MARKER=10,
       S5_EVENT_NUCLEAR_STALE=11,S5_EVENT_NUCLEAR_WARNING=12,S5_EVENT_MINIMAP_PING=13,
       S5_EVENT_ALLIED_CONTACT=14,S5_EVENT_RESCUABLE_CONTACT=15,S5_EVENT_NEUTRAL_CONTACT=16 };
#endif

static int readable(const void *p, SIZE_T n) {
    MEMORY_BASIC_INFORMATION m;
    const BYTE *q=(const BYTE*)p;
    if (!p || !n || (uintptr_t)p + n < (uintptr_t)p) return 0;
    while (n) {
        SIZE_T part;
        if (!VirtualQuery(q,&m,sizeof(m)) || m.State!=MEM_COMMIT ||
            (m.Protect & (PAGE_NOACCESS|PAGE_GUARD))) return 0;
        part=(BYTE*)m.BaseAddress + m.RegionSize-q;
        if (part>=n) return 1;
        if (!part) return 0;
        q+=part; n-=part;
    }
    return 1;
}
static int valid_unit(void *p) {
    uintptr_t v=(uintptr_t)p, first=(uintptr_t)AT(0x5b2e68);
    BYTE *u=(BYTE*)p;
    if (v<first || v-first>=1700u*0x148u || (v-first)%0x148u) return 0;
    if (!readable(p,0x148) || !readable(*(void**)(u+0x0c),0x24)) return 0;
    return !(u[0x4d]==0 && u[0x4e]==1);
}
#ifdef SCAPI_VISIBLE_PHASE_CANDIDATE
#include "stage5_visible_phase.h"
#endif
static void sample_lifecycle(void) {
    DWORD a=U8(0x63c548), p=U32(0x63c568), c=U32(0x4fefa4);
    if (a!=previous_active || p!=previous_pointer ||
        (a && previous_active && c<previous_counter)) {
        ++SCAPI_State.epoch;
        ++SCAPI_State.selection_generation;
    }
    previous_active=a; previous_pointer=p; previous_counter=c;
    SCAPI_State.active=a; SCAPI_State.tile_pointer=p;
    SCAPI_State.raw_counter=c; SCAPI_State.pause=U32(0x653940);
}
static void sample(void) {
    DWORD i,n=0,changed=0;
    void **sel=(void**)AT(0x684940);
    sample_lifecycle();
    SCAPI_State.camera_x=I32(0x51bce4); SCAPI_State.camera_y=I32(0x51bce0);
    SCAPI_State.camera_max_x=I32(0x51bcd0); SCAPI_State.camera_max_y=I32(0x51bcac);
    SCAPI_State.width_tiles=U16(0x4fef3c); SCAPI_State.height_tiles=U16(0x4fef3e);
    for (i=0;i<12;++i) {
        BYTE uniq=valid_unit(sel[i]) ? *((BYTE*)sel[i]+0xa5) : 0;
        if (sel[i]) ++n;
        if (sel[i]!=previous_selection[i] || uniq!=previous_unique[i]) changed=1;
        previous_selection[i]=sel[i]; previous_unique[i]=uniq;
    }
    if (changed || n!=previous_count) ++SCAPI_State.selection_generation;
    previous_count=n; SCAPI_State.selection_count=n;
#ifdef SCAPI_STAGE4
    stage4_sample();
    stage4_events_lifecycle();
#endif
#ifdef SCAPI_STAGE5
    stage5_sample();
    stage5_menu_sample();
    stage5_terrain_sample();
#endif
}
static DWORD game_ready(void) {
    if (!SCAPI_State.active || !SCAPI_State.tile_pointer) return SC_NO_GAME;
    if (SCAPI_State.expected_epoch!=SCAPI_State.epoch) return SC_STALE_EPOCH;
    if (SCAPI_State.pause) {
#ifdef SCAPI_STAGE4
        /* The observed scripted pause also sets the control flag. The current
           visible scene remains observable while player actions stay gated. */
        if ((SCAPI_State.operation!=SC_OBSERVE_VIEW
#ifdef SCAPI_STAGE5
             && SCAPI_State.operation!=SC_TERRAIN_VIEW && SCAPI_State.operation!=SC_RESOURCE_SITES
#endif
            ) || !U8(0x6b0cf8)) return SC_PAUSED;
#else
        return SC_PAUSED;
#endif
    }
    if (!SCAPI_State.width_tiles || !SCAPI_State.height_tiles ||
        !U32(0x51bcdc) || !U32(0x51bc78)) return SC_UNINITIALIZED;
    return SC_OK;
}
static DWORD point_on_map(int x,int y) {
    if (x<0 || y<0 || (DWORD)x>=SCAPI_State.width_tiles*32u ||
        (DWORD)y>=SCAPI_State.height_tiles*32u) return SC_OUTSIDE_MAP;
    return SC_OK;
}
static DWORD point_on_screen(int x,int y) {
    int sx=x-SCAPI_State.camera_x, sy=y-SCAPI_State.camera_y;
    /* Dimensions derive from the engine's own camera clamp, not an invented
       bottom panel rectangle. Pixel mask below handles actual UI overlap. */
    int w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    int h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    if (w<=0 || h<=0 || sx<0 || sy<0 || sx>=w || sy>=h) return SC_NOT_ON_SCREEN;
    if (!U32(0x6b28dc) || I32(0x63b364)<0 || I32(0x63b310)<=0)
        return SC_UNINITIALIZED;
    return ((PointFn)AT(0x43f2b0))(sx,sy) ? SC_UI_BLOCKED : SC_OK;
}
static int is_player_action(DWORD op) {
    return op==SC_CAMERA || op==SC_SELECT_POINT || op==SC_MOVE_SELECTED ||
           op==SC_SELECT_BOX || op==SC_SELECT_SAME
#ifdef SCAPI_STAGE4
           || op==SC_MOVE_MINIMAP
#endif
#ifdef SCAPI_STAGE5
           || (op>=SC_COMMAND_BUTTON && op<=SC_SAVE_VIEW)
#endif
           ;
}
static int action_due(DWORD now) {
#ifdef SCAPI_STAGE5
    (void)now;return stage5_rate_due();
#else
    return !action_clock_started ||
        (DWORD)(now-SCAPI_State.last_action_tick)>=SCAPI_State.action_interval_ms;
#endif
}
/* Native rectangle queries already enforce geometry; only the operable-view
   mask is added here. Target geometry table operands are retained in the
   static evidence; these dimensions never become playable observations. */
static int operable_part(void *unit) {
    BYTE *u,*sprite;
    short *extent;
    int x,y,l,t,r,b,px,py;
    int w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    int h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    if (!valid_unit(unit)) return 0;
    u=(BYTE*)unit;
    sprite=*(BYTE**)(u+0x0c);
    extent=(short*)(AT(0x6af898)+*(unsigned short*)(u+0x64)*8u);
    if (!readable(extent,8)) return 0;
    x=*(short*)(sprite+0x14); y=*(short*)(sprite+0x16);
    if (point_on_screen(x,y)==SC_OK) return 1;
    l=x-extent[0]-SCAPI_State.camera_x;
    t=y-extent[1]-SCAPI_State.camera_y;
    r=x+extent[2]-SCAPI_State.camera_x;
    b=y+extent[3]-SCAPI_State.camera_y;
    if (l<0) l=0; if(t<0) t=0;
    if (r>=w) r=w-1; if(b>=h) b=h-1;
    for(py=t;py<=b;++py) for(px=l;px<=r;++px)
        if (point_on_screen(px+SCAPI_State.camera_x,py+SCAPI_State.camera_y)==SC_OK) return 1;
    return 0;
}
static DWORD apply_selection(int count,void **list,int replacement) {
    int i;
    if (count<0 || count>12) return SC_UNINITIALIZED;
    for(i=0;i<count;++i) if(!valid_unit(list[i])) return SC_NO_SELECTABLE_UNIT;
    SCAPI_State.before_queue=U32(0x650ce0);
    ((SelectFn)AT(0x49d8d0))(count,list,replacement,1);
    SCAPI_State.after_queue=U32(0x650ce0);
    for(i=0;i<12;++i)
        if(((void**)AT(0x684940))[i]!=(i<count?list[i]:0)) return SC_NO_SELECTABLE_UNIT;
    return SC_OK;
}
static DWORD point_selection(int x,int y) {
    void *unit,*old[12]={0},*incoming[1];
    void **sel=(void**)AT(0x684940);
    DWORD result=point_on_screen(x,y);
    int i,j,count=0,merged;
    if(result) return result;
    unit=((HitFn)AT(0x4c10d0))(x,y);
    if(!valid_unit(unit)) return SC_NO_SELECTABLE_UNIT;
    incoming[0]=unit;
    if(!SCAPI_State.add_selection) return apply_selection(1,incoming,1);
    for(i=0;i<12 && sel[i];++i) {
        if(!valid_unit(sel[i])) return SC_STALE_SELECTION;
        old[count++]=sel[i];
    }
    for(i=0;i<count;++i) if(old[i]==unit) {
        for(j=i;j<count-1;++j) old[j]=old[j+1];
        old[count-1]=0;
        return apply_selection(count-1,old,0);
    }
    /* This is the game's own merge/ownership/capacity path, not an arbitrary
       list of IDs. Native Shift keeps incompatible groups unchanged. */
    merged=((MergeSelectionFn)AT(0x4c1960))(old,incoming,1,0);
    return apply_selection(merged,old,1);
}
static int query_terminated(void **query,int count) {
    return count>=0 && count<=1700 && readable(query+count,sizeof(void*)) && !query[count];
}
static DWORD rectangle_selection(int x,int y,int x2,int y2,int same) {
    void **query,*candidates[1701],*chosen[12]={0},*preferred=0;
    short rect[4];
    int i,n=0,count,w,h,swap;
    DWORD result;
    if(same) {
        result=point_on_screen(x,y); if(result) return result;
        preferred=((HitFn)AT(0x4c10d0))(x,y);
        if(!valid_unit(preferred)) return SC_NO_SELECTABLE_UNIT;
        w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
        h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
        x=SCAPI_State.camera_x; y=SCAPI_State.camera_y;
        x2=x+w-1; y2=y+h-1;
    } else {
        if(x>x2){swap=x;x=x2;x2=swap;} if(y>y2){swap=y;y=y2;y2=swap;}
        result=point_on_map(x2,y2); if(result) return result;
        result=point_on_screen(x,y); if(result) return result;
        result=point_on_screen(x2,y2); if(result) return result;
        if(x2-x<=4 && y2-y<=4) return point_selection(x,y);
    }
    if(U32(0x5b2e24)) return SC_UNINITIALIZED; /* no nested scratch query */
    rect[0]=(short)x;rect[1]=(short)y;rect[2]=(short)x2;rect[3]=(short)y2;
    query=((RectQueryFn)AT(same?0x41e010:0x41e200))(rect);
    for(i=0;i<1700;++i) {
        if(!readable(query+i,4)) break;
        if(!query[i]) break;
        if(valid_unit(query[i]) && operable_part(query[i])) candidates[n++]=query[i];
    }
    candidates[n]=0;
    if(!query_terminated(query,i)) {
        ((PopQueryFn)AT(0x41dd50))(query); return SC_UNINITIALIZED;
    }
    count=((FilterSelectionFn)AT(0x4c1ad0))(candidates,chosen,preferred);
    ((PopQueryFn)AT(0x41dd50))(query);
    if(!count) return SC_NO_SELECTABLE_UNIT;
    return apply_selection(count,chosen,1);
}
#ifdef SCAPI_STAGE4
#include "stage4_observation.h"
#ifdef SCAPI_STAGE5
#include "stage5_building_memory.h"
#include "stage5_entity_identity.h"
#endif
#include "stage4_view.h"
#include "stage4_events.h"
#ifdef SCAPI_STAGE5
#include "stage5_nuclear.h"
#endif
#endif
#ifdef SCAPI_STAGE5
#include "stage5_commands.h"
#include "stage5_auto_panel.h"
#include "stage5_terrain.h"
#include "stage5_resource_sites.h"
#include "stage5_groups.h"
#include "stage5_own_overview.h"
#include "stage5_build_sites.h"
#include "stage5_menu.h"
#endif
static DWORD run_request(void) {
    DWORD result, i, q;
    int x=SCAPI_State.x,y=SCAPI_State.y;
    void **sel=(void**)AT(0x684940);
    if (SCAPI_State.operation==SC_PROBE) return SC_OK;
    if (SCAPI_State.operation==SC_DETACH) {
        DWORD old,tmp;
        PVOID volatile *slot=(PVOID volatile*)AT(0x4e5190);
#ifdef SCAPI_STAGE4
        SCAPI_State.enable_actions=0;
#ifdef SCAPI_STAGE5
        stage5_buildings_stop();
#endif
        if(stage4_events_stop()!=SC_OK)return SC_UNINITIALIZED;
#endif
        if (!VirtualProtect((void*)slot,4,PAGE_READWRITE,&old)) return SC_UNINITIALIZED;
        if (InterlockedCompareExchangePointer(slot,(PVOID)real_tick,(PVOID)hooked_tick)!=(PVOID)hooked_tick) {
            VirtualProtect((void*)slot,4,old,&tmp); return SC_UNINITIALIZED;
        }
        SCAPI_State.hook_installed=0;
        if (!VirtualProtect((void*)slot,4,
              old==PAGE_READWRITE ? original_iat_protection : old,&tmp))
            return SC_PROTECTION_RESTORE_FAILED;
        return SC_DETACHED; /* DLL remains resident until ordinary game exit */
    }
#ifdef SCAPI_STAGE4
    if(SCAPI_State.operation==SC_CAPTURE_START || SCAPI_State.operation==SC_CAPTURE_ACK ||
       SCAPI_State.operation==SC_CAPTURE_STOP)return stage4_events_request();
#endif
#ifdef SCAPI_STAGE5
    if(SCAPI_State.operation==SC_MENU_STATE || SCAPI_State.operation==SC_MENU_OPEN ||
       SCAPI_State.operation==SC_MENU_CLOSE)return stage5_menu_request(SCAPI_State.operation);
    if(SCAPI_State.operation==SC_OWN_OVERVIEW)return stage5_own_overview();
    if(SCAPI_State.operation==SC_BUILDING_START)
        return stage5_buildings_start((DWORD)SCAPI_State.x,(DWORD)SCAPI_State.y);
    if(SCAPI_State.operation==SC_BUILDING_STOP)return stage5_buildings_stop();
    if(SCAPI_State.operation==SC_TERRAIN_VIEW)return stage5_terrain_request();
    if(SCAPI_State.operation==SC_RESOURCE_SITES)return stage5_resource_sites_request();
    if(SCAPI_State.operation==SC_PLACEMENT_PREVIEW)return stage5_placement_request();
    if(SCAPI_State.operation==SC_BUILD_SITES)return stage5_build_sites_request();
#endif
    if (SCAPI_State.hook_installed!=1 || !SCAPI_State.enable_actions) return SC_DISABLED;
    result=game_ready();
#ifdef SCAPI_STAGE4
    /* Preserve lifecycle/initialization failures. A script that pauses the
       simulation must not be reported as an ordinary player/menu pause. */
    if((result==SC_OK || result==SC_PAUSED) &&
       is_player_action(SCAPI_State.operation) && U8(0x6b0cf8))return SC_SCRIPT_CONTROLLED;
#endif
    if (result) return result;
#ifdef SCAPI_STAGE4
    if(SCAPI_State.operation==SC_OBSERVE_VIEW) {
        result=stage4_view();if(result)return result;
#ifdef SCAPI_STAGE5
        return stage5_terrain_request();
#else
        return SC_OK;
#endif
    }
    if(SCAPI_State.operation==SC_OBSERVE_SELECTED || SCAPI_State.operation==SC_LEARN_DETAILS)
        return stage4_observe(SCAPI_State.operation==SC_LEARN_DETAILS);
    if(is_player_action(SCAPI_State.operation)) {
#ifdef SCAPI_STAGE5
        if(SCAPI_State.auto_panel_fault)return SC_UNINITIALIZED;
        if(SCAPI_State.auto_panel_sequence!=SCAPI_State.auto_panel_ack)return SC_OBSERVATION_PENDING;
#endif
        /* Commands do not inherently interrupt looking at the same unit.
           The post-dispatch sampler invalidates actual selection/camera,
           visibility, lifecycle and observed-rule changes. */
    }
    if(SCAPI_State.operation==SC_MOVE_MINIMAP) {
        result=stage4_minimap_point(&x,&y); if(result) return result;
    }
#endif
#ifdef SCAPI_STAGE5
    if(SCAPI_State.queued_order>1 || SCAPI_State.group_slot>9)return SC_BAD_ARGUMENT;
    if(SCAPI_State.operation>=SC_COMMAND_CARD && SCAPI_State.operation<=SC_UNLOAD_ONE)
        return stage5_request();
    if(SCAPI_State.operation>=SC_GROUP_ASSIGN && SCAPI_State.operation<=SC_FOCUS_SELECTED)
        return stage5_groups_request();
    if(SCAPI_State.operation==SC_SAVE_VIEW) {
        if(U8(0x684914))return SC_CAMERA_LOCKED;
        SCAPI_State.actual_x=SCAPI_State.camera_x;SCAPI_State.actual_y=SCAPI_State.camera_y;
        return SC_OK;
    }
#endif
    if(SCAPI_State.add_selection>1) return SC_BAD_ARGUMENT;
    if (SCAPI_State.operation==SC_CAMERA) {
        if (U8(0x684914)) return SC_CAMERA_LOCKED;
        ((CameraFn)AT(0x405070))(x,y);
        SCAPI_State.actual_x=I32(0x51bce4); SCAPI_State.actual_y=I32(0x51bce0);
        return SC_OK; /* caller separately checks coordinates and actual display */
    }
    result=point_on_map(x,y); if (result) return result;
    if (SCAPI_State.operation==SC_UI_POINT) {
        result=point_on_screen(x,y);
        SCAPI_State.point_blocked=result;
        return result;
    }
    if (SCAPI_State.operation==SC_SELECT_POINT) {
        return point_selection(x,y);
    }
    if(SCAPI_State.operation==SC_SELECT_BOX || SCAPI_State.operation==SC_SELECT_SAME) {
        if(SCAPI_State.add_selection) return SC_BAD_ARGUMENT;
        return rectangle_selection(x,y,SCAPI_State.x2,SCAPI_State.y2,
                                   SCAPI_State.operation==SC_SELECT_SAME);
    }
    if (SCAPI_State.operation==SC_MOVE_SELECTED
#ifdef SCAPI_STAGE4
        || SCAPI_State.operation==SC_MOVE_MINIMAP
#endif
        ) {
        BYTE packet[2+2*12]; void *selected[12];
        DWORD count=0,capacity,selection_bytes,after_diff,after_full;
        if (SCAPI_State.expected_selection!=SCAPI_State.selection_generation)
            return SC_STALE_SELECTION;
        if (!SCAPI_State.selection_count) return SC_NO_SELECTION;
        for (i=0;i<12;++i) if (sel[i]) {
            if (!valid_unit(sel[i])) return SC_STALE_SELECTION;
            if (*((BYTE*)sel[i]+0x4c)!=U32(0x4f30c0)) return SC_NOT_OWNED;
            selected[count++]=sel[i];
        }
        /* The real GUI selection may differ from simulation selection if an
           earlier native selection packet was dropped. Keep native send-cache
           coherent, then explicitly synchronize exactly the ALREADY selected
           units immediately before Move. This never changes GUI selection or
           selects an additional/offscreen unit. Native opcode/encoding comes
           from 470420/424B80, not from the reference version. */
        q=U32(0x650ce0); SCAPI_State.before_queue=q;
        capacity=U32(0x650b88); selection_bytes=2+2*count;
        if (q>capacity || capacity-q<2*selection_bytes+11) {
            SCAPI_State.after_queue=q; return SC_QUEUE_FULL;
        }
        /* All exact world-coordinate targets require the current operable
           viewport. Fog/exploration can never bypass this gate. A later
           minimap endpoint will accept only integer minimap pixels. */
#ifdef SCAPI_STAGE4
        if(SCAPI_State.operation!=SC_MOVE_MINIMAP)
#endif
        {result=point_on_screen(x,y); if (result) return result;}
        ((SendSelectionFn)AT(0x470420))(count,selected);
        after_diff=U32(0x650ce0);
        if (after_diff<q || after_diff-q>selection_bytes) {
            SCAPI_State.after_queue=after_diff; return SC_QUEUE_UNCONFIRMED;
        }
        packet[0]=9; packet[1]=(BYTE)count;
        for (i=0;i<count;++i)
            *(WORD*)(packet+2+2*i)=(WORD)((EncodeFn)AT(0x424b80))(selected[i]);
        ((QueueFn)AT(0x4740f0))(packet,selection_bytes);
        after_full=U32(0x650ce0);
        if (after_full!=after_diff+selection_bytes) {
            SCAPI_State.after_queue=after_full; return SC_QUEUE_UNCONFIRMED;
        }
        /* No destination terrain, path, height or unit query. */
        ((TargetFn)AT(0x470e70))(x,y,0,0xffffu,6,
#ifdef SCAPI_STAGE5
            SCAPI_State.queued_order
#else
            0
#endif
        );
        SCAPI_State.after_queue=U32(0x650ce0);
        return SCAPI_State.after_queue==after_full+11 ? SC_OK : SC_QUEUE_UNCONFIRMED;
    }
    return SC_BAD_OP;
}
static DWORD WINAPI hooked_tick(void) {
    DWORD caller=(DWORD)(uintptr_t)__builtin_return_address(0);
    DWORD tid=GetCurrentThreadId();
    InterlockedIncrement((LONG*)&SCAPI_State.all_calls);
    if (tid!=SCAPI_State.ui_thread) {
        InterlockedIncrement((LONG*)&SCAPI_State.other_thread_calls);
        return real_tick();
    }
    if (InterlockedCompareExchange(&in_site,1,0)) {
        ++SCAPI_State.reentries; return real_tick();
    }
    /* Track transitions even at the UI thread's other clock callers. */
    sample_lifecycle();
    if (caller==(DWORD)(uintptr_t)AT(0x446c2d)) {
        DWORD now=real_tick();
        sample();
#ifdef SCAPI_VISIBLE_PHASE_CANDIDATE
        stage5_visible_phase_sample(now);
#endif
        SCAPI_State.observed_thread=tid; SCAPI_State.last_caller=caller;
        if (!SCAPI_State.site_calls) SCAPI_State.first_site_tick=now;
        ++SCAPI_State.site_calls; SCAPI_State.last_site_tick=now;
        if (InterlockedCompareExchange(&SCAPI_State.state,SC_BUSY,SC_REQUEST)==SC_REQUEST) {
            if (is_player_action(SCAPI_State.operation) && SCAPI_State.enable_actions &&
#ifdef SCAPI_STAGE5
                stage5_rate_valid() &&
#endif
                SCAPI_State.action_interval_ms &&
                SCAPI_State.action_interval_ms<0x80000000u && !action_due(now)) {
                InterlockedExchange(&SCAPI_State.state,SC_REQUEST);
                InterlockedExchange(&in_site,0);
                return real_tick();
            }
            SCAPI_State.executed_tick=now;
#ifdef SCAPI_STAGE5
            if(is_player_action(SCAPI_State.operation))stage5_rate_before();
#endif
            if (is_player_action(SCAPI_State.operation) && SCAPI_State.enable_actions &&
                (!SCAPI_State.action_interval_ms || SCAPI_State.action_interval_ms>=0x80000000u
#ifdef SCAPI_STAGE5
                 || !stage5_rate_valid()
#endif
                 ))
                SCAPI_State.result=SC_BAD_ARGUMENT;
            else SCAPI_State.result=run_request();
            sample();
            SCAPI_State.finished_tick=real_tick();
            if (is_player_action(SCAPI_State.operation)
#ifdef SCAPI_STAGE5
                && SCAPI_State.result!=SC_OBSERVATION_PENDING
#endif
                ) {
                SCAPI_State.last_action_tick=now;
                action_clock_started=1; ++SCAPI_State.action_count;
#ifdef SCAPI_STAGE5
                stage5_rate_after();
#endif
            }
            SCAPI_State.completed_serial=SCAPI_State.serial;
            InterlockedExchange(&SCAPI_State.state,SC_DONE);
        }
    }
    InterlockedExchange(&in_site,0);
    return real_tick();
}
static BOOL CALLBACK find_window(HWND w,LPARAM arg) {
    DWORD pid=0,tid=GetWindowThreadProcessId(w,&pid);
    char window_class[64];
    if (pid==GetCurrentProcessId() && IsWindowVisible(w) && !GetWindow(w,GW_OWNER) &&
        GetClassNameA(w,window_class,sizeof(window_class)) && !strcmp(window_class,"SWarClass")) {
        DWORD *pair=(DWORD*)arg;
        ++pair[0]; pair[1]=tid;
    }
    return TRUE;
}
__declspec(dllexport) DWORD WINAPI SCAPI_Start(LPVOID unused) {
    DWORD pair[2]={0,0},old,tmp;
    PVOID volatile *slot;
    BYTE expected[]={0xff,0x15,0x90,0x51,0x4e,0x00};
    (void)unused;
    if (SCAPI_State.hook_installed) return 1;
#ifdef SCAPI_STAGE5
    if(!QueryPerformanceFrequency(&stage5_frequency) || stage5_frequency.QuadPart<=0)return 0;
    SCAPI_State.qpc_frequency_low=stage5_frequency.LowPart;
    SCAPI_State.qpc_frequency_high=(DWORD)stage5_frequency.HighPart;
    SCAPI_State.action_interval_ms=SCAPI_STAGE5_MIN_INTERVAL_MS;
#endif
    game=(BYTE*)GetModuleHandleW(NULL);
    /* This candidate is pinned to the observed preferred base. Fail closed
       on a relocated executable; do not assume embedded absolute operands. */
    if ((DWORD)(uintptr_t)game!=0x400000u || !readable(AT(0x446c27),sizeof(expected)) ||
        memcmp(AT(0x446c27),expected,sizeof(expected))) return 0;
    EnumWindows(find_window,(LPARAM)pair);
    if (pair[0]!=1 || !pair[1]) return 0;
    SCAPI_State.game_base=(DWORD)(uintptr_t)game; SCAPI_State.ui_thread=pair[1];
    slot=(PVOID volatile*)AT(0x4e5190);
    real_tick=(TickFn)*slot;
    if (real_tick!=(TickFn)GetProcAddress(GetModuleHandleA("kernel32.dll"),"GetTickCount"))
        return 0; /* Do not overwrite somebody else's hook. */
    if (!VirtualProtect((void*)slot,4,PAGE_READWRITE,&old)) return 0;
    original_iat_protection=old;
    if (InterlockedCompareExchangePointer(slot,(PVOID)hooked_tick,(PVOID)real_tick)!=(PVOID)real_tick) {
        VirtualProtect((void*)slot,4,old,&tmp); return 0;
    }
    if (!VirtualProtect((void*)slot,4,old,&tmp)) {
        SCAPI_State.hook_installed=2; /* resident but unhealthy; actions stay disabled */
        return 2;
    }
    SCAPI_State.hook_installed=1;
    return 1;
}
BOOL WINAPI DllMain(HINSTANCE instance,DWORD reason,LPVOID reserved) {
    (void)instance;(void)reason;(void)reserved; return TRUE;
}
