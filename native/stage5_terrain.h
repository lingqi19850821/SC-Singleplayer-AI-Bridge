#ifndef SCAPI_STAGE5_TERRAIN_H
#define SCAPI_STAGE5_TERRAIN_H
/* Target 1.05 adaptation of BWAPI Map/placement semantics. Every engine call
   runs synchronously on the existing UI mailbox thread. No command is issued.
   21x14 is the target renderer's own maximum tile window (404479/404902).
   Placement preview buffers are the native two 6x8 grids, never a new cap. */
#define S5_TERRAIN_CELLS (21*14)
enum { S5T_EXPLORED=1,S5T_VISIBLE=2,S5T_BUILDABLE=4,S5T_CREEP_KNOWN=8,
       S5T_CREEP=16,S5T_POWER_KNOWN=32,S5T_POWER=64,S5T_KNOWN=128,
       S5T_BLOCKS_VIEW=256,S5T_MINI_KNOWN=512,S5T_OCC_OWN=1024,
       S5T_OCC_ALLY=2048,S5T_OCC_ENEMY=4096,S5T_OCC_NEUTRAL=8192,
       S5T_OCC_BUILDING=16384,S5T_STATIC_GROUND=32768,S5T_CREEP_REMEMBERED=65536 };
typedef struct S5TerrainCell {
    LONG x,y;DWORD flags,walk_mask,height_mask,ramp_mask,terrain_type,mini_known_mask;
    /* Immutable VF4 passage, qualified by the same lawful mini mask. The
       visible walk_mask above retains the engine's current active rules. */
    DWORD static_walk_mask;
} S5TerrainCell;
typedef struct S5TerrainView {
    DWORD magic,version,bytes,epoch,frame;LONG camera_x,camera_y;
    DWORD width_px,height_px,selection_generation,target_serial,count,ready,tick;
    LONG addon_offset_x,addon_offset_y;
    DWORD tileset_id;
    S5TerrainCell cells[S5_TERRAIN_CELLS];
} S5TerrainView;
typedef struct S5PlacementView {
    DWORD magic,version,bytes,epoch,frame;LONG camera_x,camera_y;
    DWORD selection_generation,target_serial,tick;
    LONG requested_x,requested_y,click_x,click_y;
    DWORD dual;
    LONG main_x,main_y;DWORD main_width,main_height;
    LONG addon_x,addon_y;DWORD addon_width,addon_height;
    DWORD main_reason,addon_reason,power_required,powered;
} S5PlacementView;
typedef DWORD (FAST *S5PlacementFn)(void*,DWORD,int,int,DWORD,DWORD,DWORD,DWORD);
typedef DWORD (FAST *S5PowerFn)(DWORD,int,int,DWORD);
typedef DWORD (FAST *S5WalkFn)(int,int);
static S5TerrainView terrain_view;
static S5PlacementView placement_view;
static ScapiAttentionClock terrain_attention;
static DWORD terrain_epoch;
static LONG terrain_camera_x,terrain_camera_y;
#define S5_TERRAIN_UNKNOWN 0xffffffffu
#define S5_TERRAIN_AMBIGUOUS 0xfffffffeu
#define S5_TERRAIN_MEGA_COUNT 32768u
typedef struct S5TerrainSemanticCache {
    DWORD valid,epoch,cv5_pointer,cv5_count,tileset,vf4_pointer,vf4_count;
    DWORD types[S5_TERRAIN_MEGA_COUNT];
} S5TerrainSemanticCache;
static S5TerrainSemanticCache terrain_semantics;
/* Legally explored static geometry. A rendered creep megatile is not its ground
   geometry: exact height uses the native overlay -> MTXM -> CV5 -> VF4 path.
   Maximum map allocation is the target's 65536-tile natural area (1.5 MiB). */
typedef struct S5TerrainObservedCell {
    DWORD rendered_mega,walk_mask,height_mask,ramp_mask,mini_known_mask,terrain_type;
} S5TerrainObservedCell;
static struct {
    S5TerrainObservedCell *cells;
    DWORD epoch,tile_pointer,width,height,raw_pointer,overlay_pointer,cv5_pointer,vf4_pointer,tileset,cv5_count,vf4_count;
} terrain_observed;
#ifndef S5_TERRAIN_CACHE_ALLOC
#define S5_TERRAIN_CACHE_ALLOC(bytes) VirtualAlloc(NULL,(bytes),MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE)
#endif
static void s5_terrain_observed_reset(void) {
    if(terrain_observed.cells)VirtualFree(terrain_observed.cells,0,MEM_RELEASE);
    memset(&terrain_observed,0,sizeof(terrain_observed));
}
static int s5_terrain_observed_prepare(void) {
    DWORD w=SCAPI_State.width_tiles,h=SCAPI_State.height_tiles,area,bytes;
    uintptr_t p=(uintptr_t)SCAPI_State.tile_pointer;
    if(!w || !h || w>256u || h>256u || !p) {
        s5_terrain_observed_reset();return 0;
    }
    area=w*h;bytes=area*sizeof(S5TerrainObservedCell);
    if(area>65536u || p>0xffffffffu-area*4u || !readable((void*)p,area*4u)) {
        s5_terrain_observed_reset();return 0;
    }
    if(terrain_observed.cells && terrain_observed.epoch==SCAPI_State.epoch &&
       terrain_observed.tile_pointer==SCAPI_State.tile_pointer &&
       terrain_observed.width==w && terrain_observed.height==h &&
       terrain_observed.raw_pointer==U32(0x63c558) &&
       terrain_observed.overlay_pointer==U32(0x63c560) &&
       terrain_observed.cv5_pointer==U32(0x6ba100) &&
       terrain_observed.vf4_pointer==U32(0x63ca98) &&
       terrain_observed.cv5_count==U32(0x63ca88) &&
       terrain_observed.vf4_count==U16(0x63ca90) &&
       terrain_observed.tileset==U16(0x4fef44))return 1;
    s5_terrain_observed_reset();
    terrain_observed.cells=(S5TerrainObservedCell*)S5_TERRAIN_CACHE_ALLOC(bytes);
    if(!terrain_observed.cells)return 0;
    memset(terrain_observed.cells,0,bytes);
    terrain_observed.epoch=SCAPI_State.epoch;
    terrain_observed.tile_pointer=SCAPI_State.tile_pointer;
    terrain_observed.raw_pointer=U32(0x63c558);
    terrain_observed.overlay_pointer=U32(0x63c560);
    terrain_observed.cv5_pointer=U32(0x6ba100);
    terrain_observed.vf4_pointer=U32(0x63ca98);
    terrain_observed.cv5_count=U32(0x63ca88);
    terrain_observed.vf4_count=U16(0x63ca90);
    terrain_observed.tileset=U16(0x4fef44);
    terrain_observed.width=w;terrain_observed.height=h;return 1;
}
static int s5_terrain_mini_on_screen(int x,int y) {
    /* Complete 8px square, including the UI mask. A partly hidden mini is
       unknown even when another point of its 32px tile is observable. */
    return point_on_screen(x,y)==SC_OK && point_on_screen(x+7,y)==SC_OK &&
        point_on_screen(x,y+7)==SC_OK && point_on_screen(x+7,y+7)==SC_OK &&
        point_on_screen(x+4,y+4)==SC_OK;
}
/* Target 1.05: CV5 load 4474AF/4474D1, 52 bytes/group; VF4 load
   44733F/44734D, 32 bytes/mega. Only inverse tileset metadata is cached.
   No MTXM/current-overlay map geometry is read here. */
static int s5_terrain_vf4_span(WORD *vf4,DWORD count) {
    return count && count<=65535u && vf4 &&
        (uintptr_t)vf4<=0xffffffffu-count*32u && readable(vf4,count*32u);
}
static int s5_terrain_semantics_prepare(DWORD epoch,WORD *cv5,DWORD count,
                                      DWORD tileset,WORD *vf4,DWORD vf4_count) {
    DWORD g,v,mega,type,old;
    if(!count || count>2048u || tileset>7 || !cv5 ||
       (uintptr_t)cv5>0xffffffffu-count*52u || !readable(cv5,count*52u) ||
       !s5_terrain_vf4_span(vf4,vf4_count)) {
        terrain_semantics.valid=0;return 0;
    }
    if(terrain_semantics.valid && terrain_semantics.epoch==epoch &&
       terrain_semantics.cv5_pointer==(DWORD)(uintptr_t)cv5 &&
       terrain_semantics.cv5_count==count && terrain_semantics.tileset==tileset &&
       terrain_semantics.vf4_pointer==(DWORD)(uintptr_t)vf4 &&
       terrain_semantics.vf4_count==vf4_count)return 1;
    terrain_semantics.valid=0;
    memset(terrain_semantics.types,0xff,sizeof(terrain_semantics.types));
    for(g=0;g<count;++g) {
        type=cv5[g*26u];
        for(v=0;v<16;++v) {
            /* CV5 references are full WORD indices. Only the rendered map
               buffer has a separate high-bit tag; never alias invalid CV5
               refs 8000h/8001h onto valid megatiles 0/1. */
            mega=cv5[g*26u+10u+v];
            if(mega>=S5_TERRAIN_MEGA_COUNT || mega>=vf4_count)continue;
            old=terrain_semantics.types[mega];
            /* Include unused0 and doodad1 in ambiguity detection. */
            terrain_semantics.types[mega]=old==S5_TERRAIN_UNKNOWN?type:
                old==type?old:S5_TERRAIN_AMBIGUOUS;
        }
    }
    terrain_semantics.epoch=epoch;terrain_semantics.cv5_pointer=(DWORD)(uintptr_t)cv5;
    terrain_semantics.cv5_count=count;terrain_semantics.tileset=tileset;
    terrain_semantics.vf4_pointer=(DWORD)(uintptr_t)vf4;terrain_semantics.vf4_count=vf4_count;
    terrain_semantics.valid=1;return 1;
}
static DWORD s5_terrain_semantic_type(DWORD mega) {
    if(!terrain_semantics.valid || mega>=S5_TERRAIN_MEGA_COUNT ||
       mega>=terrain_semantics.vf4_count)return S5_TERRAIN_UNKNOWN;
    return terrain_semantics.types[mega];
}
/* Caller already checked explored status and this mini's complete UI gate.
   Resolve underlying immutable ground through the same overlay -> MTXM path
   as native height: the creep editor can change raw MTXM to a creep graphic.
   Only ground VF4 bits are returned, never the tile identity/creep state.
   Never call nativewalk in fog: it includes current active-tile rules.
   Target MTXM 467C1F; BWAPI Map::getMiniTile/walkable reuse VF4 bit 0. */
static int s5_terrain_static_mini(int tx,int ty,int mx,int my,
                                DWORD *walk,DWORD *height,DWORD *ramp) {
    DWORD area=SCAPI_State.width_tiles*SCAPI_State.height_tiles;
    DWORD index=(DWORD)ty*SCAPI_State.width_tiles+(DWORD)tx,tile,group,mega,f;
    WORD *raw=(WORD*)(uintptr_t)U32(0x63c558);
    WORD *overlay=(WORD*)(uintptr_t)U32(0x63c560);
    WORD *cv5=(WORD*)(uintptr_t)U32(0x6ba100);
    WORD *vf4=(WORD*)(uintptr_t)U32(0x63ca98);
    DWORD count=U32(0x63ca88),vf4_count=U16(0x63ca90);
    if(!area || index>=area || U16(0x4fef44)>7 || !count || count>2048u || !cv5 ||
       (uintptr_t)cv5>0xffffffffu-count*52u || !readable(cv5,count*52u) ||
       !s5_terrain_vf4_span(vf4,vf4_count))return 0;
    tile=0;
    if(overlay) {
        if((uintptr_t)overlay>0xffffffffu-area*2u || !readable(overlay,area*2u))return 0;
        tile=overlay[index];
    }
    if(!tile) {
        if(!raw || (uintptr_t)raw>0xffffffffu-area*2u || !readable(raw,area*2u))return 0;
        tile=raw[index];
    }
    group=(tile>>4)&0x7ffu;if(group>=count)return 0;
    mega=cv5[group*26u+10u+(tile&15u)];if(mega>=vf4_count)return 0;
    f=vf4[mega*16u+(DWORD)my*4u+(DWORD)mx];
    *walk=(f&1u)!=0;*height=f&4u?2u:f&2u?1u:0u;*ramp=(f&0x10u)!=0;
    return 1;
}
/* Caller must first qualify this CURRENT-VISIBLE mini with the public UI
   gate. This helper is never called for fog or for an obscured mini. */
static int s5_terrain_visible_mini(int tx,int ty,int mx,int my,DWORD rendered_mega,
                                 DWORD *walk,DWORD *height,DWORD *ramp) {
    DWORD index=(DWORD)ty*SCAPI_State.width_tiles+(DWORD)tx,area;
    DWORD count=U32(0x63ca88),vf4_count=U16(0x63ca90),tile=0,group,mega,f;
    WORD *raw=(WORD*)(uintptr_t)U32(0x63c558);
    WORD *overlay=(WORD*)(uintptr_t)U32(0x63c560);
    WORD *cv5=(WORD*)(uintptr_t)U32(0x6ba100);
    WORD *vf4=(WORD*)(uintptr_t)U32(0x63ca98);
    area=SCAPI_State.width_tiles*SCAPI_State.height_tiles;
    if(!count || count>2048u || !cv5 ||
       (uintptr_t)cv5>0xffffffffu-count*52u || !readable(cv5,count*52u) ||
       !s5_terrain_vf4_span(vf4,vf4_count) || rendered_mega>=vf4_count || index>=area)return 0;
    if(overlay) {
        if((uintptr_t)overlay>0xffffffffu-area*2u || !readable(overlay,area*2u))return 0;
        tile=overlay[index];
    }
    if(!tile) {
        if(!raw || (uintptr_t)raw>0xffffffffu-area*2u || !readable(raw,area*2u))return 0;
        tile=raw[index];
    }
    group=(tile>>4)&0x7ffu;if(group>=count)return 0;
    mega=cv5[group*26u+10u+(tile&15u)];if(mega>=vf4_count)return 0;
    f=vf4[mega*16u+(DWORD)my*4u+(DWORD)mx];
    *height=f&4u?2u:f&2u?1u:0u;*ramp=(f&0x10u)!=0;
    /* Preserve the native walk override/partial/full rules, independently
       from the exact height source (creep changes their interpretation). */
    *walk=((S5WalkFn)AT(0x42bba0))(tx*32+mx*8,ty*32+my*8)!=0;
    return 1;
}
static void stage5_terrain_sample(void) {
    DWORD now=real_tick();
    int eligible=SCAPI_State.active && SCAPI_State.tile_pointer &&
        (!SCAPI_State.pause || U8(0x6b0cf8)) && SCAPI_State.view_wait_ms &&
        SCAPI_State.view_wait_ms<0x80000000u;
    if(terrain_epoch!=SCAPI_State.epoch || terrain_camera_x!=SCAPI_State.camera_x ||
       terrain_camera_y!=SCAPI_State.camera_y) {
        scapi_attention_reset(&terrain_attention);terrain_epoch=SCAPI_State.epoch;
        terrain_camera_x=SCAPI_State.camera_x;terrain_camera_y=SCAPI_State.camera_y;
    }
    scapi_attention_elapsed(&terrain_attention,now,eligible);
}
static DWORD s5_terrain_ready(void) {
    DWORD r=game_ready();if(r)return r;
    if(!SCAPI_State.view_wait_ms || SCAPI_State.view_wait_ms>=0x80000000u)
        return SC_BAD_ARGUMENT;
    return SC_OK;
}
static int s5_terrain_dwelled(void) {
    return terrain_epoch==SCAPI_State.epoch && terrain_camera_x==SCAPI_State.camera_x &&
        terrain_camera_y==SCAPI_State.camera_y && terrain_attention.active &&
        scapi_attention_elapsed(&terrain_attention,real_tick(),1)>=SCAPI_State.view_wait_ms;
}
static DWORD s5_tile_flags(int tx,int ty,DWORD *flags) {
    DWORD *p;
    if(tx<0 || ty<0 || (DWORD)tx>=SCAPI_State.width_tiles ||
       (DWORD)ty>=SCAPI_State.height_tiles)return SC_OUTSIDE_MAP;
    p=(DWORD*)(uintptr_t)SCAPI_State.tile_pointer+ty*SCAPI_State.width_tiles+tx;
    if(!readable(p,4))return SC_UNINITIALIZED;*flags=*p;return SC_OK;
}
static DWORD s5_footprint_gate(int tx,int ty,int w,int h) {
    int x,y;DWORD flags,r,explored=U32(0x650990);
    if(w<1 || h<1 || w>6 || h>8)return SC_INCOMPATIBLE_STATE;
    /* All four corners of every tile must be in the legal viewport; this
       rejects partial UI/viewport footprints before dynamic validation. */
    for(y=ty;y<ty+h;++y)for(x=tx;x<tx+w;++x) {
        r=s5_tile_flags(x,y,&flags);if(r)return r;
        if(flags&explored)return SC_NO_SELECTABLE_UNIT;
        r=point_on_screen(x*32,y*32);if(r)return r;
        r=point_on_screen(x*32+31,y*32);if(r)return r;
        r=point_on_screen(x*32,y*32+31);if(r)return r;
        r=point_on_screen(x*32+31,y*32+31);if(r)return r;
    }
    return SC_OK;
}
/* Observation still needs current vision; issuing a build needs explored
   terrain only. Keep these permissions separate for terrain power queries. */
static DWORD s5_footprint_visible_gate(int tx,int ty,int w,int h) {
    int x,y;DWORD flags,r=s5_footprint_gate(tx,ty,w,h),mask=U32(0x65098c);
    if(r)return r;
    for(y=ty;y<ty+h;++y)for(x=tx;x<tx+w;++x) {
        r=s5_tile_flags(x,y,&flags);if(r)return r;
        if(flags&mask)return SC_NO_SELECTABLE_UNIT;
    }
    return SC_OK;
}
static DWORD s5_placement_geometry(int *type,int *main_type,int *dx,int *dy,
                                   int *w,int *h,int *mw,int *mh) {
    int px,py;
    if(stage5_mode()!=3 || !stage5_pending_mode)return SC_NO_PENDING_TARGET;
    *type=U16(0x653a3a);*main_type=U32(0x653a28)?U16(0x653a38):*type;
    if(*type<0 || *type>=228 || *main_type<0 || *main_type>=228)
        return SC_INCOMPATIBLE_STATE;
    px=(short)U16(0x6ae968+*type*4);py=(short)U16(0x6ae96a+*type*4);
    *w=px/32;*h=py/32;
    px=(short)U16(0x6ae968+*main_type*4);py=(short)U16(0x6ae96a+*main_type*4);
    *mw=px/32;*mh=py/32;*dx=*dy=0;
    if(*w<1 || *h<1 || *w>6 || *h>8 || *mw<1 || *mh<1 || *mw>6 || *mh>8)
        return SC_INCOMPATIBLE_STATE;
    if(U32(0x653a28)) {
        if(*type<106)return SC_INCOMPATIBLE_STATE;
        px=(short)U16(0x6ae1b0+4*(*type-106));
        py=(short)U16(0x6ae1b2+4*(*type-106));
        if(px%32 || py%32)return SC_INCOMPATIBLE_STATE;
        *dx=px;*dy=py;
    }
    return SC_OK;
}
static DWORD stage5_terrain_request(void) {
    DWORD r=s5_terrain_ready(),serial=SCAPI_State.terrain_serial;
    DWORD flags,mask=U32(0x65098c),explored=U32(0x650990),n=0;
    int w,h,x,y,l,t,rr,bb,px,py,mx,my,type,mt,dx,dy,pw,ph,mw,mh,placing;
    WORD *tiles,*vf4;DWORD mega,height_bits,i,now=real_tick(),vf4_count,tileset;
    int observed_ready;
    if(!SCAPI_State.width_tiles || !SCAPI_State.height_tiles ||
       SCAPI_State.width_tiles>256u || SCAPI_State.height_tiles>256u) {
        s5_terrain_observed_reset();return r?r:SC_INCOMPATIBLE_STATE;
    }
    if(r)return r;
    w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    if(w<1 || h<1 || w>640 || h>400)return SC_INCOMPATIBLE_STATE;
    l=SCAPI_State.camera_x/32;t=SCAPI_State.camera_y/32;
    rr=(SCAPI_State.camera_x+w-1)/32;bb=(SCAPI_State.camera_y+h-1)/32;
    if(rr-l+1>21 || bb-t+1>14)return SC_INCOMPATIBLE_STATE;
    SCAPI_State.terrain_serial=serial+1;
    memset(&terrain_view,0,sizeof(terrain_view));
    terrain_view.magic=0x31525453;terrain_view.version=4;terrain_view.epoch=SCAPI_State.epoch;
    terrain_view.frame=SCAPI_State.raw_counter;terrain_view.camera_x=SCAPI_State.camera_x;
    terrain_view.camera_y=SCAPI_State.camera_y;terrain_view.width_px=w;terrain_view.height_px=h;
    terrain_view.selection_generation=SCAPI_State.selection_generation;
    terrain_view.target_serial=SCAPI_State.target_serial;terrain_view.tick=real_tick();
    terrain_view.ready=s5_terrain_dwelled();
    tileset=U16(0x4fef44);terrain_view.tileset_id=tileset<=7?tileset:S5_TERRAIN_UNKNOWN;
    placing=s5_placement_geometry(&type,&mt,&dx,&dy,&pw,&ph,&mw,&mh)==SC_OK;
    if(placing){terrain_view.addon_offset_x=dx;terrain_view.addon_offset_y=dy;}
    tiles=(WORD*)(uintptr_t)U32(0x51bcb0);vf4=(WORD*)(uintptr_t)U32(0x63ca98);
    if(!tiles || (uintptr_t)tiles>0xffffffffu-SCAPI_State.width_tiles*SCAPI_State.height_tiles*2u ||
       !readable(tiles,SCAPI_State.width_tiles*SCAPI_State.height_tiles*2u))tiles=NULL;
    vf4_count=U16(0x63ca90);
    s5_terrain_semantics_prepare(SCAPI_State.epoch,(WORD*)(uintptr_t)U32(0x6ba100),
        U32(0x63ca88),tileset,vf4,vf4_count);
    observed_ready=s5_terrain_observed_prepare();
    if(terrain_view.ready)for(y=t;y<=bb;++y)for(x=l;x<=rr;++x) {
        S5TerrainCell *cell=&terrain_view.cells[n++];cell->x=x*32;cell->y=y*32;
        DWORD legal_minis=0;int representative_ok;
        cell->terrain_type=S5_TERRAIN_UNKNOWN;
        px=x*32+16;py=y*32+16;
        if(px<SCAPI_State.camera_x)px=SCAPI_State.camera_x;
        if(py<SCAPI_State.camera_y)py=SCAPI_State.camera_y;
        if(px>=SCAPI_State.camera_x+w)px=SCAPI_State.camera_x+w-1;
        if(py>=SCAPI_State.camera_y+h)py=SCAPI_State.camera_y+h-1;
        representative_ok=point_on_screen(px,py)==SC_OK;
        for(my=0;my<4;++my)for(mx=0;mx<4;++mx)
            if(s5_terrain_mini_on_screen(x*32+mx*8,y*32+my*8))
                legal_minis|=1u<<(my*4+mx);
        if((!representative_ok && !legal_minis) || s5_tile_flags(x,y,&flags)!=SC_OK)continue;
        if(flags&explored)continue;
        /* Preserve the previous tile aggregate/label permission. Geometry
           uses each mini's own gate even when this representative is hidden. */
        if(representative_ok) {
        cell->flags=S5T_EXPLORED|S5T_KNOWN;
        if(!(flags&0x20800000u))cell->flags|=S5T_BUILDABLE;
        if(flags&0x01000000u)cell->flags|=S5T_BLOCKS_VIEW;
        if(!(flags&mask)) {
            cell->flags|=S5T_VISIBLE|S5T_CREEP_KNOWN;
            /* 400000 is preplaced creep; 40000000 is updated by the
               native visible-creep callback 4B7184/4B7192. */
            if(flags&0x40400000u)cell->flags|=S5T_CREEP;
        } else {
            /* 4B7140 visibility-gates the game's own remembered creep bit
               before 4B7184..7192 updates it. In fog this is last seen, even
               when no API query was made while the tile was visible. */
            cell->flags|=S5T_CREEP_REMEMBERED;
            if(flags&0x40400000u)cell->flags|=S5T_CREEP;
        }
        }
        if(tiles && readable(tiles+y*SCAPI_State.width_tiles+x,2)) {
            mega=tiles[y*SCAPI_State.width_tiles+x]&0x7fff;
            if(representative_ok)cell->terrain_type=s5_terrain_semantic_type(mega);
            if(observed_ready) {
                S5TerrainObservedCell *seen=terrain_observed.cells+y*SCAPI_State.width_tiles+x;
                seen->rendered_mega=mega;seen->terrain_type=cell->terrain_type;
                for(my=0;my<4;++my)for(mx=0;mx<4;++mx) {
                    DWORD bit=1u<<(my*4+mx),shift=2u*(my*4+mx),walk,ramp,static_walk,static_height,static_ramp;
                    if(!(legal_minis&bit))continue;
                    if(!(flags&mask)) {
                        if(!s5_terrain_visible_mini(x,y,mx,my,mega,&walk,&height_bits,&ramp)) {
                            seen->mini_known_mask&=~bit;continue;
                        }
                        /* Cache base terrain passage, while the current visible
                           response retains the target's native walk semantics. */
                        if(!s5_terrain_static_mini(x,y,mx,my,&static_walk,&static_height,&static_ramp)) {
                            seen->mini_known_mask&=~bit;continue;
                        }
                        seen->walk_mask=(seen->walk_mask&~bit)|(static_walk?bit:0);
                        seen->height_mask=(seen->height_mask&~(3u<<shift))|(height_bits<<shift);
                        seen->ramp_mask=(seen->ramp_mask&~bit)|(ramp?bit:0);
                        seen->mini_known_mask|=bit;
                        cell->mini_known_mask|=bit;cell->walk_mask|=walk?bit:0;
                        cell->static_walk_mask|=static_walk?bit:0;
                        cell->height_mask|=height_bits<<shift;cell->ramp_mask|=ramp?bit:0;
                        continue;
                    }
                    if(!(seen->mini_known_mask&bit) &&
                       s5_terrain_static_mini(x,y,mx,my,&walk,&height_bits,&ramp)) {
                        seen->walk_mask=(seen->walk_mask&~bit)|(walk?bit:0);
                        seen->height_mask=(seen->height_mask&~(3u<<shift))|(height_bits<<shift);
                        seen->ramp_mask=(seen->ramp_mask&~bit)|(ramp?bit:0);
                        seen->mini_known_mask|=bit;
                    }
                    if(!(seen->mini_known_mask&bit))continue;
                    cell->mini_known_mask|=bit;
                    cell->walk_mask|=seen->walk_mask&bit;
                    cell->static_walk_mask|=seen->walk_mask&bit;
                    cell->height_mask|=seen->height_mask&(3u<<shift);
                    cell->ramp_mask|=seen->ramp_mask&bit;
                }
                if(cell->mini_known_mask)cell->flags|=S5T_MINI_KNOWN;
                if(cell->mini_known_mask && (flags&mask))cell->flags|=S5T_STATIC_GROUND;
            }
        }
        if(representative_ok && placing && (U32(0x6b0358+type*4)&0x80000u) &&
           s5_footprint_visible_gate(x,y,pw,ph)==SC_OK) {
            cell->flags|=S5T_POWER_KNOWN;
            if(((S5PowerFn)AT(0x42d000))(type,x*32+pw*16,y*32+ph*16,U8(0x653a2c)))
                cell->flags|=S5T_POWER;
        }
    }
    /* Positive observed ground occupancy only. Reuse the same attention,
       detection and hit-test qualification as the public scene; raw occupied
       tile flags are never read for this output. Empty does not mean clear. */
    if(terrain_view.ready)for(i=0;i<1700;++i) {
        ScapiSceneTrack *track=&scene_tracks[i];
        BYTE *u=AT(0x5b2e68)+i*0x148u,*sp;short *edges;
        DWORD relation,occ,kind,j,local=U32(0x4f30c0);int ax,ay,cx,cy;
        if(!track->live || !track->in_camera || !track->screen.active ||
           now-track->screen.since<SCAPI_State.view_wait_ms || !valid_unit(u))continue;
        sp=*(BYTE**)(u+12);kind=*(WORD*)(u+0x64);
        if(kind>=228 || local>=12 || u[0x4c]>=12 ||
           track->sprite!=(DWORD)(uintptr_t)sp || track->unique!=u[0xa5] ||
           track->owner!=u[0x4c] || track->type!=kind ||
           (*(DWORD*)(u+0xdc)&4u) || (sp[0x0e]&0x20u) ||
           !stage4_view_anchor(u,&ax,&ay))continue;
        edges=(short*)(AT(0x6af898)+kind*8u);if(!readable(edges,8))continue;
        cx=*(short*)(sp+20);cy=*(short*)(sp+22);
        relation=u[0x4c]==local?1:u[0x4c]>=8?4:
            (U8(0x50d39c+12*local+u[0x4c])?2:3);
        occ=relation==1?S5T_OCC_OWN:relation==2?S5T_OCC_ALLY:
            relation==3?S5T_OCC_ENEMY:S5T_OCC_NEUTRAL;
        if(U32(0x6b0358+4*kind)&1u)occ|=S5T_OCC_BUILDING;
        for(j=0;j<n;++j) {
            S5TerrainCell *cell=&terrain_view.cells[j];
            if((cell->flags&S5T_VISIBLE) && cx+edges[2]>=cell->x &&
               cx-edges[0]<cell->x+32 && cy+edges[3]>=cell->y &&
               cy-edges[1]<cell->y+32)cell->flags|=occ;
        }
    }
    terrain_view.count=n;terrain_view.bytes=68+n*sizeof(S5TerrainCell);
    SCAPI_State.terrain_address=(DWORD)(uintptr_t)&terrain_view;
    SCAPI_State.terrain_bytes=terrain_view.bytes;
    SCAPI_State.terrain_serial=serial+2;return SC_OK;
}
/* 47D3B0: screen pixels -> camera tile -> placement tile, including its
   partial-left/top correction at47D451/47D470. This takes an engine CLICK,
   not the public main-building top-left used by placement_preview. */
static DWORD s5_placement_target_gate(int click_x,int click_y) {
    int type,mt,dx,dy,w,h,mw,mh,tx,ty;DWORD r;
    r=s5_placement_geometry(&type,&mt,&dx,&dy,&w,&h,&mw,&mh);if(r)return r;
    tx=(int)U16(0x4fef38)+(click_x-SCAPI_State.camera_x)/32;
    ty=(int)U16(0x4fef3a)+(click_y-SCAPI_State.camera_y)/32;
    if(tx*32<I32(0x51bcd4))++tx;
    if(ty*32<I32(0x51bcd8))++ty;
    r=s5_footprint_gate(tx,ty,w,h);if(r)return r;
    if(U32(0x653a28))return s5_footprint_gate(tx-dx/32,ty-dy/32,mw,mh);
    return SC_OK;
}
static DWORD stage5_placement_request(void) {
    DWORD r=s5_terrain_ready(),serial=SCAPI_State.placement_serial;
    int type,mt,dx,dy,w,h,mw,mh,x,y,mx,my,click_x,click_y;BYTE *actor,backup[96];
    DWORD a,b=0,owner,q;int fog;
    if(r)return r;
    r=stage5_owned_selection(1,&actor);if(r)return r;
    if(SCAPI_State.expected_target_serial!=SCAPI_State.target_serial)return SC_STALE_COMMAND;
    if(SCAPI_State.queued_order!=stage5_pending_queued_order)return SC_STALE_COMMAND;
    r=s5_placement_geometry(&type,&mt,&dx,&dy,&w,&h,&mw,&mh);if(r)return r;
    if(!s5_terrain_dwelled())return SC_UNINITIALIZED;
    if(SCAPI_State.x2<0 || SCAPI_State.x2>1 || SCAPI_State.y2)return SC_BAD_ARGUMENT;
    if(SCAPI_State.x%32 || SCAPI_State.y%32)return SC_BAD_ARGUMENT;
    r=point_on_map(SCAPI_State.x,SCAPI_State.y);if(r)return r;
    x=SCAPI_State.x+(SCAPI_State.x2?dx:0);y=SCAPI_State.y+(SCAPI_State.x2?dy:0);
    mx=x-dx;my=y-dy;
    r=s5_footprint_gate(x/32,y/32,w,h);if(r)return r;
    if(U32(0x653a28)){r=s5_footprint_gate(mx/32,my/32,mw,mh);if(r)return r;}
    fog=s5_footprint_visible_gate(x/32,y/32,w,h)!=SC_OK;
    if(U32(0x653a28) && s5_footprint_visible_gate(mx/32,my/32,mw,mh)!=SC_OK)fog=1;
    /* The native callback snaps SCREEN pixels then adds 4FEF38/3A. Camera
       movement is only 8px aligned, so world tile top-left is not itself an
       equivalent click pixel. Invert the actual click mapping explicitly. */
    click_x=SCAPI_State.camera_x+32*(x/32-(int)U16(0x4fef38));
    click_y=SCAPI_State.camera_y+32*(y/32-(int)U16(0x4fef3a));
    r=point_on_map(click_x,click_y);if(r)return r;
    r=point_on_screen(click_x,click_y);if(r)return r;
    owner=U8(0x653a2c);if(owner>=12 || owner!=actor[0x4c] ||
       (BYTE*)(uintptr_t)U32(0x68d2f8)!=actor)return SC_STALE_SELECTION;
    q=U32(0x650ce0);memcpy(backup,AT(0x653950),96);
    /* Fog permits a target, but not a query of current hidden blockers.
       Reason9 is native not-visible, projected publicly as deferred/unknown.
       The actual click follows the game's ordinary placement/order path. */
    if(fog){a=9;if(U32(0x653a28))b=9;}
    else {
        a=((S5PlacementFn)AT(0x40ce10))(actor,owner,x/32,y/32,type,1,1,0);
        if(U32(0x653a28))
            b=((S5PlacementFn)AT(0x40ce10))(actor,owner,mx/32,my/32,mt,0,1,0);
    }
    memcpy(AT(0x653950),backup,96);
    if(U32(0x650ce0)!=q)return SC_QUEUE_UNCONFIRMED;
    SCAPI_State.placement_serial=serial+1;memset(&placement_view,0,sizeof(placement_view));
    placement_view.magic=0x31504c53;placement_view.version=1;placement_view.bytes=sizeof(placement_view);
    placement_view.epoch=SCAPI_State.epoch;placement_view.frame=SCAPI_State.raw_counter;
    placement_view.camera_x=SCAPI_State.camera_x;placement_view.camera_y=SCAPI_State.camera_y;
    placement_view.selection_generation=SCAPI_State.selection_generation;
    placement_view.target_serial=SCAPI_State.target_serial;placement_view.tick=real_tick();
    placement_view.requested_x=SCAPI_State.x;placement_view.requested_y=SCAPI_State.y;
    placement_view.click_x=click_x;placement_view.click_y=click_y;placement_view.dual=U32(0x653a28)!=0;
    placement_view.main_x=mx;placement_view.main_y=my;
    placement_view.main_width=mw*32;placement_view.main_height=mh*32;
    if(placement_view.dual){placement_view.addon_x=x;placement_view.addon_y=y;
        placement_view.addon_width=w*32;placement_view.addon_height=h*32;}
    placement_view.main_reason=placement_view.dual?b:a;
    placement_view.addon_reason=placement_view.dual?a:0;
    placement_view.power_required=(U32(0x6b0358+type*4)&0x80000u)!=0;
    if(placement_view.power_required && !fog)placement_view.powered=
        ((S5PowerFn)AT(0x42d000))(type,x+w*16,y+h*16,owner)!=0;
    SCAPI_State.placement_address=(DWORD)(uintptr_t)&placement_view;
    SCAPI_State.placement_bytes=sizeof(placement_view);
    SCAPI_State.placement_serial=serial+2;return SC_OK;
}
#endif
