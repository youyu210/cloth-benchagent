"""Track owned process trees; Windows Job Object avoids PID-based termination."""
import os
import time

class ProcessTracker:
    def __init__(self,process):
        self.process=process
        self.handle=None
        if os.name=="nt":
            import ctypes as C
            from ctypes import wintypes as W
            self.C=C
            self.kernel=C.WinDLL("kernel32",use_last_error=True)
            k=self.kernel
            k.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR];k.CreateJobObjectW.restype=W.HANDLE
            k.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE];k.AssignProcessToJobObject.restype=W.BOOL
            k.TerminateJobObject.argtypes=[W.HANDLE,W.UINT];k.TerminateJobObject.restype=W.BOOL
            k.QueryInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p]
            k.QueryInformationJobObject.restype=W.BOOL
            k.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
            k.SetInformationJobObject.restype=W.BOOL
            k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
            class BasicLimit(C.Structure):
                _fields_=[("ProcessTime",C.c_int64),("JobTime",C.c_int64),("Flags",W.DWORD),
                          ("MinWorking",C.c_size_t),("MaxWorking",C.c_size_t),
                          ("ActiveLimit",W.DWORD),("Affinity",C.c_size_t),
                          ("Priority",W.DWORD),("Scheduling",W.DWORD)]
            class IO(C.Structure):
                _fields_=[(name,C.c_uint64) for name in ("read_ops","write_ops","other_ops","read_bytes","write_bytes","other_bytes")]
            class Extended(C.Structure):
                _fields_=[("Basic",BasicLimit),("IO",IO),("ProcessMemory",C.c_size_t),
                          ("JobMemory",C.c_size_t),("PeakProcess",C.c_size_t),("PeakJob",C.c_size_t)]
            class Accounting(C.Structure):
                _fields_=[("User",C.c_int64),("Kernel",C.c_int64),("PeriodUser",C.c_int64),
                          ("PeriodKernel",C.c_int64),("PageFaults",W.DWORD),
                          ("Total",W.DWORD),("Active",W.DWORD),("Terminated",W.DWORD)]
            self.Accounting=Accounting
            self.handle=k.CreateJobObjectW(None,None)
            if not self.handle:raise C.WinError(C.get_last_error())
            try:
                limit=Extended();limit.Basic.Flags=0x2000 # KILL_ON_JOB_CLOSE
                if not k.SetInformationJobObject(self.handle,9,C.byref(limit),C.sizeof(limit)):
                    raise C.WinError(C.get_last_error())
                if not k.AssignProcessToJobObject(self.handle,W.HANDLE(int(process._handle))):
                    raise C.WinError(C.get_last_error())
                self._resume_suspended_root()

            except BaseException:
                self.close()
                raise

    def _resume_suspended_root(self):
        # Popen closes its primary thread handle. Reopen only the suspended
        # process's own primary thread after assigning the Job Object.
        C=self.C
        from ctypes import wintypes as W
        k=self.kernel
        class ThreadEntry(C.Structure):
            _fields_=[("size",W.DWORD),("usage",W.DWORD),("thread_id",W.DWORD),
                      ("owner_pid",W.DWORD),("base_priority",W.LONG),
                      ("delta_priority",W.LONG),("flags",W.DWORD)]
        k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]
        k.CreateToolhelp32Snapshot.restype=W.HANDLE
        k.Thread32First.argtypes=[W.HANDLE,C.POINTER(ThreadEntry)]
        k.Thread32First.restype=W.BOOL
        k.Thread32Next.argtypes=[W.HANDLE,C.POINTER(ThreadEntry)]
        k.Thread32Next.restype=W.BOOL
        k.OpenThread.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenThread.restype=W.HANDLE
        k.ResumeThread.argtypes=[W.HANDLE];k.ResumeThread.restype=W.DWORD
        snapshot=k.CreateToolhelp32Snapshot(4,0)
        if snapshot==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
        try:
            entry=ThreadEntry();entry.size=C.sizeof(entry)
            present=k.Thread32First(snapshot,C.byref(entry))
            while present:
                if entry.owner_pid==self.process.pid:
                    thread=k.OpenThread(2,False,entry.thread_id)
                    if not thread:raise C.WinError(C.get_last_error())
                    try:
                        previous=k.ResumeThread(thread)
                        if previous!=1:raise RuntimeError("Primary thread was not suspended exactly once")
                    finally:k.CloseHandle(thread)
                    return
                present=k.Thread32Next(snapshot,C.byref(entry))
            raise RuntimeError("Suspended primary thread not found")
        finally:k.CloseHandle(snapshot)

    def active(self):
        if os.name=="nt":
            info=self.Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle,1,self.C.byref(info),self.C.sizeof(info),None):
                raise self.C.WinError(self.C.get_last_error())
            return info.Active
        try:
            os.killpg(self.process.pid,0)
            return 1
        except ProcessLookupError:
            return 0

    def terminate(self):
        if os.name=="nt":
            if self.active() and not self.kernel.TerminateJobObject(self.handle,1):
                raise self.C.WinError(self.C.get_last_error())
        else:
            import signal
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def finish(self,timeout=10):
        # Parent exit alone does not prove descendants have exited.
        if self.active():self.terminate()
        self.process.wait(timeout=timeout)
        deadline=time.monotonic()+timeout
        while self.active():
            if time.monotonic()>=deadline:raise RuntimeError("进程树仍有活动进程")
            time.sleep(.05)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None
