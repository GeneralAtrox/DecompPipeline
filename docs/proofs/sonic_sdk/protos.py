"""Interface facts only: for each candidate name, its parameter and return kinds from the SDK source, in basic types.
Parameter names are not kept. usage: python -I protos.py REPO CANDS.json OUT.json
Nearest existing: tools/sdk_fid.py - gives names, not parameter and return kinds."""
import json,pathlib,re,sys
repo=pathlib.Path(sys.argv[1]);c=json.load(open(sys.argv[2]))
M={'void':'void','Sint32':'s32','Uint32':'u32','Sint16':'s16','Uint16':'u16','Sint8':'s8','Uint8':'u8','Float':'f32','float':'f32','double':'f32','int':'s32','long':'s32','short':'s16','char':'s8','Bool':'s32','Void':'void','SYE_CBL':'s32',
 'unsigned':'u32','unsigned int':'u32','unsigned long':'u32','unsigned short':'u16','unsigned char':'u8','signed int':'s32','signed char':'s8','size_t':'u32','KMSTATUS':'s32','KMBOOL':'s32','KMFLOAT':'f32','KMDWORD':'u32','KMUINT32':'u32','KMINT32':'s32','GDFS':'void *','SY_CHAIN':'u32'}
def typ(t):
    t=re.sub(r'\b(const|volatile|register|static|extern|inline|__far|near|far)\b','',t).strip()
    if '*' in t or t.endswith(']'): return 'void *'
    t=' '.join(t.split())
    if t in M: return M[t]
    if re.match(r'^P[A-Z]',t) or t.endswith('_FUNC') or t.endswith('FUNC') or t.endswith('Func'): return 'void *'
    if re.fullmatch(r'[A-Z][A-Z0-9_]*(_E|E)?',t) or t.startswith('enum '): return 's32'
    return None
def parse(ret,args,name):
    r=typ(ret)
    if r is None: return None
    args=args.strip();ps=[]
    if args and args!='void':
        if '...' in args or '(' in args: return None
        for i,a in enumerate(args.split(',')):
            a=a.strip()
            m=re.match(r'^(.*?)(\b\w+)(\s*\[[^\]]*\])?$',a)
            if not m: return None
            head,nm,arr=m.groups()
            if not head.strip(): head,arr=a,None
            k=typ(head+('*' if arr else ''))
            if k is None or k=='void': return None
            ps.append(f"{k}{' ' if not k.endswith('*') else ''}p{i+1}")
    return f"{r}{' ' if not r.endswith('*') else ''}{name}({', '.join(ps) or 'void'})"
hdr=''.join(p.read_text(encoding='latin-1') for p in (repo/'sdk/include').glob('*.h'))
out={};no={}
for name,src in c.items():
    text=''
    p=repo/(src+'.c')
    if p.exists(): text=p.read_text(encoding='latin-1')
    got=None
    for blob in (text,hdr):
        t=re.sub(r'/\*.*?\*/','',blob,flags=re.S)
        for nm in (name,'_'+name):
            m=re.search(rf'^[ \t]*((?:[\w\*]+[ \t\*]+)+?)\**{re.escape(nm)}[ \t]*\(([^(){{}};]*)\)',t,re.M)
            if m and not m.group(1).strip().startswith(('return','else')):
                got=parse(m.group(1),m.group(2),name);break
        if got: break
    if got: out[name]=got
    else: no[name]=src
json.dump({'protos':out,'none':no},open(sys.argv[3],'w'),indent=0)
print(len(out),'with',len(no),'without',list(no)[:40])
