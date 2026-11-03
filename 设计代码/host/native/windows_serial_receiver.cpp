#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <process.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <atomic>
#include <new>

// A bounded native receiver.  No Python callback, interpreter lock or WSL pipe
// participates in posting reads.  Requests are consumed in posting order.
// First-byte completion can consume one request per small USB burst. Keep
// enough requests posted to cover host scheduling stalls during PDS routing.
static const DWORD DEPTH=256, CHUNK=65536, RING=16*1024*1024;
struct Slot { OVERLAPPED ov; char bytes[CHUNK]; bool active; };
struct Rx {
 HANDLE port, diagnostic, worker, initialized, stop_event;
 SRWLOCK lock; CONDITION_VARIABLE changed;
 bool stop, ended; std::atomic<DWORD> fatal;
 char* ring; size_t head, tail, used, peak;
 uint64_t reads, bytes, empty; std::atomic<uint64_t> errors;
 uint64_t max_repost_us, max_resume_us;
 DWORD status_interval_ms, request_bytes; ULONGLONG last_status_tick;
};
static void diagnostic(Rx* r,const char* message,DWORD error) {
 char line[256]; int n=snprintf(line,sizeof line,"%s error=%lu tick_ms=%llu\n",message,(unsigned long)error,(unsigned long long)GetTickCount64());
 DWORD written=0; WriteFile(r->diagnostic,line,(DWORD)n,&written,NULL);
}
static bool post(Rx* r,Slot& s) {
 LARGE_INTEGER started, finished, frequency;
 QueryPerformanceCounter(&started);
 ResetEvent(s.ov.hEvent); DWORD n=0;
 BOOL ok=ReadFile(r->port,s.bytes,r->request_bytes,&n,&s.ov);
 DWORD error=ok?ERROR_SUCCESS:GetLastError();
 QueryPerformanceCounter(&finished);QueryPerformanceFrequency(&frequency);
 uint64_t elapsed=(uint64_t)((finished.QuadPart-started.QuadPart)*1000000/frequency.QuadPart);
 if(elapsed>r->max_repost_us)r->max_repost_us=elapsed;
 if (!ok && error!=ERROR_IO_PENDING) { r->fatal=error; return false; }
 s.active=true; return true;
}
static bool check_status(Rx* r,bool final_check=false) {
 ULONGLONG tick=GetTickCount64();
 if(!final_check && r->status_interval_ms && tick-r->last_status_tick<r->status_interval_ms)return true;
 r->last_status_tick=tick;
 DWORD flags=0; COMSTAT queued={};
 if(!ClearCommError(r->port,&flags,&queued)) {r->fatal=GetLastError();return false;}
 if(flags) {++r->errors;diagnostic(r,"SERIAL_BRIDGE_COMM_ERROR native=1 flags",flags);}
 return true;
}
static unsigned __stdcall receive(void* value) {
 Rx* r=(Rx*)value; SetThreadPriority(GetCurrentThread(),THREAD_PRIORITY_HIGHEST);
 Slot* slots=(Slot*)calloc(DEPTH,sizeof(Slot));
 if(!slots) {r->fatal=ERROR_OUTOFMEMORY;SetEvent(r->initialized);return 1;}
 DWORD initialized=0;
 for(DWORD i=0;i<DEPTH;i++) {
  slots[i].ov.hEvent=CreateEventW(NULL,TRUE,FALSE,NULL);
  initialized=i+1;
  if(!slots[i].ov.hEvent || !post(r,slots[i])) { if(!r->fatal)r->fatal=GetLastError(); break; }
 }
 SetEvent(r->initialized);
 DWORD cursor=0;
 for(;;) {
  AcquireSRWLockExclusive(&r->lock); bool stopped=r->stop; ReleaseSRWLockExclusive(&r->lock);
  if(stopped || r->fatal)break;
  Slot& s=slots[cursor]; DWORD n=0;
  HANDLE events[2]={r->stop_event,s.ov.hEvent};
  DWORD wait=WaitForMultipleObjects(2,events,FALSE,INFINITE);
  if(wait==WAIT_OBJECT_0)break;
  if(wait!=WAIT_OBJECT_0+1) {r->fatal=GetLastError();break;}
  LARGE_INTEGER resumed, finished, frequency;QueryPerformanceCounter(&resumed);
  if(!GetOverlappedResult(r->port,&s.ov,&n,FALSE)) {
   DWORD e=GetLastError();
   AcquireSRWLockExclusive(&r->lock);stopped=r->stop;ReleaseSRWLockExclusive(&r->lock);
   if(!(stopped && e==ERROR_OPERATION_ABORTED))r->fatal=e;
   break;
  }
  s.active=false;
  if(n>r->request_bytes) {r->fatal=ERROR_INVALID_DATA;break;}
  if(!check_status(r))break;
  // Repost only after copying the completed bytes, preserving FIFO order even
  // when the driver completes a replacement synchronously.
  AcquireSRWLockExclusive(&r->lock);
  ++r->reads;r->bytes+=n;r->empty+=n==0;
  if(n>RING-r->used)r->fatal=ERROR_BUFFER_OVERFLOW;
  else if(n) {
   size_t first=n < RING-r->tail ? n : RING-r->tail;
   memcpy(r->ring+r->tail,s.bytes,first);memcpy(r->ring,s.bytes+first,n-first);
   r->tail=(r->tail+n)%RING;r->used+=n;if(r->used>r->peak)r->peak=r->used;
   WakeAllConditionVariable(&r->changed);
  }
  ReleaseSRWLockExclusive(&r->lock);
  if(r->fatal || !post(r,s))break;
  QueryPerformanceCounter(&finished);QueryPerformanceFrequency(&frequency);
  uint64_t elapsed=(uint64_t)((finished.QuadPart-resumed.QuadPart)*1000000/frequency.QuadPart);
  if(elapsed>r->max_resume_us)r->max_resume_us=elapsed;
  cursor=(cursor+1)%DEPTH;
 }
 for(DWORD i=0;i<initialized;i++)if(slots[i].active)CancelIoEx(r->port,&slots[i].ov);
 for(DWORD i=0;i<initialized;i++) {
  if(slots[i].active) {DWORD n=0;GetOverlappedResult(r->port,&slots[i].ov,&n,TRUE);}
  if(slots[i].ov.hEvent)CloseHandle(slots[i].ov.hEvent);
 }
 free(slots);
 // Error flags remain latched between polls; always inspect the final tail.
 check_status(r,true);
 char timing[256];int length=snprintf(timing,sizeof timing,
  "SERIAL_BRIDGE_NATIVE_TIMING max_repost_us=%llu max_service_us=%llu\n",
  (unsigned long long)r->max_repost_us,(unsigned long long)r->max_resume_us);
 DWORD written=0;WriteFile(r->diagnostic,timing,(DWORD)length,&written,NULL);
 if(r->fatal)diagnostic(r,"SERIAL_BRIDGE_READ_ERROR native=1",r->fatal);
 AcquireSRWLockExclusive(&r->lock);r->ended=true;WakeAllConditionVariable(&r->changed);ReleaseSRWLockExclusive(&r->lock);
 return 0;
}
extern "C" __declspec(dllexport) void __cdecl pangu_rx_stop(void* ctx);
extern "C" __declspec(dllexport) void* __cdecl pangu_rx_start_options(void* port,void* diagnostic_handle,DWORD status_interval_ms,DWORD request_bytes) {
 if(request_bytes!=4096 && request_bytes!=16384 && request_bytes!=CHUNK)return NULL;
 Rx* r=new(std::nothrow) Rx{};if(!r)return NULL;
 r->port=(HANDLE)port;r->diagnostic=(HANDLE)diagnostic_handle;
 r->status_interval_ms=status_interval_ms;r->last_status_tick=0;r->request_bytes=request_bytes;
 InitializeSRWLock(&r->lock);InitializeConditionVariable(&r->changed);
 r->ring=(char*)malloc(RING);r->initialized=CreateEventW(NULL,TRUE,FALSE,NULL);
 r->stop_event=CreateEventW(NULL,TRUE,FALSE,NULL);
 if(!r->ring || !r->initialized || !r->stop_event) {
  if(r->initialized)CloseHandle(r->initialized);
  if(r->stop_event)CloseHandle(r->stop_event);
  free(r->ring);delete r;return NULL;
 }
 r->worker=(HANDLE)_beginthreadex(NULL,0,receive,r,0,NULL);
 if(!r->worker) {CloseHandle(r->stop_event);CloseHandle(r->initialized);free(r->ring);delete r;return NULL;}
 WaitForSingleObject(r->initialized,INFINITE);
 if(r->fatal) {pangu_rx_stop(r);return NULL;}
 return r;
}
extern "C" __declspec(dllexport) void* __cdecl pangu_rx_start_configured(void* port,void* diagnostic_handle,DWORD status_interval_ms) {
 return pangu_rx_start_options(port,diagnostic_handle,status_interval_ms,CHUNK);
}
extern "C" __declspec(dllexport) void* __cdecl pangu_rx_start(void* port,void* diagnostic_handle) {
 return pangu_rx_start_configured(port,diagnostic_handle,0);
}
extern "C" __declspec(dllexport) int __cdecl pangu_rx_read(void* ctx,void* bytes,DWORD size,DWORD* count,DWORD timeout) {
 Rx* r=(Rx*)ctx;*count=0;AcquireSRWLockExclusive(&r->lock);
 while(!r->used && !r->ended && !r->fatal && !r->stop) {
  if(!SleepConditionVariableSRW(&r->changed,&r->lock,timeout,0)) {
   DWORD e=GetLastError();ReleaseSRWLockExclusive(&r->lock);return e==ERROR_TIMEOUT?0:-1;
  }
 }
 if(r->fatal) {ReleaseSRWLockExclusive(&r->lock);return -1;}
 DWORD n=(DWORD)(r->used<size?r->used:size);size_t first=n<RING-r->head?n:RING-r->head;
 memcpy(bytes,r->ring+r->head,first);memcpy((char*)bytes+first,r->ring,n-first);
 r->head=(r->head+n)%RING;r->used-=n;*count=n;
 bool ended=r->ended || r->stop;ReleaseSRWLockExclusive(&r->lock);return n?1:(ended?-1:0);
}
extern "C" __declspec(dllexport) void __cdecl pangu_rx_stats(void* ctx,uint64_t* values) {
 Rx* r=(Rx*)ctx;AcquireSRWLockExclusive(&r->lock);
 values[0]=r->reads;values[1]=r->bytes;values[2]=r->empty;values[3]=r->errors;values[4]=r->peak;values[5]=r->fatal;
 ReleaseSRWLockExclusive(&r->lock);
}
extern "C" __declspec(dllexport) void __cdecl pangu_rx_stop(void* ctx) {
 Rx* r=(Rx*)ctx;if(!r)return;
 AcquireSRWLockExclusive(&r->lock);r->stop=true;WakeAllConditionVariable(&r->changed);ReleaseSRWLockExclusive(&r->lock);
 // Wake even when an idle preview read has no total timeout. The receive
 // thread cancels only its own OVERLAPPED reads, preserving concurrent writes.
 SetEvent(r->stop_event);
 WaitForSingleObject(r->worker,INFINITE);CloseHandle(r->worker);CloseHandle(r->stop_event);
 CloseHandle(r->initialized);free(r->ring);delete r;
}
