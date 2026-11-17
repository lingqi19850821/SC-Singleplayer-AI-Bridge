"""Cooperative total time budget for direct observation reads.

The 334ms default matches the information worker's existing per-step budget.
Checks cover iteration and completed reads; a synchronous OS read cannot be
interrupted here and can overrun the deadline before the next check rejects it.
"""
import math
import time


class ObservationDeadlineExceeded(RuntimeError):
    pass


class ObservationReadBudget:
    def __init__(self,seconds=0.334,*,clock=None):
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<=0:
            raise ValueError('positive finite observation time budget required')
        self._clock=time.monotonic if clock is None else clock
        self._deadline=self._clock()+seconds

    def check(self):
        if self._clock()>=self._deadline:raise ObservationDeadlineExceeded('observation deadline exceeded')

    def reader(self,reader):
        return _BudgetedReader(reader,self)


class _BudgetedReader:
    def __init__(self,reader,budget):self._reader,self._budget=reader,budget

    def __getattr__(self,name):return getattr(self._reader,name)

    def read(self,address,size):
        self._budget.check()
        result=self._reader.read(address,size)
        self._budget.check()
        return result

    def metadata(self):
        self._budget.check()
        result=self._reader.metadata()
        self._budget.check()
        return result
