/* 32-bit bounded loader. Only the two user-authorized 1.05 paths; no arbitrary
   process or remote function endpoint. System export address is rebased from
   the module that actually owns it (including forwarded exports). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include "stage3_protocol.h"
/* Modified 2026-11-16: standalone v49/v50 builds require an exact DLL path. */
#ifndef SCAPI_STAGE5_DLL_PATH
#error Define SCAPI_STAGE5_DLL_PATH through tools/build_native.py
#endif
#if !defined(SCAPI_STAGE5) || defined(SCAPI_BUILD_QUEUE_DIAGNOSTIC)
#error This export builds the stage5 bridge only
#endif

/* TinyCC's small Win32 headers omit Toolhelp and this Vista-era declaration. */
#define TH32CS_SNAPMODULE 0x00000008
#define TH32CS_SNAPMODULE32 0x00000010
#define CP_UTF8 65001
#define MB_ERR_INVALID_CHARS 8
typedef struct tagMODULEENTRY32W {
    DWORD dwSize,th32ModuleID,th32ProcessID,GlblcntUsage,ProccntUsage;
    BYTE *modBaseAddr; DWORD modBaseSize; HMODULE hModule;
    WCHAR szModule[256],szExePath[MAX_PATH];
} MODULEENTRY32W;
__declspec(dllimport) HANDLE WINAPI CreateToolhelp32Snapshot(DWORD,DWORD);
static BOOL (WINAPI *pModule32FirstW)(HANDLE,MODULEENTRY32W*);
static BOOL (WINAPI *pModule32NextW)(HANDLE,MODULEENTRY32W*);
static BOOL (WINAPI *pQueryImageW)(HANDLE,DWORD,LPWSTR,PDWORD);
__declspec(dllimport) int WINAPI MultiByteToWideChar(UINT,DWORD,LPCSTR,int,LPWSTR,int);

static DWORD module_base(DWORD pid,const wchar_t *path,int basename) {
    MODULEENTRY32W m; DWORD base=0; HANDLE s;
    s=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
    if (s==INVALID_HANDLE_VALUE) return 0;
    m.dwSize=sizeof(m);
    if (pModule32FirstW(s,&m)) do {
        if (!_wcsicmp(basename?m.szModule:m.szExePath,path)) {base=(DWORD)m.modBaseAddr;break;}
    } while (pModule32NextW(s,&m));
    CloseHandle(s); return base;
}
static DWORD remote_system_proc(DWORD pid,const char *name) {
    FARPROC fn=GetProcAddress(GetModuleHandleA("kernel32.dll"),name);
    MEMORY_BASIC_INFORMATION m; wchar_t path[MAX_PATH],*base;
    DWORD remote;
    if (!fn || !VirtualQuery((void*)fn,&m,sizeof(m)) ||
        !GetModuleFileNameW((HMODULE)m.AllocationBase,path,MAX_PATH)) return 0;
    base=wcsrchr(path,L'\\');base=base?base+1:path;
    remote=module_base(pid,base,1);
    return remote?remote+(DWORD)fn-(DWORD)m.AllocationBase:0;
}
static int remote_call(HANDLE p,DWORD address,void *arg,DWORD *result) {
    HANDLE t; DWORD wait;
    t=CreateRemoteThread(p,NULL,0,(LPTHREAD_START_ROUTINE)address,arg,0,NULL);
    if (!t) return 0;
    wait=WaitForSingleObject(t,10000);
    if (wait!=WAIT_OBJECT_0) { CloseHandle(t); return -1; } /* outcome unknown, no retry */
    if (!GetExitCodeThread(t,result)) {CloseHandle(t);return 0;}
    CloseHandle(t);return 1;
}
int main(int argc,char **argv) {
    DWORD pid,n=MAX_PATH,loaded=0,started=0,rb,procaddr,access=0x43a;
    HANDLE p; wchar_t target[MAX_PATH],dll[MAX_PATH];
    SIZE_T bytes,written; void *remote; HMODULE local;
    FARPROC start,state; int rc;
    if (argc!=3) {fputs("usage: stage3_loader PID ABSOLUTE_BUILT_DLL\n",stderr);return 2;}
    pModule32FirstW=(void*)GetProcAddress(GetModuleHandleA("kernel32.dll"),"Module32FirstW");
    pModule32NextW=(void*)GetProcAddress(GetModuleHandleA("kernel32.dll"),"Module32NextW");
    pQueryImageW=(void*)GetProcAddress(GetModuleHandleA("kernel32.dll"),"QueryFullProcessImageNameW");
    if (!pModule32FirstW || !pModule32NextW || !pQueryImageW) return 2;
    pid=strtoul(argv[1],NULL,10);
    if (!pid || !MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,argv[2],-1,dll,MAX_PATH)) return 2;
    if (_wcsicmp(dll,SCAPI_STAGE5_DLL_PATH)) return 2;
    p=OpenProcess(access,FALSE,pid);
    if (!p) {fprintf(stderr,"OpenProcess error=%lu\n",GetLastError());return 3;}
    if (!pQueryImageW(p,0,target,&n) ||
        _wcsicmp(target,L"D:\\Starcraft\\starcraft.exe")) {CloseHandle(p);return 4;}
    local=LoadLibraryW(dll);
    if (!local) {fprintf(stderr,"local DLL load error=%lu\n",GetLastError());CloseHandle(p);return 5;}
    start=GetProcAddress(local,"_SCAPI_Start@4");state=GetProcAddress(local,"SCAPI_State");
    if (!start || !state) {fputs("missing exports\n",stderr);FreeLibrary(local);CloseHandle(p);return 5;}
    rb=module_base(pid,dll,0);
    if (rb) {fputs("already resident: use existing instance or restart game; no second install\n",stderr);FreeLibrary(local);CloseHandle(p);return 6;}
    procaddr=remote_system_proc(pid,"LoadLibraryW");
    bytes=(wcslen(dll)+1)*sizeof(wchar_t);
    remote=VirtualAllocEx(p,NULL,bytes,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    if (!remote || !procaddr) {FreeLibrary(local);CloseHandle(p);return 7;}
    if (!WriteProcessMemory(p,remote,dll,bytes,&written) || written!=bytes) {
        VirtualFreeEx(p,remote,0,MEM_RELEASE);FreeLibrary(local);CloseHandle(p);return 7;
    }
    rc=remote_call(p,procaddr,remote,&loaded);
    if (rc==-1) {fputs("load outcome unknown; remote argument retained; do not retry\n",stderr);FreeLibrary(local);CloseHandle(p);return 8;}
    VirtualFreeEx(p,remote,0,MEM_RELEASE);
    if (!rc || !loaded) {FreeLibrary(local);CloseHandle(p);return 8;}
    rb=module_base(pid,dll,0);
    if (!rb || rb!=loaded) {FreeLibrary(local);CloseHandle(p);return 9;}
    rc=remote_call(p,rb+(DWORD)start-(DWORD)local,NULL,&started);
    if (rc==-1) {
        fputs("install outcome UNKNOWN; thread may finish later; inspect mailbox; do not unload or retry\n",stderr);
        FreeLibrary(local);CloseHandle(p);return 10;
    }
    if (rc==1 && started==0) {
        DWORD unloaded=0,freeaddr=remote_system_proc(pid,"FreeLibrary");
        int cleanup=freeaddr?remote_call(p,freeaddr,(void*)loaded,&unloaded):0;
        fprintf(stderr,"install rejected before hook; DLL cleanup rc=%d result=%lu; no automatic retry\n",cleanup,unloaded);
        FreeLibrary(local);CloseHandle(p);return 10;
    }
    if (rc!=1 || (started!=1 && started!=2)) {
        fprintf(stderr,"install not confirmed rc=%d code=%lu; inspect mailbox; do not retry\n",rc,started);
        FreeLibrary(local);CloseHandle(p);return 10;
    }
    printf("{\"pid\":%lu,\"dll_base\":%lu,\"mailbox\":%lu,\"mailbox_bytes\":%u,\"access\":%lu,\"installed_probe_only\":true,\"healthy\":%s}\n",
           pid,rb,rb+(DWORD)state-(DWORD)local,(unsigned)sizeof(ScapiState),access,started==1?"true":"false");
    if (started==2) fputs("IAT protection restore failed; actions disabled; detach/inspect required\n",stderr);
    FreeLibrary(local);CloseHandle(p);return started==1?0:11;
}
