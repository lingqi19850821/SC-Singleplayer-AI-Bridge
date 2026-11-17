#ifndef SCAPI_STAGE5_VISIBLE_PHASE_H
#define SCAPI_STAGE5_VISIBLE_PHASE_H
/* Target 1.05 modal construction diagnostics, never a visible-stage event.
   Source: evidence/computer-use-20261114/cu-lifecycle-design/visible-phase-source.md.
   The existing 446C2D clock sample is after modal construction but is NOT a
   verified draw/presentation boundary. Do not enable a capability from root
   identity, CTRL_VISIBLE, repeated samples, pause or future result flags.
   There are no engine calls, writes, new hooks, result-table reads or inputs. */
enum { S5_PHASE_UNKNOWN=0,S5_PHASE_TIPS=1,S5_PHASE_VICTORY=2,S5_PHASE_DEFEAT=3 };
enum { S5_PHASE_UNSAMPLED=0,S5_PHASE_NO_GAME=1,S5_PHASE_NO_ROOT=2,
       S5_PHASE_BAD_ROOT=3,S5_PHASE_WRONG_TYPE=4,S5_PHASE_OTHER_MODAL=5,
       S5_PHASE_FLAG_HIDDEN=6,S5_PHASE_DRAW_UNVERIFIED=7 };
static void stage5_visible_phase_sample(DWORD tick) {
    BYTE *root;DWORD candidate=0,flags=0,diagnostic=S5_PHASE_NO_GAME,handler;
    InterlockedIncrement((LONG*)&SCAPI_State.phase_sample_serial);
    if(SCAPI_State.active && SCAPI_State.tile_pointer) {
        diagnostic=S5_PHASE_NO_ROOT;
        if(readable(AT(0x650888),sizeof(root))) {
            root=*(BYTE**)AT(0x650888);
            if(root) {
                diagnostic=S5_PHASE_BAD_ROOT;
                if(readable(root,0x46)) {
                    flags=*(DWORD*)(root+0x18);
                    diagnostic=S5_PHASE_WRONG_TYPE;
                    if(!*(WORD*)(root+0x22)) {
                        handler=*(DWORD*)(root+0x2a);
                        if(handler==(DWORD)(uintptr_t)AT(0x4b8350))candidate=S5_PHASE_TIPS;
                        else if(handler==(DWORD)(uintptr_t)AT(0x4c1f70))candidate=S5_PHASE_VICTORY;
                        else if(handler==(DWORD)(uintptr_t)AT(0x4c21c0))candidate=S5_PHASE_DEFEAT;
                        diagnostic=!candidate?S5_PHASE_OTHER_MODAL:
                            !(flags&8u)?S5_PHASE_FLAG_HIDDEN:S5_PHASE_DRAW_UNVERIFIED;
                    }
                }
            }
        } else diagnostic=S5_PHASE_BAD_ROOT;
    }
    SCAPI_State.phase_sample_epoch=SCAPI_State.epoch;
    SCAPI_State.phase_sample_tick=tick;
    SCAPI_State.phase_candidate=candidate;
    SCAPI_State.phase_candidate_flags=flags;
    SCAPI_State.phase_diagnostic=diagnostic;
    /* Fail closed even for a valid, enabled, repeatedly sampled root. Disabled
       is independent from visibility: its bit never removes modal identity. */
    SCAPI_State.visible_phase=S5_PHASE_UNKNOWN;
    SCAPI_State.visible_phase_capabilities=0;
    InterlockedIncrement((LONG*)&SCAPI_State.phase_sample_serial);
}
#endif
