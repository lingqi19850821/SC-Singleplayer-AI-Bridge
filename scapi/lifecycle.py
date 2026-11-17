"""Durable menu permits for the trusted CU host; never executes game input.

The public player can request/poll operations. Only the host can quiesce,
claim individual menu inputs, verify effects, and bind a new game session.
"""
import json
import math
from pathlib import Path
import secrets
import sqlite3
import time


class Lifecycle:
    def __init__(self, path, *, profile_id, save_dir, wall=time.time, monotonic=time.monotonic):
        if not isinstance(profile_id,str) or not profile_id.strip():
            raise ValueError('explicit profile_id required')
        self.profile=profile_id
        self.save_dir=Path(save_dir).resolve()
        if not self.save_dir.is_dir():raise ValueError('existing verified save_dir required')
        self.wall,self.mono=wall,monotonic
        self.db=sqlite3.connect(path,timeout=2)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS lifecycle_profile(
            profile TEXT PRIMARY KEY,mode TEXT NOT NULL,session TEXT,
            last_save_wall REAL,last_save_mono REAL,last_input_wall REAL,last_input_mono REAL,
            expected_session TEXT);
          CREATE TABLE IF NOT EXISTS lifecycle_operation(
            id TEXT PRIMARY KEY,profile TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,
            slot TEXT,name TEXT,session TEXT NOT NULL,created REAL NOT NULL,evidence TEXT,result TEXT,
            FOREIGN KEY(profile) REFERENCES lifecycle_profile(profile));
          CREATE UNIQUE INDEX IF NOT EXISTS lifecycle_one_pending ON lifecycle_operation(profile)
            WHERE state IN ('pending','executing','unknown');
          CREATE TABLE IF NOT EXISTS lifecycle_slot(
            id TEXT PRIMARY KEY,profile TEXT NOT NULL,name TEXT NOT NULL,size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,created REAL NOT NULL,UNIQUE(profile,name));
          CREATE INDEX IF NOT EXISTS lifecycle_slot_page ON lifecycle_slot(profile,created,id);
        ''')
        if 'expected_session' not in {row[1] for row in self.db.execute('PRAGMA table_info(lifecycle_profile)')}:
            self.db.execute('ALTER TABLE lifecycle_profile ADD COLUMN expected_session TEXT')
        self.db.execute('INSERT OR IGNORE INTO lifecycle_profile(profile,mode) VALUES(?,?)',
                        (self.profile,'unbound'))
        self.db.commit()

    def close(self):self.db.close()
    def _profile(self):
        return self.db.execute('SELECT * FROM lifecycle_profile WHERE profile=?',(self.profile,)).fetchone()
    def _operation(self,op_id):
        return self.db.execute('SELECT * FROM lifecycle_operation WHERE profile=? AND id=?',
                               (self.profile,op_id)).fetchone()
    @staticmethod
    def _session(session):
        required=('pid','process_creation','epoch')
        if not isinstance(session,dict) or set(session)!=set(required) or any(type(session[k]) is not int or session[k]<=0 for k in required):
            raise ValueError('host verified pid/process_creation/epoch required')
        return json.dumps(session,sort_keys=True,separators=(',',':'))
    def _elapsed(self,old_wall,old_mono):
        if old_wall is None:return float('inf')
        elapsed_wall=max(0,self.wall()-old_wall)
        elapsed_mono=self.mono()-old_mono
        # Same system-wide monotonic clock: wall jumps cannot earn a burst or
        # shorten cooldown. Across reboot, reconstruct conservatively from wall.
        return min(elapsed_wall,elapsed_mono) if elapsed_mono>=0 else elapsed_wall
    def remaining_seconds(self):
        row=self._profile()
        elapsed=self._elapsed(row['last_save_wall'],row['last_save_mono'])
        return 0 if elapsed>=300 else max(0,math.ceil(300-elapsed-1e-9))
    def status(self):
        row=self._profile()
        remaining=self.remaining_seconds()
        pending=self.db.execute("SELECT id,kind,state,slot,name FROM lifecycle_operation WHERE profile=? AND state IN ('pending','executing','unknown')",(self.profile,)).fetchone()
        return {'status':'observed','mode':row['mode'],'can_save':row['mode']=='gameplay' and remaining==0,
                'remaining_seconds':remaining,'pending':dict(pending) if pending else None,
                'gameplay_allowed':row['mode']=='gameplay'}
    def bind_session(self,session):
        """Trusted host only, after current process/epoch and collector handshake."""
        encoded=self._session(session)
        with self.db:
            row=self._profile()
            if row['mode'] not in ('unbound','rebind_required','gameplay'):
                raise ValueError('operation not resolved; cannot bind')
            if row['mode']=='rebind_required' and encoded==row['session']:
                raise ValueError('load/restart requires a newly observed session')
            if row['mode']=='rebind_required' and encoded!=row['expected_session']:
                raise ValueError('binding must match the verified receipt session')
            self.db.execute('UPDATE lifecycle_profile SET mode=?,session=?,expected_session=NULL WHERE profile=?',
                            ('gameplay',encoded,self.profile))
        return self.status()
    def request(self,kind,*,slot=None):
        if kind not in ('save','load','restart','objectives'):raise ValueError('unsupported menu operation')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self._profile()
            if state['mode']!='gameplay':
                return {'status':'unavailable','reason':'menu_or_session_pending','automatic_retry':False}
            remaining=self.remaining_seconds()
            if kind=='save' and remaining:
                return {'status':'unavailable','reason':'save_cooldown','remaining_seconds':remaining}
            name=None
            if kind=='load':
                saved=self.db.execute('SELECT * FROM lifecycle_slot WHERE profile=? AND id=?',(self.profile,slot)).fetchone()
                if saved is None:return {'status':'rejected','reason':'unknown_save_slot'}
                path=self.save_dir/(saved['name']+'.sng')
                if not path.is_file() or (path.stat().st_size,path.stat().st_mtime_ns)!=(saved['size'],saved['mtime_ns']):
                    return {'status':'unavailable','reason':'registered_save_changed'}
                name=saved['name']
            elif slot is not None:raise ValueError('slot only valid for load')
            op_id=secrets.token_hex(12)
            if kind=='save':
                name='AI'+secrets.token_hex(3).upper()
                if (self.save_dir/(name+'.sng')).exists():
                    return {'status':'unavailable','reason':'save_name_collision','automatic_retry':False}
                slot=secrets.token_hex(12)
            self.db.execute('INSERT INTO lifecycle_operation(id,profile,kind,state,slot,name,session,created) VALUES(?,?,?,?,?,?,?,?)',
                            (op_id,self.profile,kind,'pending',slot,name,state['session'],self.wall()))
            self.db.execute('UPDATE lifecycle_profile SET mode=? WHERE profile=?',('menu_pending',self.profile))
        return {'status':'host_action_required','operation_id':op_id,'operation':kind,
                'slot':slot,'save_name':name,'gameplay_allowed':False}
    def host_begin(self,op_id,*,session,quiescent):
        """Host evidence is private; public dispatch must not expose this method."""
        required=('actions_disabled','building_closed','events_stopped','no_request_in_flight','native_interval_elapsed')
        if set(quiescent)!=set(required) or not all(quiescent[k] is True for k in required):
            raise ValueError('gameplay/capture must be quiescent before menus')
        with self.db:
            op=self._operation(op_id)
            if op is None or op['state']!='pending' or op['session']!=self._session(session):
                raise ValueError('stale menu permit')
            self.db.execute('UPDATE lifecycle_operation SET state=? WHERE id=?',('executing',op_id))
        return {'operation_id':op_id,'operation':op['kind'],'save_name':op['name'],'slot':op['slot']}
    def claim_menu_input(self,op_id):
        """Host calls once BEFORE every individual CU input. No earned bursts."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            op=self._operation(op_id)
            if op is None or op['state']!='executing':raise ValueError('menu permit not executing')
            state=self._profile()
            elapsed=self._elapsed(state['last_input_wall'],state['last_input_mono'])
            if elapsed+1e-9<0.334:
                return {'status':'unavailable','reason':'rate_limited','remaining_ms':math.ceil((0.334-elapsed)*1000)}
            self.db.execute('UPDATE lifecycle_profile SET last_input_wall=?,last_input_mono=? WHERE profile=?',
                            (self.wall(),self.mono(),self.profile))
        return {'status':'allowed'}
    def host_finish(self,op_id,*,outcome,evidence):
        if outcome not in ('success','failed','unknown'):raise ValueError('explicit outcome required')
        if not isinstance(evidence,dict):raise ValueError('structured host evidence required')
        with self.db:
            op=self._operation(op_id)
            if op is None or op['state'] not in ('executing','reconciling'):raise ValueError('stale/replayed receipt')
            if outcome=='success':
                if not evidence.get('returned_to_game') or not evidence.get('ui_evidence'):
                    raise ValueError('actual GUI result evidence required')
                if op['kind']=='save':
                    path=self.save_dir/(op['name']+'.sng')
                    if not path.is_file() or path.stat().st_size<=0:
                        raise ValueError('new actual save file required')
                    if not evidence.get('save_dialog_confirmed'):
                        raise ValueError('save completion observation required')
                    stat=path.stat()
                    self.db.execute('INSERT INTO lifecycle_slot VALUES(?,?,?,?,?,?)',
                        (op['slot'],self.profile,op['name'],stat.st_size,stat.st_mtime_ns,self.wall()))
                    self.db.execute('UPDATE lifecycle_profile SET last_save_wall=?,last_save_mono=? WHERE profile=?',
                                    (self.wall(),self.mono(),self.profile))
                if op['kind'] in ('load','restart'):
                    new_session=evidence.get('session')
                    if self._session(new_session)==op['session']:
                        raise ValueError('actual changed session/epoch required')
                    previous=json.loads(op['session'])
                    if any(new_session[k]!=previous[k] for k in ('pid','process_creation')):
                        raise ValueError('unexpected process change')
                    if not evidence.get('old_tickets_rejected') or not evidence.get('collector_rebound'):
                        raise ValueError('old tickets invalidation + capture rebind required')
                if op['kind']=='objectives' and (not isinstance(evidence.get('objectives'),list) or
                        not evidence['objectives'] or any(not isinstance(x,str) or not x.strip() for x in evidence['objectives'])):
                    raise ValueError('actually displayed objective text required')
            mode=('rebind_required' if op['kind'] in ('load','restart') else 'gameplay') if outcome=='success' else ('outcome_unknown' if outcome=='unknown' else 'gameplay')
            if outcome=='failed' and not evidence.get('returned_to_game'):
                mode='outcome_unknown'
            self.db.execute('UPDATE lifecycle_operation SET state=?,evidence=?,result=? WHERE id=?',
                            ('unknown' if mode=='outcome_unknown' else outcome,json.dumps(evidence),outcome,op_id))
            expected=self._session(evidence['session']) if mode=='rebind_required' else None
            self.db.execute('UPDATE lifecycle_profile SET mode=?,expected_session=? WHERE profile=?',(mode,expected,self.profile))
        return self.operation_status(op_id)
    def host_reconcile(self,op_id,*,outcome,evidence):
        """Resolve an uncertain result from NEW observations; never reissue input."""
        if outcome not in ('success','failed'):raise ValueError('definite newly observed outcome required')
        with self.db:
            op=self._operation(op_id)
            if op is None or op['state']!='unknown' or self._profile()['mode']!='outcome_unknown':
                raise ValueError('no uncertain operation to reconcile')
            if not evidence.get('reconciliation_evidence'):
                raise ValueError('new independent observation required')
            self.db.execute('UPDATE lifecycle_operation SET state=? WHERE id=?',('reconciling',op_id))
            return self.host_finish(op_id,outcome=outcome,evidence=evidence)
    def operation_status(self,op_id,*,text_budget_bytes=None):
        op=self._operation(op_id)
        if op is None:return {'status':'rejected','reason':'unknown_operation'}
        result={'status':'observed','operation_id':op_id,'operation':op['kind'],'state':op['state'],
                'slot':op['slot'],'save_name':op['name'],'mode':self._profile()['mode'],
                'remaining_seconds':self.remaining_seconds(),'automatic_retry':False}
        if op['kind']=='objectives' and op['state']=='success':
            shown=json.loads(op['evidence'])['objectives']
            needed=len(json.dumps(shown,ensure_ascii=False).encode('utf-8'))
            if text_budget_bytes is None:
                result.update(objectives_available=True,required_text_budget_bytes=needed)
            elif type(text_budget_bytes) is not int or text_budget_bytes<1:
                raise ValueError('positive text budget required')
            elif text_budget_bytes<needed:
                result.update(status='larger_text_budget_required',required_text_budget_bytes=needed)
            else:result['objectives']=shown
        return result
    def list_saves(self,*,limit,text_budget_bytes,cursor=0):
        if type(limit) is not int or limit<1 or type(text_budget_bytes) is not int or text_budget_bytes<1:
            raise ValueError('explicit positive count/text budgets required')
        if type(cursor) is not int or cursor<0:raise ValueError('nonnegative cursor required')
        if text_budget_bytes<2:
            return {'status':'larger_text_budget_required','required_bytes_at_least':2}
        iterator=self.db.execute(
            'SELECT id AS slot,name,created AS saved_at FROM lifecycle_slot WHERE profile=? ORDER BY created,id LIMIT ? OFFSET ?',
            (self.profile,limit+1,cursor))
        items=[];needed=2;more=False
        for row in iterator:
            if len(items)==limit:more=True;break
            item=dict(row)
            next_bytes=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))+(2 if items else 0)
            if needed+next_bytes>text_budget_bytes:
                return {'status':'larger_text_budget_required','required_bytes_at_least':needed+next_bytes}
            items.append(item);needed+=next_bytes
        return {'status':'observed','saves':items,'next_cursor':cursor+len(items) if more else None,'has_more':more}
