"""Old against sq_fast + game_o2, every frame and the sound, over every recording and test script. Resumes from results.jsonl; stops at the first difference.
Nearest existing: port/tools/regress.py compares against goldens at a few chosen frames, not two builds at every frame."""
import pathlib, subprocess, shutil, json, hashlib, struct, time, concurrent.futures as cf, sys
T=pathlib.Path(sys.argv[1]); S=T/'port_sm'; R=T/'replays'; P=T/'proof'; P.mkdir(exist_ok=True)  # the scratch folder: port_o1.exe and port_n.exe in port_sm, recordings in replays, the disc in disc
CUE=T/'disc/Toy Commander (Europe) (En,Fr,De,Es).cue'; RES=P/'results.jsonl'; START=time.time(); BUDGET=3300
def pad(f):
    b=f.read_bytes(); hl=struct.unpack('<I',b[8:12])[0]; at=12+hl; n=struct.unpack('<I',b[at:at+4])[0]; return b[at+4:at+4+n]
def vb(f): return struct.unpack('<I',f.read_bytes()[-4:])[0]
recs=sorted(R.glob('*.ptrec'),key=vb,reverse=True); keep=[]; skipped=[]
for f in recs:
    p=pad(f); o=next((k for k in keep if pad(k).startswith(p)),None)
    if o: skipped.append((f.name,o.name))
    else: keep.append(f)
print('prefix of a longer recording, not run:',skipped,flush=True)
INP={'frontend':(7000,'START@900,START@1200,A@1500,A@1800,A@2100,A@2400,A@2700,A@3000,DOWN@3400+4,DOWN@3460+4,A@3550+4,B@4000+4,UP@4100+4,A@4200+4,B@4500+4,DOWN@4600+4,DOWN@4660+4,A@4800+4,B@5100+4,B@5300+4',[]),
 'mission1':(13000,'START@900,START@1200,A@1500,A@1800,A@2100,A@2400,A@2700,A@3000,A@3600,A@3900,A@4200,A@4800,A@5400,UP@10250+300,A@10400+5,A@10450+5,START@11000,DOWN@11200+4,DOWN@11260+4,DOWN@11320+4,DOWN@11380+4,A@11500+4,LEFT@11700+4,A@11800+4',[]),
 'novmu':(5000,'START@900,START@1200,A@1500,A@1800,A@2100,A@2400,A@2700,A@3000',['--vmu','none']),
 'attract_long':(30000,None,[])}
jobs=[('rec_'+f.stem,vb(f),['--replay',str(f)]) for f in keep]
for n,(stop,i,x) in INP.items(): jobs.append(('script_'+n,stop,(['--input',i] if i else [])+x))
print('jobs',[(a,b) for a,b,_ in jobs],flush=True)
done={json.loads(l)['job'] for l in RES.read_text().splitlines()} if RES.exists() else set()
def run(name,exe,yuv,stop,extra):
    O=P/name; shutil.rmtree(O,ignore_errors=True); O.mkdir(parents=True)
    cmd=[str(S/f'{exe}.exe'),'--disc',str(CUE),'--log',str(O/'port.log'),'--data-directory',str(O/'data'),'--dump-dir',str(O),'--headless','--fixed-clock','1','--seconds','1500','--size','640x480','--record','off','--present','tick','--stop-at',str(stop),'--pvr-yuv',yuv,'--dump-every','1','--audio-dump','a.wav']+extra
    t=time.time(); c=subprocess.run(cmd,cwd=O,stdin=subprocess.DEVNULL,stdout=open(O/'out.txt','w'),stderr=subprocess.STDOUT,timeout=1600).returncode
    fr={}
    for p in O.rglob('frame*'):
        if p.is_file(): fr[p.name]=hashlib.blake2b(p.read_bytes(),digest_size=12).hexdigest(); p.unlink()
    w=O/'a.wav'; wh=hashlib.blake2b(w.read_bytes(),digest_size=12).hexdigest() if w.exists() else None; wn=w.stat().st_size if w.exists() else 0
    log=(O/'port.log').read_text(errors='replace').splitlines()
    ex=[l for l in log if l.startswith('EXIT')]; oos=[l[:160] for l in log if 'out of step' in l or 'FAULT' in l or 'halt' in l.lower()][:4]
    if w.exists(): w.unlink()
    return dict(exit=c,frames=fr,wav=wh,wavn=wn,exitline=ex[-1:] ,oos=oos,secs=round(time.time()-t))
for name,stop,extra in jobs:
    if name in done: continue
    if time.time()-START>BUDGET-400: print('budget reached, run again to go on',flush=True); sys.exit(5)
    with cf.ThreadPoolExecutor(2) as ex:
        a=ex.submit(run,name+'_old','port_o1','table',stop,extra); b=ex.submit(run,name+'_new','port_n','simd',stop,extra); a,b=a.result(),b.result()
    fa,fb=a.pop('frames'),b.pop('frames'); common=sorted(set(fa)&set(fb)); diff=[k for k in common if fa[k]!=fb[k]]
    r=dict(job=name,stop=stop,frames_old=len(fa),frames_new=len(fb),distinct=len(set(fa.values())),differ=len(diff),first=diff[:3],wav_same=a['wav']==b['wav'],old=a,new=b)
    with open(RES,'a') as h: h.write(json.dumps(r)+chr(10))
    print(name,'stop',stop,'frames',len(fa),len(fb),'distinct',r['distinct'],'differ',len(diff),'wav',a['wavn'],r['wav_same'],'exit',a['exit'],b['exit'],a['exitline'],a['oos'][:1],'secs',a['secs'],b['secs'],flush=True)
    if diff or len(fa)!=len(fb) or not r['wav_same'] or a['exit']!=b['exit'] or not fa:
        print('DIFFERENCE: stopped; folders kept',name,flush=True); sys.exit(4)
    shutil.rmtree(P/(name+'_old'),ignore_errors=True); shutil.rmtree(P/(name+'_new'),ignore_errors=True)
print('ALL DONE',flush=True)
