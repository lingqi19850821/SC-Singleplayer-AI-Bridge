"""Lawfully explored terrain projection in the existing PanelKnowledge SQLite.

Reference reads never query native state. Current visible nativewalk is not
promoted to immutable geometry; the explicit static attachment is independent.
"""
from copy import deepcopy
import base64
import hashlib
import hmac
import json


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def aligned(value):
    return type(value) is int and 0<=value<=0x7fffffe0 and value%32==0


GRID_CELL_SIZE=16
GRID_LEGEND='?=unexplored 0/1/2=ground_height R=ramp /=partial_edge #=solid_block ~=water_or_void; 1char=16px(4chars=64px); air crosses #/~'
VOID_CATEGORIES=frozenset(('water','water_boundary','space','space_boundary',
    'pit','pit_boundary','lava_or_magma','lava_or_magma_boundary','tar','tar_boundary'))


def _matrix4(value):
    if not isinstance(value,list) or len(value)!=4:return None
    if any(not isinstance(row,list) or len(row)!=4 for row in value):return None
    return value


def _label_category(label):
    if isinstance(label,dict):
        category=label.get('category')
        if isinstance(category,str):return category
    return None


def _surface_value(entry,key):
    if not isinstance(entry,dict):return None
    surface=entry.get('surface_observation')
    if not isinstance(surface,dict):return None
    item=surface.get(key)
    if not isinstance(item,dict) or 'value' not in item:return None
    return item['value']


def _cell_symbol(walk,heights,ramps,category,cy,cx):
    """One 16x16 cell from its four 8px subcells; unknown never becomes ground."""
    walk=_matrix4(walk);heights=_matrix4(heights);ramps=_matrix4(ramps)
    if walk is None or heights is None:return '?'
    walkable_cnt=0;ramp_cnt=0;height=None
    for dy in (0,1):
        for dx in (0,1):
            row,col=cy*2+dy,cx*2+dx
            value=walk[row][col]
            if type(value) is not bool:return '?'
            if value:walkable_cnt+=1
            level=heights[row][col]
            if type(level) is not int or level not in (0,1,2):return '?'
            height=level if height is None else min(height,level)
            if ramps is not None and ramps[row][col] is True:ramp_cnt+=1
    if ramp_cnt>0 and walkable_cnt>=2:return 'R'
    if walkable_cnt==4:return str(height)
    if walkable_cnt:return '/'
    return '~' if category in VOID_CATEGORIES else '#'


def _tile_symbols(walk,heights,ramps,category):
    return [_cell_symbol(walk,heights,ramps,category,cy,cx)
            for cy,cx in ((0,0),(0,1),(1,0),(1,1))]


def _all_walk_true(walk):
    walk=_matrix4(walk)
    return walk is not None and all(value is True for row in walk for value in row)


def _stored_entries(store,left,top,right,bottom):
    """One bounded read of already-learned terrain; never native IO."""
    db=getattr(store,'db',None);scope=getattr(store,'scope',None)
    if db is None or not isinstance(scope,str):return {}
    try:
        rows=db.execute('SELECT y,x,payload FROM terrain_reference_v1 '
                        'WHERE scope=? AND y>=? AND y<? AND x>=? AND x<? ORDER BY y,x',
                        (scope,top,bottom,left,right)).fetchall()
    except Exception:
        return {}
    result={}
    for row in rows:
        try:
            y,x,payload=row
            entry=json.loads(payload)
        except (TypeError,ValueError):
            continue
        if isinstance(entry,dict):result[(x,y)]=entry
    return result


def _row_intervals(points):
    by_row={}
    for x,y in points:by_row.setdefault(y,[]).append(x)
    result=[]
    for y in sorted(by_row):
        xs=sorted(set(by_row[y]));segments=[];start=previous=xs[0]
        for x in xs[1:]:
            if x==previous+32:
                previous=x;continue
            segments.append(str(start)+'-'+str(previous+32));start=previous=x
        segments.append(str(start)+'-'+str(previous+32))
        result.append('y'+str(y)+':'+','.join(segments))
    return result


def _ramp_boxes(cells):
    seen=set();boxes=[]
    for cell in sorted(cells,key=lambda value:(value[1],value[0])):
        if cell in seen:continue
        seen.add(cell);stack=[cell];xs=[];ys=[]
        while stack:
            x,y=stack.pop();xs.append(x);ys.append(y)
            for point in ((x-16,y),(x+16,y),(x,y-16),(x,y+16)):
                if point in cells and point not in seen:
                    seen.add(point);stack.append(point)
        boxes.append([min(xs),min(ys),max(xs)+16,max(ys)+16])
    return sorted(boxes,key=lambda box:(box[1],box[0],box[3],box[2]))


def _grid_payload(origin_x,origin_y,tiles_x,tiles_y,symbols,flags,
                  camera=None,observed_tick_ms=None):
    rows=[];ramp_cells=set();no_build=[];blocks_view=[];creep_current=[];creep_fog=[]
    for tile_y in range(tiles_y):
        y=origin_y+tile_y*32
        # A 32px tile is a 2x2 block: two chars in each of two 16px rows.
        lines=[[''],['']]
        for tile_x in range(tiles_x):
            x=origin_x+tile_x*32
            values=symbols.get((x,y))
            if not isinstance(values,list) or len(values)!=4:values=['?','?','?','?']
            lines[0].extend(values[0:2]);lines[1].extend(values[2:4])
            for index,symbol in enumerate(values):
                if symbol=='R':
                    ramp_cells.add((x+(index%2)*16,y+(index//2)*16))
            flag=flags.get((x,y)) or {}
            if flag.get('no_build') is True:no_build.append((x,y))
            if flag.get('blocks_view') is True:blocks_view.append((x,y))
            if flag.get('creep_current') is True:creep_current.append((x,y))
            if flag.get('creep_fog') is True:creep_fog.append((x,y))
        for subrow in (0,1):
            text=''.join(lines[subrow])
            rows.append('y'+str(y+subrow*16)+':'+' '.join(
                text[index:index+4] for index in range(0,len(text),4)))
    width_chars=tiles_x*2
    payload={'status':'observed','origin':{'x':origin_x,'y':origin_y},
             'cell_size_px':GRID_CELL_SIZE,'legend':GRID_LEGEND,
             'x_axis':' '.join(str(origin_x+index*GRID_CELL_SIZE)
                               for index in range(0,width_chars,4)),
             'rows':rows}
    if isinstance(camera,dict):payload['camera']=deepcopy(camera)
    if type(observed_tick_ms) is int:payload['observed_tick_ms']=observed_tick_ms
    ramps=_ramp_boxes(ramp_cells)
    if ramps:payload['ramps']=ramps
    if no_build:payload['no_build']=_row_intervals(no_build)
    if blocks_view:payload['blocks_view']=_row_intervals(blocks_view)
    if creep_current:payload['creep_current']=_row_intervals(creep_current)
    if creep_fog:payload['creep_fog']=_row_intervals(creep_fog)
    return payload


def build_screen_terrain(terrain,store=None):
    """Render the observed live/fog screen as compact 16x16px characters.

    Current cells use live walkability so doors are visible immediately. Fog
    cells use their static ground attachment, while remembered creep and an
    unresolved terrain label may fall back to this scope's lawfully learned
    SQLite history. Any missing 8px subcell keeps its 16px cell unknown.
    """
    if not isinstance(terrain,dict):return None
    cells=terrain.get('cells')
    if not isinstance(cells,list) or not cells:return None
    observed=[]
    for cell in cells:
        if not isinstance(cell,dict):continue
        if aligned(cell.get('x')) and aligned(cell.get('y')):observed.append(cell)
    if not observed:return None
    min_x=min(cell['x'] for cell in observed);max_x=max(cell['x'] for cell in observed)
    min_y=min(cell['y'] for cell in observed);max_y=max(cell['y'] for cell in observed)
    stored=_stored_entries(store,min_x,min_y,max_x+32,max_y+32)
    symbols={};flags={}
    for cell in observed:
        x,y=cell['x'],cell['y']
        # Real decoder cells always carry this key (including explicit None for
        # a hidden representative). Absence is only a legacy synthetic fixture.
        if 'explored' in cell and cell.get('explored') is not True:
            symbols[(x,y)]=['?','?','?','?']
            continue
        visible=cell.get('currently_visible') is True
        if visible:
            walk=cell.get('walkable_8px')
        else:
            walk=cell.get('static_walkable_8px')
            if walk is None:walk=cell.get('walkable_8px')
        heights=cell.get('ground_height_8px')
        ramps=cell.get('ramp_marked_8px')
        category=_label_category(cell.get('terrain'))
        entry=stored.get((x,y))
        if category is None and isinstance(entry,dict):
            geometry=entry.get('geometry')
            if isinstance(geometry,dict):
                category=_label_category(geometry.get('terrain'))
        values=_tile_symbols(walk,heights,ramps,category)
        symbols[(x,y)]=values
        creep=cell.get('creep')
        observation=cell.get('creep_observation')
        creep_current=visible and creep is True and observation=='current'
        creep_fog=(not visible and creep is True and observation=='last_observed')
        if not visible and _surface_value(entry,'creep') is True:creep_fog=True
        buildable=cell.get('terrain_buildable')
        blocks_view=cell.get('blocks_view')
        if not visible:
            # Fog has no current building/view authority; retain only the
            # last-observed map facts already in this scope's history.
            if buildable is None:buildable=_surface_value(entry,'terrain_buildable')
            if blocks_view is None:blocks_view=_surface_value(entry,'blocks_view')
        flags[(x,y)]={'no_build':_all_walk_true(walk) and buildable is False
                                  and not all(value=='R' for value in values),
                      'blocks_view':blocks_view is True,
                      'creep_current':creep_current,'creep_fog':creep_fog}
    return _grid_payload(min_x,min_y,(max_x-min_x)//32+1,(max_y-min_y)//32+1,
                         symbols,flags,camera=terrain.get('camera'),
                         observed_tick_ms=terrain.get('observed_tick_ms'))


def merge_known(old,new):
    """Unknown/obscured subcells do not erase legitimately learned geometry."""
    if new is None:return deepcopy(old)
    if isinstance(new,list):
        if old is None:return deepcopy(new)
        return [merge_known(a,b) for a,b in zip(old,new)]
    if isinstance(new,dict):
        result=deepcopy(old) if isinstance(old,dict) else {}
        for key,value in new.items():
            result[key]=deepcopy(value) if key=='terrain' else merge_known(result.get(key),value)
        return result
    return deepcopy(new)


class TerrainReference:
    def __init__(self,knowledge):
        self.knowledge=knowledge;self.db=knowledge.db;self.scope=knowledge.scope
        self.db.execute('''CREATE TABLE IF NOT EXISTS terrain_reference_v1(
            scope TEXT NOT NULL,y INTEGER NOT NULL,x INTEGER NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(scope,y,x))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS terrain_resource_sites_v1(
            scope TEXT NOT NULL,site_token TEXT NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,
            payload TEXT NOT NULL,PRIMARY KEY(scope,site_token))''')
        self.db.execute('CREATE INDEX IF NOT EXISTS terrain_resource_sites_range_v1 '
                        'ON terrain_resource_sites_v1(scope,y,x,site_token)')
        self.db.commit()

    @staticmethod
    def _resource_mask(snapshot,visibility,expected,current_camera,census_serial):
        """Only the attachment of this exact complete native census can clear."""
        if not isinstance(visibility,dict):return None
        width,height=visibility.get('width'),visibility.get('height')
        tick= snapshot.get('observed_tick_ms')
        if (visibility.get('complete') is not True or visibility.get('scope')!='current_operable_viewport'
                or not isinstance(current_camera,dict) or set(current_camera)!={'x','y','width','height'}
                or any(type(v) is not int or v<0 for v in current_camera.values())
                or not current_camera['width'] or not current_camera['height']
                or not isinstance(visibility.get('camera'),dict)
                or any(type(v) is not int for v in visibility['camera'].values())
                or visibility.get('camera')!=current_camera
                or type(visibility.get('epoch')) is not int or visibility['epoch']!=expected['epoch']
                or type(tick) is not int or tick<0 or type(visibility.get('tick')) is not int or visibility['tick']!=tick
                or any(type(visibility.get(k)) is not int or visibility[k]<0 for k in
                       ('frame','selection_generation','selection_count','census_serial'))
                or type(census_serial) is not int or census_serial<=0 or visibility['census_serial']!=census_serial
                or type(width) is not int or type(height) is not int or not 0<width<=256 or not 0<height<=256
                or not isinstance(visibility.get('bits'),bytes)
                or len(visibility['bits'])!=(width*height+7)//8):return None
        if current_camera['x']+current_camera['width']>width*32 or current_camera['y']+current_camera['height']>height*32:return None
        return visibility

    @staticmethod
    def _resource_visible(footprint,mask):
        if mask is None:return False
        width,height=mask['width'],mask['height']
        camera=mask['camera']
        if (footprint['left']<camera['x'] or footprint['top']<camera['y']
                or footprint['right']>camera['x']+camera['width']
                or footprint['bottom']>camera['y']+camera['height']):return False
        if footprint['right']>width*32 or footprint['bottom']>height*32:return False
        return all(mask['bits'][(y*width+x)//8]>>(y*width+x)%8&1
                   for y in range(footprint['top']//32,(footprint['bottom']-1)//32+1)
                   for x in range(footprint['left']//32,(footprint['right']-1)//32+1))

    def observe_resources(self,snapshot,*,visibility,observed_session,expected_session,
                          include_selected_amount=False,selected=None,current_camera=None,census_serial=None,published_tokens=()):
        """Merge one lawful resource census; reference history never drives game IO.

        A missing row is only absent from the census. A complete matching
        operable-viewport mask must cover every old footprint tile before recording an
        observed absence. Resource amount is admitted only by a successful real
        selection's one-shot UI read, never by an automatic census.
        """
        keys=('pid','process_creation','epoch')
        if (any(type(expected_session.get(k)) is not int or expected_session[k]<=0 for k in keys)
                or any(type(observed_session.get(k)) is not int or observed_session[k]!=expected_session[k] for k in keys)):
            return {'status':'rejected','reason':'resource_scope_mismatch'}
        if snapshot.get('status')!='observed':return deepcopy(snapshot)
        tick=snapshot.get('observed_tick_ms')
        if (snapshot.get('scope')!='current_player_vision' or type(tick) is not int or tick<0
                or not isinstance(snapshot.get('sites'),list)):
            raise ValueError('complete lawful resource census required')
        mask=self._resource_mask(snapshot,visibility,expected_session,current_camera,census_serial)
        latest=self.db.execute("SELECT max(json_extract(payload,'$.observed_tick_ms')) "
                               'FROM terrain_resource_sites_v1 WHERE scope=?',(self.scope,)).fetchone()[0]
        if latest is not None and tick<latest:mask=None
        amount_allowed=(include_selected_amount is True and mask is not None
            and isinstance(selected,dict) and all(type(selected.get(k)) is int and selected[k]==expected_session[k] for k in keys)
            and selected.get('count')==1 and type(selected.get('count')) is int
            and type(selected.get('generation')) is int and selected['generation']==mask['selection_generation']
            and mask['selection_count']==1)
        current={};changes=[]
        # Validate the complete input before modifying even one stored site.
        for row in snapshot['sites']:
            if not isinstance(row,dict):raise ValueError('invalid resource site')
            kind,form=row.get('resource_kind'),row.get('form')
            position,footprint=row.get('position'),row.get('footprint')
            if (kind not in ('mineral','gas') or form not in
                    (('mineral_field',) if kind=='mineral' else ('geyser','gas_building'))
                    or not isinstance(position,dict) or set(position)!={'x','y'}
                    or any(type(v) is not int or not 0<=v<2**31 for v in position.values())
                    or not isinstance(footprint,dict) or set(footprint)!={'left','top','right','bottom'}
                    or any(type(v) is not int or not 0<=v<2**31 for v in footprint.values())
                    or not footprint['left']<=position['x']<footprint['right']
                    or not footprint['top']<=position['y']<footprint['bottom']
                    or not isinstance(row.get('relation'),str) or not row['relation']):
                raise ValueError('invalid resource site fields')
            token=row.get('site_token')
            if (token!=self.knowledge.token('resource_site',kind+':'+str(position['x'])+':'+str(position['y']))
                    or token in current):raise ValueError('invalid or duplicate resource site token')
            previous=self.db.execute('SELECT payload FROM terrain_resource_sites_v1 WHERE scope=? AND site_token=?',
                                     (self.scope,token)).fetchone()
            previous=json.loads(previous[0]) if previous else None
            entry={key:deepcopy(row[key]) for key in ('site_token','resource_kind','form','position','relation','footprint')}
            entry.update(presence='observed_present',observed_tick_ms=tick,temporal='last_observed',
                         remaining=deepcopy(previous['remaining']) if previous else {'status':'unknown'})
            amount=row.get('remaining')
            if amount is not None:
                if (not isinstance(amount,dict) or set(amount)!={'value','observed_tick_ms'}
                        or type(amount['value']) is not int or not 0<=amount['value']<=65535
                        or type(amount['observed_tick_ms']) is not int or amount['observed_tick_ms']!=tick):
                    raise ValueError('invalid selected resource amount')
                if amount_allowed:entry['remaining']={'status':'last_observed',**amount}
            if previous and tick<previous['observed_tick_ms']:
                # A suspended older sample cannot overwrite newer selected data.
                entry=previous
            old={k:v for k,v in (previous or {}).items() if k!='observed_tick_ms'}
            if previous and canonical(old)==canonical({k:v for k,v in entry.items() if k!='observed_tick_ms'}):entry=previous
            else:changes.append((self.scope,token,position['x'],position['y'],canonical(entry)))
            current[token]=entry
        if amount_allowed and sum('remaining' in row for row in snapshot['sites'])>1:
            raise ValueError('only one real selected resource can supply an amount')
        if mask is not None:
            for token,payload in self.db.execute('SELECT site_token,payload FROM terrain_resource_sites_v1 WHERE scope=?',(self.scope,)):
                if token in current:continue
                old=json.loads(payload)
                if not self._resource_visible(old['footprint'],mask):continue
                entry=deepcopy(old)
                if entry['presence']!='visibly_absent':
                    entry.update(presence='visibly_absent',observed_tick_ms=tick)
                    changes.append((self.scope,token,entry['position']['x'],entry['position']['y'],canonical(entry)))
                # Reference-only history is not silently loaded into a new
                # public baseline. A known published site can change in place.
                if token in published_tokens:current[token]=entry
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO terrain_resource_sites_v1 VALUES(?,?,?,?,?)',changes)
        return {'status':'observed','scope':'observed_resource_sites','observed_tick_ms':tick,'sites':current,
                'membership_semantics':'last_observed_sites; absence_requires_operable_screen_confirmation'}

    def _read_resources(self,args):
        bad={'status':'rejected','reason':'invalid_reference_arguments','source':'terrain','layer':'resources'}
        if set(args)-{'source','layer','rect','cursor','limit'}:return bad
        limit=args.get('limit',32);rect=args.get('rect');cursor=args.get('cursor')
        if type(limit) is not int or not 1<=limit<=64:return bad
        if rect is not None and (not isinstance(rect,dict) or set(rect)!={'left','top','right','bottom'}
                or not all(aligned(v) for v in rect.values()) or rect['left']>=rect['right'] or rect['top']>=rect['bottom']):return bad
        query={'layer':'resources','rect':rect};after=(-1,-1,'')
        if cursor is not None:
            if not isinstance(cursor,str) or len(cursor)>2048:return bad
            try:
                raw=base64.b64decode(cursor+'='*((-len(cursor))%4),altchars=b'-_',validate=True)
                signature,body=raw[:16],raw[16:];decoded=json.loads(body)
                if (not hmac.compare_digest(signature,hmac.new(self.knowledge.salt.encode(),body,hashlib.sha256).digest()[:16])
                        or decoded['scope_tag']!=hashlib.sha256(self.scope.encode()).hexdigest()[:16]
                        or decoded['query']!=query or any(type(decoded[k]) is not int or decoded[k]<0 for k in ('x','y'))
                        or not isinstance(decoded['site_token'],str)):return bad
                after=(decoded['y'],decoded['x'],decoded['site_token'])
            except (ValueError,KeyError,TypeError):return bad
        sql='SELECT y,x,site_token,payload FROM terrain_resource_sites_v1 WHERE scope=? AND (y,x,site_token)>(?,?,?)'
        values=[self.scope,*after]
        if rect is not None:
            sql+=' AND y>=? AND y<? AND x>=? AND x<?';values.extend([rect['top'],rect['bottom'],rect['left'],rect['right']])
        rows=self.db.execute(sql+' ORDER BY y,x,site_token LIMIT ?',(*values,limit+1)).fetchall()
        more=len(rows)>limit
        return {'status':'ok','source':'terrain','layer':'resources','coordinate_system':'world_pixels',
                'temporal':'last_observed','entries':[json.loads(row[3]) for row in rows[:limit]],'has_more':more,
                'next_cursor':self._cursor(query,rows[limit-1][0],rows[limit-1][1],rows[limit-1][2]) if more else None}

    @staticmethod
    def geometry_qualification(cell):
        value=cell.get('ground_geometry_known_8px')
        if value is None:return None
        if (not isinstance(value,list) or len(value)!=4 or
                any(not isinstance(row,list) or len(row)!=4 for row in value) or
                any(type(v) is not bool for row in value for v in row)):
            raise ValueError('invalid lawful subcell qualification')
        return value

    @staticmethod
    def geometry(cell):
        result={};qualified=TerrainReference.geometry_qualification(cell)
        for key,kind in (('ground_height_8px',int),('ramp_marked_8px',bool),
                         ('walkable_8px',bool)):
            # Visible nativewalk includes active-tile rules, not just fixed VF4.
            if key=='walkable_8px':
                value=cell.get('static_walkable_8px')
                if value is None and cell.get('ground_geometry_source')=='explored_static_ground':value=cell.get(key)
            else:value=cell.get(key)
            if value is None:continue
            if (not isinstance(value,list) or len(value)!=4 or
                any(not isinstance(row,list) or len(row)!=4 for row in value) or
                any(v is not None and (type(v) is not kind or
                    (key=='ground_height_8px' and v not in (0,1,2))) for row in value for v in row)):
                raise ValueError('invalid lawful geometry grid')
            if qualified is not None and any(value[row][col] is not None and not qualified[row][col]
                                             for row in range(4) for col in range(4)):
                raise ValueError('geometry outside lawful subcell qualification')
            if any(v is not None for row in value for v in row):result[key]=deepcopy(value)
        label=cell.get('terrain')
        if (cell.get('tile_summary_status')=='observed' and isinstance(label,dict)
                and label.get('status') in ('identified','transition')
                and label.get('category')!='creep_covered_ground'):
            result['terrain']=deepcopy(label)
        return result

    def observe(self,terrain,*,observed_session,expected_session):
        """Caller supplies identities from the completed lawful sample boundary."""
        keys=('pid','process_creation','epoch')
        if (any(type(expected_session.get(k)) is not int or expected_session[k]<=0 for k in keys)
                or any(type(observed_session.get(k)) is not int or observed_session[k]!=expected_session[k] for k in keys)
                or type(terrain.get('epoch')) is not int or terrain.get('epoch')!=expected_session['epoch']):
            return {'status':'rejected','reason':'terrain_scope_mismatch'}
        if terrain.get('status')!='observed':return {'status':'unchanged','new_cells':0,'updated_cells':0}
        # Stage all validation before the transaction: bad samples never partly commit.
        changes=[];seen=set();new_cells=updated_cells=0
        for cell in terrain.get('cells',[]):
            if not isinstance(cell,dict):raise ValueError('invalid terrain cell')
            x,y=cell.get('x'),cell.get('y')
            if not aligned(x) or not aligned(y):raise ValueError('invalid terrain coordinates')
            if (x,y) in seen:raise ValueError('duplicate terrain cell')
            seen.add((x,y))
            explored=cell.get('explored')
            if explored is not True:
                # The representative point may be hidden while other complete
                # 8px squares passed the native UI and exploration gates. Only
                # the decoder's explicit per-square qualification admits this
                # path; an unexplored tile or a bare nonempty grid cannot.
                if explored is not None or cell.get('tile_summary_status')!='tile_summary_not_observed':continue
                qualified=self.geometry_qualification(cell)
                if qualified is None or not any(v for row in qualified for v in row):continue
            geometry=self.geometry(cell)
            observed_surface={key:cell[key] for key in ('terrain_buildable','blocks_view')
                              if type(cell.get(key)) is bool and cell.get('tile_summary_status')=='observed'
                              and cell.get('currently_visible') is True}
            if (explored is True and cell.get('tile_summary_status')=='observed' and
                    type(cell.get('creep')) is bool and cell.get('creep_observation') in ('current','last_observed')):
                observed_surface['creep']=cell['creep']
            if not geometry and not observed_surface:continue
            found=self.db.execute('SELECT payload FROM terrain_reference_v1 WHERE scope=? AND y=? AND x=?',
                                  (self.scope,y,x)).fetchone()
            previous=json.loads(found[0]) if found else None
            old=previous.get('geometry',{}) if previous else {}
            merged=merge_known(old,geometry)
            surface=deepcopy(previous.get('surface_observation',{})) if previous else {}
            for key,value in observed_surface.items():
                if surface.get(key,{}).get('value') is value:continue
                basis=cell['creep_observation'] if key=='creep' else 'observed_tile_summary'
                surface[key]={'value':value,'temporal':'last_observed',
                              'recorded_tick_ms':terrain.get('observed_tick_ms'),'observation_basis':basis}
                if basis!='last_observed':surface[key]['observed_tick_ms']=terrain.get('observed_tick_ms')
            if canonical(old)==canonical(merged) and surface==(previous.get('surface_observation',{}) if previous else {}):continue
            entry=deepcopy(previous) if previous else {'x':x,'y':y,
                'first_observed_tick_ms':terrain.get('observed_tick_ms')}
            entry.update(geometry=merged,geometry_observed_tick_ms=terrain.get('observed_tick_ms'),
                         temporal='lawfully_observed_fixed_geometry')
            if surface:entry['surface_observation']=surface
            changes.append((self.scope,y,x,canonical(entry)))
            if previous:updated_cells+=1
            else:new_cells+=1
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO terrain_reference_v1 VALUES(?,?,?,?)',changes)
        result={'status':'available' if changes else 'unchanged','source':'terrain',
                'new_cells':new_cells,'updated_cells':updated_cells}
        if changes:
            xs=[r[2] for r in changes];ys=[r[1] for r in changes]
            result['bounds']={'left':min(xs),'top':min(ys),'right':max(xs)+32,'bottom':max(ys)+32}
        return result

    def _cursor(self,query,y,x,site_token=None):
        # Opaque pagination must not expose the native address in private scope.
        scope_tag=hashlib.sha256(self.scope.encode()).hexdigest()[:16]
        value={'query':query,'scope_tag':scope_tag,'y':y,'x':x}
        if site_token is not None:value['site_token']=site_token
        body=canonical(value).encode()
        signature=hmac.new(self.knowledge.salt.encode(),body,hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(signature+body).decode().rstrip('=')

    def read_grid(self,args):
        """Render one bounded rectangle entirely from last-observed SQLite rows.

        This is history, never a current screen: every remembered creep tile is
        creep_fog, and a rectangle tile with no stored row is unknown.
        """
        bad={'status':'rejected','reason':'invalid_reference_arguments','source':'terrain'}
        if not isinstance(args,dict):return bad
        if args.get('source','terrain')!='terrain' or args.get('format','grid')!='grid':return bad
        if args.get('layer') not in (None,'terrain'):return bad
        if set(args)-{'source','format','layer','rect','limit'}:return bad
        if 'x' in args or 'y' in args or 'cursor' in args:return bad
        rect=args.get('rect')
        if (not isinstance(rect,dict) or set(rect)!={'left','top','right','bottom'}
                or not all(aligned(rect[key]) for key in ('left','top','right','bottom'))
                or rect['left']>=rect['right'] or rect['top']>=rect['bottom']):return bad
        tiles_x=(rect['right']-rect['left'])//32
        tiles_y=(rect['bottom']-rect['top'])//32
        if tiles_x*tiles_y>1024:return bad
        limit=args.get('limit',32)
        if type(limit) is not int or not 1<=limit<=64:return bad
        rows=self.db.execute('SELECT y,x,payload FROM terrain_reference_v1 '
                             'WHERE scope=? AND y>=? AND y<? AND x>=? AND x<? ORDER BY y,x',
                             (self.scope,rect['top'],rect['bottom'],
                              rect['left'],rect['right'])).fetchall()
        stored={}
        for y,x,payload in rows:
            entry=json.loads(payload)
            if isinstance(entry,dict):stored[(x,y)]=entry
        symbols={};flags={}
        for y in range(rect['top'],rect['bottom'],32):
            for x in range(rect['left'],rect['right'],32):
                entry=stored.get((x,y))
                if entry is None:
                    symbols[(x,y)]=['?','?','?','?']
                    continue
                geometry=entry.get('geometry')
                if not isinstance(geometry,dict):geometry={}
                values=_tile_symbols(geometry.get('walkable_8px'),
                    geometry.get('ground_height_8px'),geometry.get('ramp_marked_8px'),
                    _label_category(geometry.get('terrain')))
                symbols[(x,y)]=values
                flags[(x,y)]={'no_build':_all_walk_true(geometry.get('walkable_8px'))
                                  and _surface_value(entry,'terrain_buildable') is False
                                  and not all(value=='R' for value in values),
                              'blocks_view':_surface_value(entry,'blocks_view') is True,
                              'creep_current':False,
                              'creep_fog':_surface_value(entry,'creep') is True}
        payload=_grid_payload(rect['left'],rect['top'],tiles_x,tiles_y,symbols,flags)
        payload.update(status='ok',source='terrain',format='grid',
                       coordinate_system='world_pixels')
        return payload

    def read(self,args):
        bad={'status':'rejected','reason':'invalid_reference_arguments','source':'terrain'}
        if not isinstance(args,dict):return bad
        value=args.get('format','cells')
        if value not in ('cells','grid'):return bad
        if args.get('source')!='terrain':return bad
        if args.get('layer')=='resources':
            if value=='grid':return bad
            return self._read_resources({key:item for key,item in args.items() if key!='format'})
        if value=='grid':return self.read_grid(args)
        if args.get('layer')=='terrain':
            args={key:value for key,value in args.items() if key not in ('layer','format')}
        else:
            args={key:value for key,value in args.items() if key!='format'}
        if set(args)-{'source','x','y','rect','cursor','limit'} or args.get('source')!='terrain':return bad
        limit=args.get('limit',32)
        if type(limit) is not int or not 1<=limit<=64:return bad
        single='x' in args or 'y' in args
        rect=args.get('rect');cursor=args.get('cursor')
        if single:
            if rect is not None or cursor is not None or not aligned(args.get('x')) or not aligned(args.get('y')):return bad
            row=self.db.execute('SELECT payload FROM terrain_reference_v1 WHERE scope=? AND y=? AND x=?',
                                (self.scope,args['y'],args['x'])).fetchone()
            return ({'status':'ok','source':'terrain','entry':json.loads(row[0]),
                     'coordinate_system':'world_pixels','tile_size':32} if row else
                    {'status':'not_learned','source':'terrain','x':args['x'],'y':args['y']})
        if rect is not None and (not isinstance(rect,dict) or set(rect)!= {'left','top','right','bottom'}
                or not all(aligned(v) for v in rect.values()) or rect['left']>=rect['right'] or rect['top']>=rect['bottom']):return bad
        query={'rect':rect};after=(-1,-1)
        if cursor is not None:
            if not isinstance(cursor,str) or len(cursor)>2048:return bad
            try:
                raw=base64.b64decode(cursor+'='*((-len(cursor))%4),altchars=b'-_',validate=True)
                signature,body=raw[:16],raw[16:];decoded=json.loads(body)
                if (not hmac.compare_digest(signature,hmac.new(self.knowledge.salt.encode(),body,hashlib.sha256).digest()[:16])
                        or decoded['scope_tag']!=hashlib.sha256(self.scope.encode()).hexdigest()[:16] or decoded['query']!=query
                        or not aligned(decoded['x']) or not aligned(decoded['y'])):return bad
                after=(decoded['y'],decoded['x'])
            except (ValueError,KeyError,TypeError):return bad
        sql='SELECT y,x,payload FROM terrain_reference_v1 WHERE scope=? AND (y,x)>(?,?)'
        values=[self.scope,*after]
        if rect is not None:
            sql+=' AND y>=? AND y<? AND x>=? AND x<?'
            values.extend([rect['top'],rect['bottom'],rect['left'],rect['right']])
        rows=self.db.execute(sql+' ORDER BY y,x LIMIT ?',(*values,limit+1)).fetchall()
        entries=[json.loads(row[2]) for row in rows[:limit]];more=len(rows)>limit
        return {'status':'ok','source':'terrain','coordinate_system':'world_pixels','tile_size':32,
                'entries':entries,'has_more':more,
                'next_cursor':self._cursor(query,rows[limit-1][0],rows[limit-1][1]) if more else None}


def project_automatic_view(view):
    """Automatic wire keeps UI metadata; fixed map lives in SQLite.

    Existing remembered_buildings updates remain the independent building overlay.
    Placement still uses the actual native preview, never this historical map.
    """
    result=deepcopy(view)
    terrain=result.get('terrain')
    if isinstance(terrain,dict):
        cells=terrain.pop('cells',[]);terrain.pop('occupancy_semantics',None)
        terrain['reference_source']='terrain'
        power=[{'x':cell['x'],'y':cell['y'],'power':cell['power_for_current_placement']}
               for cell in cells if type(cell.get('power_for_current_placement')) is bool]
        if power:
            terrain['placement_power_observation']={'selection_generation':terrain.get('selection_generation'),
                'target_generation':terrain.get('target_generation'),'observed_tick_ms':terrain.get('observed_tick_ms'),
                'cells':power,'temporal':'current_placement_observation'}
    units=result.get('units',[])
    for unit in units.values() if isinstance(units,dict) else units:
        for key in ('ground_elevation','underlying_ground_elevation','terrain_sample_position',
                    'terrain_sample_status','terrain','terrain_ground_walkable','ramp_marker_at_position',
                    'terrain_contains_ramp_marker'):
            unit.pop(key,None)
    keyed_native=isinstance(terrain,dict) and terrain.get('entity_identity')=='scope_salted_entity_token'
    if isinstance(units,list) and (units or keyed_native):
        keyed={}
        for unit in units:
            token=unit.get('entity_token')
            if (not isinstance(token,str) or not token.startswith('entity:') or len(token)!=23
                    or any(c not in '0123456789abcdef' for c in token[7:])):
                if keyed_native:
                    raise ValueError('keyed visible roster is missing lawful entity identity')
                result['entity_identity_status']='legacy_unkeyed'
                break
            if token in keyed:raise ValueError('duplicate visible entity identity')
            keyed[token]=unit
        else:
            result.update(units=keyed,count=len(keyed),entity_identity_status='scope_keyed',
                          membership_semantics='current_visible_roster; removal_does_not_prove_death')
    return result
