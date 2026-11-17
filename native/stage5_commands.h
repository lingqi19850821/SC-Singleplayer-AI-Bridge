#ifndef SCAPI_STAGE5_COMMANDS_H
#define SCAPI_STAGE5_COMMANDS_H
/* Native button invocation ABI: 4A1B84..4A1B91 loads CX from button+0E and
   DL from the Shift flag, then calls button+08. Only the current visible
   card is accepted; a caller never supplies a native address or unit type. */
typedef void (FAST *S5ButtonFn)(unsigned,unsigned);
typedef void (FAST *S5InputFn)(void*);
typedef void (FAST *S5CancelSlotFn)(unsigned);
typedef void (FAST *S5RightClickFn)(int,int,void*,unsigned,unsigned);
typedef struct ScapiButton {
    DWORD slot,enabled,handler,argument,label_address,category,node,record;
} ScapiButton;
static ScapiButton stage5_buttons[9];
/* Identity of the last committed card, independent of temporary UI hiding.
   Live rows below remain empty/unavailable while the native UI suppresses it. */
static ScapiButton stage5_card_identity[9];
#include "stage5_cargo.h"
static DWORD stage5_card_selection,stage5_card_epoch,stage5_card_menu;
static DWORD stage5_queue_values[6],stage5_queue_selection;
static DWORD stage5_queue_names[5],stage5_queue_valid=1;
static DWORD stage5_producer_selection,stage5_producer_epoch,stage5_producer_confirmed;
static DWORD stage5_pending_selection,stage5_pending_epoch;
static DWORD stage5_pending_mode,stage5_pending_signature[5];
static DWORD stage5_pending_queued_order;

static DWORD stage5_mode(void) {
    if(U32(0x653948))return 3; /* actual building placement */
    if(U8(0x69f48c))return 2; /* actual order targeting */
    return 0;
}
static void stage5_mode_signature(DWORD *s) {
    s[0]=stage5_mode();s[1]=U8(0x69f484);s[2]=U8(0x69f488);
    s[3]=U8(0x69f490);s[4]=U16(0x653a3a)|(U8(0x6539c0)<<16);
}
static DWORD stage5_owned_selection(int single,BYTE **first) {
    DWORD i,n=0;void **sel=(void**)AT(0x684940);
    if(SCAPI_State.expected_selection!=SCAPI_State.selection_generation)return SC_STALE_SELECTION;
    for(i=0;i<12;++i)if(sel[i]) {
        if(!valid_unit(sel[i]))return SC_STALE_SELECTION;
        if(*((BYTE*)sel[i]+0x4c)!=U32(0x4f30c0))return SC_NOT_OWNED;
        if(!n && first)*first=(BYTE*)sel[i];++n;
    }
    if(!n)return SC_NO_SELECTION;
    if(single && n!=1)return SC_INCOMPATIBLE_STATE;
    return SC_OK;
}
/* As in the proven Move path: synchronize exactly the already selected GUI
   group, without selecting an additional or offscreen unit. */
static DWORD stage5_sync_selection(void) {
    BYTE packet[26];void *selected[12];DWORD count=0,i,q,after,bytes,capacity;
    void **sel=(void**)AT(0x684940);
    DWORD result=stage5_owned_selection(0,NULL);if(result)return result;
    for(i=0;i<12;++i)if(sel[i])selected[count++]=sel[i];
    bytes=2+2*count;q=U32(0x650ce0);capacity=U32(0x650b88);
    if(q>capacity || capacity-q<2*bytes+32)return SC_QUEUE_FULL;
    ((SendSelectionFn)AT(0x470420))(count,selected);
    after=U32(0x650ce0);
    if(after<q || after-q>bytes)return SC_QUEUE_UNCONFIRMED;
    packet[0]=9;packet[1]=(BYTE)count;
    for(i=0;i<count;++i)*(WORD*)(packet+2+2*i)=(WORD)((EncodeFn)AT(0x424b80))(selected[i]);
    ((QueueFn)AT(0x4740f0))(packet,bytes);
    return U32(0x650ce0)==after+bytes?SC_OK:SC_QUEUE_UNCONFIRMED;
}
static int stage5_handler_allowed(DWORD handler) {
    /* These are the target's native command-card callbacks, never an address
       supplied by an external caller. Generic callback ABI is independently
       established at 4A1B91. Submenu callback is outside this code interval. */
    return (handler>=0x47a760u && handler<=0x47b0c0u) ||
           handler==0x4a17f0u || handler==0x4a17d0u || handler==0x4a17c0u ||
           handler==0x4b5820u || handler==0x4b58a0u;
}
static DWORD stage5_card_collect_valid;
static DWORD stage5_collect_card(ScapiButton *rows) {
    BYTE *node,*record,*table;DWORD seen[9],n=0,i,slots=0;
    BYTE *primary=*(BYTE**)AT(0x68d2f8);WORD labels;
    stage5_card_collect_valid=0;
    if(!SCAPI_State.selection_count || !valid_unit(primary) ||
       primary[0x4c]!=U32(0x4f30c0) || U8(0x68c9dc))return 0;
    /* The primary must belong to the actual GUI selection, including groups. */
    for(i=0;i<12;++i)if(((void**)AT(0x684940))[i]==primary)break;
    if(i==12)return 0;
    node=*(BYTE**)AT(0x68ca50);table=*(BYTE**)AT(0x68d344);
    if(!readable(node,0x46) || !readable(table,2))return 0;
    labels=*(WORD*)table;
    if(!labels || !readable(table,2+2*labels))return 0;
    node=*(BYTE**)(node+0x42);
    while(node) {
        DWORD slot,flags,handler,offset;
        if(n>=9 || !readable(node,0x46))return 0;
        for(i=0;i<n;++i)if(seen[i]==(DWORD)(uintptr_t)node)return 0;
        seen[n++]=(DWORD)(uintptr_t)node;
        slot=*(WORD*)(node+0x20);flags=*(DWORD*)(node+0x18);
        if(flags&8) {
            WORD label;
            if(slot<1 || slot>9 || slots&(1u<<slot))return 0;
            slots|=1u<<slot;record=*(BYTE**)(node+0x26);
            if(!readable(record,20))return 0;
            handler=*(DWORD*)(record+8)-(DWORD)(uintptr_t)game+0x400000u;
            label=*(WORD*)(record+16);
            if(!label || label>labels)return 0;
            offset=*(WORD*)(table+2*label);
            if(offset<2+2*labels || !readable(table+offset,3))return 0;
            rows[slot-1].slot=slot;
            rows[slot-1].enabled=!(flags&2) && stage5_handler_allowed(handler);
            rows[slot-1].handler=handler;rows[slot-1].argument=*(WORD*)(record+14);
            rows[slot-1].label_address=(DWORD)(uintptr_t)(table+offset+2);
            rows[slot-1].category=table[offset+1];
            rows[slot-1].node=(DWORD)(uintptr_t)node;
            rows[slot-1].record=(DWORD)(uintptr_t)record;
        }
        node=*(BYTE**)node;
    }
    stage5_card_collect_valid=1;return slots;
}
static void stage5_sample(void) {
    ScapiButton fresh[9];DWORD count=0,i,producer=0,values[6]={0},signature[5],changed,next,suppressed;BYTE *u;
    int ended=0;
    /* A reader acquires this aligned serial BEFORE reading state and buffers.
       Odd/even publication prevents a previous count from parsing new rows. */
    InterlockedIncrement((LONG*)&SCAPI_State.command_sample_serial);
    memset(fresh,0,sizeof(fresh));
    suppressed=SCAPI_State.active && SCAPI_State.tile_pointer && U8(0x68c9dc);
    if(SCAPI_State.active && SCAPI_State.tile_pointer && !stage5_collect_card(fresh))
        memset(fresh,0,sizeof(fresh));
    if(stage5_producer_selection!=SCAPI_State.selection_generation ||
       stage5_producer_epoch!=SCAPI_State.epoch) {
        stage5_producer_confirmed=0;
        stage5_producer_selection=SCAPI_State.selection_generation;
        stage5_producer_epoch=SCAPI_State.epoch;
    }
    for(i=0;i<9;++i)if(fresh[i].slot) {
        ++count;
        if(fresh[i].handler==0x47a840 || fresh[i].handler==0x47a860 || fresh[i].handler==0x47a9a0)
            stage5_producer_confirmed=1;
    }
    producer=stage5_producer_confirmed;
    changed=stage5_card_selection!=SCAPI_State.selection_generation ||
       stage5_card_epoch!=SCAPI_State.epoch || stage5_card_menu!=U16(0x68c9bc);
    /* Only explicit temporary suppression may preserve an identity. A true
       collect failure still commits an empty identity and expires old tickets.
       Context/menu changes also invalidate during suppression. On restoration
       every field, including enabled/handler/record, is compared unchanged. */
    changed=changed || (!suppressed && memcmp(fresh,stage5_card_identity,sizeof(fresh)));
    if(changed) {
        memcpy(stage5_card_identity,fresh,sizeof(fresh));
        stage5_card_selection=SCAPI_State.selection_generation;
        stage5_card_epoch=SCAPI_State.epoch;stage5_card_menu=U16(0x68c9bc);
    }
    memcpy(stage5_buttons,fresh,sizeof(fresh));
    SCAPI_State.command_address=(DWORD)(uintptr_t)stage5_buttons;
    SCAPI_State.command_count=count;
    if(changed) {
        next=SCAPI_State.command_generation+1;
        if(!next)SCAPI_State.enable_actions=0;
        InterlockedExchange((LONG*)&SCAPI_State.command_generation,next);
    }
    u=*(BYTE**)AT(0x684940);
    count=0;stage5_queue_valid=1;
    memset(stage5_queue_names,0,sizeof(stage5_queue_names));
    SCAPI_State.queue_names_address=(DWORD)(uintptr_t)stage5_queue_names;
    if(producer && SCAPI_State.selection_count==1 && valid_unit(u) &&
       u[0x4c]==U32(0x4f30c0) && u[0xa4]<5) {
        values[5]=u[0xa4];
        for(i=0;i<5;++i) {
            DWORD kind=*(WORD*)(u+0x98+2*((u[0xa4]+i)%5));
            values[i]=kind;
            if(kind==228){ended=1;continue;}
            if(kind>228 || ended){stage5_queue_valid=0;continue;}
            ++count;
            stage5_queue_names[i]=(DWORD)(uintptr_t)((UnitNameFn)AT(0x425390))(kind);
            if(!stage5_queue_names[i])stage5_queue_valid=0;
        }
    } else {
        for(i=0;i<5;++i)values[i]=228;
        if(producer && SCAPI_State.selection_count==1 && valid_unit(u) &&
           u[0x4c]==U32(0x4f30c0) && u[0xa4]>=5)stage5_queue_valid=0;
    }
    /* Never expose partial names as a usable queue. Six is an impossible
       count, so guarded readers also fail explicitly on post-action samples
       whose command result must continue to describe the submitted action. */
    if(!stage5_queue_valid){count=6;memset(stage5_queue_names,0,sizeof(stage5_queue_names));}
    changed=memcmp(values,stage5_queue_values,sizeof(values)) ||
            stage5_queue_selection!=SCAPI_State.selection_generation;
    if(changed) {
        memcpy(stage5_queue_values,values,sizeof(values));
        stage5_queue_selection=SCAPI_State.selection_generation;
    }
    SCAPI_State.queue_count=count;
    SCAPI_State.queue_type0=values[0];SCAPI_State.queue_type1=values[1];
    SCAPI_State.queue_type2=values[2];SCAPI_State.queue_type3=values[3];SCAPI_State.queue_type4=values[4];
    /* Publish the change marker only after every queue field. UI redraws do
       not erase the independently confirmed selected producer capability. */
    if(changed) {
        next=SCAPI_State.queue_serial+1;
        if(!next)SCAPI_State.enable_actions=0;
        InterlockedExchange((LONG*)&SCAPI_State.queue_serial,next);
    }
    stage5_mode_signature(signature);
    if(stage5_pending_mode && (stage5_pending_selection!=SCAPI_State.selection_generation ||
       stage5_pending_epoch!=SCAPI_State.epoch ||
       memcmp(signature,stage5_pending_signature,sizeof(signature)))) {
        stage5_pending_mode=0;
        stage5_pending_queued_order=0;
        if(++SCAPI_State.target_serial==0)SCAPI_State.enable_actions=0;
    }
    SCAPI_State.pending_queued_order=stage5_pending_queued_order;
    stage5_sample_cargo();
    if(!InterlockedIncrement((LONG*)&SCAPI_State.command_sample_serial))SCAPI_State.enable_actions=0;
}
static void stage5_record_mode(void) {
    stage5_pending_mode=stage5_mode();
    stage5_pending_queued_order=stage5_pending_mode?SCAPI_State.queued_order:0;
    SCAPI_State.pending_queued_order=stage5_pending_queued_order;
    if(stage5_pending_mode) {
        stage5_pending_selection=SCAPI_State.selection_generation;
        stage5_pending_epoch=SCAPI_State.epoch;
        stage5_mode_signature(stage5_pending_signature);
        if(++SCAPI_State.target_serial==0)SCAPI_State.enable_actions=0;
    }
    SCAPI_State.command_phase=stage5_pending_mode;
}
static ScapiButton *stage5_find_handler(DWORD a,DWORD b) {
    DWORD i;for(i=0;i<9;++i)if(stage5_buttons[i].slot && stage5_buttons[i].enabled &&
        (stage5_buttons[i].handler==a || (b && stage5_buttons[i].handler==b)))return &stage5_buttons[i];
    return NULL;
}
static DWORD stage5_press(ScapiButton *button) {
    DWORD result,q,menu=U16(0x68c9bc),old_mode=stage5_mode();BYTE *actor=NULL;
#ifdef SCAPI_BUILD_QUEUE_DIAGNOSTIC
    BYTE probe_old_shift;
#endif
    if(!button || !button->slot || !button->enabled)return SC_COMMAND_UNAVAILABLE;
    /* Initiating train/morph/research/upgrade work needs the actor on screen
       at execution, even if a still-current button ticket was issued earlier.
       The native consumers may act on multiple actual selected units, so all
       selected actors must pass; Move/ordinary orders retain their own rules. */
    if(button->handler==0x47a840 || button->handler==0x47a9a0 ||
       button->handler==0x47ae70 || button->handler==0x47adf0 ||
       button->handler==0x47a9d0 || button->handler==0x47ad50) {
        DWORD i;
        result=stage5_owned_selection(0,NULL);if(result)return result;
        for(i=0;i<12;++i) {
            BYTE *selected=((BYTE**)AT(0x684940))[i];
            if(selected && !panel_visible(selected))return SC_NOT_ON_SCREEN;
        }
    }
    /* Existing building actors can also start addon/build work through the
       native placement callbacks. Worker construction keeps its existing
       target-point rules; this checks only a selected building's initiation. */
    if(button->handler==0x47ad80 || button->handler==0x47adc0) {
        DWORD i;
        result=stage5_owned_selection(0,NULL);if(result)return result;
        for(i=0;i<12;++i) {
            BYTE *selected=((BYTE**)AT(0x684940))[i];DWORD kind;
            if(!selected)continue;
            kind=*(WORD*)(selected+0x64);
            if(kind<228 && (U32(0x6b0358+4*kind)&1u) && !panel_visible(selected))
                return SC_NOT_ON_SCREEN;
        }
    }
    /* Only target-confirmed ordinary Move/Attack/Patrol callbacks accept
       queued=True. In particular build/research/skills never ignore it. */
    if(SCAPI_State.queued_order && button->handler!=0x47a8c0 &&
       button->handler!=0x47a8e0 && button->handler!=0x47a930 &&
       button->handler!=0x47a9b0
#ifdef SCAPI_BUILD_QUEUE_DIAGNOSTIC
       && button->handler!=0x47ad80 && button->handler!=0x47adc0
#endif
       )return SC_COMMAND_UNAVAILABLE;
    if(button->handler==0x47a770) {
        DWORD kind;
        if(stage5_mode())return SC_PENDING_TARGET;
        result=stage5_owned_selection(1,&actor);if(result)return result;
        if(*(BYTE**)AT(0x68d2f8)!=actor)return SC_STALE_SELECTION;
        kind=*(WORD*)(actor+0x64);
        if(kind<131 || kind>133)return SC_INCOMPATIBLE_STATE;
        if(!panel_visible(actor))return SC_NOT_ON_SCREEN;
    }
    if(button->handler==0x47a8d0 && button->slot==9) {
        result=stage5_owned_selection(1,&actor);if(result)return result;
        if(*(WORD*)(actor+0x64)!=7 || actor[0x4d]!=33)return SC_INCOMPATIBLE_STATE;
    }
    if(button->handler==0x47a8a0) {
        DWORD kind;
        result=stage5_owned_selection(1,&actor);if(result)return result;
        kind=*(WORD*)(actor+0x64);
        if(kind>=228 || !(U32(0x6b0358+4*kind)&1) || *(DWORD*)(actor+0xdc)&1)return SC_INCOMPATIBLE_STATE;
    }
    if(button->handler==0x47a9e0 || button->handler==0x47ad60) {
        result=stage5_owned_selection(1,&actor);if(result)return result;
        if((button->handler==0x47a9e0 && actor[0xc8]>=44) ||
           (button->handler==0x47ad60 && actor[0xc9]>=61))return SC_INCOMPATIBLE_STATE;
    }
    if(button->handler==0x47a860 && !stage5_queue_valid)return SC_INCOMPATIBLE_STATE;
    if(button->handler==0x47a860 && !SCAPI_State.queue_count)return SC_COMMAND_UNAVAILABLE;
    result=stage5_sync_selection();if(result)return result;
    q=U32(0x650ce0);SCAPI_State.before_queue=q;
#ifdef SCAPI_BUILD_QUEUE_DIAGNOSTIC
    probe_old_shift=U8(0x63b410);U8(0x63b410)=(BYTE)SCAPI_State.queued_order;
#endif
    ((S5ButtonFn)AT(button->handler))(button->argument,SCAPI_State.queued_order);
#ifdef SCAPI_BUILD_QUEUE_DIAGNOSTIC
    U8(0x63b410)=probe_old_shift;
#endif
    SCAPI_State.after_queue=U32(0x650ce0);
    stage5_record_mode();
    if(SCAPI_State.after_queue<q)return SC_QUEUE_UNCONFIRMED;
    if(button->handler==0x47a770) {
        DWORD i,n=0,primary_seen=0;BYTE *selected,*primary=*(BYTE**)AT(0x68d2f8);
        /* 47A770 -> 4C0B60 -> 49D8D0 changes the actual GUI selection
           synchronously, then sends it through the native selection sender.
           Its own spatial query filters type 35 by mother+80; never replace
           that callback with a bridge-side unit scan or expose coordinates.
           Success is the observed selection, not a production/order packet. */
        if(((BYTE**)AT(0x684940))[0]==actor && primary==actor) {
            for(i=1;i<12;++i)if(((BYTE**)AT(0x684940))[i])break;
            if(i==12)return SC_NOT_APPLIED;
        }
        for(i=0;i<12;++i) {
            selected=((BYTE**)AT(0x684940))[i];if(!selected)continue;
            if(!valid_unit(selected) || selected[0x4c]!=actor[0x4c] ||
               *(WORD*)(selected+0x64)!=35 || *(BYTE**)(selected+0x80)!=actor)
                return SC_QUEUE_UNCONFIRMED;
            if(selected==primary)primary_seen=1;
            ++n;
        }
        if(!n || !primary_seen)return SC_QUEUE_UNCONFIRMED;
        SCAPI_State.command_phase=0;
        /* The frame-loop's mandatory post-action sample publishes the fresh
           generation/count and invalidates the old Hatchery/card tickets. */
        return SC_OK;
    }
    if(SCAPI_State.after_queue>q){SCAPI_State.command_phase=4;return SC_OK;}
    if(stage5_pending_mode || U16(0x68c9bc)!=menu || (old_mode && !stage5_mode()))return SC_OK;
    return SC_NOT_APPLIED;
}
static DWORD s5_placement_target_gate(int click_x,int click_y);
static DWORD stage5_target_point(int x,int y) {
    BYTE event[20]={0},old_shift;WORD oldx,oldy;DWORD result,q,mode=stage5_mode();
    result=point_on_map(x,y);if(result)return result;
    result=point_on_screen(x,y);if(result)return result;
    if(!mode || !stage5_pending_mode)return SC_NO_PENDING_TARGET;
    if(SCAPI_State.expected_target_serial!=SCAPI_State.target_serial)return SC_STALE_COMMAND;
    if(SCAPI_State.queued_order!=stage5_pending_queued_order)return SC_STALE_COMMAND;
#ifndef SCAPI_BUILD_QUEUE_DIAGNOSTIC
    if(mode==3 && SCAPI_State.queued_order)return SC_COMMAND_UNAVAILABLE;
#endif
    if(mode==3){result=s5_placement_target_gate(x,y);if(result)return result;}
    result=stage5_sync_selection();if(result)return result;
    *(short*)(event+0x0e)=(short)(x-SCAPI_State.camera_x);
    *(short*)(event+0x10)=(short)(y-SCAPI_State.camera_y);
    oldx=U16(0x6b3ee0);oldy=U16(0x6b3ee4);
    U16(0x6b3ee0)=*(WORD*)(event+0x0e);U16(0x6b3ee4)=*(WORD*)(event+0x10);
    q=U32(0x650ce0);SCAPI_State.before_queue=q;
    /* The real target-click chain 4478A0 -> 4C0BA0 -> 4C0F97 reads this
       modifier byte before calling 470E70. Scope it to this synchronous UI
       callback and restore the prior value before returning to the game. */
    old_shift=U8(0x63b410);U8(0x63b410)=(BYTE)stage5_pending_queued_order;
    ((S5InputFn)AT(mode==3?0x47d3b0:0x4478a0))(event);
    U8(0x63b410)=old_shift;
    U16(0x6b3ee0)=oldx;U16(0x6b3ee4)=oldy;
    SCAPI_State.after_queue=U32(0x650ce0);
    SCAPI_State.command_phase=stage5_mode();
    if(SCAPI_State.after_queue>q){stage5_pending_mode=0;stage5_pending_queued_order=0;
        SCAPI_State.pending_queued_order=0;SCAPI_State.command_phase=4;return SC_OK;}
    return SCAPI_State.after_queue==q?SC_NOT_APPLIED:SC_QUEUE_UNCONFIRMED;
}
static DWORD stage5_request(void) {
    DWORD op=SCAPI_State.operation,result;ScapiButton *button=NULL;BYTE *u=NULL;
    int x=SCAPI_State.x,y=SCAPI_State.y;
    if(SCAPI_State.queued_order>1)return SC_BAD_ARGUMENT;
    if(SCAPI_State.queued_order && op!=SC_RIGHT_CLICK && op!=SC_ATTACK_POINT &&
       op!=SC_PATROL_POINT && op!=SC_ATTACK_MINIMAP && op!=SC_PATROL_MINIMAP &&
       op!=SC_COMMAND_BUTTON && op!=SC_COMMAND_TARGET)return SC_COMMAND_UNAVAILABLE;
    if(op==SC_CANCEL_PENDING) {
        DWORD mode=stage5_mode();if(!mode)return SC_NO_PENDING_TARGET;
        /* ESC is UI cancellation, including when the actor has died or the
           selection has changed. It grants no command on an unselected unit. */
        ((S5ButtonFn)AT(mode==3?0x4a17d0:0x4a17c0))(0,0);
        stage5_record_mode();
        return stage5_mode()?SC_NOT_APPLIED:SC_OK;
    }
    result=stage5_owned_selection(0,&u);if(result)return result;
    if(op==SC_COMMAND_CARD){SCAPI_State.command_phase=stage5_mode();return stage5_queue_valid?SC_OK:SC_INCOMPATIBLE_STATE;}
    if(op==SC_COMMAND_TARGET)return stage5_target_point(x,y);
    if(op==SC_RIGHT_CLICK) {
        BYTE *target,*sprite;DWORD mask=U32(0x65098c),status;
        if(stage5_mode())return SC_PENDING_TARGET;
        result=point_on_map(x,y);if(result)return result;
        result=point_on_screen(x,y);if(result)return result;
        target=((HitFn)AT(0x4c10d0))(x,y);
        if(valid_unit(target)) {
            sprite=*(BYTE**)(target+12);status=*(DWORD*)(target+0xdc);
            /* An unobserved/cloaked hit can never become a unit target. The
               native context command may still act on the clicked ground. */
            if(!(sprite[12]&mask) || ((status&0x300) && !(*(DWORD*)(target+0xe4)&mask)))target=NULL;
        } else target=NULL;
        result=stage5_sync_selection();if(result)return result;
        SCAPI_State.before_queue=U32(0x650ce0);
        /* Same 470DD0 sender as the actual viewport right-click at 47EAF7.
           The engine resolves Move/Attack/Follow/EnterTransport/etc. from
           the real selected group and this current native hit. */
        ((S5RightClickFn)AT(0x470dd0))(x,y,target,228,SCAPI_State.queued_order);
        SCAPI_State.after_queue=U32(0x650ce0);SCAPI_State.command_phase=4;
        return SCAPI_State.after_queue==SCAPI_State.before_queue+10?SC_OK:SC_QUEUE_UNCONFIRMED;
    }
    if(op==SC_ATTACK_POINT || op==SC_PATROL_POINT || op==SC_ATTACK_MINIMAP || op==SC_PATROL_MINIMAP) {
        if(stage5_mode())return SC_PENDING_TARGET;
        if(op==SC_ATTACK_MINIMAP || op==SC_PATROL_MINIMAP) {
            result=stage4_minimap_point(&x,&y);if(result)return result;
        } else {result=point_on_screen(x,y);if(result)return result;}
        button=stage5_find_handler((op==SC_PATROL_POINT || op==SC_PATROL_MINIMAP)?0x47a9b0:0x47a8e0,
                                  (op==SC_PATROL_POINT || op==SC_PATROL_MINIMAP)?0:0x47a930);
        result=stage5_press(button);if(result)return result;
        if(op==SC_ATTACK_MINIMAP || op==SC_PATROL_MINIMAP) {
            /* Native minimap ground-order path: no world-coordinate caller or
               hidden unit target; exactly the current real selection. */
            if(!stage5_pending_mode)return SC_NO_PENDING_TARGET;
            SCAPI_State.before_queue=U32(0x650ce0);
            ((TargetFn)AT(0x470e70))(x,y,NULL,228,U8(0x69f490),SCAPI_State.queued_order);
            ((S5ButtonFn)AT(0x4b5820))(0,0);
            stage5_pending_mode=0;stage5_pending_queued_order=0;
            SCAPI_State.pending_queued_order=0;SCAPI_State.command_phase=4;
            SCAPI_State.after_queue=U32(0x650ce0);
            return SCAPI_State.after_queue==SCAPI_State.before_queue+11?SC_OK:SC_QUEUE_UNCONFIRMED;
        }
        SCAPI_State.expected_target_serial=SCAPI_State.target_serial;
        return stage5_target_point(x,y);
    }
    if(op==SC_COMMAND_BUTTON) {
        if(SCAPI_State.expected_command_generation!=SCAPI_State.command_generation)return SC_STALE_COMMAND;
        if(SCAPI_State.command_slot<1 || SCAPI_State.command_slot>9)return SC_BAD_ARGUMENT;
        return stage5_press(&stage5_buttons[SCAPI_State.command_slot-1]);
    }
    result=stage5_owned_selection(1,&u);if(result)return result;
    if(op==SC_UNLOAD_ONE) {
        ScapiCargo *cargo;BYTE *passenger,*node;
        if(stage5_mode())return SC_PENDING_TARGET;
        if(SCAPI_State.expected_cargo_serial!=SCAPI_State.cargo_serial)return SC_CARGO_CHANGED;
        if(SCAPI_State.cargo_slot>=SCAPI_State.cargo_count || SCAPI_State.cargo_slot>=8)return SC_BAD_ARGUMENT;
        cargo=&stage5_cargo[SCAPI_State.cargo_slot];passenger=(BYTE*)(uintptr_t)cargo->unit;
        node=(BYTE*)(uintptr_t)cargo->node;
        if(!cargo->enabled)return SC_COMMAND_UNAVAILABLE;
        if(!valid_unit(passenger) || passenger[0xa5]!=cargo->unique ||
           passenger[0x4c]!=U32(0x4f30c0) || passenger[0x4c]!=U32(0x4f30b4) ||
           *(BYTE**)(passenger+0x80)!=u || !(*(DWORD*)(passenger+0xdc)&0x40) ||
           !readable(node,0x46) || *(BYTE**)(node+0x26)!=passenger ||
           !(*(DWORD*)(node+0x18)&8) || *(DWORD*)(node+0x18)&2)return SC_CARGO_CHANGED;
        result=stage5_sync_selection();if(result)return result;
        SCAPI_State.before_queue=U32(0x650ce0);
        ((S5UnloadOneFn)AT(0x472390))(passenger);
        SCAPI_State.after_queue=U32(0x650ce0);SCAPI_State.command_phase=4;
        return SCAPI_State.after_queue==SCAPI_State.before_queue+3?SC_OK:SC_QUEUE_UNCONFIRMED;
    }
    if(op==SC_RESUME_CONSTRUCTION) {
        BYTE *building;DWORD kind;
        if(*(WORD*)(u+0x64)!=7)return SC_INCOMPATIBLE_STATE;
        result=point_on_screen(x,y);if(result)return result;
        building=((HitFn)AT(0x4c10d0))(x,y);
        if(!valid_unit(building))return SC_NO_SELECTABLE_UNIT;
        if(building[0x4c]!=U32(0x4f30c0))return SC_NOT_OWNED;
        kind=*(WORD*)(building+0x64);
        if(kind>=228 || !(U32(0x6b0358+kind*4)&1) || *(DWORD*)(building+0xdc)&1)return SC_INCOMPATIBLE_STATE;
        result=stage5_sync_selection();if(result)return result;
        SCAPI_State.before_queue=U32(0x650ce0);
        /* The target's native contextual right-click sender, independently
           verified at 470DD0..470E13 and its UI caller 47EAF7. */
        ((S5RightClickFn)AT(0x470dd0))(x,y,building,228,0);
        SCAPI_State.after_queue=U32(0x650ce0);SCAPI_State.command_phase=4;
        return SCAPI_State.after_queue==SCAPI_State.before_queue+10?SC_OK:SC_QUEUE_UNCONFIRMED;
    }
    if(op==SC_CANCEL_TRAIN_SLOT) {
        if(!stage5_queue_valid)return SC_INCOMPATIBLE_STATE;
        if(SCAPI_State.expected_queue_serial!=SCAPI_State.queue_serial)return SC_QUEUE_CHANGED;
        if(SCAPI_State.train_slot>=SCAPI_State.queue_count || SCAPI_State.train_slot>=5)return SC_BAD_ARGUMENT;
        if(!stage5_find_handler(0x47a860,0))return SC_COMMAND_UNAVAILABLE;
        result=stage5_sync_selection();if(result)return result;
        SCAPI_State.before_queue=U32(0x650ce0);
        ((S5CancelSlotFn)AT(0x471760))(SCAPI_State.train_slot);
        SCAPI_State.after_queue=U32(0x650ce0);SCAPI_State.command_phase=4;
        return SCAPI_State.after_queue==SCAPI_State.before_queue+3?SC_OK:SC_QUEUE_UNCONFIRMED;
    }
    if(op==SC_HALT_CONSTRUCTION) {
        if(*(WORD*)(u+0x64)!=7 || u[0x4d]!=33)return SC_INCOMPATIBLE_STATE;
        /* Native halt is the currently visible ESC (slot nine), not the
           ordinary Stop button sharing the same callback. */
        button=&stage5_buttons[8];
        if(button->handler!=0x47a8d0)return SC_COMMAND_UNAVAILABLE;
    } else if(op==SC_CANCEL_CONSTRUCTION) {
        DWORD kind=*(WORD*)(u+0x64);
        if(kind>=228 || !(U32(0x6b0358+kind*4)&1) || *(DWORD*)(u+0xdc)&1)return SC_INCOMPATIBLE_STATE;
        button=stage5_find_handler(0x47a8a0,0x47a8b0);
    } else if(op==SC_CANCEL_RESEARCH)button=stage5_find_handler(0x47a9e0,0);
    else if(op==SC_CANCEL_UPGRADE)button=stage5_find_handler(0x47ad60,0);
    /* Target1.05 uses A8B0/packet19 for unit morphs, but A8A0/packet18
       for building mutations (e.g. Hatchery -> Lair). Keep the actual
       enabled card and existing A8A0 incomplete-building guard. */
    else if(op==SC_CANCEL_MORPH)button=stage5_find_handler(0x47a8b0,0x47a8a0);
    else if(op==SC_CANCEL_ADDON)button=stage5_find_handler(0x47ad70,0);
    else return SC_BAD_OP;
    return stage5_press(button);
}
#endif
