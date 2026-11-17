"""Selected-panel weapon labels from the loaded target 1.05 tooltip tables.

4A3F1E indexes 6B1CA0 by weapon, then 4AA3D0/4D5AB0 resolves
the one-based label through the loaded TBL at 68D344. No unit database.
New strings require a native legal health-visible selected panel and consume
the caller's existing explicit PanelText budget, including their terminator.
Known cross-run labels are compared only for legally health-visible types.
"""
import struct
import codecs
from .panel_card import PanelText


class WeaponNames:
    def __init__(self, reader, game_base):
        self.reader = reader
        self.delta = game_base - 0x400000

    def read_selected(self, state, text):
        if not state['panel_available'] or not state['panel_has_health']:
            return {}, ()
        def u16(address):
            return struct.unpack('<H', self.reader.read(address, 2))[0]
        table = struct.unpack('<I', self.reader.read(0x68d344 + self.delta, 4))[0]
        if not table:
            return {}, ()
        count = u16(table)
        names = {}; stamp = []; strings = []; mappings=[]
        for channel in ('ground', 'air'):
            weapon = state[f'panel_{channel}_weapon']
            if not 0 <= weapon < 130:
                continue
            mapping_address=(0x6ae418 if channel=='ground' else 0x6ada90)+self.delta+state['panel_type']
            mappings.append((channel,mapping_address,self.reader.read(mapping_address,1)[0]))
            label_address = 0x6b1ca0 + self.delta + 2 * weapon
            label = u16(label_address)
            if not 1 <= label <= count:
                continue
            offset_address = table + 2 * label
            offset = u16(offset_address)
            if offset < 2 + 2 * count:
                continue
            # Ground and air frequently share one tooltip; charge it once.
            if weapon not in names:
                reader = self.reader
                parts = []
                class Capture:
                    def read(self, address, count):
                        data = reader.read(address, count)
                        parts.append(data)
                        return data
                captured = PanelText(Capture(), text.remaining, text.encoding)
                try:
                    name = captured.string(table + offset)
                finally:
                    text.remaining = captured.remaining
                raw = b''.join(parts).split(b'\0', 1)[0] + b'\0'
                strings.append((table + offset, raw))
                if name:
                    names[weapon] = name
            stamp.append((label_address, label, offset_address, offset))
        return names, (table, count, tuple(stamp), tuple(strings),tuple(mappings))

    def selected_evidence(self, stamp, encoding):
        if not stamp:return {},{}
        table,_,labels,strings,mappings=stamp
        raw_by_address=dict(strings)
        by_weapon={}
        for address,label,_,offset in labels:
            raw=raw_by_address.get(table+offset)
            if raw is not None:
                weapon=(address-0x6b1ca0-self.delta)//2
                by_weapon[weapon]={'label':label,'raw':raw,'encoding':codecs.lookup(encoding).name}
        result={}
        for channel,_,mapping in mappings:
            # Mapping may be present even for an invalid TBL label entry.
            result[channel+'_attack']={'mapping':mapping}
        return by_weapon,result

    def reuse(self, observed, knowledge):
        """Compare known bytes only; never scan/decode a new loaded string.

        Each SQL seek returns one known candidate for this observed key. On a
        mismatch the first different byte strictly extends the loaded prefix,
        so failed seeks are bounded by known label length, not history count.
        Buffers contain at most one loaded prefix per current tooltip; no
        historical raw/name lists are accumulated. Each added byte is read
        only up to a previously legally learned candidate length.
        """
        accepted={};stamps={};labels={};mappings={};buffers={};table=None
        for kind,channel,profile,encoding in observed:
            weapon=profile['weapon'];key=(kind,channel,knowledge.canonical(profile))
            mapping_address=(0x6ae418 if channel=='ground_attack' else 0x6ada90)+self.delta+kind
            if mapping_address not in mappings:
                mappings[mapping_address]=self.reader.read(mapping_address,1)[0]
            mapping=mappings[mapping_address]
            if table is None:
                table=struct.unpack('<I',self.reader.read(0x68d344+self.delta,4))[0]
                count=struct.unpack('<H',self.reader.read(table,2))[0] if table else 0
            if not table:continue
            if weapon not in labels:
                labels[weapon]=None
                label_address=0x6b1ca0+self.delta+2*weapon
                label=struct.unpack('<H',self.reader.read(label_address,2))[0]
                if not 1<=label<=count:continue
                offset_address=table+2*label
                offset=struct.unpack('<H',self.reader.read(offset_address,2))[0]
                if offset<2+2*count:continue
                labels[weapon]=(label_address,label,offset_address,offset)
            loaded_label=labels[weapon]
            if loaded_label is None:continue
            label_address,label,offset_address,offset=loaded_label
            actual=buffers.setdefault(table+offset,bytearray())
            prefix=b''
            while True:
                row=knowledge.weapon_candidate(kind,channel,profile,mapping,label,prefix,encoding)
                if row is None:break
                raw=row['raw']
                if len(actual)<len(raw):
                    actual.extend(self.reader.read(table+offset+len(actual),len(raw)-len(actual)))
                mismatch=next((index for index in range(len(prefix),len(raw)) if actual[index]!=raw[index]),None)
                if mismatch is not None:
                    prefix=bytes(actual[:mismatch+1])
                    continue
                # Decoding authority is the explicit encoding of the original
                # legal detailed read. View observations have no new encoding.
                name=bytes(b for b in raw[:-1] if b>=32 or b in (9,10)).decode(row['encoding'],errors='strict')
                if name!=row['name']:raise ValueError('stored weapon label/encoding mismatch')
                accepted[key]=row
                identity=(weapon,label,raw)
                stamp=(table,count,(loaded_label,),((table+offset,raw),),())
                grouped=stamps.setdefault(identity,(stamp,{}))
                grouped[1][mapping_address]=(channel,mapping_address,mapping)
                break
        return accepted,tuple((*stamp[:4],tuple(mapping.values())) for stamp,mapping in stamps.values())

    def unchanged(self, stamp):
        if not stamp:
            return True
        table, count, labels, strings, mappings = stamp
        if struct.unpack('<I', self.reader.read(0x68d344 + self.delta, 4))[0] != table:
            return False
        if struct.unpack('<H', self.reader.read(table, 2))[0] != count:
            return False
        for address, label, offset_address, offset in labels:
            if struct.unpack('<H', self.reader.read(address, 2))[0] != label:
                return False
            if struct.unpack('<H', self.reader.read(offset_address, 2))[0] != offset:
                return False
        for address, raw in strings:
            if self.reader.read(address, len(raw)) != raw:
                return False
        for _,address,value in mappings:
            if self.reader.read(address,1)!=bytes((value,)):
                return False
        return True
