"""Cooperative cancellation and phase callbacks without UI dependencies."""
import subprocess
import time

class Cancelled(RuntimeError): pass

class ExecutionControl:
    def __init__(self,cancelled=None,phase=None,process=None,verify=None,case=None):
        self.cancelled=cancelled or (lambda:False)
        self.phase=phase or (lambda value:None)
        self.process=process or (lambda pid:None)
        self.verify=verify or (lambda:None)
        self.case=case or (lambda case_id,index,total:None)

    def check(self):
        if self.cancelled():raise Cancelled("任务取消请求已登记")

    def wait(self,process,timeout):
        deadline=time.monotonic()+timeout
        while True:
            self.check()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise subprocess.TimeoutExpired("gipc",timeout)
            try:return process.wait(timeout=min(.2,remaining))
            except subprocess.TimeoutExpired:pass
