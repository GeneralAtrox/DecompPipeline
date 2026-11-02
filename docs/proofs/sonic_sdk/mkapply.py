"""Pseudo workspaces for xproject's names route: OLD holds name and interface facts at the matched addresses, NEW holds a game's catalog, code ranges and import.json.
usage: python -I mkapply.py WORKDIR TAG=WORKSPACE [TAG=WORKSPACE ...]; WORKDIR holds data/protos.json and data/m_TAG.json.
Nearest existing: xproject.py names - takes a workspace as its source, so this builds one holding only name facts."""
import json,pathlib,shutil,sys,collections
S=pathlib.Path(sys.argv[1]);OUT=S/'apply'
G=dict(x.split('=',1) for x in sys.argv[2:])  # game tag=its workspace folder
P=json.load(open(S/'data/protos.json'))
rep={}
for g,ws in G.items():
    ws=pathlib.Path(ws);m=json.load(open(S/f'data/m_{g}.json',encoding='utf-8'))
    c=[r for r in m['exact'] if not r.get('status') and r.get('prior')=='unnamed']
    ok=[r for r in c if r['name'] in P['protos']]
    d=OUT/g;new=d/'new';old=d/'old'
    for x in (new,old):
        (x/'state').mkdir(parents=True,exist_ok=True);(x/'readable/names').mkdir(parents=True,exist_ok=True)
    proj=json.load(open(ws/'project.json',encoding='utf-8'))
    shutil.copy((ws/proj['binary']).resolve(),d/'image.bin')
    for x in (new,old):
        for f in ('catalog.json','code_ranges.txt'): shutil.copy(ws/'state'/f,x/'state'/f)
    pn=dict(proj);pn['binary']='../image.bin'
    (new/'project.json').write_text(json.dumps(pn,indent=1))
    po=dict(pn)
    for k in ('game_ranges','game_end','extra_functions'):
        po.pop(k,None)
    po['game_ranges']=[]
    (old/'project.json').write_text(json.dumps(po,indent=1))
    (old/'readable/names/sonic_sdk.json').write_text(json.dumps({'functions':{r['addr']:{'proto':P['protos'][r['name']]} for r in ok},'globals':{}},indent=1))
    (new/'state/import.json').write_text(json.dumps({'from':str(old),'matches':{r['addr']:{'from':r['addr'],'kind':'exact','ratio':1.0,'pairs':1} for r in ok},'address_map':{r['addr']:r['addr'] for r in ok}},indent=1))
    rep[g]=dict(clean_new=len(c),with_interface=len(ok),no_interface=[r['name'] for r in c if r['name'] not in P['protos']],near_refused=len(m['near']),near_unnamed_refused=sum(r.get('prior')=='unnamed' for r in m['near']),exact_refused=sum(bool(r.get('status')) for r in m['exact']),project_keys=[k for k in proj if 'range' in k or 'end' in k])
    print(g,rep[g])
json.dump(rep,open(S/'data/apply_report.json','w'),indent=1)
