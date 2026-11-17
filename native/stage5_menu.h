#ifndef SCAPI_STAGE5_MENU_H
#define SCAPI_STAGE5_MENU_H
/* Target 1.05 normal Game Menu, not trigger Pause/Unpause or pause-flag writes.
   Static mapping: evidence/api-all-fixes-20261112/menu-static/.
   The HUD id1 callback 4A8930 calls 47A630 -> 46D220 -> 4D5E90 and returns.
   The current Game Menu id-3 button dispatches 4DB4C0 -> 47A2D0 -> 479E80;
   native dialog destruction sends event1 to 46D1D0, including normal unpause.
   All calls remain on the bridge's existing native UI thread. */
typedef struct S5MenuEvent {
    DWORD value,parameter,reserved;
    WORD type,x,y,padding;
} S5MenuEvent;
typedef DWORD (FAST *S5MenuButtonFn)(void*,const S5MenuEvent*);
#ifndef S5_MENU_BUTTON_CALL
#define S5_MENU_BUTTON_CALL(button,event) \
    ((S5MenuButtonFn)(uintptr_t)(*(DWORD*)((button)+0x2a)))((button),(event))
#endif
static BYTE *stage5_menu_owned_root;
static DWORD stage5_menu_owned_epoch;
static const BYTE s5_menu_open_code[]={
    0xc7,0x05,0xbc,0x28,0x65,0x00,0x80,0x9e,0x47,0x00,
    0xba,0xd0,0xa2,0x47,0x00,0xb9,0xc8,0x32,0x4f,0x00,0xe9,0xd7,0x2b,0xff,0xff
};
static const BYTE s5_menu_resume_code[]={
    0x66,0x83,0x7a,0x0c,0x0e,0x75,0x17,0x8b,0x02,0x83,0xe8,0x02,0x75,0x10,
    0x66,0x8b,0x41,0x20,0x66,0x3d,0xfd,0xff,0x74,0x0b,0x66,0x3d,0xfe,0xff,
    0x74,0x05,0xe9,0x1d,0x00,0x00,0x00,0x0f,0xbf,0xc0,0xa3,0x34,0x42,0x6b,
    0x00,0x8b,0x49,0x32,0xe8,0x6d,0xc4,0xff,0xff,0xb8,0x01,0x00,0x00,0x00,0xc3
};
static const BYTE s5_menu_hud_code[]={
    0x53,0x56,0x57,0x8b,0xfa,0x33,0xc0,0x8b,0xf1,0x66,0x8b,0x47,0x0c,
    0x83,0xf8,0x03,0x74,0x58,0x83,0xf8,0x0e,0x0f,0x85,0xdc,0x00,0x00,0x00,
    0x8b,0x07,0x83,0xe8,0x00,0x74,0x26,0x83,0xe8,0x02,0x0f,0x85,0xcc,0x00,0x00,0x00,
    0x66,0x83,0x7e,0x20,0x01,0x0f,0x85,0xc1,0x00,0x00,0x00,
    0xe8,0xc5,0x1c,0xfd,0xff,0x8b,0xd7,0x8b,0xce,0xe8,0x4c,0x2b,0x03,0x00,
    0x5f,0x5e,0x5b,0xc3
};
static int s5_menu_code_valid(DWORD address,const BYTE *bytes,SIZE_T length) {
    return readable(AT(address),length) && !memcmp(AT(address),bytes,length);
}
static int s5_menu_dialog(BYTE *root,DWORD handler) {
    return readable(root,0x46) && !*(WORD*)(root+0x22) &&
        *(DWORD*)(root+0x2a)==(DWORD)(uintptr_t)AT(handler) &&
        (*(DWORD*)(root+0x18)&8u) && !(*(DWORD*)(root+0x18)&2u);
}
static BYTE *s5_menu_current_root(void) {
    BYTE *root=*(BYTE**)AT(0x650888);
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer || !U32(0x653940) ||
       U8(0x650bdc) || U8(0x6b0cf8) || U8(0x65017c)!=1 ||
       U32(0x6528bc)!=(DWORD)(uintptr_t)AT(0x479e80) ||
       *(BYTE**)AT(0x6b424c)!=root || !s5_menu_dialog(root,0x47a2d0))return NULL;
    return root;
}
static void stage5_menu_sample(void) {
    BYTE *root=s5_menu_current_root();
    SCAPI_State.pause=U32(0x653940); /* mailbox observation, never the game flag */
    SCAPI_State.menu_open=root!=NULL;
    if(stage5_menu_owned_epoch!=SCAPI_State.epoch || root!=stage5_menu_owned_root) {
        stage5_menu_owned_root=NULL;stage5_menu_owned_epoch=0;
    }
}
static int s5_menu_next(BYTE *node,BYTE **next) {
    if(!node){*next=NULL;return 1;}
    if(!readable(node,0x46))return 0;
    *next=*(BYTE**)node;return 1;
}
static BYTE *s5_menu_button(BYTE *root,WORD id,DWORD handler) {
    BYTE *node,*slow,*fast,*found=NULL;DWORD flags;
    if(!readable(root,0x46))return NULL;
    node=slow=fast=*(BYTE**)(root+0x42);
    /* The native control chain has no imposed policy cap. Detect malformed
       cycles without an allocation or an unbounded visited-node array. */
    while(node) {
        if(!readable(node,0x46) || *(BYTE**)(node+0x32)!=root)return NULL;
        if(*(WORD*)(node+0x20)==id) {
            flags=*(DWORD*)(node+0x18);
            if(found || !(flags&8u) || (flags&2u) ||
               *(DWORD*)(node+0x2a)!=(DWORD)(uintptr_t)AT(handler))return NULL;
            found=node;
        }
        if(!s5_menu_next(node,&node) || !s5_menu_next(slow,&slow) ||
           !s5_menu_next(fast,&fast) || !s5_menu_next(fast,&fast))return NULL;
        if(fast && slow==fast)return NULL;
    }
    return found;
}
static DWORD stage5_menu_request(DWORD op) {
    BYTE *root,*button;S5MenuEvent event;
    if(op!=SC_MENU_STATE && op!=SC_MENU_OPEN && op!=SC_MENU_CLOSE)return SC_BAD_OP;
    stage5_menu_sample();
    if(SCAPI_State.expected_epoch!=SCAPI_State.epoch)return SC_STALE_EPOCH;
    if(!SCAPI_State.active || !SCAPI_State.tile_pointer)return SC_NO_GAME;
    if(SCAPI_State.hook_installed!=1)return SC_DISABLED;
    if(op==SC_MENU_STATE)return SC_OK;
    /* This private host control lane must protect initial input compaction
       before gameplay is armed. It never enables ordinary game actions;
       hook/epoch/lifecycle authority and native Menu ownership still apply. */
    if(U8(0x6b0cf8))return SC_SCRIPT_CONTROLLED;
    /* This menu pauses only single-player. Never present a multiplayer menu
       as a stopped game or invoke a separate native multiplayer Pause order. */
    if(U8(0x650bdc))return SC_INCOMPATIBLE_STATE;
    memset(&event,0,sizeof(event));event.type=0x0e;event.value=2;
    if(op==SC_MENU_OPEN) {
        if(SCAPI_State.menu_open)return SC_OK; /* existing human menu stays unowned */
        if(U32(0x653940))return SC_PAUSED;
        if(*(BYTE**)AT(0x650888))return SC_INCOMPATIBLE_STATE;
        root=*(BYTE**)AT(0x68d1ac);
        if(!s5_menu_dialog(root,0x4a8af0))return SC_COMMAND_UNAVAILABLE;
        button=s5_menu_button(root,1,0x4a8930);
        if(!button)return SC_COMMAND_UNAVAILABLE;
        if(!s5_menu_code_valid(0x47a630,s5_menu_open_code,sizeof(s5_menu_open_code)) ||
           !s5_menu_code_valid(0x4a8930,s5_menu_hud_code,sizeof(s5_menu_hud_code)))return SC_UNINITIALIZED;
        S5_MENU_BUTTON_CALL(button,&event);
        root=s5_menu_current_root();
        if(root){stage5_menu_owned_root=root;stage5_menu_owned_epoch=SCAPI_State.epoch;}
        stage5_menu_sample();
        return root?SC_OK:SC_QUEUE_UNCONFIRMED;
    }
    root=s5_menu_current_root();
    if(!root || root!=stage5_menu_owned_root || stage5_menu_owned_epoch!=SCAPI_State.epoch)
        return SC_INCOMPATIBLE_STATE;
    button=s5_menu_button(root,0xfffdu,0x4db4c0);
    if(!button)return SC_COMMAND_UNAVAILABLE;
    if(!s5_menu_code_valid(0x4db4c0,s5_menu_resume_code,sizeof(s5_menu_resume_code)))return SC_UNINITIALIZED;
    S5_MENU_BUTTON_CALL(button,&event);
    stage5_menu_sample();
    return !U32(0x653940) && !*(BYTE**)AT(0x650888)?SC_OK:SC_QUEUE_UNCONFIRMED;
}
#endif
