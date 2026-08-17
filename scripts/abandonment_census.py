import os,re,sys,json,time,subprocess,collections
ROOT="/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)"
RAGD=os.path.join(ROOT,"RAG"); WT=os.path.join(ROOT,"GIT WORKTREES","rag-runtime-kernel")
SKIPDIR=(".git","__pycache__","node_modules",".pytest_cache",".venv","states")
TEXT=(".md",".py",".json",".jsonl",".txt",".sh",".ps1",".yml",".yaml",".cfg",".tla",".html",".toml",".ini",".out")
files=[]
for base,dirs,fns in os.walk(ROOT):
    dirs[:]=[d for d in dirs if d not in SKIPDIR]
    for fn in fns:
        p=os.path.join(base,fn); r=os.path.relpath(p,ROOT).replace(os.sep,"/")
        try: st=os.stat(p)
        except OSError: continue
        files.append((r,p,st.st_size,st.st_mtime))
def role(r):
    b=os.path.basename(r); d=r.replace(os.sep,"/")
    if "/tests/" in d and b.startswith("test_") and b.endswith(".py"): return "TEST(pytest-discovered)"
    if b in ("__init__.py","__main__.py","conftest.py","setup.py"): return "ENTRYPOINT"
    if d.endswith(("_proof.out","_naive.out")) or "/.boot/" in d: return "GENERATED-ARTIFACT"
    if b.endswith((".bak",".jsonl")): return "GENERATED-ARTIFACT"
    if d.startswith("RAG/AUDIT_") or d.startswith("RAG/session_log"): return "GENERATED-ARTIFACT"
    if d.endswith((".tla",".cfg")): return "FORMAL-SPEC"
    if b.endswith(".md"): return "DOC"
    if b.endswith(".py"): return "SOURCE"
    return "DATA"
corpus={}
GEN=("ABANDONMENT_CENSUS","AUDIT_GRAND","AUDIT_S189_FULL","BOOTMAP_MANIFEST","session_log_","/.boot/","_proof.out","_naive.out",".bak")
for r,p,s,m in files:
    if any(g in r for g in GEN): continue
    if os.path.splitext(r)[1].lower() in TEXT and s < 6_000_000:
        try: corpus[r]=open(p,encoding="utf-8",errors="replace").read()
        except OSError: pass
now=time.time(); rows=[]
for r,p,s,m in files:
    b=os.path.basename(r); stem=os.path.splitext(b)[0]
    refs=0
    for q,txt in corpus.items():
        if q==r: continue
        if b in txt or (len(stem)>7 and stem in txt): refs+=1
    rows.append((r,s,(now-m)/86400.0,refs,role(r)))
REACHED_BY_CONVENTION=("TEST(pytest-discovered)","ENTRYPOINT","GENERATED-ARTIFACT","FORMAL-SPEC")
aband=[x for x in rows if x[3]==0 and x[4] not in REACHED_BY_CONVENTION]
byrole=collections.Counter(x[4] for x in rows)
print("files:",len(rows)," corpus(excl generated):",len(corpus)," roles:",dict(byrole))
print("GENUINELY ABANDONED:",len(aband))
for x in sorted(aband,key=lambda y:-y[2]): print("   ",x[4],"|",x[0],"| %.0fd"%x[2])
o=[]
o.append("# ABANDONMENT CENSUS - S189 (role-aware)")
o.append("")
o.append("Reachability, not existence. A file is CONNECTED if another file names it")
o.append("OR it is reached by a documented convention (pytest discovery, package")
o.append("entrypoint, generated artifact, TLC spec). Naive name-matching alone")
o.append("flagged 60 live test files as abandoned; role classification removes that.")
o.append("")
o.append("| role | count |")
o.append("|---|---|")
for k,v in sorted(byrole.items()): o.append("| %s | %d |"%(k,v))
o.append("")
o.append("## GENUINELY ABANDONED: %d"%len(aband))
o.append("")
if aband:
    o.append("| file | role | KB | age (days) |")
    o.append("|---|---|---|---|")
    for r,s,age,refs,rl in sorted(aband,key=lambda y:-y[2]):
        o.append("| `%s` | %s | %.0f | %.0f |"%(r,rl,s/1024.0,age))
else:
    o.append("None. Every non-generated file is named by something else.")
open(os.path.join(RAGD,"ABANDONMENT_CENSUS_S189.md"),"w",encoding="utf-8").write(chr(10).join(o)+chr(10))
print("CENSUS2_WRITTEN")
