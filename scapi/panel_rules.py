"""Private compatibility checks against this run's loaded panel rules.

These values never become learned values. Only local-player completed grades
are sampled here; opponent grades must come from a legal selected observation.
"""
import struct


class PanelRules:
    def __init__(self,reader,game_base):
        self.reader=reader
        self.delta=game_base-0x400000

    def read(self,address,count):
        return self.reader.read(address+self.delta,count)

    def byte(self,address):
        return self.read(address,1)[0]

    def word(self,address):
        return struct.unpack('<H',self.read(address,2))[0]

    def shielded(self,kind):
        if not 0<=kind<228:raise RuntimeError('invalid panel type')
        return bool(self.byte(0x6abb68+kind))

    def own_completed_grades(self):
        owner=struct.unpack('<I',self.read(0x4f30c0,4))[0]
        if owner>=12:raise RuntimeError('invalid local player')
        original=self.read(0x50d018+46*owner,46)
        expansion=self.read(0x50f066+15*owner+46,15)
        if struct.unpack('<I',self.read(0x4f30c0,4))[0]!=owner:
            raise RuntimeError('local player changed during grade read')
        return owner,tuple(original+expansion)

    def profiles(self,kind,ground_weapon,air_weapon,armor_extra):
        if not 0<=kind<228:raise RuntimeError('invalid panel type')
        result={}
        if armor_extra is not None:
            result['armor']={'format':1,'base':self.byte(0x6ae500+kind),
                'group':self.byte(0x6b00a0+kind),'increment':1,'extra':armor_extra}
        for channel,weapon in (('ground_attack',ground_weapon),('air_attack',air_weapon)):
            if weapon>=130:continue
            factor=self.byte(0x6b0e28+weapon)
            if kind in (32,10,65,77):factor=2
            if kind==58:factor=1
            result[channel]={'format':1,'weapon':weapon,'group':self.byte(0x6b1988+weapon),
                'base':self.word(0x6b1e30+2*weapon)*factor,
                'increment':self.word(0x6b16e8+2*weapon)*factor}
        return result
