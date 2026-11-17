#ifndef SCAPI_STAGE4_HOOKS_H
#define SCAPI_STAGE4_HOOKS_H
/* Private, target-pinned UI-thread instrumentation. The caller supplies whole
   position-independent instruction prefixes and installs/removes only while
   that engine thread is outside the site. Never a public address/patch API.
   Trampolines remain allocated until ordinary process exit after detach. */
#include <windows.h>
#include <stdint.h>
#include <string.h>

typedef struct ScapiHookRegisters {
    DWORD edi,esi,ebp,saved_esp,ebx,edx,ecx,eax,eflags;
} ScapiHookRegisters;
typedef void (__cdecl *ScapiHookCallback)(DWORD,ScapiHookRegisters*);
typedef struct ScapiCodeHook {
    BYTE *site,*block;
    DWORD count,installed,original_protection;
    BYTE original[16],replacement[16];
} ScapiCodeHook;
enum { SC_HOOK_OK,SC_HOOK_BAD_ARGUMENT,SC_HOOK_MISMATCH,SC_HOOK_ALLOC_FAILED,
       SC_HOOK_PROTECT_FAILED,SC_HOOK_RESTORE_FAILED,SC_HOOK_FLUSH_FAILED };

static DWORD scapi_hook_install(ScapiCodeHook *h,BYTE *site,const BYTE *expected,
                               DWORD count,DWORD id,ScapiHookCallback callback) {
    MEMORY_BASIC_INFORMATION m;
    BYTE *b,*trampoline;
    DWORD old,tmp;
    if(!h || !site || !expected || !callback || count<5 || count>16 || h->installed)
        return SC_HOOK_BAD_ARGUMENT;
    if(!VirtualQuery(site,&m,sizeof(m)) || m.State!=MEM_COMMIT ||
       (m.Protect&(PAGE_NOACCESS|PAGE_GUARD)) ||
       (uintptr_t)site+count>(uintptr_t)m.BaseAddress+m.RegionSize) return SC_HOOK_BAD_ARGUMENT;
    if(memcmp(site,expected,count)) return SC_HOOK_MISMATCH;
    if(h->block) {
        b=h->block;
        if(h->site!=site || h->count!=count || memcmp(h->original,expected,count) ||
           *(DWORD*)(b+4)!=id || *(DWORD*)(b+9)!=(DWORD)(uintptr_t)callback)
            return SC_HOOK_BAD_ARGUMENT;
    } else {
    b=VirtualAlloc(NULL,64,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    if(!b)return SC_HOOK_ALLOC_FAILED;
    trampoline=b+32;
    memcpy(trampoline,site,count);
    trampoline[count]=0xe9;
    *(DWORD*)(trampoline+count+1)=(DWORD)(site+count-(trampoline+count+5));
    /* pushfd; pushad; push esp (register frame); push id; call callback;
       add esp,8; popad; popfd; jmp original-prefix trampoline. */
    b[0]=0x9c;b[1]=0x60;b[2]=0x54;b[3]=0x68;*(DWORD*)(b+4)=id;
    b[8]=0xb8;*(DWORD*)(b+9)=(DWORD)(uintptr_t)callback;
    b[13]=0xff;b[14]=0xd0;b[15]=0x83;b[16]=0xc4;b[17]=8;
    b[18]=0x61;b[19]=0x9d;b[20]=0xe9;*(DWORD*)(b+21)=(DWORD)(trampoline-(b+25));
    if(!VirtualProtect(b,64,PAGE_EXECUTE_READ,&tmp) || !FlushInstructionCache(GetCurrentProcess(),b,64)) {
        VirtualFree(b,0,MEM_RELEASE);return SC_HOOK_PROTECT_FAILED;
    }
    }
    if(!VirtualProtect(site,count,PAGE_EXECUTE_READWRITE,&old)) {
        if(!h->block)VirtualFree(b,0,MEM_RELEASE);return SC_HOOK_PROTECT_FAILED;
    }
    h->original_protection=old;
    h->site=site;h->block=b;h->count=count;
    memcpy(h->original,site,count);memset(h->replacement,0x90,count);
    h->replacement[0]=0xe9;*(DWORD*)(h->replacement+1)=(DWORD)(b-(site+5));
    memcpy(site,h->replacement,count);h->installed=1;
    if(!FlushInstructionCache(GetCurrentProcess(),site,count)) {
        memcpy(site,h->original,count);h->installed=0;
        FlushInstructionCache(GetCurrentProcess(),site,count);
        VirtualProtect(site,count,h->original_protection,&tmp);return SC_HOOK_FLUSH_FAILED;
    }
    if(!VirtualProtect(site,count,old,&tmp)) return SC_HOOK_RESTORE_FAILED;
    return SC_HOOK_OK;
}
static DWORD scapi_hook_remove(ScapiCodeHook *h) {
    DWORD old,tmp;
    if(!h || !h->installed)return SC_HOOK_BAD_ARGUMENT;
    if(memcmp(h->site,h->replacement,h->count))return SC_HOOK_MISMATCH;
    if(!VirtualProtect(h->site,h->count,PAGE_EXECUTE_READWRITE,&old))return SC_HOOK_PROTECT_FAILED;
    memcpy(h->site,h->original,h->count);h->installed=0;
    if(!FlushInstructionCache(GetCurrentProcess(),h->site,h->count)) {
        VirtualProtect(h->site,h->count,old,&tmp);return SC_HOOK_FLUSH_FAILED;
    }
    if(!VirtualProtect(h->site,h->count,h->original_protection,&tmp))return SC_HOOK_RESTORE_FAILED;
    return SC_HOOK_OK;
}
#endif
