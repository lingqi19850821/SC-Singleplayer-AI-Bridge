"""Private acknowledged observation transport; no engine calls from the worker.

Only the dedicated cumulative acknowledgment field is writable here. Ordinary
actions continue to use the existing mailbox and its shared rate limit.
"""
import struct
import threading
from tools.stage5_client import State

HEADER = struct.Struct('<8I')
SLOT = struct.Struct('<5Iii3I')
MAGIC = 0x314d4253
_consumers_lock = threading.Lock()
_consumers = set()


class BuildingSource:
    def __init__(self, client, *, epoch=None, capacity=3400, refresh_ms=1000, batch_limit=256):
        for value in (capacity, refresh_ms, batch_limit):
            if type(value) is not int or not 1 <= value < 2**31:
                raise ValueError('positive explicit building transport configuration required')
        self.client = client
        initial = client.peek()
        self.epoch = initial['epoch']
        self._width = initial['width_tiles']*32
        self._height = initial['height_tiles']*32
        if epoch is not None and epoch != self.epoch:
            raise RuntimeError('building_capture_session_changed')
        if any(not 1 <= initial.get(k,0) < 2**31 for k in ('view_wait_ms','dynamic_wait_ms')):
            raise RuntimeError('building_capture_attention_unconfigured')
        self._lease = None
        self._owns_lease = False
        self._registered = False
        self._next_ack = None
        self._batch_limit = batch_limit
        key = f'Local\\SCAPI.BuildingMemory.{client.process["pid"]}.{client.process["creation_time_filetime"]}.{client.address}'
        self._key = key
        with _consumers_lock:
            if key in _consumers:
                raise RuntimeError('building_memory_consumer_already_active')
            _consumers.add(key)
            self._registered = True
        try:
            self._lease = client.api.k.CreateMutexW(None, False, key)
            if not self._lease:
                raise client.api.failure('CreateMutexW(building consumer)')
            wait = client.api.k.WaitForSingleObject(self._lease, 0)
            if wait not in (0, 0x80):
                raise RuntimeError('building_memory_consumer_already_active')
            self._owns_lease = True
            result = client.request('building-start', capacity, refresh_ms, self.epoch)
            if result['result'] != 'ok':
                raise RuntimeError('building_capture_start:' + result['result'])
            state = client.peek()
            self._address = state['building_address']
            self._bytes = state['building_bytes']
            self._capacity = state['building_capacity']
            if (not self._address or self._capacity != capacity or
                    self._bytes != HEADER.size + SLOT.size*capacity or
                    state['building_epoch'] != self.epoch or not self._width or not self._height):
                raise RuntimeError('building_capture_descriptor_invalid')
            header = self._header()
            self._next_ack = header[5]
        except BaseException:
            self.close()
            raise

    def _header(self):
        h = HEADER.unpack(self.client.reader.read(self._address, HEADER.size))
        magic, version, epoch, capacity, write, ack, enabled, fault = h
        if (magic != MAGIC or version != 1 or epoch != self.epoch or
                capacity != self._capacity or enabled not in (0, 1) or
                not 1 <= ack <= write+1 or write > 0xfffffffe):
            raise RuntimeError('building_capture_header_invalid_or_changed')
        return h

    def read_batch(self):
        state = self.client.peek()
        if (state['building_address'] != self._address or
                state['building_epoch'] != self.epoch or state['building_bytes'] != self._bytes):
            raise RuntimeError('building_capture_session_changed')
        h = self._header()
        _, _, epoch, capacity, write, ack, enabled, fault = h
        # Our last committed ack may not yet have reached the UI producer.
        # Never reread slots that producer is now entitled to reuse.
        first = max(ack, self._next_ack)
        if write-first+1 > capacity or first > write+1:
            raise RuntimeError('building_capture_sequence_range_invalid')
        records = []
        for seq in range(first, min(write+1, first+self._batch_limit)):
            address = self._address+HEADER.size+((seq-1) % capacity)*SLOT.size
            raw = self.client.reader.read(address, SLOT.size)
            fields = SLOT.unpack(raw)
            confirm = struct.unpack('<I', self.client.reader.read(address, 4))[0]
            commit, identity, kind, owner, visual, x, y, tick, observed, reserved = fields
            if commit != seq or confirm != seq:
                return None
            if (not identity or kind >= 228 or owner >= 12 or observed not in (0,1) or
                    reserved != 0 or not 0 <= x < self._width or not 0 <= y < self._height):
                raise RuntimeError('building_capture_record_invalid')
            records.append({'sequence': seq, 'identity': identity, 'kind': kind,
                            'owner': owner, 'visual': visual, 'x': x, 'y': y,
                            'last_seen_tick_ms': tick, 'currently_observed': bool(observed)})
        return {'epoch': epoch, 'write_seq': write, 'ack_seq': first,
                'enabled': bool(enabled), 'fault': fault, 'records': records,
                'session_changed': state['epoch'] != self.epoch}

    def ack(self, next_sequence):
        h = self._header()
        if (type(next_sequence) is not int or
                not self._next_ack <= next_sequence <= h[4]+1):
            raise ValueError('invalid cumulative building acknowledgment')
        state = self.client.peek()
        if state['building_epoch'] != self.epoch or state['building_address'] != self._address:
            raise RuntimeError('building_ack_session_changed')
        self.client._write(State.building_ack.offset, struct.pack('<I', next_sequence))
        self._next_ack = next_sequence

    def stop(self):
        """Stop only this observation producer, leaving the simulation running."""
        state = self.client.peek()
        if not state['building_enabled']:
            return {'result':'ok','already_disabled':True,'fault':state['building_fault']}
        result = self.client.request('building-stop', 0, 0, self.epoch)
        if result['result'] != 'ok' and self.client.peek()['building_enabled']:
            raise RuntimeError('building_capture_stop:' + result['result'])
        return result

    def close(self):
        if self._lease:
            if self._owns_lease:
                self.client.api.k.ReleaseMutex(self._lease)
            self.client.api.k.CloseHandle(self._lease)
            self._lease = None
            self._owns_lease = False
        if self._registered:
            with _consumers_lock:
                _consumers.discard(self._key)
            self._registered = False
