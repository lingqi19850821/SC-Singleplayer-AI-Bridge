"""Read only previously captured allowed events with an explicit page size."""
from tools.stage4_event_journal import Journal
from .nuclear_observation import nuclear_event

class EventLog:
    def __init__(self,path,run_id,*,encoding,weapon_knowledge=None):
        if not isinstance(encoding,str) or not encoding:raise ValueError('explicit text encoding required')
        self._journal=Journal(path);self._run=run_id;self._encoding=encoding
        self._weapon_knowledge=weapon_knowledge
        self._journal.status(run_id)
    def bind_weapon_knowledge(self,knowledge):
        """Use the player's existing knowledge scope for event/panel joins."""
        self._weapon_knowledge=knowledge
    def close(self):self._journal.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def head(self):
        s=self._journal.status(self._run)
        return {'last_sequence':s.last_sequence,'event_count':s.event_count,
                'incomplete':s.overflow or s.gap_events!=0,'lost_events':s.lost_max}
    def read(self,*,limit,cursor=None):
        page=self._journal.read_events(self._run,limit=limit,cursor=cursor)
        events=[]
        for e in page.events:
            row={'sequence':e.sequence,'observed_tick_ms':e.observed_tick,
                 'received_monotonic_ns':e.received_monotonic_ns}
            if e.type==1:
                row.update(kind='text',text=e.text.decode(self._encoding,errors='strict'))
            elif e.type==2:
                row.update(kind='own_under_attack',minimap_pixel={'x':e.x,'y':e.y})
            elif e.type==9:
                # Only the native onscreen-own-victim gate may produce this
                # kind. Never interpret a legacy generic alert's payload as
                # weapon data, and never expose a raw weapon or attacker id.
                row.update(kind='own_under_attack',feedback='visible_weapon_impact',
                           minimap_pixel={'x':e.x,'y':e.y})
                if not 0<=e.color<130 or not e.text or b'\0' in e.text:
                    raise ValueError('invalid captured weapon feedback')
                name=e.text.decode(self._encoding,errors='strict')
                weapon={'name':name}
                if self._weapon_knowledge is not None:
                    weapon['weapon_token']=self._weapon_knowledge.token('weapon',e.color)
                row['weapon']=weapon
            elif e.type==3:
                row.update(kind='offscreen_enemy_contact',contact=e.color)
                if e.x>=0 and e.y>=0:row['minimap_pixel']={'x':e.x,'y':e.y}
            elif e.type==4:
                row.update(kind='contact_no_longer_current',contact=e.color)
            elif e.type==5:
                row.update(kind='enemy_in_view')
            elif e.type==6:
                row.update(kind='research_completed')
            elif e.type==7:
                row.update(kind='upgrade_completed')
            elif e.type==8:
                row.update(kind={1:'production_completed',2:'building_completed'}.get(e.color,'notification'),
                           camera_target={'x':e.x,'y':e.y})
                if e.color in (1,2):
                    # Current capture copies the completing own entity's name.
                    # Older journals did not capture it; preserve that absence.
                    row['unit_name' if e.color==1 else 'building_name']=e.text.decode(self._encoding,errors='strict') if e.text else None
            elif e.type in (10,11,12):
                row.update(nuclear_event(e.type,color=e.color,x=e.x,y=e.y))
            elif e.type==13:
                # Native capture projects only the actually displayed minimap
                # ping cell to an aligned, clamped camera top-left.  Neither
                # trigger identity nor its undisplayed world location is public.
                if e.color!=0 or e.text or e.x<0 or e.y<0 or e.x%8 or e.y%8:
                    raise ValueError('invalid captured minimap ping')
                row.update(kind='minimap_ping',camera_target={'x':e.x,'y':e.y})
            else:
                # New raw native record types require a separate permission
                # mapping. Unknown fields are never forwarded by default.
                row.update(kind='unsupported_event')
            if e.type in (6,7) and e.color==1:
                # Protocol 18 captures this at the native Space-target write.
                # Legacy color-zero records carry no verified location.
                row['camera_target']={'x':e.x,'y':e.y}
            events.append(row)
        return {**self.head(),'events':events,'next_cursor':page.next_cursor,'has_more':page.has_more}
