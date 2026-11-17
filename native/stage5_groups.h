#ifndef SCAPI_STAGE5_GROUPS_H
#define SCAPI_STAGE5_GROUPS_H
/* Target 1.05 native groups, not an API-owned list of unit IDs. Static chain:
   4452A4..445339 -> 470D10(action=0,slot) -> packet 13/0 -> 462F50;
   462E30(slot) -> 49D8D0(real GUI) -> 470D10(1,slot,list,count);
   packet 13/1 -> 462FE0 (native ownership/lifecycle/compatibility cleanup).
   The game's table stores encoded identities at 4FFBC8+(player*18+slot)*48.
   Only its ten public digit slots are exposed. No persistent private unit list.
   Included after stage5_commands.h; run_request owns epoch/control/rate gates. */
typedef void *(FAST *S5DecodeGroupUnitFn)(unsigned);
typedef void (FAST *S5GroupCommandFn)(unsigned,unsigned,void**,unsigned);
typedef void (FAST *S5SelectGroupFn)(unsigned);

static DWORD stage5_groups_context(void) {
    /* UI-side functions use the local network index C4 and current player B4.
       A receive/replay context must never be mistaken for the local UI context. */
    if(U32(0x4f30c4)>=8 || U32(0x4f30c0)>=12 ||
       U32(0x4f30b4)!=U32(0x4f30c0) || U32(0x4f30b8)!=U32(0x4f30c4))
        return SC_UNINITIALIZED;
    return SC_OK;
}
static DWORD stage5_groups_capacity(DWORD bytes) {
    DWORD q=U32(0x650ce0),capacity=U32(0x650b88);
    SCAPI_State.before_queue=q;SCAPI_State.after_queue=q;
    return q>capacity || capacity-q<bytes ? SC_QUEUE_FULL : SC_OK;
}
static DWORD stage5_groups_members(DWORD slot,void **chosen,DWORD *count) {
    DWORD *native=(DWORD*)AT(0x4ffbc8+(U32(0x4f30c4)*18+slot)*48);
    DWORD raw=0,n=0,i,j;BYTE *unit,*sprite;
    if(!readable(native,48))return SC_UNINITIALIZED;
    while(raw<12 && native[raw])++raw;
    if(!raw)return SC_NO_SELECTION;
    /* Validate the whole native prefix before calling the original resolver:
       424B20 itself does not bound-check corrupt encoded low bits. */
    for(i=0;i<raw;++i) {
        DWORD encoded=native[i],index=encoded&0x7ff;
        if(!index || index>1700 || encoded>>11>255)return SC_INCOMPATIBLE_STATE;
        for(j=0;j<i;++j)if(native[j]==encoded)return SC_INCOMPATIBLE_STATE;
        unit=((S5DecodeGroupUnitFn)AT(0x424b20))(encoded);
        if(!unit)continue; /* native dead/old-uniqueness rejection */
        if(!valid_unit(unit))return SC_STALE_SELECTION;
        sprite=*(BYTE**)(unit+12);
        if(sprite[14]&0x20 || unit[0x4c]!=U32(0x4f30c0))continue;
        if(raw>1 && !((SelectableFn)AT(0x424940))(unit))continue;
        chosen[n++]=unit;
    }
    if(!n)return SC_NO_SELECTABLE_UNIT;
    *count=n;return SC_OK;
}
static DWORD stage5_groups_assign(DWORD slot) {
    DWORD result,q;
    result=stage5_owned_selection(0,NULL);if(result)return result;
    /* sync_selection reserves both its diff/full packets plus 32 bytes.
       The following native hotkey packet needs three of those bytes. */
    SCAPI_State.before_queue=U32(0x650ce0);
    result=stage5_sync_selection();SCAPI_State.after_queue=U32(0x650ce0);
    if(result)return result;
    q=SCAPI_State.after_queue;
    ((S5GroupCommandFn)AT(0x470d10))(0,slot,NULL,0);
    SCAPI_State.after_queue=U32(0x650ce0);
    /* This confirms enqueueing. The game's later receive pass owns the actual
       group-table write, just as it does for Ctrl+digit. Never auto-retry. */
    return SCAPI_State.after_queue==q+3?SC_OK:SC_QUEUE_UNCONFIRMED;
}
static DWORD stage5_groups_select(DWORD slot) {
    void *chosen[12]={0};DWORD n=0,i,result,q,after,bytes;
    int camera_x=I32(0x51bce4),camera_y=I32(0x51bce0);
    result=stage5_groups_members(slot,chosen,&n);if(result)return result;
    bytes=2+2*n;
    result=stage5_groups_capacity(2*bytes+3);if(result)return result;
    q=U32(0x650ce0);
    /* 470420 clears the native last-hotkey byte at 650950, updates its send
       cache, and sends at most one full-selection-sized diff. Therefore the
       subsequent original group handler cannot enter its repeated-digit
       centering branch. This is one group action, not n per-unit actions. */
    ((SendSelectionFn)AT(0x470420))(n,chosen);
    after=U32(0x650ce0);SCAPI_State.after_queue=after;
    if(after<q || after-q>bytes || U8(0x650950)!=0xff)return SC_QUEUE_UNCONFIRMED;
    ((S5SelectGroupFn)AT(0x462e30))(slot);
    SCAPI_State.after_queue=U32(0x650ce0);
    SCAPI_State.actual_x=I32(0x51bce4);SCAPI_State.actual_y=I32(0x51bce0);
    if(SCAPI_State.after_queue!=after+3 || SCAPI_State.actual_x!=camera_x ||
       SCAPI_State.actual_y!=camera_y)return SC_QUEUE_UNCONFIRMED;
    for(i=0;i<12;++i)
        if(((void**)AT(0x684940))[i]!=(i<n?chosen[i]:NULL))return SC_QUEUE_UNCONFIRMED;
    return SC_OK;
}
static DWORD stage5_groups_focus(void) {
    void **sel=(void**)AT(0x684940);DWORD i,raw=0,n=0,result;
    LONG sx=0,sy=0;BYTE *unit,*sprite;
    result=stage5_owned_selection(0,NULL);if(result)return result;
    if(U8(0x684914))return SC_CAMERA_LOCKED;
    for(i=0;i<12;++i)if(sel[i])++raw;
    for(i=0;i<12;++i)if(sel[i]) {
        unit=(BYTE*)sel[i];sprite=*(BYTE**)(unit+12);
        if(sprite[14]&0x20)continue;
        if(raw>1 && !((SelectableFn)AT(0x424940))(unit))continue;
        sx+=*(short*)(sprite+0x14);sy+=*(short*)(sprite+0x16);++n;
    }
    if(!n)return SC_NO_SELECTABLE_UNIT;
    /* Exact target 462DC0..462DE6 arithmetic, applied to the actual current
       selection rather than overwriting a native group to call that function:
       mean sprite position, native 320/200 offsets, eight-pixel alignment.
       405070 remains responsible for the real map-edge clamp. */
    ((CameraFn)AT(0x405070))(((sx/(LONG)n)-320)&~7,((sy/(LONG)n)-200)&~7);
    SCAPI_State.actual_x=I32(0x51bce4);SCAPI_State.actual_y=I32(0x51bce0);
    return SC_OK;
}
static DWORD stage5_groups_request(void) {
    DWORD op=SCAPI_State.operation,result;
    if(SCAPI_State.queued_order)return SC_BAD_ARGUMENT;
    if(op!=SC_GROUP_ASSIGN && op!=SC_GROUP_SELECT && op!=SC_FOCUS_SELECTED)return SC_BAD_OP;
    if(op!=SC_FOCUS_SELECTED && SCAPI_State.group_slot>=10)return SC_BAD_ARGUMENT;
    result=stage5_groups_context();if(result)return result;
    /* Existing explicit ESC remains responsible for an armed build/target
       cursor. A group operation cannot silently cancel that separate action. */
    if(stage5_mode())return SC_PENDING_TARGET;
    if(op==SC_FOCUS_SELECTED)return stage5_groups_focus();
    if(op==SC_GROUP_ASSIGN)return stage5_groups_assign(SCAPI_State.group_slot);
    return stage5_groups_select(SCAPI_State.group_slot);
}
#endif
