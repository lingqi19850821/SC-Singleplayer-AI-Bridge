"""Curated original-game Tips gated only by legally acquired public names.

Unit gates store only Tip eligibility and source provenance, not another unit
Catalog. Internal PanelKnowledge names do not prove delivery to the player.
No game/process reads, parsing Tip text into knowledge, or numerical grants.
"""
import json
from pathlib import Path

from .experience import _bytes, _connect, _now, _positive, _text


class TipsRuntime:
    def __init__(self, store, *, profile_id, panel_scope=None):
        self.profile = _text(profile_id, 'profile_id')
        self.db, self._owns = _connect(store)
        scope = getattr(store, 'scope', panel_scope)
        self._tips = json.loads(Path(__file__).with_name('tips_runtime.json').read_text(encoding='utf-8'))['items']
        for statement in (
            '''CREATE TABLE IF NOT EXISTS tips_reference_v1(tip_id TEXT PRIMARY KEY,payload TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS tips_panel_scope_v1(
               profile TEXT NOT NULL, scope TEXT NOT NULL, PRIMARY KEY(profile,scope))''',
            '''CREATE TABLE IF NOT EXISTS tips_button_name_v1(
               profile TEXT NOT NULL, name TEXT NOT NULL, source_ref TEXT NOT NULL,
               observed_at_utc TEXT NOT NULL, PRIMARY KEY(profile,name))''',
            '''CREATE TABLE IF NOT EXISTS tips_delivery_v1(
               profile TEXT NOT NULL, tip_id TEXT NOT NULL, offered_at_utc TEXT NOT NULL,
               delivered_at_utc TEXT, PRIMARY KEY(profile,tip_id))''',
            '''CREATE TABLE IF NOT EXISTS tips_public_gate_v1(
               profile TEXT NOT NULL, tip_id TEXT NOT NULL, source_ref TEXT NOT NULL,
               observed_at_utc TEXT NOT NULL, PRIMARY KEY(profile,tip_id))''',
        ):
            self.db.execute(statement)
        self.db.executemany('INSERT OR REPLACE INTO tips_reference_v1 VALUES(?,?)',
                           [(tip['id'],json.dumps(tip,ensure_ascii=False)) for tip in self._tips])
        self.db.commit()
        if scope is not None:
            self.bind_panel_scope(scope)

    def close(self):
        if self._owns:
            self.db.close()

    def reference_read(self, *, tip_id=None, cursor=None, limit=32):
        """Only Tips already lawfully offered to this profile are readable."""
        if tip_id is not None:
            row=self.db.execute('SELECT r.payload FROM tips_reference_v1 r JOIN tips_delivery_v1 d '
                'ON d.tip_id=r.tip_id WHERE d.profile=? AND r.tip_id=?',(self.profile,tip_id)).fetchone()
            return ({'status':'ok','source':'tips','entry':json.loads(row[0])} if row else
                    {'status':'not_learned','source':'tips','tip_id':tip_id})
        if cursor is not None and not self.db.execute('SELECT 1 FROM tips_delivery_v1 WHERE profile=? AND tip_id=?',
                                                     (self.profile,cursor)).fetchone():
            return {'status':'rejected','reason':'invalid_reference_cursor','source':'tips'}
        rows=self.db.execute('SELECT tip_id FROM tips_delivery_v1 WHERE profile=? AND tip_id>? ORDER BY tip_id LIMIT ?',
                             (self.profile,cursor or '',limit+1)).fetchall()
        return {'status':'ok','source':'tips','entries':[{'tip_id':row[0]} for row in rows[:limit]],
                'next_cursor':rows[limit-1][0] if len(rows)>limit else None}

    def bind_panel_scope(self, scope):
        """Record scope provenance only; internal panel names grant no Tip gates."""
        scope = _text(scope, 'panel_scope')
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='panel_name'").fetchone()
        if not exists:
            raise ValueError('panel_name table required: use the existing PanelKnowledge SQLite')
        self.db.execute('INSERT OR IGNORE INTO tips_panel_scope_v1 VALUES(?,?)',
                        (self.profile, scope))
        self.db.commit()

    def observe_button_names(self, names, *, source_ref):
        """Trusted host only, after the final public response has been flushed.

        Pass eligible enabled labels actually delivered by command_card. This is
        not a player tool accepting arbitrary assertions. Unknown labels are not
        stored and no index or additional label is returned.
        """
        _text(source_ref, 'source_ref')
        if not isinstance(names, list):
            raise ValueError('names must be a list of legal public button labels')
        for name in names:
            _text(name, 'button name')
        allowed = {name.casefold() for tip in self._tips for name in tip['button_names']}
        recognized = {name.casefold() for name in names if name.casefold() in allowed}
        self.db.executemany('INSERT OR IGNORE INTO tips_button_name_v1 VALUES(?,?,?,?)',
            [(self.profile, name, source_ref, _now()) for name in recognized])
        self.db.commit()

    def _known_names(self):
        # A public operation can populate panel_name internally and subsequently
        # return only a subset, or be rejected by its final consistency guard.
        # Scope membership therefore cannot establish player-delivered names.
        return {row[0] for row in self.db.execute(
            'SELECT name FROM tips_button_name_v1 WHERE profile=?', (self.profile,))}

    def observe_public_names(self, names, *, source_ref):
        """Trusted host only, after flushing the final successful public result.

        Extract only actual public name fields, including completion names with
        no public type ID; keep resulting Tip gate + provenance, not another unit
        catalog. Never use internal panel_name, Tip text, event free text, failed
        responses, intermediate reads or inferred labels as ingestion sources.
        """
        _text(source_ref, 'source_ref')
        if not isinstance(names, list):
            raise ValueError('names must be a list of legal public names')
        known = {_text(name, 'public name').casefold() for name in names}
        unlocked = [tip['id'] for tip in self._tips if tip['gate'] == 'any_known_name'
                    and any(name.casefold() in known for name in tip['unit_names'] + tip['button_names'])]
        self.db.executemany('INSERT OR IGNORE INTO tips_public_gate_v1 VALUES(?,?,?,?)',
            [(self.profile, tip_id, source_ref, _now()) for tip_id in unlocked])
        self.db.commit()

    def pending(self, *, limit, text_budget_bytes, capabilities=(), startup_only=False):
        """Budgeted offers. Only acknowledge after the response reaches the player.

        capabilities must come from the host's actual lifecycle implementation;
        values are restart/objectives/save/load, not inferred from Tip text.
        Byte budget covers compact UTF-8 JSON of the complete items array.
        """
        _positive(limit, 'limit'); _positive(text_budget_bytes, 'text_budget_bytes')
        if type(startup_only) is not bool:
            raise ValueError('startup_only must be bool')
        if not isinstance(capabilities, (tuple, list, set, frozenset)):
            raise ValueError('capabilities must be an explicit collection')
        if any(not isinstance(c, str) for c in capabilities):
            raise ValueError('capability names must be strings')
        capabilities = set(capabilities)
        known = self._known_names()
        public_gates = {r[0] for r in self.db.execute(
            'SELECT tip_id FROM tips_public_gate_v1 WHERE profile=?', (self.profile,))}
        delivered = {r[0] for r in self.db.execute('''SELECT tip_id FROM tips_delivery_v1
            WHERE profile=? AND delivered_at_utc IS NOT NULL''', (self.profile,))}
        candidates = []
        for tip in self._tips:
            if tip['id'] in delivered or not set(tip['requires']).issubset(capabilities):
                continue
            if startup_only and tip['gate'] != 'startup':
                continue
            if tip['gate'] == 'any_known_name' and tip['id'] not in public_gates and not any(
                    name.casefold() in known for name in tip['button_names']):
                continue
            candidates.append(tip)
        chosen = candidates[:limit]
        items = [dict(id=t['id'], source_ids=t['source_ids'], text=t['text'],
                      source_kind='user_selected_original_game_tip',
                      effect_runtime_verified=False) for t in chosen]
        required = _bytes(items)
        if required > text_budget_bytes:
            return dict(status='budget_required', required_text_budget_bytes=required,
                        text_budget_bytes=text_budget_bytes, items=[],
                        has_more=bool(candidates))
        self.db.executemany('''INSERT OR IGNORE INTO tips_delivery_v1
            (profile,tip_id,offered_at_utc) VALUES(?,?,?)''',
            [(self.profile, tip['id'], _now()) for tip in chosen])
        self.db.commit()
        return dict(status='ok', items=items, text_bytes=required,
                    has_more=len(candidates) > len(chosen))

    def acknowledge(self, tip_ids):
        """Acknowledge only previously offered IDs; no name or skill unlocks."""
        if not isinstance(tip_ids, list):
            raise ValueError('tip_ids must be a list')
        for tip_id in tip_ids:
            _text(tip_id, 'tip_id')
            if not self.db.execute('SELECT 1 FROM tips_delivery_v1 WHERE profile=? AND tip_id=?',
                                   (self.profile, tip_id)).fetchone():
                raise ValueError('tip was never offered to this profile')
        self.db.executemany('''UPDATE tips_delivery_v1 SET delivered_at_utc=?
            WHERE profile=? AND tip_id=? AND delivered_at_utc IS NULL''',
            [(_now(), self.profile, tip_id) for tip_id in tip_ids])
        self.db.commit()
