"""Learned panel values persist; observed owner grades belong to one run.

Rule profiles are private applicability checks, never a source of new knowledge.
Old unprofiled records are retained but cannot prove cross-map applicability.
"""
import hashlib
import codecs
from contextlib import contextmanager
import json
import secrets
import sqlite3


class PanelKnowledge:
    def __init__(self, scope, path=':memory:'):
        self.scope = str(scope)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS panel_scope(scope TEXT PRIMARY KEY,salt TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS public_reference_v1(
            scope TEXT NOT NULL,token TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(scope,token));
          CREATE TABLE IF NOT EXISTS pending_public_reference_notice_v1(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,identity TEXT NOT NULL UNIQUE,
            scope TEXT NOT NULL,run_ref TEXT NOT NULL,source TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS pending_public_reference_scope_v1
            ON pending_public_reference_notice_v1(scope,run_ref,sequence);
          CREATE TABLE IF NOT EXISTS panel_grade(
            scope TEXT,owner INTEGER,group_id INTEGER,grade INTEGER,
            PRIMARY KEY(scope,owner,group_id));
          CREATE TABLE IF NOT EXISTS panel_value(
            seq INTEGER PRIMARY KEY,scope TEXT,kind INTEGER,channel TEXT,
            grade INTEGER,base INTEGER,bonus INTEGER,
            UNIQUE(scope,kind,channel,grade,base,bonus));
          CREATE TABLE IF NOT EXISTS panel_name(
            scope TEXT,kind INTEGER,name TEXT,PRIMARY KEY(scope,kind));
          CREATE INDEX IF NOT EXISTS panel_name_label_v1 ON panel_name(kind,name);
          CREATE TABLE IF NOT EXISTS panel_skills(
            scope TEXT,kind INTEGER,signature TEXT,PRIMARY KEY(scope,kind));
          CREATE TABLE IF NOT EXISTS learned_panel_v2(
            seq INTEGER PRIMARY KEY,kind INTEGER,channel TEXT,profile TEXT,
            grade INTEGER,base INTEGER,bonus INTEGER,
            UNIQUE(kind,channel,profile,grade,base,bonus));
          CREATE TABLE IF NOT EXISTS observed_panel_rule_v2(
            scope TEXT,kind INTEGER,owner INTEGER,channel TEXT,profile TEXT,
            PRIMARY KEY(scope,kind,owner,channel));
          CREATE TABLE IF NOT EXISTS observed_armor_extra_v2(
            scope TEXT,kind INTEGER,owner INTEGER,extra INTEGER,
            PRIMARY KEY(scope,kind,owner));
          CREATE TABLE IF NOT EXISTS learned_panel_skills_v2(
            kind INTEGER,signature TEXT,PRIMARY KEY(kind,signature));
          CREATE TABLE IF NOT EXISTS learned_panel_skills_v3(
            kind INTEGER,signature TEXT,PRIMARY KEY(kind,signature));
          CREATE TABLE IF NOT EXISTS learned_panel_skill_name_v1(
            kind INTEGER,slot INTEGER,name TEXT,PRIMARY KEY(kind,slot,name));
          CREATE INDEX IF NOT EXISTS learned_panel_skill_label_v1 ON learned_panel_skill_name_v1(kind,name);
          CREATE TABLE IF NOT EXISTS panel_migration_v1(name TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS observed_panel_build_options_v1(
            scope TEXT,kind INTEGER,signature TEXT,PRIMARY KEY(scope,kind));
          CREATE INDEX IF NOT EXISTS observed_panel_build_kind_v1 ON observed_panel_build_options_v1(kind);
          CREATE TABLE IF NOT EXISTS pending_panel_observation_v1(
            receipt INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,sequence INTEGER,payload TEXT,
            UNIQUE(scope,sequence));
          CREATE TABLE IF NOT EXISTS observed_panel_weapon_v1(
            scope TEXT,kind INTEGER,channel TEXT,profile TEXT,weapon INTEGER,name TEXT,
            PRIMARY KEY(scope,kind,channel,profile));
          CREATE TABLE IF NOT EXISTS learned_panel_weapon_v2(
            seq INTEGER PRIMARY KEY,kind INTEGER,channel TEXT,profile TEXT,
            weapon INTEGER,name TEXT,label INTEGER,raw BLOB,encoding TEXT,mapping INTEGER,
            UNIQUE(kind,channel,profile,weapon,name,label,raw,encoding,mapping));
          CREATE INDEX IF NOT EXISTS learned_weapon_prefix_v2 ON learned_panel_weapon_v2
            (kind,channel,profile,mapping,label,raw,encoding,name);
          CREATE INDEX IF NOT EXISTS learned_weapon_encoding_prefix_v2 ON learned_panel_weapon_v2
            (kind,channel,profile,mapping,label,encoding,raw,name);
        ''')
        if self.db.execute('SELECT 1 FROM panel_migration_v1 WHERE name=?',('skill_name_index_v1',)).fetchone() is None:
            with self.db:
                self.db.execute('''INSERT OR IGNORE INTO learned_panel_skill_name_v1
                    SELECT kind,json_extract(value,'$.slot'),json_extract(value,'$.name')
                    FROM learned_panel_skills_v3,json_each(signature)
                    WHERE json_type(value,'$.slot')='integer' AND json_type(value,'$.name')='text' ''')
                self.db.execute('INSERT INTO panel_migration_v1 VALUES(?)',('skill_name_index_v1',))
        self.db.execute('INSERT OR IGNORE INTO panel_scope VALUES(?,?)',
                        (self.scope,secrets.token_hex(16)))
        self.salt=self.db.execute('SELECT salt FROM panel_scope WHERE scope=?',
                                  (self.scope,)).fetchone()[0]
        self._names=dict(self.db.execute('SELECT kind,name FROM panel_name WHERE scope=?',(self.scope,)))
        self._public_reference_cache={}
        self.db.commit()
        from .terrain_reference import TerrainReference
        self.terrain_reference=TerrainReference(self)

    def close(self):
        self.db.close()

    def remember_public_reference(self, token, source, facts, *, name=None, owner_token=None,observation=None,
                                  notice_run_ref=None):
        """Project only already lawful public facts, never import old scopes."""
        if not isinstance(token,str) or not token or not isinstance(facts,dict):
            return None
        if notice_run_ref is not None and (not isinstance(notice_run_ref,str) or not notice_run_ref):
            raise ValueError('nonempty reference notice run required')
        if observation is not None and (not isinstance(observation,dict) or
                set(observation)!={'observed_tick_ms','observed_frame','observed_epoch'} or
                any(type(value) is not int or not 0<=value<2**32 for value in observation.values()) or
                not observation['observed_epoch']):
            raise ValueError('invalid reference observation metadata')
        source=source+':'+str(owner_token or '')
        signature=self.canonical({'facts':facts,'owner_token':owner_token,'name':name})
        cache_key=(self.scope,token,source)
        if self._public_reference_cache.get(cache_key)==signature:return None
        row=self.db.execute('SELECT payload FROM public_reference_v1 WHERE scope=? AND token=?',
                            (self.scope,token)).fetchone()
        payload=json.loads(row[0]) if row else {'token':token,'sources':{}}
        if not facts and not row:
            self._public_reference_cache[cache_key]=signature
            return None
        material={'facts':facts,'owner_token':owner_token}
        old_name=payload.get('name')
        if name:payload['name']=name
        old=payload['sources'].get(source,{})
        if self.canonical(old.get('facts'))==self.canonical(facts) and old.get('owner_token')==owner_token and old_name==payload.get('name'):
            self._public_reference_cache[cache_key]=signature
            return None
        from datetime import datetime,timezone
        if observation is None:
            material['observed_at_utc']=datetime.now(timezone.utc).isoformat()
        else:
            material['recorded_at_utc']=datetime.now(timezone.utc).isoformat()
            material.update(observation)
        payload['sources'][source]=material
        payload['revision']=hashlib.sha256(self.canonical(payload['sources']).encode()).hexdigest()[:16]
        notice={'token':token,'revision':payload['revision']}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO public_reference_v1 VALUES(?,?,?)',
                            (self.scope,token,self.canonical(payload)))
            if notice_run_ref is not None:
                self.db.execute('INSERT INTO pending_public_reference_notice_v1 '
                    '(identity,scope,run_ref,source,payload) VALUES(?,?,?,?,?)',
                    (secrets.token_hex(24),self.scope,notice_run_ref,source,self.canonical(notice)))
        self._public_reference_cache[cache_key]=signature
        return notice

    def pending_reference_notices(self, *, scope=None, run_ref=None, limit=64):
        """Read a page of unhanded notices, retaining their original provenance."""
        if type(limit) is not int or limit<1:
            raise ValueError('positive reference notice page size required')
        query='SELECT identity,scope,run_ref,source,payload FROM pending_public_reference_notice_v1'
        conditions=[];args=[]
        for key,value in (('scope',scope),('run_ref',run_ref)):
            if value is not None:conditions.append(key+'=?');args.append(value)
        if conditions:query+=' WHERE '+' AND '.join(conditions)
        rows=self.db.execute(query+' ORDER BY sequence LIMIT ?',args+[limit]).fetchall()
        return [dict(identity=row[0],scope=row[1],run_ref=row[2],source=row[3],entry=json.loads(row[4]))
                for row in rows]

    def ack_reference_notices(self, identities):
        """Only the durable host outbox may release this source queue."""
        if (not isinstance(identities,(list,tuple)) or
                any(not isinstance(value,str) or not value for value in identities)):
            raise ValueError('reference notice identity list required')
        with self.db:
            self.db.executemany('DELETE FROM pending_public_reference_notice_v1 WHERE identity=?',
                                ((value,) for value in identities))

    def reference_read(self, *, token=None, cursor=None, limit=32):
        if token is not None:
            row=self.db.execute('SELECT payload FROM public_reference_v1 WHERE scope=? AND token=?',
                                (self.scope,token)).fetchone()
            if row is None:return {'status':'not_learned','source':'knowledge','token':token}
            return {'status':'ok','source':'knowledge','applicability':'lawfully_observed_in_current_scope',
                    'live_unit_snapshot':False,'entry':json.loads(row[0])}
        if cursor is not None and not self.db.execute('SELECT 1 FROM public_reference_v1 WHERE scope=? AND token=?',
                                                      (self.scope,cursor)).fetchone():
            return {'status':'rejected','reason':'invalid_reference_cursor','source':'knowledge'}
        rows=self.db.execute('SELECT token,payload FROM public_reference_v1 WHERE scope=? AND token>? '
                             'ORDER BY token LIMIT ?', (self.scope,cursor or '',limit+1)).fetchall()
        entries=[]
        for key,value in rows[:limit]:
            payload=json.loads(value)
            entries.append({key:payload[key] for key in ('token','name','revision') if key in payload})
        return {'status':'ok','source':'knowledge','entries':entries,
                'next_cursor':rows[limit-1][0] if len(rows)>limit else None}

    def token(self, category, value):
        digest=hashlib.sha256(f'{self.salt}:{category}:{value}'.encode()).hexdigest()[:16]
        return f'{category}:{digest}'

    def observe_grade(self, owner, group_id, grade):
        if 0<=owner<12 and 0<=group_id<61:
            self.db.execute('INSERT OR REPLACE INTO panel_grade VALUES(?,?,?,?)',
                            (self.scope,owner,group_id,grade))

    def grade(self, owner, group_id):
        row=self.db.execute('SELECT grade FROM panel_grade WHERE scope=? AND owner=? AND group_id=?',
                             (self.scope,owner,group_id)).fetchone()
        return row[0] if row else None

    def remember_name(self, kind, name):
        self.db.execute('INSERT OR REPLACE INTO panel_name VALUES(?,?,?)',
                        (self.scope,kind,name))
        self._names[kind]=name

    def name(self, kind):
        return self._names.get(kind)

    def known_name(self, kind, current_label):
        """Compare a caller-verified current lawful label; never preload names."""
        return self.db.execute('SELECT 1 FROM panel_name WHERE kind=? AND name=? LIMIT 1',
                               (kind,current_label)).fetchone() is not None

    def has_name_history(self, kind):
        """One indexed existence check, without loading other mission names."""
        return self.db.execute('SELECT 1 FROM panel_name WHERE kind=? LIMIT 1',(kind,)).fetchone() is not None

    @staticmethod
    def canonical(profile):
        return json.dumps(profile,sort_keys=True,separators=(',',':'))

    def bind_profiles(self, kind, owner, profiles):
        for channel,profile in profiles.items():
            self.db.execute('INSERT OR REPLACE INTO observed_panel_rule_v2 VALUES(?,?,?,?,?)',
                (self.scope,kind,owner,channel,self.canonical(profile)))

    def profiles(self, kind, owner):
        return {channel:json.loads(profile) for channel,profile in self.db.execute(
            'SELECT channel,profile FROM observed_panel_rule_v2 WHERE scope=? AND kind=? AND owner=?',
            (self.scope,kind,owner))}

    def observe_armor_extra(self,kind,owner,extra):
        self.db.execute('INSERT OR REPLACE INTO observed_armor_extra_v2 VALUES(?,?,?,?)',
                        (self.scope,kind,owner,extra))

    def armor_extra(self,kind,owner):
        row=self.db.execute('SELECT extra FROM observed_armor_extra_v2 WHERE scope=? AND kind=? AND owner=?',
                            (self.scope,kind,owner)).fetchone()
        return row[0] if row else None

    def _profile(self,profile):
        # Formatter-only callers without a checked rule profile stay run-scoped.
        return self.canonical(profile if profile is not None else {'unverified_run':self.scope})

    def learn(self, kind, channel, value, profile=None):
        self.db.execute('INSERT OR IGNORE INTO learned_panel_v2(kind,channel,profile,grade,base,bonus) VALUES(?,?,?,?,?,?)',
                        (kind,channel,self._profile(profile),value['upgrade'],value['base'],value['bonus']))

    def matches(self, kind, channel, value, profile=None):
        return self.db.execute('SELECT 1 FROM learned_panel_v2 WHERE kind=? AND channel=? AND profile=? AND grade=? AND base=? AND bonus=?',
            (kind,channel,self._profile(profile),value['upgrade'],value['base'],value['bonus'])).fetchone() is not None

    def value(self, kind, channel, grade=None, profile=None):
        if grade is None:return None
        args=[kind,channel,self._profile(profile)]
        query='SELECT base,bonus,grade FROM learned_panel_v2 WHERE kind=? AND channel=? AND profile=?'
        row=None
        if grade is not None:
            row=self.db.execute(query+' AND grade=? ORDER BY seq DESC LIMIT 1',args+[grade]).fetchone()
        if row is None:
            row=self.db.execute(query+' ORDER BY seq DESC LIMIT 1',args).fetchone()
        return dict(zip(('base','bonus','upgrade'),row)) if row else None

    def skills(self, kind, current=None, learn=False):
        signature=self.canonical(current)
        if learn:
            self.db.execute('INSERT OR IGNORE INTO learned_panel_skills_v3 VALUES(?,?)',
                            (kind,signature))
            self.db.executemany('INSERT OR IGNORE INTO learned_panel_skill_name_v1 VALUES(?,?,?)',
                               ((kind,item['slot'],item['name']) for item in current or () if 'name' in item))
            return current,True
        known=self.db.execute('SELECT 1 FROM learned_panel_skills_v3 WHERE kind=? AND signature=?',
                              (kind,signature)).fetchone() is not None
        return (current,True) if known else (None,False)

    def known_skill_name(self,kinds,slot,name):
        """Reuse a legally learned current label; no group/offscreen learning."""
        return any(self.db.execute('SELECT 1 FROM learned_panel_skill_name_v1 '
            'WHERE kind=? AND name=?',(kind,name)).fetchone() is not None for kind in set(kinds))

    def build_options(self,kind,current=None,learn=False):
        """Current mission permission wins; older learned lists remain historical."""
        if learn:
            self.db.execute('INSERT OR REPLACE INTO observed_panel_build_options_v1 VALUES(?,?,?)',
                            (self.scope,kind,self.canonical(current)))
            return current
        row=self.db.execute('SELECT signature FROM observed_panel_build_options_v1 WHERE scope=? AND kind=?',
                            (self.scope,kind)).fetchone()
        if row:return json.loads(row[0])
        row=self.db.execute('SELECT signature FROM observed_panel_build_options_v1 WHERE kind=? '
                            "AND json_extract(signature,'$.status')='complete' ORDER BY rowid DESC LIMIT 1",
                            (kind,)).fetchone()
        if not row:return None
        historical=json.loads(row[0])
        historical['status']='last_observed_other_mission'
        historical['options']=[{**item,'availability':'unverified_in_current_mission'}
                               for item in historical.get('options') or ()]
        return historical

    def queue_observation(self,sequence,payload):
        """Inside the learning transaction, before releasing native storage."""
        self.db.execute('INSERT OR IGNORE INTO pending_panel_observation_v1(scope,sequence,payload) VALUES(?,?,?)',
                        (self.scope,sequence,self.canonical(payload)))

    def pending_observations(self):
        """One natural native completion per handoff; pending reports all work.

        Unacknowledged rows survive worker restart and scope/epoch changes in
        this same player profile. No learned history is bulk loaded or evicted.
        """
        count=self.db.execute('SELECT count(*) FROM pending_panel_observation_v1').fetchone()[0]
        row=self.db.execute('SELECT receipt,payload FROM pending_panel_observation_v1 ORDER BY receipt LIMIT 1').fetchone()
        return {'observations':[json.loads(row[1])] if row else [],
                'receipts':[row[0]] if row else [],'pending':count}

    def ack_observations(self,receipts):
        if not isinstance(receipts,(list,tuple)) or any(type(value) is not int or value<1 for value in receipts):
            raise ValueError('durable observation receipt list required')
        with self.db:
            self.db.executemany('DELETE FROM pending_panel_observation_v1 WHERE receipt=?',((value,) for value in receipts))

    def commit(self):
        self.db.commit()

    @contextmanager
    def observation_scope(self,scope):
        """Bind old completed evidence to its original run in this same store.

        Rule-compatible learned values remain reusable. Owner grades, names,
        tokens and mission build permissions cannot contaminate a newer run.
        """
        if scope==self.scope:
            yield
            return
        previous=(self.scope,self.salt,self._names)
        try:
            self.db.execute('INSERT OR IGNORE INTO panel_scope VALUES(?,?)',(scope,secrets.token_hex(16)))
            self.scope=scope
            self.salt=self.db.execute('SELECT salt FROM panel_scope WHERE scope=?',(scope,)).fetchone()[0]
            self._names=dict(self.db.execute('SELECT kind,name FROM panel_name WHERE scope=?',(scope,)))
            yield
        finally:self.scope,self.salt,self._names=previous

    @contextmanager
    def transaction(self):
        names = self._names.copy()
        try:
            with self.db:
                yield
        except BaseException:
            self._names = names
            raise

    def learn_weapon(self, kind, channel, weapon, name, profile, evidence=None):
        # Only selected detailed observations supply this evidence. Legacy
        # run-scoped rows remain valid in their run but never promote blindly.
        if (channel not in ('ground_attack', 'air_attack') or
                type(weapon) is not int or not 0 <= weapon < 130 or
                not isinstance(name, str) or not name or profile is None or
                profile.get('weapon') != weapon):
            raise ValueError('legal weapon label and matching rule profile required')
        if evidence is not None:
            raw=evidence['raw'];encoding=codecs.lookup(evidence['encoding']).name
            if (not isinstance(raw,bytes) or not raw.endswith(b'\0') or b'\0' in raw[:-1] or
                    bytes(b for b in raw[:-1] if b>=32 or b in (9,10)).decode(encoding,errors='strict')!=name or
                    type(evidence['label']) is not int or not 1<=evidence['label']<=65535 or
                    type(evidence['mapping']) is not int or not 0<=evidence['mapping']<=255):
                raise ValueError('verified loaded weapon label evidence required')
        self.db.execute('INSERT OR REPLACE INTO observed_panel_weapon_v1 VALUES(?,?,?,?,?,?)',
            (self.scope, kind, channel, self.canonical(profile), weapon, name))
        if evidence is not None:
            self.db.execute('INSERT OR IGNORE INTO learned_panel_weapon_v2 '
                '(kind,channel,profile,weapon,name,label,raw,encoding,mapping) VALUES(?,?,?,?,?,?,?,?,?)',
                (kind,channel,self.canonical(profile),weapon,name,evidence['label'],raw,
                 encoding,evidence['mapping']))

    def has_weapon_candidates(self, kind, channel, profile):
        query='SELECT 1 FROM learned_panel_weapon_v2 WHERE kind=? AND channel=? AND profile=?'
        args=[kind,channel,self.canonical(profile)]
        return self.db.execute(query+' LIMIT 1',args).fetchone() is not None

    def weapon_candidate(self, kind, channel, profile, mapping, label, prefix=b'', encoding=None):
        """One indexed lexical seek; history is retained without enumerating it."""
        query=('SELECT weapon,name,label,raw,encoding,mapping FROM learned_panel_weapon_v2 '
               'WHERE kind=? AND channel=? AND profile=? AND mapping=? AND label=?')
        args=[kind,channel,self.canonical(profile),mapping,label]
        if encoding is not None:
            query+=' AND encoding=?';args.append(codecs.lookup(encoding).name)
        if prefix:
            query+=' AND raw>=?';args.append(prefix)
            # BLOB range containing exactly this byte prefix, including ff.
            upper=bytearray(prefix)
            while upper and upper[-1]==255:upper.pop()
            if upper:
                upper[-1]+=1
                query+=' AND raw<?';args.append(bytes(upper))
        row=self.db.execute(query+' ORDER BY raw,encoding,name,seq LIMIT 1',args).fetchone()
        if row is None:return None
        candidate=dict(zip(('weapon','name','label','raw','encoding','mapping'),row))
        if candidate['weapon']!=profile.get('weapon'):
            raise ValueError('stored weapon/profile mismatch')
        candidate.update(kind=kind,channel=channel,profile=self.canonical(profile))
        return candidate

    def weapon(self, kind, channel, profile, reused=None):
        if profile is None:
            return None
        row = self.db.execute('SELECT weapon,name FROM observed_panel_weapon_v1 '
            'WHERE scope=? AND kind=? AND channel=? AND profile=?',
            (self.scope, kind, channel, self.canonical(profile))).fetchone()
        if row is None and reused is not None:
            known=reused.get((kind,channel,self.canonical(profile)))
            if known is not None:row=(known['weapon'],known['name'])
        return {'weapon_token':self.token('weapon', row[0]), 'name':row[1]} if row else None

    def project(self, kind, owner, groups, profiles=None, reused_weapons=None):
        """Only last legally observed grades, never current enemy raw grades."""
        result={'type_token':self.token('type',kind),'owner_token':self.token('owner',owner)}
        name=self.name(kind)
        if name is not None:result['name']=name
        details={}
        allow_unprofiled=profiles is None
        profiles=self.profiles(kind,owner) if profiles is None else profiles
        for channel,group_id in groups.items():
            grade=self.grade(owner,group_id)
            # Zero is the public fallback, never an observed grade or a key
            # authorizing reuse of learned level-zero numerical details.
            result[channel+'_upgrade']=grade if grade is not None else 0
            if channel not in profiles and (profiles or not allow_unprofiled):continue
            value=self.value(kind,channel,grade,profiles.get(channel))
            if value is not None:details[channel]=value
        if details:result['details']=details
        weapons={channel:known for channel,profile in profiles.items()
                 if channel in ('ground_attack','air_attack') and
                 (known:=self.weapon(kind,channel,profile,reused_weapons)) is not None}
        if weapons:result['weapons']=weapons
        return result
