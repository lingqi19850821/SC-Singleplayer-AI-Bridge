"""Labels for observed terrain and the actual unit centre's ground layer.

Palette facts: Chkdraft, MIT, notice in terrain_labels.LICENSE.txt.  The raw
CV5 identifiers remain private. Material names never override native walking
or height flags; unnamed transition families retain their inference status.
"""
import json
from pathlib import Path

HEIGHT_NAMES=('low_ground','high_ground','very_high_ground')
_REFERENCE=json.loads(Path(__file__).with_name('terrain_labels.json').read_text(encoding='utf8'))
_SETS={t['era']:t for t in _REFERENCE['tilesets']}
_BASE={era:{v['terrain_type']:v['palette_name'] for v in t['base_types']} for era,t in _SETS.items()}
_EDGES={era:{v['terrain_type']:v for v in t['transitions']} for era,t in _SETS.items()}

def _family(name):
    words=name.lower().split()
    if 'water' in words:return 'water'
    if name=='Space':return 'space'
    if name=='Bottomless Pit':return 'pit'
    if 'lava' in words or name=='Magma':return 'lava_or_magma'
    if name=='Tar':return 'tar'
    if name=='Ice':return 'ice'
    return 'ground'

def terrain_label(tileset,kind):
    """Only name an unambiguous rendered-tile family, never a whole map."""
    if kind==0xfffffffe:return {'status':'unknown','reason':'ambiguous_rendered_tile'}
    if kind in (0,0xffffffff):return {'status':'unknown','reason':'unidentified_rendered_tile'}
    if kind==1:return {'status':'identified','name':'Doodad','category':'doodad','reason':'doodad_surface'}
    name=_BASE.get(tileset,{}).get(kind)
    if name:return {'status':'identified','name':name,'category':_family(name)}
    edge=_EDGES.get(tileset,{}).get(kind)
    if edge:
        names=edge['endpoint_palette_names'];families={_family(n) for n in names}
        category=next((f+'_boundary' for f in ('water','space','pit','lava_or_magma','tar','ice') if f in families),None)
        if category is None:
            category='height_boundary' if any(n.startswith('High ') for n in names) and not all(n.startswith('High ') for n in names) else 'material_boundary'
        return {'status':'transition','name':' / '.join(names)+' edge','category':category,
                'between':names,'basis':'editor_adjacency_family','exact_edge_geometry':'not_classified'}
    return {'status':'unknown','reason':'unmapped_editor_type'}

def attach_unit_terrain(fields,terrain):
    """Match the same native snapshot's actual centre, not its hit-test anchor."""
    if len(fields)==17:return {}
    x,y,domain=fields[17:20]
    result={'movement_domain':'air' if domain else 'ground','ground_elevation':None}
    if x<0 or y<0:
        result['terrain_sample_status']='centre_not_currently_observed'
        return result
    p={'x':x,'y':y};result['terrain_sample_position']=p
    cell=next((c for c in terrain['cells'] if c['x']==x//32*32 and c['y']==y//32*32),None)
    if cell is None or cell['ground_height_8px'] is None:
        result['terrain_sample_status']='ground_geometry_unknown'
        return result
    row,col=(y%32)//8,(x%32)//8
    level=cell['ground_height_8px'][row][col]
    if level is None:
        result['terrain_sample_status']='ground_geometry_unknown'
        return result
    elevation={'level':level,'type':HEIGHT_NAMES[level]}
    result.update(terrain_sample_status='observed',terrain=cell['terrain'],
                  terrain_ground_walkable=cell['walkable_8px'][row][col])
    if domain:
        result['underlying_ground_elevation']=elevation
    else:
        result['ground_elevation']=elevation
    if cell.get('ramp_marked_8px') is not None:
        result['ramp_marker_at_position']=cell['ramp_marked_8px'][row][col]
        result['terrain_contains_ramp_marker']=cell['contains_ramp_marker']
    return result
