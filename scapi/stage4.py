"""Stage-4 candidate: constrained minimap actions and selected-panel observations.

No full unit database, map triggers, raw addresses or hidden-state fields are
returned. Production attention durations remain subject to human confirmation.
"""
from dataclasses import asdict
from pathlib import Path
import struct
from .actions import Actions, SelectionTicket, action_result
from tools.stage4_client import Client
from .panel_knowledge import PanelKnowledge
from .panel_card import PanelText,TextBudgetRequired,read_own_card,visible_work_progress,visible_ammunition
from .observation_budget import ObservationDeadlineExceeded
from .panel_rules import PanelRules
from .weapon_names import WeaponNames
from .visible_status import visible_panel_status

# Private protocol type, independent of the map's displayed unit name.
LARVA_TYPE=35

def selected_panel_result(reply,knowledge=None,card=None,profiles=None,weapon_names=None,
                          weapon_evidence=None,reused_weapons=None,commit=True):
    if reply['result']!='ok':return action_result(reply)
    s=reply['developer_after']
    if not s['panel_available']:return {'status':'unavailable'}
    temporary=knowledge is None
    knowledge=knowledge or PanelKnowledge('formatter-only')
    kind=s['panel_type'];owner=s['panel_owner']
    panel={'type_token':knowledge.token('type',kind),'owner_token':knowledge.token('owner',owner),
           'owned':bool(s['panel_owned']),
           'armor_upgrade':s['panel_armor_level']}
    if s['panel_rescuable']:panel['rescuable']=True
    if s['panel_has_health']:panel['hp']={'current':s['panel_hp'],'maximum':s['panel_max_hp']}
    for channel in ('ground','air'):
        if s[f'panel_{channel}_weapon']<130:panel[channel+'_attack_upgrade']=s[f'panel_{channel}_level']
    if s['panel_has_shields'] and s['panel_has_health']:
        panel['shields']={'current':s['panel_shields'],'maximum':s['panel_max_shields'],
                          'upgrade':s['panel_shield_level']}
        knowledge.observe_grade(owner,15,s['panel_shield_level'])
    if s['panel_owned'] and s['panel_has_energy']:
        panel['energy']={'current':s['panel_energy'],'maximum':s['panel_max_energy']}
    details={};complete=bool(s['panel_has_health'])
    channels={'armor':{'base':s['panel_armor_base'],'bonus':s['panel_armor_bonus'],
                       'upgrade':s['panel_armor_level']}} if s['panel_has_health'] else {}
    for channel in ('ground','air'):
        if s['panel_has_health'] and s[f'panel_{channel}_weapon']<130:
            channels[channel+'_attack']={'base':s[f'panel_{channel}_base'],
                'bonus':s[f'panel_{channel}_bonus'],'upgrade':s[f'panel_{channel}_level']}
    for channel,value in channels.items():
        native=channel.removesuffix('_attack')
        knowledge.observe_grade(owner,s[f'panel_{native}_upgrade_id'],value['upgrade'])
        if profiles is not None and channel not in profiles:
            complete=False;continue
        profile=profiles.get(channel) if profiles is not None else None
        if s['details_ready']:knowledge.learn(kind,channel,value,profile)
        matches=knowledge.matches(kind,channel,value,profile)
        complete=complete and matches
        known=value if matches else knowledge.value(kind,channel,value['upgrade'],profile)
        if known is not None:details[channel]=known
    if profiles is not None:knowledge.bind_profiles(kind,owner,profiles)
    if details:panel['details']=details
    weapons={}
    if s['panel_has_health'] and profiles is not None:
        for channel in ('ground_attack','air_attack'):
            profile=profiles.get(channel)
            if profile is None:continue
            weapon=s['panel_'+channel.removesuffix('_attack')+'_weapon']
            name=(weapon_names or {}).get(weapon)
            if s['details_ready'] and name is not None:
                evidence=(weapon_evidence or {}).get(channel)
                knowledge.learn_weapon(kind,channel,weapon,name,profile,evidence)
            known=knowledge.weapon(kind,channel,profile,reused_weapons)
            if known is not None:weapons[channel]=known
            # Legacy formatter-only calls retain their numeric-only contract.
            if weapon_names is not None and known is None:complete=False
    if weapons:panel['weapons']=weapons
    if s['panel_owned']:
        if card is None:complete=False
        else:
            panel['basic_commands']=card['basic_commands']
            skills,matched=knowledge.skills(kind,card['skills'],learn=bool(s['details_ready']))
            if skills:panel['skills']=skills
            complete=complete and matched
    if complete:panel['fully_known']=True
    if commit:knowledge.commit()
    if temporary:knowledge.close()
    return {'status':'observed','panel':panel}

class Stage4(Actions):
    client_factory=Client

    def _request(self,op,x,y,epoch,selection=None,**kwargs):
        # A native completion can race the host's before-command flush. This
        # result is emitted BEFORE any command/rate charge; only that definite
        # non-execution permits a resubmission after durable persistence.
        reply=super()._request(op,x,y,epoch,selection,**kwargs)
        if reply and reply['result']=='observation_pending':
            from .automatic_panel import drain_completed
            drain_completed(self,encoding=getattr(self,'_automatic_panel_encoding','cp1252'))
            return super()._request(op,x,y,epoch,selection,**kwargs)
        return reply

    def control_state(self):
        """Current control ownership; subtitles alone never imply a lock."""
        r=self._request('probe',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        if s['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        active=bool(s['active'] and s['tile_pointer'])
        mode=('inactive' if not active else 'script' if s['control_locked'] else
              'paused' if s['pause'] else 'player')
        return {'status':'observed','observed_tick_ms':s['executed_tick'],
                'control':mode,'paused':bool(s['pause']),
                'actions_allowed':bool(mode=='player' and s['enable_actions']==1 and s['hook_installed']==1)}

    def __init__(self,pid,*,knowledge_path=None):
        super().__init__(pid)
        try:
            path=knowledge_path or Path(__file__).resolve().parents[1]/'build/stage4/panel-knowledge.sqlite3'
            self._knowledge=PanelKnowledge(f'{self._pid}:{self._creation}:{self._client.address}:{self._epoch}',path)
            self._rules=PanelRules(self._client.reader,self._client.peek()['game_base'])
            self._weapons=WeaponNames(self._client.reader,self._client.peek()['game_base'])
        except BaseException:
            super().close();raise

    def close(self):
        try:self._knowledge.close()
        finally:super().close()

    def _reused_weapons(self, observed, encoding=None):
        reader=getattr(self,'_weapons',None)
        if reader is None:return {},()
        def candidates():
            seen=set()
            for kind,profiles in observed:
                for channel,profile in profiles.items():
                    if channel not in ('ground_attack','air_attack'):continue
                    key=(kind,channel,self._knowledge.canonical(profile))
                    if key in seen:continue
                    seen.add(key)
                    if (self._knowledge.weapon(kind,channel,profile) is None and
                            self._knowledge.has_weapon_candidates(kind,channel,profile)):
                        yield kind,channel,profile,encoding
        return reader.reuse(candidates(),self._knowledge)

    def observe_view(self,*,unit_budget,text_budget_bytes=65536,encoding='cp1252'):
        """Current verified view items; explicit budget, no silent truncation."""
        if type(unit_budget) is not int or unit_budget<1:raise ValueError('positive unit_budget required')
        if type(text_budget_bytes) is not int or text_budget_bytes<1 or not encoding:
            raise ValueError('positive text budget and encoding required')
        r=self._request('observe-view',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after'];count=s['view_count']
        if s['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        if count>1700:raise RuntimeError('invalid native view length')
        if count>unit_budget:return {'status':'larger_observation_budget_required','required_units':count}
        terrain_extension=getattr(self._client,'protocol_version',0)>=19
        raw=self._client.reader.read(s['view_address'],count*(80 if terrain_extension else 68)) if count else b''
        attachment={}
        attachment_reader=getattr(self,'_view_attachment',None)
        if attachment_reader is not None:
            attachment=attachment_reader(r)
            if attachment.get('status')!='observed':return attachment
            attachment={key:value for key,value in attachment.items() if key!='status'}
        entity_identity=attachment.get('terrain',{}).get('entity_identity')=='scope_salted_entity_token'
        seen_entities=set()
        supply=getattr(self,'_supply',None)
        supply_snapshot=supply.snapshot() if supply is not None else None
        own_grades=self._rules.own_completed_grades()
        classified=[];cache={};reused_names={};name_sources={}
        name_text=PanelText(self._client.reader,text_budget_bytes,encoding)
        for fields in struct.iter_unpack('<Iii13IiiiI' if terrain_extension else '<Iii13Ii',raw):
            visual,x,y,relation,hp,shields,energy,flags,kind,owner,ground,air,armor,gweapon,aweapon,health,extra=fields[:17]
            if entity_identity:
                identity=flags>>5
                lifetime_identity=getattr(self._client,'protocol_version',0)>=25
                if ((not 1<=identity<=0x07ffffff if lifetime_identity else
                     flags&~0x00ffffff or not 1<=identity&2047<=1700) or identity in seen_entities):
                    raise RuntimeError('invalid native visible entity identity')
                seen_entities.add(identity)
                flags&=31
                fields=(*fields[:7],flags,*fields[8:])
            if relation not in (1,2,3,4) or flags&~31:raise RuntimeError('invalid native view item')
            visual_bits=0
            if getattr(self._client,'protocol_version',0)>=27:
                if health&~127 or health&48==48:raise RuntimeError('invalid native visible status bits')
                visual_bits=health&126;health&=1
                fields=(*fields[:15],health,*fields[16:])
            if kind>=228 or owner>=12 or any(g>61 for g in (ground,air,armor)) or health not in (0,1):
                raise RuntimeError('invalid native private classification')
            if (getattr(self._client,'protocol_version',0)>=27 and energy and
                    self._knowledge.name(kind) is None and self._knowledge.has_name_history(kind)):
                if kind in name_sources:
                    if name_sources[kind]!=energy:return {'status':'rejected','reason':'observation_changed_during_read'}
                else:
                    name_sources[kind]=energy  # protocol27 private loaded-label pointer
                    try:candidate=name_text.string(energy)
                    except TextBudgetRequired:return {'status':'larger_text_budget_required'}
                    if self._knowledge.known_name(kind,candidate):reused_names[kind]=candidate
            if terrain_extension:
                tx,ty,domain=fields[17:]
                if domain not in (0,1) or ((tx,ty)!=(-1,-1) and not (0<=tx<s['width_tiles']*32 and 0<=ty<s['height_tiles']*32)):
                    raise RuntimeError('invalid unit terrain sample')
            # Opponent extra armor remains the last legal selected observation.
            extra=extra if relation==1 else self._knowledge.armor_extra(kind,owner)
            key=(kind,gweapon,aweapon,extra)
            if key not in cache:cache[key]=(self._rules.profiles(*key),self._rules.shielded(kind))
            profiles,shielded=cache[key]
            classified.append((fields,profiles if health else {},shielded,visual_bits))
        reused,weapon_stamps=self._reused_weapons((fields[8],profiles) for fields,profiles,_,_ in classified)
        after=self._client.peek()
        same_rules=all(self._rules.profiles(*key)==value[0] and self._rules.shielded(key[0])==value[1]
                       for key,value in cache.items())
        same_weapons=all(self._weapons.unchanged(stamp) for stamp in weapon_stamps)
        final=self._client.peek()
        if (any(after[k]!=s[k] for k in ('epoch','camera_x','camera_y','view_serial','completed_serial')) or
                self._rules.own_completed_grades()!=own_grades or
                not same_rules or not same_weapons or
                any(self._client.reader.read(address,len(raw))!=raw for address,raw in name_text.stamp) or
                any(final[k]!=s[k] for k in ('epoch','camera_x','camera_y','view_serial','completed_serial'))):
            return {'status':'rejected','reason':'observation_changed_during_read'}
        self._sync_own_grades(own_grades)
        for kind,name in reused_names.items():self._knowledge.remember_name(kind,name)
        units=[]
        for index,(fields,profiles,shielded,visual_bits) in enumerate(classified):
            visual,x,y,relation,hp,shields,energy,flags,kind,owner,ground,air,armor,gweapon,aweapon,health,extra=fields[:17]
            item={'appearance_token':f'sprite:{visual}',
                  'relation':{1:'own',2:'allied',3:'enemy',4:'neutral'}[relation]}
            if getattr(self._client,'protocol_version',0)>=27:
                item['visible_states']=[name for mask,name in ((2,'cloaked'),(4,'burrowed'),(8,'ensnared'))
                                        if visual_bits&mask]
                appearance={}
                if visual_bits&32:appearance['construction_stage']='finishing'
                elif visual_bits&16:appearance['construction_stage']='in_progress'
                if visual_bits&64:
                    if not 1<=hp<=5000:raise RuntimeError('invalid native building effect count')
                    appearance['damage_effect_count']=hp
                if appearance:item['building_appearance']=appearance
            if kind!=LARVA_TYPE:item['position']={'x':x,'y':y}
            if entity_identity:
                native_flags=struct.unpack_from('<I',raw,index*(80 if terrain_extension else 68)+28)[0]
                identity=native_flags>>5
                item['entity_token']=self._knowledge.token('entity',
                    f'lifetime:{identity}' if getattr(self._client,'protocol_version',0)>=25 else identity)
            if flags&16:item['rescuable']=True
            if terrain_extension and kind==LARVA_TYPE:
                # Keep the non-location movement fact, but never sample or
                # expose the larva's centre, hit-test point or ground layer.
                item['movement_domain']='air' if fields[19] else 'ground'
            elif terrain_extension:
                from .terrain_semantics import attach_unit_terrain
                item['position_basis']='observed_visible_point'
                item.update(attach_unit_terrain(fields,attachment['terrain']))
                if 'terrain_sample_position' in item:
                    if item['position']!=item['terrain_sample_position']:
                        item['observed_point']=item['position']
                    item['position']=dict(item['terrain_sample_position'])
                    item['position_basis']='unit_center'
            groups={'ground_attack':ground,'air_attack':air,'armor':armor}
            if health and shielded:groups['shields']=15
            item.update(self._knowledge.project(kind,owner,groups,profiles,reused))
            amount=supply.value(kind,supply_snapshot) if supply is not None else None
            if amount is not None and amount>0:item['supply_provided']=amount
            # Precise current values belong to actual selection observations.
            # Screen dwell alone never reveals every unit's health or energy.
            units.append(item)
        self._knowledge.commit()
        return {'status':'observed','observed_tick_ms':s['executed_tick'],'units':units,**attachment}

    def _sync_own_grades(self,snapshot):
        owner,grades=snapshot
        for group,grade in enumerate(grades):self._knowledge.observe_grade(owner,group,grade)

    def _validate_ticket(self,ticket):
        if not isinstance(ticket,SelectionTicket):raise ValueError('selection ticket required')
        if any(type(v) is not int or v<0 for v in asdict(ticket).values()):raise ValueError('invalid ticket')
        if ticket.pid!=self._pid or ticket.process_creation!=self._creation:return False
        if ticket.epoch>=2**32 or ticket.generation>=2**32 or not 1<=ticket.count<=12:raise ValueError('invalid ticket')
        return True

    def minimap_geometry(self):
        r=self._request('probe',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        if s['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        if not s['active'] or not s['minimap_width'] or not s['minimap_height']:
            return {'status':'unavailable'}
        return {'status':'available','coordinate_space':'screen_integer_pixels',
                'map_image':{'left':s['minimap_left'],'top':s['minimap_top'],
                             'width':s['minimap_width'],'height':s['minimap_height']}}

    def move_minimap(self,ticket,pixel_x,pixel_y):
        """One native Move for the real selected group; camera stays in place."""
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        r=self._request('move-minimap',pixel_x,pixel_y,ticket.epoch,ticket.generation)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        return action_result({**r,'operation':'move'})

    def selected_panel(self,ticket,*,learn_details=False,text_budget_bytes=None,encoding=None):
        if type(learn_details) is not bool:raise ValueError('learn_details must be boolean')
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        r=self._request('learn-details' if learn_details else 'observe-selected',0,0,
                        ticket.epoch,ticket.generation)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after'];card=None;name=None;name_status=None;status_text=None
        weapon_names={};weapon_stamp=();weapon_evidence={};weapon_reader=getattr(self,'_weapons',None)
        if not s['panel_available']:return {'status':'unavailable'}
        if text_budget_bytes is not None:
            if type(text_budget_bytes) is not int or text_budget_bytes<1 or not encoding:
                raise ValueError('explicit positive text budget and encoding required')
            text=PanelText(self._client.reader,text_budget_bytes,encoding)
            try:
                if s['panel_name_address']:name=text.string(s['panel_name_address'])
                if s['panel_owned']:card=read_own_card(self._client.reader,s,text)
                status_text=visible_panel_status(self._client.reader,s,text)
                if weapon_reader is not None:
                    weapon_names,weapon_stamp=weapon_reader.read_selected(s,text)
                    by_weapon,by_channel=weapon_reader.selected_evidence(weapon_stamp,encoding)
                    for channel,mapping in by_channel.items():
                        weapon=s['panel_'+channel.removesuffix('_attack')+'_weapon']
                        if weapon in by_weapon:weapon_evidence[channel]={**by_weapon[weapon],**mapping}
            except TextBudgetRequired:name_status='larger_read_budget_required'
            except ObservationDeadlineExceeded:return {'status':'unavailable','reason':'selected_panel_deadline_exceeded'}
            except RuntimeError:return {'status':'rejected','reason':'observation_changed_during_read'}
        own_grades=self._rules.own_completed_grades()
        extra=s['panel_armor_bonus']-s['panel_armor_level']
        profiles=self._rules.profiles(s['panel_type'],s['panel_ground_weapon'],s['panel_air_weapon'],extra)
        reused,weapon_stamps=self._reused_weapons([(s['panel_type'],profiles if s['panel_has_health'] else {})],encoding)
        # The UI can enable a researched button after the first native snapshot
        # but before this external card read. A mailbox peek cannot reconcile
        # that pending UI change. Before learning a new ready skill/cost, sample
        # once more at the native UI site so its visibility clock is current.
        expected_serial=s['completed_serial']
        if card is not None and s['details_ready'] and not self._knowledge.skills(s['panel_type'],card['skills'])[1]:
            confirmation=self._request('observe-selected',0,0,ticket.epoch,ticket.generation)
            if not confirmation:return {'status':'outcome_unknown','automatic_retry':False}
            if confirmation['result']!='ok':return action_result(confirmation)
            confirmed=confirmation['developer_after']
            if (any(confirmed[k]!=s[k] for k in ('epoch','selection_generation','camera_x','camera_y',
                        'details_started_tick','details_active','panel_available','panel_type','panel_owner',
                        'panel_has_health','panel_ground_weapon','panel_air_weapon')) or not confirmed['details_ready']):
                return {'status':'rejected','reason':'observation_changed_during_read'}
            expected_serial=confirmed['completed_serial']
        current=self._client.peek()
        same_own_grades=True
        if s['panel_owned']:
            same_own_grades=s['panel_owner']==own_grades[0]
            for channel in ('armor','ground','air'):
                group=s[f'panel_{channel}_upgrade_id']
                if group<61 and s[f'panel_{channel}_level']!=own_grades[1][group]:same_own_grades=False
        if (any(current[k]!=s[k] for k in ('epoch','selection_generation','camera_x','camera_y',
                    'details_started_tick','details_active','panel_available','panel_type','panel_owner',
                    'panel_has_health','panel_ground_weapon','panel_air_weapon')) or current['completed_serial']!=expected_serial or
                (s['details_ready'] and not current['details_ready']) or not same_own_grades or
                self._rules.own_completed_grades()!=own_grades or
                self._rules.profiles(s['panel_type'],s['panel_ground_weapon'],s['panel_air_weapon'],extra)!=profiles or
                (weapon_reader is not None and (not weapon_reader.unchanged(weapon_stamp) or
                    any(not weapon_reader.unchanged(stamp) for stamp in weapon_stamps)))):
            return {'status':'rejected','reason':'observation_changed_during_read'}
        progress=None;ammunition=None
        if getattr(self._client,'protocol_version',0)>=23:
            try:
                progress=visible_work_progress(self._client.reader,s)
                ammunition=visible_ammunition(self._client.reader,s)
            except ObservationDeadlineExceeded:return {'status':'unavailable','reason':'selected_panel_deadline_exceeded'}
            except RuntimeError:return {'status':'rejected','reason':'observation_changed_during_read'}
        final=self._client.peek()
        if any(final[k]!=current[k] for k in ('epoch','selection_generation','camera_x','camera_y',
                'completed_serial','details_started_tick','details_active','details_ready','panel_available',
                'panel_type','panel_owner','panel_has_health','panel_ground_weapon','panel_air_weapon')):
            return {'status':'rejected','reason':'observation_changed_during_read'}
        with self._knowledge.transaction():
            self._sync_own_grades(own_grades)
            self._knowledge.observe_armor_extra(s['panel_type'],s['panel_owner'],extra)
            result=selected_panel_result(r,self._knowledge,card,profiles,
                weapon_names if weapon_reader is not None else None,weapon_evidence,reused,commit=False)
            if name is not None:
                self._knowledge.remember_name(s['panel_type'],name)
                result['panel']['name']=name
        builds=self._knowledge.build_options(s['panel_type'])
        if s['panel_owned'] and builds is not None:
            result['panel']['build_options_status']=builds['status']
            if builds['options'] is not None:result['panel']['build_options']=builds['options']
        if progress is not None:result['panel']['work_progress']=progress
        if ammunition is not None:result['panel']['ammunition']=ammunition
        if status_text is not None:result['panel']['status_text']=status_text
        if name_status:result['panel']['name_status']=name_status
        return result
