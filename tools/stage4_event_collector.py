"""Development event recorder. Fixed native stream only; no public raw memory API.

Runs independently of model requests. Capacity, polling and batch size are
explicit development settings. A full ring or lifecycle break is visible;
there is no silent eviction, automatic game pause or text-content deduplication.
"""
import argparse
import ctypes as C
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import struct
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.stage3_client import MailboxBusyError
from tools.stage4_client import Client
from tools.stage4_event_journal import Event,Journal

HEADER_FIELDS='''magic version header_bytes slot_bytes record_bytes capacity epoch text_bytes
record_text_offset slot_commit_offset slot_hash_offset reserved0 write_seq ack_seq
write_meta ack_meta flags lost rejected_full rejected_oversize rejected_epoch rejected_argument
published acked invalid_events last_error reserved1 reserved2 reserved3 reserved4'''.split()

def record_hash(data):
    value=2166136261
    for byte in data:value=((value^byte)*16777619)&0xffffffff
    return value

def decode_slot(raw,sequence,epoch):
    if len(raw)!=260:raise ValueError('event slot size mismatch')
    commit,checksum=struct.unpack_from('<II',raw)
    ep,seq,tick,kind,x,y,color,n=struct.unpack_from('<IIIIiiII',raw,8)
    if commit!=sequence or seq!=sequence or ep!=epoch or n>218:
        raise ValueError('event slot is inconsistent')
    record=raw[8:]
    if record_hash(record[:32]+record[32:32+n])!=checksum:
        raise ValueError('event slot checksum mismatch')
    return Event(seq,ep,tick,kind,x,y,color,record[32:32+n],n)

class StreamReader:
    def __init__(self,client,generation):
        self.client=client;self.generation=generation
        s=client.peek()
        self.address=s['events_address'];self.capacity=s['events_capacity']
        self.epoch=s['events_epoch'];self.bytes=s['events_bytes']
        if not self.address or self.bytes!=120+260*self.capacity:raise ValueError('event allocation mismatch')

    def header(self):
        s=self.client.peek()
        if (s['events_generation'],s['events_address'],s['events_capacity'],s['events_epoch'])!=(
            self.generation,self.address,self.capacity,self.epoch):
            raise ValueError('capture generation changed; stop this recorder')
        for _ in range(16):
            a=dict(zip(HEADER_FIELDS,struct.unpack('<30I',self.client.reader.read(self.address,120))))
            if a['write_meta']&1 or a['ack_meta']&1:continue
            b=dict(zip(HEADER_FIELDS,struct.unpack('<30I',self.client.reader.read(self.address,120))))
            if a['write_meta']!=b['write_meta'] or a['ack_meta']!=b['ack_meta']:continue
            if (b['magic'],b['version'],b['header_bytes'],b['slot_bytes'],b['record_bytes'],
                b['capacity'],b['epoch'],b['text_bytes'],b['record_text_offset'],b['slot_commit_offset'],
                b['slot_hash_offset'])!=(0x34564553,1,120,260,252,self.capacity,self.epoch,218,32,0,4):
                raise ValueError('unsupported event header')
            if not 1<=b['ack_seq']<=b['write_seq']<=0xffffffff or b['write_seq']-b['ack_seq']>self.capacity:
                raise ValueError('inconsistent event sequence window')
            if b['flags']&~7:raise ValueError('unknown event fault flag')
            return b
        raise MailboxBusyError('event status changed during bounded snapshot')

    def read_batch(self,limit):
        if type(limit) is not int or not 1<=limit<=self.capacity:raise ValueError('explicit bounded batch limit required')
        h=self.header();events=[]
        end=min(h['write_seq'],h['ack_seq']+limit)
        for seq in range(h['ack_seq'],end):
            address=self.address+120+((seq-1)%self.capacity)*260
            raw=self.client.reader.read(address,260)
            event=decode_slot(raw,seq,self.epoch)
            if struct.unpack('<I',self.client.reader.read(address,4))[0]!=seq:
                raise ValueError('event changed while unacknowledged')
            events.append(event)
        return h,events

    def acknowledge(self,next_sequence):
        wire=next_sequence if next_sequence<2**31 else next_sequence-2**32
        reply=self.client.request('capture-ack',wire)
        if reply['result']!='ok':raise ValueError('event acknowledge rejected: '+reply['result'])

def write_status(path,value):
    value['updated_utc']=datetime.now(timezone.utc).isoformat()
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf8');tmp.replace(path)

def main(*,client_factory=Client,evidence_stage='stage4',evidence_root=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid',type=int,required=True);p.add_argument('--capacity',type=int,required=True)
    p.add_argument('--batch-limit',type=int,required=True);p.add_argument('--poll-ms',type=int,required=True)
    p.add_argument('--journal',type=Path,required=True);p.add_argument('--status-file',type=Path,required=True)
    p.add_argument('--stop-file',type=Path,required=True)
    p.add_argument('--resume-run',type=int);p.add_argument('--attach-generation',type=int)
    p.add_argument('--drain-only',action='store_true');a=p.parse_args()
    root=(Path(evidence_root) if evidence_root is not None else
          Path(__file__).resolve().parents[1]/('evidence/'+evidence_stage)).resolve()
    root.relative_to((Path(__file__).resolve().parents[1]/'evidence').resolve())
    for path in (a.journal,a.status_file,a.stop_file):path.resolve().relative_to(root)
    if not 1<=a.batch_limit<=a.capacity or not 1<=a.poll_ms<2**31:raise ValueError('explicit valid development settings required')
    if (a.resume_run is None)!=(a.attach_generation is None) or a.drain_only and a.resume_run is None:
        raise ValueError('resume needs both run and generation; drain-only needs a retained run')
    if a.stop_file.exists() or a.status_file.exists():raise ValueError('choose unused status/stop paths')
    status={'at_utc':datetime.now(timezone.utc).isoformat(),'developer_only':True,'running':False,
            'settings':{'capacity':a.capacity,'batch_limit':a.batch_limit,'poll_ms':a.poll_ms},
            'journal':str(a.journal.resolve()),'text_events':0,'attack_events':0,'research_events':0,'upgrade_events':0,'busy_polls':0}
    c=None;reader=None;journal=None;run=None;failure=None
    try:
        c=client_factory(a.pid);status['process']=c.process
        if a.resume_run is None:
            reply=c.request('capture-start',a.capacity)
            if reply['result']!='ok':raise ValueError('capture start rejected: '+reply['result'])
        s=c.peek();reader=StreamReader(c,s['events_generation'])
        if a.resume_run is not None and (reader.generation!=a.attach_generation or reader.capacity!=a.capacity):
            raise ValueError('retained capture identity does not match')
        journal=Journal(a.journal)
        run=journal.start_run(reader.epoch) if a.resume_run is None else a.resume_run
        persisted=journal.status(run)
        known_head=persisted.last_sequence
        if persisted.epoch!=reader.epoch or persisted.closed_ns is not None:raise ValueError('retained journal run does not match open capture')
        status.update(running=True,run_id=run,capture_generation=reader.generation)
        c.api.k.GetTickCount64.argtypes=[];c.api.k.GetTickCount64.restype=C.c_ulonglong
        status['persist_latency_ms']={'samples':0,'total':0,'max':0}
        write_status(a.status_file,status)
        last_fault=None
        while not a.stop_file.exists():
            try:
                header,events=reader.read_batch(a.batch_limit)
                fault=(header['flags'],header['lost'])
                if events or fault!=last_fault:
                    result=journal.append(run,events,overflow=bool(header['flags']&1),lost=header['lost'],ack=reader.acknowledge)
                    status.update(native_status=reader.header(),persisted=asdict(journal.status(run)),last_batch=asdict(result))
                    # A previous append may have committed before a busy ack.
                    # Replay is then all duplicates but still new to these
                    # in-memory counters. Count the persisted head once.
                    if any(e.sequence>known_head for e in events):
                        status['text_events']+=sum(e.type==1 and e.sequence>known_head for e in events)
                        status['attack_events']+=sum(e.type==2 and e.sequence>known_head for e in events)
                        status['research_events']+=sum(e.type==6 and e.sequence>known_head for e in events)
                        status['upgrade_events']+=sum(e.type==7 and e.sequence>known_head for e in events)
                        now_ms=c.api.k.GetTickCount64()&0xffffffff
                        for e in events:
                            if e.sequence>known_head:
                                elapsed=(now_ms-e.observed_tick)&0xffffffff
                                stat=status['persist_latency_ms'];stat['samples']+=1;stat['total']+=elapsed
                                stat['max']=max(stat['max'],elapsed)
                    known_head=status['persisted']['last_sequence'];last_fault=fault
                    write_status(a.status_file,status)
                if header['flags']&6 and not a.drain_only:raise ValueError('native stream invalidated; explicit recovery required')
                if a.drain_only and not events:break
            except MailboxBusyError:
                status['busy_polls']+=1
            time.sleep(a.poll_ms/1000)
    except BaseException as exc:
        failure=type(exc).__name__+': '+str(exc);status['error']=failure
    finally:
        if c:
            try:
                if c.peek()['state'] in (1,2):raise ValueError('mailbox in flight; cleanup outcome requires inspection')
                stopped=c.request('capture-stop');status['capture_stop_result']=stopped['result']
                if reader and journal and run and not failure:
                    while True:
                        header,events=reader.read_batch(a.batch_limit)
                        if not events:break
                        journal.append(run,events,overflow=bool(header['flags']&1),lost=header['lost'],ack=reader.acknowledge)
                    status.update(native_status=reader.header(),persisted=asdict(journal.status(run)))
            except BaseException as exc:status['cleanup_error']=type(exc).__name__+': '+str(exc)
            c.close()
        if journal:
            if run and not failure and not status.get('cleanup_error'):
                journal.finish_run(run)
                status['persisted']=asdict(journal.status(run))
            journal.close()
        status.update(running=False,finished_utc=datetime.now(timezone.utc).isoformat())
        write_status(a.status_file,status)
    print(json.dumps(status))
    return 1 if failure or status.get('cleanup_error') else 0

if __name__=='__main__':raise SystemExit(main())
