"""Action results for a playing caller. Developer mailbox fields stay internal.

Selection uses actual operable view points/rectangles and native GUI rules.
Enemy/neutral inspection selection grants no command permission. Exact Move
targets must be in the current operable viewport, including unknown areas.
Unit observations, hidden properties and terrain queries are not provided.
"""
from dataclasses import asdict, dataclass
from tools.stage3_client import Client,MailboxBusyError

@dataclass(frozen=True)
class SelectionTicket:
    pid: int
    process_creation: int
    epoch: int
    generation: int
    count: int

def action_result(reply):
    """Explicit allowlist; never forward a developer response wholesale."""
    op=reply['operation']; result=reply['result']; s=reply['developer_after']
    if result in ('queue_unconfirmed','unknown'):
        return {'status':'outcome_unknown','reason':result,'automatic_retry':False}
    if result!='ok':return {'status':'rejected','reason':result}
    if op=='camera':
        return {'status':'applied','camera':{'x':s['actual_x'],'y':s['actual_y']}}
    if op=='move':return {'status':'enqueued'}
    if op in ('select','box','same'):return {'status':'selected'}
    raise ValueError('not a public action')

class Actions:
    client_factory=Client

    def __init__(self,pid):
        self._client=self.client_factory(pid)
        try:
            s=self._client.peek()
            if s['hook_installed']!=1:raise RuntimeError('adapter_not_ready')
            self._epoch=s['epoch']
            self._pid=pid
            self._creation=self._client.process['creation_time_filetime']
        except BaseException:
            self._client.close()
            raise

    def close(self):self._client.close()
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()

    def session(self):
        s=self._client.peek()
        return {'active':bool(s['active'] and s['tile_pointer']),
                'session_changed':s['epoch']!=self._epoch,
                'actions_enabled':s['enable_actions']==1 and s['hook_installed']==1}

    def _request(self,op,x,y,epoch,selection=None,**kwargs):
        try:return self._client.request(op,x,y,epoch,selection,**kwargs)
        except TimeoutError:return None # Pending work is never automatically resubmitted.
        except MailboxBusyError as error:
            result={'operation':op,'result':'busy','developer_after':{}}
            if op=='menu-state':
                acquiring=error.args==('another client owns mailbox',)
                result['diagnostic']={'operation':op,'phase':'mutex_acquire' if acquiring else 'existing_request_in_flight',
                    'mutex_wait_ms':200 if acquiring else 0,'publication':'not_started',
                    'detail':str(error),'automatic_retry':False}
            return result

    def camera(self,x,y):
        r=self._request('camera',x,y,self._epoch)
        return action_result(r) if r else {'status':'outcome_unknown','automatic_retry':False}

    def _select(self,op,x,y,**kwargs):
        r=self._request(op,x,y,self._epoch,**kwargs)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        result=action_result(r)
        if result['status']=='selected':
            s=r['developer_after']
            result['selection']=asdict(SelectionTicket(self._pid,self._creation,
                  s['epoch'],s['selection_generation'],s['selection_count']))
        return result

    def select_at(self,x,y,*,add=False):
        """Shift-add/toggle when add=True; otherwise ordinary point selection."""
        return self._select('select',x,y,add=add)

    def select_box(self,x1,y1,x2,y2):
        return self._select('box',x1,y1,x2=x2,y2=y2)

    def select_same_type(self,x,y):
        """Native screen same-type semantics, at most twelve actual units."""
        return self._select('same',x,y)

    def current_selection(self):
        r=self._request('probe',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        if s['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        return {'selection':asdict(SelectionTicket(self._pid,self._creation,
                    s['epoch'],s['selection_generation'],s['selection_count']))}

    def move_selected(self,ticket,x,y):
        if not isinstance(ticket,SelectionTicket):raise ValueError('selection ticket required')
        values=asdict(ticket)
        if any(type(v) is not int or v<0 for v in values.values()):raise ValueError('invalid selection ticket')
        if ticket.pid!=self._pid or ticket.process_creation!=self._creation:
            return {'status':'rejected','reason':'different_process'}
        if ticket.epoch>=2**32 or ticket.generation>=2**32 or not 1<=ticket.count<=12:
            raise ValueError('invalid selection ticket')
        r=self._request('move',x,y,ticket.epoch,ticket.generation)
        return action_result(r) if r else {'status':'outcome_unknown','automatic_retry':False}
