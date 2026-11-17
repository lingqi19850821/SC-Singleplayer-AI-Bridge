#ifndef SCAPI_STAGE5_RESOURCE_SITES_H
#define SCAPI_STAGE5_RESOURCE_SITES_H
/* Target1.05:41FB00 mineral176..178;41F710 adds geyser188;
   4246C0 gas buildings110/149/157. This read-only operation uses the existing
   view_items transport, never the viewport scene or its attention/identity. */
static DWORD stage5_resource_class(DWORD kind) {
    if(kind>=176 && kind<=178)return 1;
    if(kind==188 || kind==110 || kind==149 || kind==157)return 2;
    return 0;
}
#define S5_RESOURCE_VISIBILITY_MAGIC 0x31565352u
typedef struct S5ResourceVisibility {
    DWORD magic,version,bytes,epoch,frame,tick,width,height,selection_generation,count;
    LONG camera_x,camera_y;DWORD viewport_width,viewport_height;
    BYTE bits[8192];
} S5ResourceVisibility;
static S5ResourceVisibility stage5_resource_visibility;
static DWORD stage5_resource_visible(BYTE *u,BYTE *sp,int *bounds,DWORD *tiles,DWORD mask) {
    DWORD kind=*(WORD*)(u+0x64);
    short *edges=(short*)(AT(0x6af898)+kind*8u);
    int x=*(short*)(sp+20),y=*(short*)(sp+22),l,t,r,b,xx,yy;
    int width=(int)SCAPI_State.width_tiles*32,height=(int)SCAPI_State.height_tiles*32;
    if(x<0 || y<0 || x>=width || y>=height || !readable(edges,8) ||
       edges[0]<0 || edges[1]<0 || edges[2]<0 || edges[3]<0 ||
       edges[0]>=width || edges[2]>=width || edges[1]>=height || edges[3]>=height)
        return SC_INCOMPATIBLE_STATE;
    l=x-edges[0];t=y-edges[1];r=x+edges[2];b=y+edges[3];
    if(l<0)l=0;if(t<0)t=0;if(r>=width)r=width-1;if(b>=height)b=height-1;
    bounds[0]=l;bounds[1]=t;bounds[2]=r+1;bounds[3]=b+1;
    for(yy=t/32;yy<=b/32;++yy)for(xx=l/32;xx<=r/32;++xx) {
        DWORD bit=(DWORD)yy*SCAPI_State.width_tiles+(DWORD)xx;
        if(!(tiles[bit]&mask))return SC_OK;
    }
    return SC_NO_SELECTABLE_UNIT; /* explored fog is not current vision */
}
static int stage5_resource_amount(BYTE *u,WORD *amount) {
    BYTE *root,*node,*text;DWORD kind=*(WORD*)(u+0x64);
    if(SCAPI_State.selection_count!=1 || ((BYTE**)AT(0x684940))[0]!=u ||
       *(BYTE**)AT(0x68d2f8)!=u || U8(0x68c9dc) || U8(0x68ca6c)!=15 ||
       ((kind==110 || kind==149 || kind==157) && !(*(DWORD*)(u+0xdc)&1u)))return 0;
    root=*(BYTE**)AT(0x68ca68);if(!readable(root,0x46))return 0;
    node=((S5FindControlFn)AT(0x4d69f0))(root,0xfffffff5u);
    if(!readable(node,0x46) || *(WORD*)(node+0x20)!=0xfff5 || !(*(DWORD*)(node+0x18)&8u))return 0;
    text=*(BYTE**)(node+0x14);if(!readable(text,1) || !*text)return 0;
    /* Target UI4A6280/4A633A/4A634A reads WORD+D0;4A6291 caches it.
       Read only for explicit x1, after actual selected resource panel checks. */
    *amount=*(WORD*)(u+0xd0);
    return U16(0x68cb48)==*amount;
}
static DWORD stage5_resource_sites_request(void) {
    DWORD i,n=0,r,kind,owner,local,mask,count,serial;DWORD *tiles;int w,h,x,y;
    S5ResourceVisibility *visibility=&stage5_resource_visibility;
    if(SCAPI_State.hook_installed!=1)return SC_DISABLED;
    r=game_ready();if(r)return r;
    if((SCAPI_State.x!=0 && SCAPI_State.x!=1) || SCAPI_State.y || SCAPI_State.x2 || SCAPI_State.y2)
        return SC_BAD_ARGUMENT;
    if(!SCAPI_State.width_tiles || !SCAPI_State.height_tiles ||
       SCAPI_State.width_tiles>256 || SCAPI_State.height_tiles>256)return SC_INCOMPATIBLE_STATE;
    local=U32(0x4f30c0);mask=U32(0x65098c);
    if(local>=12 || !mask || mask>255)return SC_UNINITIALIZED;
    w=SCAPI_State.width_tiles*32-SCAPI_State.camera_max_x;
    h=SCAPI_State.height_tiles*32-SCAPI_State.camera_max_y;
    if(w<1 || w>640 || h<1 || h>400 || SCAPI_State.camera_x<0 || SCAPI_State.camera_y<0 ||
       (DWORD)SCAPI_State.camera_x>SCAPI_State.camera_max_x ||
       (DWORD)SCAPI_State.camera_y>SCAPI_State.camera_max_y)return SC_INCOMPATIBLE_STATE;
    count=SCAPI_State.width_tiles*SCAPI_State.height_tiles;
    tiles=(DWORD*)(uintptr_t)SCAPI_State.tile_pointer;
    if((uintptr_t)tiles>0xffffffffu-count*4u || !readable(tiles,count*4u))return SC_UNINITIALIZED;
    serial=SCAPI_State.terrain_serial;
    if((serial&1u) || serial>=0xfffffffeu)return SC_INCOMPATIBLE_STATE;
    InterlockedIncrement((LONG*)&SCAPI_State.terrain_serial);
    memset(visibility,0,sizeof(*visibility));visibility->magic=S5_RESOURCE_VISIBILITY_MAGIC;
    visibility->version=2;visibility->bytes=56+(count+7)/8;
    visibility->epoch=SCAPI_State.epoch;visibility->frame=SCAPI_State.raw_counter;
    visibility->tick=SCAPI_State.executed_tick;visibility->width=SCAPI_State.width_tiles;
    visibility->height=SCAPI_State.height_tiles;visibility->count=count;
    visibility->selection_generation=SCAPI_State.selection_generation;
    visibility->camera_x=SCAPI_State.camera_x;visibility->camera_y=SCAPI_State.camera_y;
    visibility->viewport_width=w;visibility->viewport_height=h;
    /* Disappearance permission is screen-confirmed, independently from the
       all-player-vision positive resource census below. UI/fog/offscreen=0. */
    for(i=0;i<count;++i)if(!(tiles[i]&mask)) {
        x=(i%SCAPI_State.width_tiles)*32;y=(i/SCAPI_State.width_tiles)*32;
        if(point_on_screen(x,y)==SC_OK && point_on_screen(x+31,y)==SC_OK &&
           point_on_screen(x,y+31)==SC_OK && point_on_screen(x+31,y+31)==SC_OK)
            visibility->bits[i/8]|=(BYTE)(1u<<(i%8));
    }
    SCAPI_State.terrain_address=(DWORD)(uintptr_t)visibility;
    SCAPI_State.terrain_bytes=visibility->bytes;
    InterlockedIncrement((LONG*)&SCAPI_State.terrain_serial);
    /* Mailbox is BUSY throughout mutation. A decoder must bind operation48,
       request/completed serial and epoch; another view request invalidates it. */
    SCAPI_State.view_count=0;SCAPI_State.view_serial=SCAPI_State.serial;
    SCAPI_State.view_address=(DWORD)(uintptr_t)view_items;
    for(i=0;i<1700;++i) {
        BYTE *u=AT(0x5b2e68)+i*0x148u,*sp;ScapiViewItem *item;int bounds[4];WORD amount;
        if(!valid_unit(u))continue;
        kind=*(WORD*)(u+0x64);if(!stage5_resource_class(kind))continue;
        sp=*(BYTE**)(u+12);owner=u[0x4c];
        if(owner>=12 || !(sp[12]&mask) ||
           ((*(DWORD*)(u+0xdc)&0x300u) && !(*(DWORD*)(u+0xe4)&mask)))continue;
        r=stage5_resource_visible(u,sp,bounds,tiles,mask);
        if(r==SC_NO_SELECTABLE_UNIT)continue;
        if(r)return r;
        item=&view_items[n++];memset(item,0,sizeof(*item));
        item->x=*(short*)(sp+20);item->y=*(short*)(sp+22);
        item->relation=owner==local?1:owner>=8?4:(U8(0x50d39c+12*local+owner)?2:3);
        item->private_type=kind;item->private_owner=owner;
        item->private_terrain_x=item->private_terrain_y=-1;
        item->private_ground_group=(DWORD)bounds[0];item->private_air_group=(DWORD)bounds[1];
        item->private_armor_group=(DWORD)bounds[2];item->private_ground_weapon=(DWORD)bounds[3];
        if(SCAPI_State.x && stage5_resource_amount(u,&amount)) {item->flags=1;item->energy=amount;}
    }
    SCAPI_State.view_count=n;return SC_OK;
}
#endif
