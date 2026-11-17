#ifndef SCAPI_STAGE4_VIEW_H
#define SCAPI_STAGE4_VIEW_H
#include "stage4_attention_clock.h"
/* Natural target pool bound. Raw tracking is private, not public unit identity.
   Passive dynamic values require continuous verified screen visibility. */
typedef struct ScapiViewItem {
    DWORD visual;LONG x,y;DWORD relation;DWORD hp,shields,energy,flags;
    DWORD private_type,private_owner,private_ground_group,private_air_group,private_armor_group;
    DWORD private_ground_weapon,private_air_weapon,private_health_visible;
    LONG private_own_armor_extra;
#ifdef SCAPI_STAGE5
    LONG private_terrain_x,private_terrain_y;
    DWORD private_terrain_domain;
#endif
} ScapiViewItem;
typedef struct ScapiSceneTrack {
    DWORD live,unique,owner,type,sprite;
    ScapiAttentionClock screen,outside;
    DWORD in_camera,contact_id,contact_state;
} ScapiSceneTrack;
enum { S4_VIEW_DYNAMIC=1,S4_VIEW_SHIELDS=2,S4_VIEW_ENERGY=4,S4_VIEW_HEALTH=8,
       S4_VIEW_RESCUABLE=16 };
/* Protocol27 packs these only after every old boolean-health use below.
   The field remains a DWORD and both historical row sizes stay unchanged. */
enum { S4_VISUAL_CLOAKED=2,S4_VISUAL_BURROWED=4,S4_VISUAL_ENSNARED=8,
       S4_VISUAL_CONSTRUCTION=16,S4_VISUAL_ALMOST_BUILT=32,S4_VISUAL_DAMAGE=64 };
typedef BYTE *(FAST *S4FindImageRangeFn)(BYTE*,DWORD,DWORD,DWORD);
#ifndef S4_VISUAL_FIND_IMAGE
/* Native 411E40: ECX=sprite, EDX unused, stack=(first,last), ret8.
   Reuse its read-only image lookup; never inspect a hidden status timer. */
#define S4_VISUAL_FIND_IMAGE(sp,lo,hi) ((S4FindImageRangeFn)AT(0x411e40))((sp),0,(lo),(hi))
#endif
static int stage4_ensnare_visual(BYTE *sp) {
    BYTE *image;
    if(!readable(sp,0x24) || !*(BYTE**)(sp+0x1c))return 0;
    image=S4_VISUAL_FIND_IMAGE(sp,0x180u,0x182u);
    /* These are the actual green images created/removed at 43E3FC/43E33D.
       A hidden or wholly clipped image does not establish a visible effect. */
    return readable(image,0x2c) && *(WORD*)(image+8)>=0x180u &&
        *(WORD*)(image+8)<=0x182u && !(*(WORD*)(image+0xc)&0x40u) &&
        *(short*)(image+0x28)>0 && *(short*)(image+0x2a)>0;
}
static DWORD stage4_visual_status_bits(BYTE *u,BYTE *sp) {
    BYTE *primary=*(BYTE**)(sp+0x18),*sub;
    DWORD bits=0,status=*(DWORD*)(u+0xdc);
    /* 43D5C1/43D5D0 set native unit Burrowed and sprite Burrowed together.
       Detection permissions still come exclusively from the earlier gate. */
    if((status&0x10u) && (sp[0xe]&0x40u))bits|=S4_VISUAL_BURROWED;
    /* 4110E0 recognizes precisely 2/3/5/6 as the cloak image family.
       4/7 are decloak modes and are deliberately not labelled cloaked. */
    if(!(status&0x10u) && readable(primary,0xe) &&
       !(*(WORD*)(primary+0xc)&0x40u) &&
       (primary[0xa]==2 || primary[0xa]==3 || primary[0xa]==5 || primary[0xa]==6))
        bits|=S4_VISUAL_CLOAKED;
    if(stage4_ensnare_visual(sp))bits|=S4_VISUAL_ENSNARED;
    else {
        /* 41F330 attaches the overlay to the weapon subunit when present. */
        sub=*(BYTE**)(u+0x70);
        if(sub!=u && valid_unit(sub) && sub[0x4c]==u[0x4c] &&
           stage4_ensnare_visual(*(BYTE**)(sub+0xc)))bits|=S4_VISUAL_ENSNARED;
    }
    return bits; /* No hallucination identity or parasite bit is included. */
}
static int stage4_image_visible(BYTE *image) {
    return readable(image,0x2c) && !(*(WORD*)(image+0xc)&0x40u) &&
        *(short*)(image+0x28)>0 && *(short*)(image+0x2a)>0;
}
static DWORD stage4_building_damage_effects(BYTE *sp) {
    BYTE *image=*(BYTE**)(sp+0x1c),*base=AT(0x52eba0);
    DWORD steps=0,count=0;uintptr_t offset;
    /* 408CC8..408CF5 and 40C065..40C094 prove the native 5000 x 0x40
       image pool. This is its actual capacity, not a display truncation. */
    while(image) {
        if((uintptr_t)image<(uintptr_t)base || ++steps>5000u)return 0;
        offset=(uintptr_t)image-(uintptr_t)base;
        if(offset>=5000u*0x40u || (offset&0x3fu) || !readable(image,0x40) ||
           *(BYTE**)(image+0x3c)!=sp)return 0;
        /* Native damage helpers 411B50/411C70 use precisely these two
           contiguous small/large fire-or-bleeding image families. */
        if(*(WORD*)(image+8)>=0x1c2u && *(WORD*)(image+8)<=0x1edu &&
           stage4_image_visible(image))++count;
        image=*(BYTE**)(image+4);
    }
    return count; /* A malformed chain establishes no damage appearance. */
}
static DWORD stage4_building_appearance(BYTE *sp,DWORD type,DWORD *damage_count) {
    BYTE *primary=*(BYTE**)(sp+0x18);WORD *normal;DWORD construction,bits=0;
    *damage_count=0;
    if(type>=228 || !(U32(0x6b0358+type*4u)&1u) ||
       !stage4_image_visible(primary))return 0;
    /* 409E98 stores the requested iscript animation at image+16;
       native construction finish blocks request AlmostBuilt (0x0F). */
    if(primary[0x16]==0xfu)bits|=S4_VISUAL_ALMOST_BUILT;
    else {
        /* 41F435 chooses this type's loaded construction image; 41F44B
           restores the sprite's default image. Ambiguous equal IDs omit. */
        construction=U32(0x6ad000+type*4u);
        normal=(WORD*)(AT(0x68be88)+*(WORD*)(sp+8)*2u);
        if(construction && construction<=0xffffu && readable(normal,2) &&
           construction!=*normal && *(WORD*)(primary+8)==construction)
            bits|=S4_VISUAL_CONSTRUCTION;
    }
    *damage_count=stage4_building_damage_effects(sp);
    if(*damage_count)bits|=S4_VISUAL_DAMAGE;
    return bits;
}
static ScapiViewItem scene_view[1700],view_items[1700];
static ScapiSceneTrack scene_tracks[1700];
static DWORD scene_epoch,scene_count,contact_serial;
#ifdef SCAPI_STAGE5
/* Called only after the existing scene qualification and dwell. The public
   hit-test anchor may be a footprint edge; never substitute it for footing. */
static void stage5_view_terrain_source(ScapiViewItem *item,BYTE *u,BYTE *sp) {
    int x=*(short*)(sp+20),y=*(short*)(sp+22);
    item->private_terrain_x=item->private_terrain_y=-1;
    item->private_terrain_domain=(*(DWORD*)(u+0xdc)&4u)?1u:0u;
    if(point_on_screen(x,y)==SC_OK) {
        item->private_terrain_x=x;item->private_terrain_y=y;
    }
}
#endif
static void stage4_forget(ScapiSceneTrack *track) {
    if(track->contact_state==1 && SCAPI_State.events_enabled)
        stage4_publish(S4_EVENT_STALE,0,0,track->contact_id);
    memset(track,0,sizeof(*track));
}
static void stage4_view_reset(void) {
    DWORD i;for(i=0;i<1700;++i)stage4_forget(&scene_tracks[i]);
    scene_count=0;
    SCAPI_State.view_started_tick=0;SCAPI_State.view_ready=0;SCAPI_State.view_count=0;
}
static void stage4_contacts_restart(void) {
    DWORD i;for(i=0;i<1700;++i) {
        scapi_attention_reset(&scene_tracks[i].outside);
        scene_tracks[i].contact_id=scene_tracks[i].contact_state=0;
        scene_tracks[i].in_camera=0;
    }
}
static int stage4_view_anchor(BYTE *u,int *ax,int *ay) {
    BYTE *sp=*(BYTE**)(u+12);WORD type=*(WORD*)(u+0x64);
    DWORD mask=U32(0x65098c),status=*(DWORD*)(u+0xdc);
    int x=*(short*)(sp+20),y=*(short*)(sp+22),xs[3],ys[3],l,t,r,b,i,j;
    int w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    int h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    short *e=(short*)(AT(0x6af898)+type*8u);
    if(type>=228 || !readable(e,8) || !(sp[12]&mask) ||
       ((status&0x300) && !(*(DWORD*)(u+0xe4)&mask)))return 0;
    if(point_on_screen(x,y)==SC_OK && ((HitFn)AT(0x4c10d0))(x,y)==u){*ax=x;*ay=y;return 1;}
    l=x-e[0];t=y-e[1];r=x+e[2];b=y+e[3];
    if(l<SCAPI_State.camera_x)l=SCAPI_State.camera_x;
    if(t<SCAPI_State.camera_y)t=SCAPI_State.camera_y;
    if(r>=SCAPI_State.camera_x+w)r=SCAPI_State.camera_x+w-1;
    if(b>=SCAPI_State.camera_y+h)b=SCAPI_State.camera_y+h-1;
    if(l>r || t>b)return 0;
    xs[0]=l;xs[1]=(l+r)/2;xs[2]=r;ys[0]=t;ys[1]=(t+b)/2;ys[2]=b;
    for(i=0;i<3;++i)for(j=0;j<3;++j)
        if(point_on_screen(xs[i],ys[j])==SC_OK && ((HitFn)AT(0x4c10d0))(xs[i],ys[j])==u) {
            *ax=xs[i];*ay=ys[j];return 1;
        }
    return 0; /* conservative omission if no unoccluded point was found */
}
static void stage4_contact_hint(BYTE *sp,int *x,int *y) {
    DWORD scale=SCAPI_State.minimap_scale;*x=*y=-1;
    /* Optional coarse output only; never part of contact eligibility.
       Existing map geometry is used, no minimap image/drawing hook. */
    if(!scale || !SCAPI_State.minimap_width || !SCAPI_State.minimap_height)return;
    *x=SCAPI_State.minimap_left+*(short*)(sp+20)/(int)scale;
    *y=SCAPI_State.minimap_top+*(short*)(sp+22)/(int)scale;
}
static void stage4_view_sample(void) {
    DWORD i,now=real_tick(),start=now,local=U32(0x4f30c0),mask=U32(0x65098c);
    int configured=SCAPI_State.view_wait_ms && SCAPI_State.view_wait_ms<0x80000000u &&
        SCAPI_State.dynamic_wait_ms && SCAPI_State.dynamic_wait_ms<0x80000000u;
    int observation_paused=SCAPI_State.pause && !U8(0x6b0cf8);
#ifdef SCAPI_STAGE5
    stage5_buildings_begin(now);
#endif
    SCAPI_State.scene_address=(DWORD)(uintptr_t)scene_tracks;
    SCAPI_State.scene_bytes=sizeof(scene_tracks);
    if(scene_epoch!=SCAPI_State.epoch || !SCAPI_State.active || observation_paused) {
        stage4_view_reset();scene_epoch=SCAPI_State.epoch;
    }
    if(!SCAPI_State.active || observation_paused || !SCAPI_State.tile_pointer || local>=12 ||
       (!configured && !SCAPI_State.events_enabled)) {
#ifdef SCAPI_STAGE5
        stage5_buildings_end(now);
#endif
        return;
    }
    scene_count=0;++SCAPI_State.scene_frame_count;
#ifdef SCAPI_STAGE5
    stage5_entity_frame_begin();
#endif
    for(i=0;i<1700;++i) {
        BYTE *u=AT(0x5b2e68)+i*0x148u,*sp=*(BYTE**)(u+12);
        ScapiSceneTrack *track=&scene_tracks[i];
        DWORD type,owner,rel,elapsed;int inside,anchor=0,x=0,y=0;
        if(!sp || (u[0x4d]==0 && u[0x4e]==1) || !readable(sp,0x24)) {
            if(track->live)stage4_forget(track);continue;
        }
        owner=u[0x4c];type=*(WORD*)(u+0x64);
#ifdef SCAPI_STAGE5
        /* All own actors are lawful roster sources even outside the camera.
           Never call the identity tracker for hidden foreign entities. */
        if(owner==local && type<228)stage5_entity_observe(u,1);
#endif
        /* Apply the existing screen-anchor cloak/burrow detection rule before
           tracking, so an undetected unit cannot accrue an offscreen contact. */
        if(owner>=12 || type>=228 || !(sp[12]&mask) ||
           ((*(DWORD*)(u+0xdc)&0x300u) && !(*(DWORD*)(u+0xe4)&mask))) {
            if(track->live)stage4_forget(track);continue;
        }
        if(!track->live || track->unique!=u[0xa5] || track->owner!=owner ||
           track->type!=type || track->sprite!=(DWORD)(uintptr_t)sp) {
            stage4_forget(track);track->live=1;track->unique=u[0xa5];
            track->owner=owner;track->type=type;track->sprite=(DWORD)(uintptr_t)sp;
        }
        rel=owner==local?1:owner>=8?4:(U8(0x50d39c+12*local+owner)?2:3);
        inside=operable_part(u);
        if(inside)anchor=stage4_view_anchor(u,&x,&y);
        if(SCAPI_State.events_enabled && rel==3) {
            if(inside) {
                scapi_attention_reset(&track->outside);
                if(track->contact_state==1)stage4_publish(S4_EVENT_STALE,0,0,track->contact_id);
                track->contact_state=0;
                if(!track->in_camera && anchor)stage4_publish(S4_EVENT_IN_VIEW,0,0,0);
            } else {
                elapsed=scapi_attention_elapsed(&track->outside,now,1);
                if(!track->contact_state && SCAPI_State.contact_wait_ms &&
                   SCAPI_State.contact_wait_ms<0x80000000u && elapsed>=SCAPI_State.contact_wait_ms) {
                    int hx,hy;stage4_contact_hint(sp,&hx,&hy);
                    if(contact_serial==0xffffffffu)track->contact_state=2;
                    else {
                        track->contact_id=++contact_serial;
                        track->contact_state=stage4_publish(S4_EVENT_CONTACT,hx,hy,track->contact_id)==0?1:2;
                    }
                }
            }
        } else scapi_attention_reset(&track->outside);
        track->in_camera=inside && anchor;
        elapsed=scapi_attention_elapsed(&track->screen,now,inside && anchor);
        if(configured && track->screen.active && elapsed>=SCAPI_State.view_wait_ms) {
            ScapiViewItem *item=&scene_view[scene_count++];
#if SCAPI_VERSION >= 27
            DWORD damage_effect_count;
#endif
            memset(item,0,sizeof(*item));item->visual=*(WORD*)(sp+8);
            item->x=x;item->y=y;item->relation=rel;
            item->private_type=type;item->private_owner=owner;
#ifdef SCAPI_STAGE5
            /* The private observation serial never reuses packet incarnations.
               Foreign entities reach it only through the lawful dwell gate. */
            item->flags|=stage5_entity_observe(u,owner==local)<<5;
            stage5_view_terrain_source(item,u,sp);
            if(rel==3 && (U32(0x6b0358+type*4u)&1u))
                stage5_buildings_seen(u,type,owner,item->visual,x,y,now);
#endif
            if(owner!=local && stage4_rescuable_owner(owner))item->flags|=S4_VIEW_RESCUABLE;
            item->private_ground_weapon=panel_weapon_kind(u,0);
            item->private_ground_group=item->private_ground_weapon<130?U8(0x6b1988+item->private_ground_weapon):61;
            item->private_air_weapon=panel_weapon_kind(u,1);
            item->private_air_group=item->private_air_weapon<130?U8(0x6b1988+item->private_air_weapon):61;
            item->private_armor_group=U8(0x6b00a0+type);
            item->private_health_visible=panel_health_visible(u);
            if(owner==local && item->private_health_visible)
                item->private_own_armor_extra=(LONG)(((UnitValueFn)AT(0x43b220))(u)&255u)-
                    (LONG)panel_upgrade(local,item->private_armor_group);
            if(elapsed>=SCAPI_State.dynamic_wait_ms ||
               (SCAPI_State.selection_count==1 && ((BYTE**)AT(0x684940))[0]==u)) {
                item->flags|=S4_VIEW_DYNAMIC;
                if(item->private_health_visible) {
                    item->flags|=S4_VIEW_HEALTH;item->hp=(*(DWORD*)(u+8)+255u)/256u;
                    if(U8(0x6abb68+type)) {item->flags|=S4_VIEW_SHIELDS;item->shields=*(DWORD*)(u+0x60)/256u;}
                }
                if(owner==local && (U32(0x6b0358+type*4)&0x200000)) {
                    item->flags|=S4_VIEW_ENERGY;item->energy=*(WORD*)(u+0xa2)/256u;
                }
            }
#if SCAPI_VERSION >= 27
            /* Keep health's old 0/1 meaning through HP and own-grade work;
               publish only visual facts of this already qualified view row. */
            item->private_health_visible|=stage4_visual_status_bits(u,sp);
            item->private_health_visible|=stage4_building_appearance(sp,type,&damage_effect_count);
            /* Protocol27 retires the public HP slot. Keep only the count of
               currently rendered building damage effects, never hidden HP. */
            item->hp=damage_effect_count;
            /* Protocol27 reuses the retired public energy slot privately.
               The host may compare this current lawful name against learned
               candidates; it must not learn or publish an unknown label. */
            item->energy=(DWORD)(uintptr_t)((UnitNameFn)AT(0x425390))(type);
#endif
        }
    }
#ifdef SCAPI_STAGE5
    stage5_entity_frame_end();
    stage5_buildings_end(now);
#endif
    SCAPI_State.view_ready=scene_count!=0;
    now=real_tick()-start;
    if(now>SCAPI_State.scene_sample_max_ms)SCAPI_State.scene_sample_max_ms=now;
}
static DWORD stage4_view(void) {
#ifdef SCAPI_STAGE5
    if(stage5_entity_fault)return SC_INCOMPATIBLE_STATE;
#endif
    SCAPI_State.view_count=0;SCAPI_State.view_serial=SCAPI_State.serial;
    SCAPI_State.view_address=(DWORD)(uintptr_t)view_items;
    if(!SCAPI_State.view_wait_ms || SCAPI_State.view_wait_ms>=0x80000000u ||
       !SCAPI_State.dynamic_wait_ms || SCAPI_State.dynamic_wait_ms>=0x80000000u)return SC_BAD_ARGUMENT;
    memcpy(view_items,scene_view,scene_count*sizeof(*view_items));
    SCAPI_State.view_count=scene_count;return SC_OK;
}
#endif
