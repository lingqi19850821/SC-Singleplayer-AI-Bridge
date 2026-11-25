"""Learned panel values persist; observed owner grades belong to one run.

Rule profiles are private applicability checks, never a source of new knowledge.
Old unprofiled records are retained but cannot prove cross-map applicability.
"""
import hashlib
import codecs
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3

KNOWLEDGE_MODES = frozenset(('official_standard', 'isolated_scope'))
RESOURCE_KINDS = frozenset((176, 177, 178, 188))
CRITTER_KINDS = frozenset((89, 90, 93, 94, 95, 96))


class PanelKnowledge:
    def __init__(self, scope, path=':memory:', *, shared_path=None, knowledge_mode='official_standard'):
        if knowledge_mode not in KNOWLEDGE_MODES:
            raise ValueError('knowledge_mode must be official_standard or isolated_scope')
        self.scope = str(scope)
        self.knowledge_mode = knowledge_mode
        self.shared_path = None
        self._shared_attached = False
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA busy_timeout = 3000')
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
          CREATE TABLE IF NOT EXISTS learned_unit_name_v1(
            kind INTEGER PRIMARY KEY,name TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS learned_unit_fully_known_v1(
            kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS learned_unit_stats_known_v1(
            kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS learned_unit_owned_known_v1(
            kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS learned_unit_detector_v1(
            kind INTEGER PRIMARY KEY CHECK(kind >= 0 AND kind < 228),
            learned_at_utc TEXT NOT NULL);
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
        if shared_path is not None and self.knowledge_mode == 'official_standard':
            shared_str = str(shared_path)
            if shared_str != ':memory:':
                Path(shared_str).parent.mkdir(parents=True, exist_ok=True)
            self.db.execute('ATTACH DATABASE ? AS shared', (shared_str,))
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS shared.learned_panel_v2(
                seq INTEGER PRIMARY KEY,kind INTEGER,channel TEXT,profile TEXT,
                grade INTEGER,base INTEGER,bonus INTEGER,
                UNIQUE(kind,channel,profile,grade,base,bonus));
              CREATE TABLE IF NOT EXISTS shared.learned_panel_skills_v3(
                kind INTEGER,signature TEXT,PRIMARY KEY(kind,signature));
              CREATE TABLE IF NOT EXISTS shared.learned_panel_skill_name_v1(
                kind INTEGER,slot INTEGER,name TEXT,PRIMARY KEY(kind,slot,name));
              CREATE INDEX IF NOT EXISTS shared.learned_panel_skill_label_v1
                ON learned_panel_skill_name_v1(kind,name);
              CREATE TABLE IF NOT EXISTS shared.learned_panel_weapon_v2(
                seq INTEGER PRIMARY KEY,kind INTEGER,channel TEXT,profile TEXT,
                weapon INTEGER,name TEXT,label INTEGER,raw BLOB,encoding TEXT,mapping INTEGER,
                UNIQUE(kind,channel,profile,weapon,name,label,raw,encoding,mapping));
              CREATE INDEX IF NOT EXISTS shared.learned_weapon_prefix_v2
                ON learned_panel_weapon_v2(kind,channel,profile,mapping,label,raw,encoding,name);
              CREATE INDEX IF NOT EXISTS shared.learned_weapon_encoding_prefix_v2
                ON learned_panel_weapon_v2(kind,channel,profile,mapping,label,encoding,raw,name);
              CREATE TABLE IF NOT EXISTS shared.learned_unit_name_v1(
                kind INTEGER,name TEXT,PRIMARY KEY(kind,name));
              CREATE TABLE IF NOT EXISTS shared.learned_unit_fully_known_v1(
                kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shared.learned_unit_stats_known_v1(
                kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shared.learned_unit_owned_known_v1(
                kind INTEGER PRIMARY KEY,marked_at_utc TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shared.learned_unit_detector_v1(
                kind INTEGER PRIMARY KEY CHECK(kind >= 0 AND kind < 228),
                learned_at_utc TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shared.learned_supply_v1(
                kind INTEGER NOT NULL CHECK(kind >= 0 AND kind < 228),
                raw INTEGER NOT NULL CHECK(raw >= 0 AND raw <= 255),
                amount INTEGER NOT NULL CHECK(amount = ((raw + 1) >> 1)),
                PRIMARY KEY(kind,raw)) WITHOUT ROWID;
            ''')
            self.shared_path = shared_str
            self._shared_attached = True
        from .terrain_reference import TerrainReference
        self.terrain_reference=TerrainReference(self)

    def close(self):
        self.db.close()

    @staticmethod
    def _sanitize_card_items(items):
        if not isinstance(items, list):
            return items
        return [{k: v for k, v in item.items() if k != 'enabled'} if isinstance(item, dict) else item
                for item in items]

    @classmethod
    def _merge_card_items(cls, old_items, new_items):
        old_clean = cls._sanitize_card_items(old_items) if isinstance(old_items, list) else []
        new_clean = cls._sanitize_card_items(new_items) if isinstance(new_items, list) else []
        combined = old_clean + new_clean
        if not all(isinstance(x, dict) and 'slot' in x and 'name' in x for x in combined):
            return cls._sanitize_card_items(new_items) if new_items is not None else old_clean
        merged = {}
        for item in old_clean:
            merged[(item['slot'], item['name'])] = dict(item)
        for item in new_clean:
            key = (item['slot'], item['name'])
            if key in merged:
                updated = dict(merged[key])
                updated.update(item)
                merged[key] = updated
            else:
                merged[key] = dict(item)
        return [merged[key] for key in sorted(merged)]

    @classmethod
    def _merge_static_facts(cls, old_facts, new_facts):
        merged = dict(old_facts) if isinstance(old_facts, dict) else {}
        for key, value in new_facts.items():
            if key in ('basic_commands', 'skills'):
                merged[key] = cls._merge_card_items(merged.get(key), value)
            else:
                merged[key] = value
        if 'build_options_status' in new_facts and 'build_options' not in new_facts:
            merged.pop('build_options', None)
        for key in ('basic_commands', 'skills'):
            if key in merged and key not in new_facts:
                merged[key] = cls._merge_card_items(merged[key], None)
        return merged

    def reference_facts(self, token, source, *, owner_token=None):
        """Read current scope's stored static facts for one token/source pair."""
        if not isinstance(token, str) or not token or not isinstance(source, str) or not source:
            return None
        row = self.db.execute('SELECT payload FROM public_reference_v1 WHERE scope=? AND token=?',
                              (self.scope, token)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        entry = payload.get('sources', {}).get(source + ':' + str(owner_token or ''))
        return entry.get('facts') if isinstance(entry, dict) else None

    def remember_public_reference(self, token, source, facts, *, name=None, owner_token=None,observation=None,
                                  notice_run_ref=None, merge_static=False):
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
        signature=self.canonical({'facts':facts,'owner_token':owner_token,'name':name,'merge':bool(merge_static)})
        cache_key=(self.scope,token,source)
        if self._public_reference_cache.get(cache_key)==signature:return None
        row=self.db.execute('SELECT payload FROM public_reference_v1 WHERE scope=? AND token=?',
                            (self.scope,token)).fetchone()
        payload=json.loads(row[0]) if row else {'token':token,'sources':{}}
        if not facts and not row:
            self._public_reference_cache[cache_key]=signature
            return None
        old_name=payload.get('name')
        if name:payload['name']=name
        old=payload['sources'].get(source,{})
        if merge_static:
            if not facts and source not in payload['sources'] and old_name==payload.get('name'):
                self._public_reference_cache[cache_key]=signature
                return None
            effective_facts=self._merge_static_facts(old.get('facts'),facts)
        else:
            effective_facts=facts
        material={'facts':effective_facts,'owner_token':owner_token}
        if self.canonical(old.get('facts'))==self.canonical(effective_facts) and old.get('owner_token')==owner_token and old_name==payload.get('name'):
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
            entry={key:payload[key] for key in ('token','name','revision') if key in payload}
            sources=payload.get('sources')
            if isinstance(sources,dict) and any(
                    isinstance(src,dict) and isinstance(src.get('facts'),dict)
                    and src['facts'].get('fully_known') is True
                    for src in sources.values()):
                entry['fully_known']=True
            entries.append(entry)
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
        self.db.execute('INSERT OR REPLACE INTO main.learned_unit_name_v1 VALUES(?,?)',
                        (kind,name))
        if self._shared_attached:
            self.db.execute('INSERT OR IGNORE INTO shared.learned_unit_name_v1 VALUES(?,?)',
                            (kind,name))
        self._names[kind]=name

    def name(self, kind):
        name=self._names.get(kind)
        if name is not None:
            return name
        # A durable name inherited with a reloaded profile may be projected
        # without a current-scope text read. While any live scope still holds
        # panel_name history, a new scope must revalidate the loaded label, so
        # the durable row must never contaminate that newer run.
        if self.db.execute('SELECT 1 FROM panel_name WHERE kind=? LIMIT 1',
                           (kind,)).fetchone() is not None:
            return None
        row=self.db.execute('SELECT name FROM main.learned_unit_name_v1 WHERE kind=?',
                            (kind,)).fetchone()
        return row[0] if row else None

    def mark_stats_known(self, kind, marked_at_utc=None):
        """Record completed numeric stats for any observed relation."""
        if marked_at_utc is None:
            from datetime import datetime, timezone
            marked_at_utc=datetime.now(timezone.utc).isoformat()
        self.db.execute('INSERT OR REPLACE INTO main.learned_unit_stats_known_v1 VALUES(?,?)',
                        (kind,marked_at_utc))
        if self._shared_attached:
            self.db.execute('INSERT OR REPLACE INTO shared.learned_unit_stats_known_v1 VALUES(?,?)',
                            (kind,marked_at_utc))

    def mark_owned_known(self, kind, marked_at_utc=None):
        """Record a completed owned-card observation and its legacy full marker."""
        if marked_at_utc is None:
            from datetime import datetime, timezone
            marked_at_utc=datetime.now(timezone.utc).isoformat()
        self.db.execute('INSERT OR REPLACE INTO main.learned_unit_owned_known_v1 VALUES(?,?)',
                        (kind,marked_at_utc))
        self.db.execute('INSERT OR REPLACE INTO main.learned_unit_fully_known_v1 VALUES(?,?)',
                        (kind,marked_at_utc))
        if self._shared_attached:
            self.db.execute('INSERT OR REPLACE INTO shared.learned_unit_owned_known_v1 VALUES(?,?)',
                            (kind,marked_at_utc))
            self.db.execute('INSERT OR REPLACE INTO shared.learned_unit_fully_known_v1 VALUES(?,?)',
                            (kind,marked_at_utc))

    def mark_fully_known(self, kind, marked_at_utc=None):
        """Legacy callers stay consistent with the split durable markers."""
        if marked_at_utc is None:
            from datetime import datetime, timezone
            marked_at_utc=datetime.now(timezone.utc).isoformat()
        self.mark_stats_known(kind, marked_at_utc)
        self.mark_owned_known(kind, marked_at_utc)

    def mark_detector(self, kind, *, commit=True):
        """Record a legally visible selected-panel ``Detector`` status label."""
        if (isinstance(kind,bool) or not isinstance(kind,int) or
                not 0<=kind<228):
            raise ValueError('native type required')
        from datetime import datetime, timezone
        learned_at_utc=datetime.now(timezone.utc).isoformat()
        self.db.execute('INSERT OR IGNORE INTO main.learned_unit_detector_v1'
                        '(kind,learned_at_utc) VALUES(?,?)',(kind,learned_at_utc))
        if self._shared_attached:
            self.db.execute('INSERT OR IGNORE INTO shared.learned_unit_detector_v1'
                            '(kind,learned_at_utc) VALUES(?,?)',(kind,learned_at_utc))
        if commit:self.db.commit()

    def detector(self, kind):
        """True only when observed; unknown stays None, never false."""
        for schema in self._learning_schemas():
            if self._has_kind_row(schema,'learned_unit_detector_v1',kind):
                return True
        return None

    def _has_kind_row(self, schema, table, kind):
        if not self._learning_table_exists(schema, table):
            return False
        return self.db.execute(f'SELECT 1 FROM {schema}.{table} WHERE kind=? LIMIT 1',
                               (kind,)).fetchone() is not None

    def is_stats_known(self, kind):
        """Stats are complete for any relation; legacy full markers still count."""
        for schema in self._learning_schemas():
            if (self._has_kind_row(schema, 'learned_unit_stats_known_v1', kind) or
                    self._has_kind_row(schema, 'learned_unit_fully_known_v1', kind)):
                return True
        return False

    def is_owned_card_known(self, kind):
        """Owned-card truth; legacy rows require skills proof to avoid non-owned."""
        schemas=self._learning_schemas()
        if any(self._has_kind_row(schema, 'learned_unit_owned_known_v1', kind)
               for schema in schemas):
            return True
        if not any(self._has_kind_row(schema, 'learned_unit_fully_known_v1', kind)
                   for schema in schemas):
            return False
        return any(self._has_kind_row(schema, 'learned_panel_skills_v3', kind)
                   for schema in schemas)

    def has_historical_nonempty_build_options(self, kind):
        return self.db.execute(
            "SELECT 1 FROM observed_panel_build_options_v1 WHERE kind=? "
            "AND json_extract(signature,'$.status')='complete' "
            "AND json_array_length(json_extract(signature,'$.options'))>0 LIMIT 1",
            (kind,)).fetchone() is not None

    def has_current_scope_build_options(self, kind):
        return self.db.execute(
            "SELECT 1 FROM observed_panel_build_options_v1 WHERE scope=? AND kind=? "
            "AND json_extract(signature,'$.status')='complete' LIMIT 1",
            (self.scope,kind)).fetchone() is not None

    def needs_learning(self, kind, *, owned, has_health=True, incomplete_building=False):
        """Single sparse truth for an unlearned public row; never preloads."""
        if incomplete_building or not has_health:
            return False
        if kind in RESOURCE_KINDS or kind in CRITTER_KINDS:
            return False
        if not self.is_stats_known(kind):
            return True
        if not owned:
            return False
        if not self.is_owned_card_known(kind):
            return True
        if (self.has_historical_nonempty_build_options(kind) and
                not self.has_current_scope_build_options(kind)):
            return True
        return False

    _DURABLE_LEARNING_TABLES=('learned_unit_name_v1','learned_panel_v2',
        'learned_panel_weapon_v2','learned_panel_skills_v3',
        'learned_panel_skill_name_v1','learned_supply_v1',
        'learned_unit_stats_known_v1','learned_unit_owned_known_v1',
        'learned_unit_fully_known_v1','learned_unit_detector_v1')

    def kind_for_token(self, token):
        """Resolve one opaque type token by the private salted token space."""
        if not isinstance(token,str) or not token.startswith('type:'):
            return None
        for kind in range(228):
            if self.token('type',kind)==token:
                return kind
        return None

    def _learning_schemas(self):
        return ('main','shared') if self._shared_attached else ('main',)

    def _learning_table_exists(self, schema, name):
        return self.db.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None

    def _durable_kinds(self):
        kinds=set()
        for schema in self._learning_schemas():
            for table in self._DURABLE_LEARNING_TABLES:
                if not self._learning_table_exists(schema,table):
                    continue
                kinds.update(kind for (kind,) in self.db.execute(
                    f'SELECT DISTINCT kind FROM {schema}.{table}') if type(kind) is int)
        return kinds

    def _catalog_name(self, kind):
        """Host-side label preference; shared names are never the display name."""
        name=self._names.get(kind)
        if name is None:
            row=self.db.execute('SELECT name FROM main.learned_unit_name_v1 WHERE kind=?',
                                (kind,)).fetchone()
            if row is not None:
                name=row[0]
        if name is None and self._shared_attached:
            row=self.db.execute('SELECT name FROM shared.learned_unit_name_v1 '
                                'WHERE kind=? ORDER BY name LIMIT 1',(kind,)).fetchone()
            if row is not None:
                name=row[0]
        return name

    def _fully_known_marker(self, kind):
        for schema in self._learning_schemas():
            for table in ('learned_unit_owned_known_v1', 'learned_unit_stats_known_v1',
                          'learned_unit_fully_known_v1'):
                if not self._learning_table_exists(schema, table):
                    continue
                row=self.db.execute(f'SELECT marked_at_utc FROM {schema}.{table} '
                                    'WHERE kind=?',(kind,)).fetchone()
                if row is not None:
                    return row[0]
        return None

    def _learned_channels(self, kind):
        channels={}
        for schema in self._learning_schemas():
            if not self._learning_table_exists(schema,'learned_panel_v2'):
                continue
            for channel,base,bonus,grade in self.db.execute(
                    f'SELECT channel,base,bonus,grade FROM {schema}.learned_panel_v2 '
                    'WHERE kind=? ORDER BY seq DESC',(kind,)):
                if isinstance(channel,str) and channel not in channels:
                    channels[channel]={'base':base,'bonus':bonus,'grade':grade}
        return {channel:channels[channel] for channel in sorted(channels)}

    def _learned_weapons(self, kind):
        weapons={}
        for schema in self._learning_schemas():
            if not self._learning_table_exists(schema,'learned_panel_weapon_v2'):
                continue
            for channel,name in self.db.execute(
                    f'SELECT channel,name FROM {schema}.learned_panel_weapon_v2 '
                    'WHERE kind=? ORDER BY seq DESC',(kind,)):
                if (isinstance(channel,str) and isinstance(name,str) and
                        channel not in weapons):
                    weapons[channel]=name
        return {channel:weapons[channel] for channel in sorted(weapons)}

    def _learned_weapon_names(self, kind):
        names=set()
        for schema in self._learning_schemas():
            if not self._learning_table_exists(schema,'learned_panel_weapon_v2'):
                continue
            names.update(name for (name,) in self.db.execute(
                f'SELECT name FROM {schema}.learned_panel_weapon_v2 WHERE kind=?',
                (kind,)) if isinstance(name,str) and name)
        return names

    def _skills_exact(self, kind):
        for schema in self._learning_schemas():
            if (self._learning_table_exists(schema,'learned_panel_skills_v3') and
                    self.db.execute(f'SELECT 1 FROM {schema}.learned_panel_skills_v3 '
                                    'WHERE kind=? LIMIT 1',(kind,)).fetchone() is not None):
                return True
        return False

    def _skill_names(self, kind):
        names=set()
        for schema in self._learning_schemas():
            if self._learning_table_exists(schema,'learned_panel_skills_v3'):
                for (signature,) in self.db.execute(
                        f'SELECT signature FROM {schema}.learned_panel_skills_v3 WHERE kind=?',
                        (kind,)):
                    try:
                        items=json.loads(signature)
                    except (TypeError,ValueError):
                        continue
                    if not isinstance(items,list):
                        continue
                    for item in items:
                        name=item.get('name') if isinstance(item,dict) else None
                        if isinstance(name,str) and name:
                            names.add(name)
            if self._learning_table_exists(schema,'learned_panel_skill_name_v1'):
                names.update(name for (name,) in self.db.execute(
                    f'SELECT name FROM {schema}.learned_panel_skill_name_v1 WHERE kind=?',
                    (kind,)) if isinstance(name,str) and name)
        return sorted(names)

    def _supply_provided(self, kind):
        for schema in self._learning_schemas():
            if not self._learning_table_exists(schema,'learned_supply_v1'):
                continue
            # A durable profile may hold several map-specific raw facts for one
            # kind; the deterministic last-raw amount is a summary, not a claim
            # about the current mission's loaded rule.
            row=self.db.execute(
                f'SELECT amount FROM {schema}.learned_supply_v1 WHERE kind=? '
                'ORDER BY raw DESC LIMIT 1',(kind,)).fetchone()
            if row is not None:
                return row[0]
        return None

    def _learned_summary(self, kind):
        summary={'channels':sorted(self._learned_channels(kind)),
                 'weapons':sorted(self._learned_weapon_names(kind)),
                 'skills_exact':self._skills_exact(kind),
                 'skill_names':self._skill_names(kind),
                 'supply_provided':self._supply_provided(kind)}
        if self.detector(kind) is True:summary['detector']=True
        return summary

    def client_learning_catalog(self):
        """Private host/GUI catalog; never part of a model-facing operation."""
        entries=[]
        for kind in self._durable_kinds():
            full=self._fully_known_marker(kind) is not None
            entry={'token':self.token('type',kind),'name':self._catalog_name(kind),
                   'learning_status':'fully_known' if full else 'partial_learned',
                   'durable':True,'learned_summary':self._learned_summary(kind)}
            if full:
                entry['fully_known']=True
            entries.append(entry)
        entries.sort(key=lambda row:(row['name'] is None,
                                     (row['name'] or '').casefold(),row['token']))
        return {'status':'ok','entries':entries}

    def client_learning_detail(self, token):
        """Private host/GUI detail; raw kind/profile/weapon bytes never leave."""
        kind=self.kind_for_token(token)
        if kind is None or kind not in self._durable_kinds():
            return {'status':'not_learned','token':token}
        marked=self._fully_known_marker(kind)
        full=marked is not None
        learned={'channels':self._learned_channels(kind),
                 'weapons':self._learned_weapons(kind),
                 'skills_exact':self._skills_exact(kind),
                 'skill_names':self._skill_names(kind),
                 'supply_provided':self._supply_provided(kind),
                 'marked_fully_known_at_utc':marked}
        if self.detector(kind) is True:learned['detector']=True
        detail={'status':'ok','token':token,'name':self._catalog_name(kind),
                'learning_status':'fully_known' if full else 'partial_learned',
                'durable':True,'learned':learned}
        if full:
            detail['fully_known']=True
        return detail

    def known_name(self, kind, current_label):
        """Compare a caller-verified current lawful label; never preload names."""
        if self.db.execute('SELECT 1 FROM panel_name WHERE kind=? AND name=? LIMIT 1',
                           (kind,current_label)).fetchone() is not None:
            return True
        if self._shared_attached:
            return self.db.execute('SELECT 1 FROM shared.learned_unit_name_v1 WHERE kind=? AND name=? LIMIT 1',
                                   (kind,current_label)).fetchone() is not None
        return False

    def has_name_history(self, kind):
        """One indexed existence check, without loading other mission names."""
        if self.db.execute('SELECT 1 FROM panel_name WHERE kind=? LIMIT 1',(kind,)).fetchone() is not None:
            return True
        if self._shared_attached:
            return self.db.execute('SELECT 1 FROM shared.learned_unit_name_v1 WHERE kind=? LIMIT 1',
                                   (kind,)).fetchone() is not None
        return False

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
        prof = self._profile(profile)
        self.db.execute('INSERT OR IGNORE INTO learned_panel_v2(kind,channel,profile,grade,base,bonus) VALUES(?,?,?,?,?,?)',
                        (kind,channel,prof,value['upgrade'],value['base'],value['bonus']))
        if self._shared_attached and profile is not None:
            self.db.execute('INSERT OR IGNORE INTO shared.learned_panel_v2(kind,channel,profile,grade,base,bonus) VALUES(?,?,?,?,?,?)',
                            (kind,channel,prof,value['upgrade'],value['base'],value['bonus']))

    def matches(self, kind, channel, value, profile=None):
        prof = self._profile(profile)
        args = (kind,channel,prof,value['upgrade'],value['base'],value['bonus'])
        if self.db.execute('SELECT 1 FROM learned_panel_v2 WHERE kind=? AND channel=? AND profile=? AND grade=? AND base=? AND bonus=?',
                           args).fetchone() is not None:
            return True
        if self._shared_attached and profile is not None:
            return self.db.execute('SELECT 1 FROM shared.learned_panel_v2 WHERE kind=? AND channel=? AND profile=? AND grade=? AND base=? AND bonus=?',
                                   args).fetchone() is not None
        return False

    def value(self, kind, channel, grade=None, profile=None):
        if grade is None:return None
        prof = self._profile(profile)
        args=[kind,channel,prof]
        schemas = ('main', 'shared') if (self._shared_attached and profile is not None) else ('main',)
        for schema in schemas:
            row=self.db.execute(f'SELECT base,bonus,grade FROM {schema}.learned_panel_v2 '
                                'WHERE kind=? AND channel=? AND profile=? AND grade=? ORDER BY seq DESC LIMIT 1',
                                args+[grade]).fetchone()
            if row is not None:
                return dict(zip(('base','bonus','upgrade'),row))
        for schema in schemas:
            row=self.db.execute(f'SELECT base,bonus,grade FROM {schema}.learned_panel_v2 '
                                'WHERE kind=? AND channel=? AND profile=? ORDER BY seq DESC LIMIT 1',
                                args).fetchone()
            if row is not None:
                return dict(zip(('base','bonus','upgrade'),row))
        return None

    def skills(self, kind, current=None, learn=False):
        signature=self.canonical(current)
        if learn:
            self.db.execute('INSERT OR IGNORE INTO learned_panel_skills_v3 VALUES(?,?)',
                            (kind,signature))
            names = [(kind,item['slot'],item['name']) for item in current or () if 'name' in item]
            self.db.executemany('INSERT OR IGNORE INTO learned_panel_skill_name_v1 VALUES(?,?,?)', names)
            if self._shared_attached:
                self.db.execute('INSERT OR IGNORE INTO shared.learned_panel_skills_v3 VALUES(?,?)',
                                (kind,signature))
                self.db.executemany('INSERT OR IGNORE INTO shared.learned_panel_skill_name_v1 VALUES(?,?,?)', names)
            return current,True
        known=self.db.execute('SELECT 1 FROM learned_panel_skills_v3 WHERE kind=? AND signature=?',
                              (kind,signature)).fetchone() is not None
        if not known and self._shared_attached:
            known=self.db.execute('SELECT 1 FROM shared.learned_panel_skills_v3 WHERE kind=? AND signature=?',
                                  (kind,signature)).fetchone() is not None
        return (current,True) if known else (None,False)

    def learn_skill_names(self,kind,skills):
        """Learn visible group-card labels without an exact single-unit card signature.

        A multi-selection exposes only the shared 3x3 card, never a single unit's
        complete skill set. Recording those labels in ``learned_panel_skill_name_v1``
        lets the current card show a lawfully observed label again, but deliberately
        does not write ``learned_panel_skills_v3``: a group-only card must never
        become an exact-signature match for an individual unit.

        Like :meth:`skills`, this method leaves the commit to its caller. It returns
        the ``(slot, name)`` pairs actually inserted, in input order, including a
        pair newly inserted only into the attached shared store.
        """
        if type(kind) is not int:
            raise ValueError('integer native type required')
        try:
            items=list(skills)
        except TypeError:
            raise ValueError('iterable skill items required') from None
        pairs=[];seen=set()
        for item in items:
            if not isinstance(item,dict):
                raise ValueError('skill item mapping required')
            slot=item.get('slot');name=item.get('name')
            if type(slot) is not int or not isinstance(name,str):
                raise ValueError('integer slot and visible text label required')
            if not name:continue  # An empty label is unknown, never learned.
            pair=(slot,name)
            if pair not in seen:
                seen.add(pair);pairs.append(pair)
        inserted=[]
        for slot,name in pairs:
            main=self.db.execute('INSERT OR IGNORE INTO learned_panel_skill_name_v1 VALUES(?,?,?)',
                                 (kind,slot,name))
            new=main.rowcount>0
            if self._shared_attached:
                shared=self.db.execute(
                    'INSERT OR IGNORE INTO shared.learned_panel_skill_name_v1 VALUES(?,?,?)',
                    (kind,slot,name))
                new=new or shared.rowcount>0
            if new:inserted.append((slot,name))
        return inserted

    def known_skill_name(self,kinds,slot,name):
        """Reuse a legally learned current label; no group/offscreen learning."""
        kind_set = set(kinds)
        if any(self.db.execute('SELECT 1 FROM learned_panel_skill_name_v1 '
               'WHERE kind=? AND name=?',(kind,name)).fetchone() is not None for kind in kind_set):
            return True
        if self._shared_attached:
            return any(self.db.execute('SELECT 1 FROM shared.learned_panel_skill_name_v1 '
                       'WHERE kind=? AND name=?',(kind,name)).fetchone() is not None for kind in kind_set)
        return False

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
            if self._shared_attached:
                self.db.execute('INSERT OR IGNORE INTO shared.learned_panel_weapon_v2 '
                    '(kind,channel,profile,weapon,name,label,raw,encoding,mapping) VALUES(?,?,?,?,?,?,?,?,?)',
                    (kind,channel,self.canonical(profile),weapon,name,evidence['label'],raw,
                     encoding,evidence['mapping']))

    def has_weapon_candidates(self, kind, channel, profile):
        args=[kind,channel,self.canonical(profile)]
        if self.db.execute('SELECT 1 FROM learned_panel_weapon_v2 WHERE kind=? AND channel=? AND profile=? LIMIT 1',
                           args).fetchone() is not None:
            return True
        if self._shared_attached:
            return self.db.execute('SELECT 1 FROM shared.learned_panel_weapon_v2 WHERE kind=? AND channel=? AND profile=? LIMIT 1',
                                   args).fetchone() is not None
        return False

    def _weapon_candidate_in(self, schema, kind, channel, profile, profile_json, mapping, label, prefix, encoding):
        query=(f'SELECT weapon,name,label,raw,encoding,mapping FROM {schema}.learned_panel_weapon_v2 '
               'WHERE kind=? AND channel=? AND profile=? AND mapping=? AND label=?')
        args=[kind,channel,profile_json,mapping,label]
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
        candidate.update(kind=kind,channel=channel,profile=profile_json)
        return candidate

    def weapon_candidate(self, kind, channel, profile, mapping, label, prefix=b'', encoding=None):
        """One indexed lexical seek; history is retained without enumerating it."""
        profile_json=self.canonical(profile)
        cand=self._weapon_candidate_in('main',kind,channel,profile,profile_json,mapping,label,prefix,encoding)
        if self._shared_attached:
            shared_cand=self._weapon_candidate_in('shared',kind,channel,profile,profile_json,mapping,label,prefix,encoding)
            if shared_cand is not None and (
                    cand is None or (shared_cand['raw'],shared_cand['encoding'],shared_cand['name']) <
                    (cand['raw'],cand['encoding'],cand['name'])):
                cand=shared_cand
        return cand

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
