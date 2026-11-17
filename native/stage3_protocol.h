#ifndef SCAPI_STAGE3_PROTOCOL_H
#define SCAPI_STAGE3_PROTOCOL_H
#include <windows.h>

/* Development transport, not an observation schema or arbitrary memory API.
   One mailbox per injected process; native selection capacity is 12. */
#define SCAPI_MAGIC 0x33504353u
#ifdef SCAPI_STAGE5
#ifdef SCAPI_VISIBLE_PHASE_CANDIDATE
#define SCAPI_VERSION 28u
#else
#define SCAPI_VERSION 27u
#endif
#elif defined(SCAPI_STAGE4)
#define SCAPI_VERSION 10u
#else
#define SCAPI_VERSION 2u
#endif
enum { SC_IDLE, SC_REQUEST, SC_BUSY, SC_DONE };
enum { SC_PROBE=1, SC_CAMERA=2, SC_SELECT_POINT=3, SC_MOVE_SELECTED=4,
       SC_DETACH=5, SC_UI_POINT=6, SC_SELECT_BOX=7, SC_SELECT_SAME=8,
       SC_MOVE_MINIMAP=9, SC_OBSERVE_SELECTED=10, SC_LEARN_DETAILS=11,
       SC_CAPTURE_START=12, SC_CAPTURE_ACK=13, SC_CAPTURE_STOP=14,SC_OBSERVE_VIEW=15,
       SC_COMMAND_CARD=16,SC_COMMAND_BUTTON=17,SC_COMMAND_TARGET=18,SC_CANCEL_TRAIN_SLOT=19,
       SC_ATTACK_POINT=20,SC_PATROL_POINT=21,SC_ATTACK_MINIMAP=22,SC_PATROL_MINIMAP=23,
       SC_HALT_CONSTRUCTION=24,SC_CANCEL_CONSTRUCTION=25,SC_CANCEL_RESEARCH=26,
       SC_CANCEL_UPGRADE=27,SC_CANCEL_MORPH=28,SC_CANCEL_ADDON=29,SC_RESUME_CONSTRUCTION=30,
       SC_RIGHT_CLICK=31,SC_CANCEL_PENDING=32,SC_UNLOAD_ONE=33,SC_GROUP_ASSIGN=34,SC_GROUP_SELECT=35,SC_FOCUS_SELECTED=36,SC_SAVE_VIEW=37,SC_OWN_OVERVIEW=38,
       SC_BUILDING_START=39,SC_BUILDING_STOP=40,SC_TERRAIN_VIEW=41,SC_PLACEMENT_PREVIEW=42,
       SC_BUILD_SITES=43,SC_MENU_STATE=45,SC_MENU_OPEN=46,SC_MENU_CLOSE=47,SC_RESOURCE_SITES=48 };
enum { SC_OK, SC_BAD_OP, SC_NO_GAME, SC_STALE_EPOCH, SC_PAUSED,
       SC_CAMERA_LOCKED, SC_UNINITIALIZED, SC_OUTSIDE_MAP, SC_NOT_ON_SCREEN,
       SC_UI_BLOCKED, SC_NO_SELECTABLE_UNIT, SC_NO_SELECTION,
       SC_STALE_SELECTION, SC_NOT_OWNED, SC_QUEUE_UNCONFIRMED,
       SC_BAD_THREAD, SC_DETACHED, SC_DISABLED, SC_PROTECTION_RESTORE_FAILED,
       SC_QUEUE_FULL, SC_BAD_ARGUMENT,SC_SCRIPT_CONTROLLED,
       SC_COMMAND_UNAVAILABLE,SC_STALE_COMMAND,SC_INCOMPATIBLE_STATE,
       SC_NO_PENDING_TARGET,SC_NOT_APPLIED,SC_PENDING_TARGET,SC_QUEUE_CHANGED,SC_CARGO_CHANGED,
       SC_OBSERVATION_PENDING };
typedef struct ScapiState {
    DWORD magic, version, bytes;
    volatile LONG state;
    DWORD serial, operation, expected_epoch, expected_selection;
    LONG x, y;
    DWORD enable_actions; /* explicit developer arm; install starts probe only */
    DWORD result, completed_serial;
    DWORD hook_installed, game_base, ui_thread, observed_thread;
    DWORD all_calls, site_calls, other_thread_calls, reentries;
    DWORD epoch, active, pause, raw_counter, tile_pointer;
    LONG camera_x, camera_y, camera_max_x, camera_max_y;
    DWORD width_tiles, height_tiles, selection_generation, selection_count;
    DWORD first_site_tick, last_site_tick, last_caller;
    DWORD before_queue, after_queue;
    LONG actual_x, actual_y;
    DWORD point_blocked;
    LONG x2,y2;
    DWORD add_selection, action_interval_ms;
    DWORD last_action_tick, executed_tick, finished_tick, action_count;
#ifdef SCAPI_STAGE4
    LONG minimap_left, minimap_top;
    DWORD minimap_width, minimap_height, minimap_scale;
    DWORD details_wait_ms, details_started_tick, details_active, details_ready;
    DWORD panel_available, panel_owned, panel_type, panel_hp, panel_max_hp;
    DWORD panel_shields, panel_max_shields, panel_has_shields, panel_energy;
    DWORD panel_armor_level, panel_shield_level, panel_armor_base;
    DWORD panel_ground_weapon, panel_air_weapon;
    DWORD panel_ground_base, panel_ground_bonus, panel_air_base, panel_air_bonus;
    DWORD panel_ground_level, panel_air_level;
    DWORD panel_has_energy, panel_name_address;
    DWORD events_address, events_bytes, events_capacity, events_epoch;
    DWORD events_enabled, events_fault, event_hooks, events_generation;
    DWORD view_wait_ms,view_started_tick,view_ready,view_address,view_count,view_serial;
    DWORD contact_wait_ms,dynamic_wait_ms,scene_frame_count,control_locked;
    DWORD scene_address,scene_bytes,scene_sample_max_ms;
    DWORD panel_owner,panel_visual;
    DWORD panel_has_health;
    DWORD panel_armor_bonus,panel_max_energy;
    DWORD panel_ground_upgrade_id,panel_air_upgrade_id,panel_armor_upgrade_id;
    DWORD panel_rescuable;
#endif
#ifdef SCAPI_STAGE5
    DWORD command_slot,expected_command_generation,command_generation;
    DWORD command_address,command_count,command_phase;
    DWORD queue_serial,expected_queue_serial,train_slot,queue_count;
    DWORD queue_type0,queue_type1,queue_type2,queue_type3,queue_type4;
    DWORD action_qpc_low,action_qpc_high,finished_qpc_low,finished_qpc_high;
    DWORD qpc_frequency_low,qpc_frequency_high;
    DWORD target_serial,expected_target_serial;
    DWORD cargo_address,cargo_count,cargo_serial,expected_cargo_serial,cargo_slot;
    DWORD command_sample_serial; /* odd while card/queue/cargo buffers change */
    DWORD queued_order,group_slot,pending_queued_order;
    DWORD own_address,own_count,own_serial;
    DWORD building_address,building_bytes,building_capacity,building_epoch;
    DWORD building_enabled,building_fault,building_ack;
    DWORD placement_address,placement_bytes,placement_serial;
    DWORD terrain_address,terrain_bytes,terrain_serial;
    DWORD auto_panel_address,auto_panel_bytes,auto_panel_sequence,auto_panel_ack,auto_panel_fault;
    DWORD queue_names_address; /* five selected-production name pointers, guarded with command_sample_serial */
    DWORD build_sites_address,build_sites_bytes,build_sites_serial;
    DWORD menu_open;
#ifdef SCAPI_VISIBLE_PHASE_CANDIDATE
    /* Opt-in diagnostic candidate only. No verified rendering boundary yet.
       Serial is odd during publication. Candidate identity is developer-only;
       visible_phase and capabilities remain zero (unknown / unavailable). */
    DWORD phase_sample_serial,phase_sample_epoch,phase_sample_tick;
    DWORD phase_candidate,phase_candidate_flags,phase_diagnostic;
    DWORD visible_phase,visible_phase_capabilities;
#endif
#endif
} ScapiState;
#endif
