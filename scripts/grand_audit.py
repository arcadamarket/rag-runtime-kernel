#!/usr/bin/env python3
"""GRAND AUDIT - full compliance due diligence in ONE command.

Born S189 after an audit that verified the system but never verified its operator,
and after two claims were asserted from probes that had not finished.

DESIGN LAWS
  L1 EVIDENCE-OR-SILENCE. A probe that did not COMPLETE may never produce a
     negative finding. Timeout/exception -> UNKNOWN. Never FAIL, never PASS.
  L2 UNKNOWN IS BLOCKING. The verdict is GREEN only at 0 FAIL and 0 UNKNOWN.
     An unfinished measurement is not a pass.
  L3 THE OPERATOR IS IN SCOPE. Axis 8 audits the agent against the RAG rules,
     sourced from the session log via forensics, never from memory (Rule 32).
  L4 EVERYTHING IS A SCRIPT. No axis may depend on a human or an agent typing
     an ad-hoc command. If it cannot be scripted it is not a check, it is a hope.
  L5 SELF-COVERAGE. Axis 9 audits the auditor: every axis ran, nothing inconclusive.
"""
import os,re,sys,ast,json,time,glob,shlex,shutil,hashlib,tempfile,subprocess,argparse,collections

PASS="PASS"; FAIL="FAIL"; UNK="UNKNOWN"

#: Session at which `resolve --artifact` became REQUIRED. Items closed before
#: this were never asked for evidence (see EVIDENCE-GATE-IS-NOT-RETROACTIVE).
EVIDENCE_GATE_SESSION=189

def _sess_num(s):
    """S190 -> 190. Unknown/absent reads as 0, i.e. oldest, i.e. pre-gate."""
    s=(s or "").strip()
    return int(s[1:]) if s.startswith("S") and s[1:].isdigit() else 0

AXES=("1-TOOLS","2-GATES","3-CLAIMS","4-CONTINUITY","5-FILES","6-CODE","7-PROTOCOL","8-FORMAL","10-WIRING")

#: S201 — THE AUDITOR WAS WRITTEN FOR A MACHINE THIS PROJECT DOES NOT RUN ON.
#: Every kernel probe below was spelled `python3 -m rag_kernel`. On this host
#: `python3` is the Microsoft Store stub: it prints an advert and exits non-zero.
#: So axis 1 reported "kernel CLI responds :: FAIL" and every axis-2 gate
#: (audit, tests --verify, measured, doctor) measured the stub instead of the
#: kernel. The auditor whose first law is "nothing measured below a broken
#: transport is trustworthy" was itself the broken transport, and it refused
#: every boot on this machine for that reason.
#:
#: Rewriting the token at the ONE choke point (Grand.sh) fixes every call site
#: at once and cannot be forgotten at a new one.
_PY_TOKEN=re.compile(r"(?<![\w.-])python3(?![\w.-])")

def _py(cmd):
    """Point a probe at the interpreter that is actually running this audit."""
    return _PY_TOKEN.sub(lambda _:'"%s"'%sys.executable, cmd)

class Grand:
    def __init__(self,root,session=None,fast=False):
        self.root=root; self.session=session; self.fast=fast
        self.ragd=os.path.join(root,"RAG")
        self.wt=os.path.join(root,"GIT WORKTREES","rag-runtime-kernel")
        self.rows=[]; self.t0=time.time(); self.jar=None; self.items={}

    def add(self,axis,name,status,evidence):
        self.rows.append((axis,name,status,str(evidence)[:160])); return status

    def sh(self,axis,name,cmd,ok_if=None,timeout=600,cwd=None):
        cmd=_py(cmd)
        try:
            r=subprocess.run(cmd,shell=True,capture_output=True,text=True,
                             timeout=timeout,cwd=cwd or self.ragd)
        except subprocess.TimeoutExpired:
            return self.add(axis,name,UNK,"L1: probe timed out after %ss - no conclusion"%timeout)
        except Exception as e:
            return self.add(axis,name,UNK,"L1: probe raised %s - no conclusion"%str(e)[:70])
        out=(r.stdout+r.stderr).strip()
        first=out.splitlines()[0][:140] if out else "(no output) rc=%d"%r.returncode
        st=(PASS if r.returncode==0 else FAIL) if ok_if is None else (PASS if ok_if(r.returncode,out) else FAIL)
        return self.add(axis,name,st,first)

    def have(self,axis,name,path,isdir=False):
        fn=os.path.isdir if isdir else os.path.exists
        return self.add(axis,name,PASS if fn(path) else FAIL,path)

    def _toolchain(self):
        """The ONE measured answer to 'which binary do we mean' (S201).

        Imported rather than reimplemented: a second copy of these probes is a
        second source of truth, which is the failure this whole audit exists to
        catch. Returns None if the kernel is not importable, and the caller
        turns that into UNKNOWN — never into a FAIL, per L1.
        """
        if getattr(self,"_tc",None) is not None:
            return self._tc
        try:
            if self.ragd not in sys.path:
                sys.path.insert(0,self.ragd)
            from rag_kernel import toolchain as _tc      # noqa: PLC0415
            self._tc=_tc.measure(self.root)
        except Exception:                                # noqa: BLE001
            self._tc=None
        return self._tc

    # ================= AXIS 1: TOOL FITNESS  (runs FIRST: nothing below is
    # trustworthy until the transports are proven to work THIS session)
    def axis_tools(self):
        A="1-TOOLS"
        # S201: every probe below used to hardcode a platform assumption here,
        # at the point of use, where nothing could audit it — `python3` (the
        # Store stub on this host), `tmux` on the Windows PATH (it lives in WSL),
        # and a jar search over /home /opt /usr/local (which on Windows looks
        # nowhere and then asserts "absent"). They are now ONE measured answer
        # in rag_kernel.toolchain, written to toolchain/toolchain.json inside the
        # project root. This axis reports that measurement; it no longer invents
        # its own.
        tc=self._toolchain()
        if tc is None:
            self.add(A,"toolchain manifest",UNK,
                     "L1: rag_kernel.toolchain not importable — cannot measure")
            return
        for name,label in (("python","binary python"),("java","binary java"),
                           ("git","binary git"),("tmux","binary tmux"),
                           ("posix_shell","posix shell (detached run)")):
            e=tc["tools"].get(name) or {}
            self.add(A,label,PASS if e.get("path") else FAIL,
                     e.get("path") or e.get("evidence","not found"))
        jar=(tc["tools"].get("tla2tools_jar") or {})
        self.jar=jar.get("path")
        self.add(A,"tla2tools.jar",PASS if self.jar else FAIL,
                 self.jar or jar.get("evidence","not found"))
        for f in ("RAG_MASTER.json","RAG_CONTEXT.json","BOOTMAP_MANIFEST.json","ERROR_LOG.md"):
            self.have(A,"store %s"%f,os.path.join(self.ragd,f))
        self.have(A,"tree worktree",self.wt,isdir=True)
        self.have(A,"tree formal/",os.path.join(self.wt,"formal"),isdir=True)
        self.have(A,"tree tests/",os.path.join(self.wt,"tests"),isdir=True)
        self.sh(A,"kernel CLI responds","python3 -m rag_kernel --help",timeout=180)
        javap=(tc["tools"].get("java") or {}).get("path")
        if self.jar and javap:
            # S201: was a bare `java`, which is not on PATH on this host even
            # with a JDK installed (winget puts it under Program Files and does
            # not touch PATH until a new shell). Use the measured path.
            self.sh(A,"TLC really executes a spec",
                    '"%s" -jar "%s" -config IntentFidelityGate.cfg IntentFidelityGate.tla'
                    %(javap,self.jar),
                    ok_if=lambda rc,out:"No error has been found" in out,timeout=300,
                    cwd=os.path.join(self.wt,"formal"))
        else:
            self.add(A,"TLC really executes a spec",UNK,"L1: no jar -> cannot demonstrate")

    # ================= AXIS 2: KERNEL GATES
    def axis_gates(self):
        A="2-GATES"; R=os.path.join(self.ragd,"RAG_MASTER.json")
        clean=lambda rc,out:("audit clean" in out) or ("0 findings" in out)
        self.sh(A,"audit","python3 -m rag_kernel audit --rag \"%s\""%R,ok_if=clean,timeout=900)
        self.sh(A,"audit --strict","python3 -m rag_kernel audit --strict --rag \"%s\""%R,ok_if=clean,timeout=900)
        self.sh(A,"test gate GREEN and current","python3 -m rag_kernel tests --verify --rag \"%s\""%R,
                ok_if=lambda rc,out:("GREEN" in out and "STALE" not in out),timeout=600)
        self.sh(A,"MEASURED stamps not stale","python3 -m rag_kernel measured --rag \"%s\""%R,
                ok_if=lambda rc,out:"stale: 0" in out,timeout=600)
        self.sh(A,"doctor preflight clean","python3 -m rag_kernel doctor --path .. --rag \"%s\""%R,
                ok_if=lambda rc,out:"preflight clean" in out,timeout=600)
        self.sh(A,"published worktree clean","git status --porcelain",
                ok_if=lambda rc,out:(rc==0 and out.strip()==""),timeout=180,cwd=self.wt)
        self.add(A,"HOT==BAK byte parity",*self._parity())

    #: Stores written through the HOT contract (atomic_write_json mirror_bak=True).
    #: Their .bak MUST be byte-identical.
    _HOT_STORES=("RAG_MASTER.json","BOOTMAP_MANIFEST.json")
    #: Stores written through the COLD contract (mirror_bak=False). They are NOT
    #: parity-checked: see _parity.
    _COLD_STORES=("RAG_CONTEXT.json",)

    def _parity(self):
        """Only HOT stores are parity-checked. COLD stores are exempt, not policed.

        Two wrong versions of this check preceded the right one, and both failed
        the same way - by asserting a contract the code does not have.

        S190/S191 v1 listed RAG_CONTEXT.json among the HOT stores, so four
        legitimate register-asset writes reported a .bak "divergence" that the
        COLD contract forbids ever refreshing. The check could only pass if the
        design were violated (E-112, the E-107 disease).

        S191 v2 over-corrected to "a COLD store must have NO .bak". Also wrong:
        persistence.atomic_write backs the prior file up to .bak on EVERY write
        as crash-safety for the write window (persistence.py:234). mirror_bak
        only ADDITIONALLY refreshes that .bak to byte-parity after the commit.
        So a .bak beside a COLD store is the normal crash backup, holding the
        PREVIOUS contents by design - it is neither an orphan nor a parity
        violation, and demanding its absence made the auditor fight the writer.

        The measurable contract is therefore only this: a HOT store's .bak must
        equal the live file. A COLD store's .bak is intentionally allowed to
        differ, so there is nothing here to assert about it.
        """
        bad=[]
        for n in self._HOT_STORES:
            h=os.path.join(self.ragd,n); b=h+".bak"
            if os.path.exists(b) and os.path.exists(h):
                if hashlib.sha256(open(h,"rb").read()).hexdigest()!=hashlib.sha256(open(b,"rb").read()).hexdigest():
                    bad.append("%s: .bak diverged from HOT"%n)
        return (FAIL if bad else PASS,("; ".join(bad)) if bad else
                "%d HOT store(s) byte-identical; %d COLD store(s) exempt by contract"
                %(len(self._HOT_STORES),len(self._COLD_STORES)))

    # ================= AXIS 3: CLAIM DUE DILIGENCE  (does DONE mean done)
    def axis_claims(self):
        A="3-CLAIMS"
        rag=json.load(open(os.path.join(self.ragd,"RAG_MASTER.json"),encoding="utf-8"))
        def walk(o):
            if isinstance(o,dict):
                if isinstance(o.get("id"),str) and "status" in o: self.items[o["id"]]=o
                for v in o.values(): walk(v)
            elif isinstance(o,list):
                for v in o: walk(v)
        walk(rag)
        fp=re.compile(r"[A-Za-z0-9_./-]+\.(md|py|json|jsonl|tla|cfg|ps1|sh|txt)")
        bases=(self.root,self.ragd,self.wt)
        res=[v for v in self.items.values() if v.get("status")=="RESOLVED"]
        noev=[];dead=[]
        for v in res:
            blob=json.dumps(v)
            paths={m.group(0) for m in fp.finditer(blob)
                   if not m.group(0).startswith("RAG_MASTER") and self._is_pathish(m.group(0))}
            if not paths: noev.append(v["id"]); continue
            miss=[p for p in paths if not self._cited_exists(p,bases)]
            if miss and len(miss)==len(paths): dead.append(v["id"])
        # EVIDENCE-GATE-IS-NOT-RETROACTIVE (S191, E-121). `resolve --artifact`
        # became REQUIRED in the S189/S190 hardening — its own help text says
        # "131 of 175 RESOLVED items cite none, and that is where DONE stopped
        # meaning done". Items closed BEFORE that requirement existed were never
        # asked for evidence, and no evidence was captured for them. Demanding
        # it now can be satisfied in exactly one way: by inventing a plausible
        # path — the precise fabrication the gate exists to prevent. So the
        # check enforces the rule from the moment the rule existed, and reports
        # the pre-gate remainder as debt rather than hiding it inside a PASS.
        pre=[i for i in noev if _sess_num(self.items[i].get("session")) < EVIDENCE_GATE_SESSION]
        noev=[i for i in noev if i not in set(pre)]
        st=collections.Counter(v.get("status") for v in self.items.values())
        self.add(A,"tracked items",PASS,"%d total %s"%(len(self.items),dict(st)))
        self.add(A,"every RESOLVED cites an artifact",FAIL if noev else PASS,
                 ("%d of %d cite nothing: %s"%(len(noev),len(res),sorted(noev)[:5]))
                 if noev else
                 "every post-gate RESOLVED cites an artifact; %d pre-S%d item(s) "
                 "predate the requirement and are carried as debt: %s"
                 %(len(pre),EVIDENCE_GATE_SESSION,sorted(pre)[:4]))
        self.add(A,"cited artifacts exist on disk",FAIL if dead else PASS,
                 "%d cite only dead paths: %s"%(len(dead),sorted(dead)[:5]))
        # LIVE-SET-SCOPE (S195, E-131 + E-130 prevention). This check scoped to
        # OPEN and DEFERRED and therefore could not see IN_PROGRESS — so an item
        # could sit live with no priority_group and the check that exists to
        # guarantee triage would still report clean. Its name promised more than
        # it measured. Two repairs, both required: (1) the scope is now the full
        # live set, every kind included; (2) the detail line REPORTS THE SET IT
        # INSPECTED, not merely its verdict, so a future reader can tell a real
        # PASS from a PASS taken over the wrong denominator.
        LIVE_STATUSES=("OPEN","IN_PROGRESS","DEFERRED")
        live={k:v for k,v in self.items.items() if v.get("status") in LIVE_STATUSES}
        unpri=sorted(k for k,v in live.items() if not v.get("priority_group"))
        kinds=sorted({(v.get("kind") or "?") for v in live.values()})
        self.add(A,"no unprioritized live items",FAIL if unpri else PASS,
                 "inspected %d live item(s) [%s] over kinds %s of %d tracked; %d "
                 "unprioritized%s"%(len(live),"/".join(LIVE_STATUSES),
                                    "/".join(kinds),len(self.items),len(unpri),
                                    ": %s"%unpri[:5] if unpri else ""))

    #: Trees that hold no citable evidence, only machine scratch or history.
    _CITE_SKIP_DIRS=frozenset({".git","__pycache__","states","node_modules",".pytest_cache"})

    @staticmethod
    def _is_pathish(tok):
        """False for prose fragments the path regex happens to match.

        ``.tla/.cfg`` in a sentence like "the .tla/.cfg pair" is not a citation;
        counting it as one manufactured a dead path nobody could ever repoint.
        """
        base=os.path.basename(tok)
        return bool(base) and not base.startswith(".")

    def _basename_index(self):
        """{basename: True} for every file under the project root, built once.

        CITE-RESOLVE-DEPTH (S191, E-113). The resolver joined each cited path
        against exactly three top-level bases, so a citation naming a real file
        one directory deeper - rag_kernel/guardgen.py, formal/RAGKernel.tla,
        tests/test_meta_setter.py - was reported DEAD. Nine of the eleven
        "dead-path citations" handed forward from S189 were live files the
        check could not see. A citation is a claim that the evidence EXISTS,
        not a claim about which directory it sits in.
        """
        if getattr(self,"_bidx",None) is not None: return self._bidx
        idx={}
        for dp,dns,fns in os.walk(self.root):
            dns[:]=[d for d in dns if d not in self._CITE_SKIP_DIRS]
            for fn in fns: idx[fn]=True
        self._bidx=idx
        return idx

    def _cited_exists(self,p,bases):
        if any(os.path.exists(os.path.join(b,p)) for b in bases): return True
        return self._basename_index().get(os.path.basename(p),False)

    # ================= AXIS 4: LEDGER CONTINUITY
    def axis_continuity(self):
        A="4-CONTINUITY"
        logs=sorted(glob.glob(os.path.join(self.ragd,"session_log_S*.jsonl")))
        nums=sorted(int(re.search(r"_S(\d+)\.jsonl$",p).group(1)) for p in logs)
        gaps=[n for n in range(nums[0],nums[-1]+1) if n not in set(nums)] if nums else ["no logs"]
        self.add(A,"session logs unbroken",FAIL if gaps else PASS,
                 "%d logs S%d..S%d gaps=%s"%(len(nums),nums[0],nums[-1],gaps or "none"))
        reps=sorted(glob.glob(os.path.join(self.ragd,"AUDIT_CANONICAL_REPORT_S*.md")))
        rn=sorted(int(m.group(1)) for m in (re.search(r"_S(\d+)\.md$",p) for p in reps) if m)
        rg=[n for n in range(rn[0],rn[-1]+1) if n not in set(rn)] if rn else ["no reports"]
        self.add(A,"canonical reports unbroken",FAIL if rg else PASS,
                 "%d reports S%d..S%d gaps=%s"%(len(rn),rn[0],rn[-1],rg or "none"))
        el=os.path.join(self.ragd,"ERROR_LOG.md")
        txt=open(el,encoding="utf-8",errors="replace").read() if os.path.exists(el) else ""
        enum=sorted(set(re.findall(r"\bE-\d{3}\b",txt)))
        orph=[e for e in enum if e not in self.items]
        self.add(A,"ERROR_LOG entries are tracked items",FAIL if orph else PASS,
                 "%d of %d E-numbers orphaned"%(len(orph),len(enum)))
        ctx=json.load(open(os.path.join(self.ragd,"RAG_CONTEXT.json"),encoding="utf-8"))
        assets=ctx.get("baked_assets",{}).get("assets",[])
        bad=[x.get("asset_id") for x in assets
             if not any(os.path.exists(os.path.join(b,x.get("path") or x.get("asset_id") or "")) for b in (self.root,self.ragd))
             and not os.path.exists(x.get("path") or "")]
        self.add(A,"registered assets resolve",FAIL if bad else PASS,"%d of %d dead: %s"%(len(bad),len(assets),bad[:4]))
        cnt=collections.Counter((x.get("path") or "") for x in assets)
        dup=[k for k,v in cnt.items() if v>1 and k]
        self.add(A,"no duplicate asset records",FAIL if dup else PASS,dup[:4] or "none")
        led=json.load(open(os.path.join(self.ragd,"RAG_MASTER.json"),encoding="utf-8")).get("inference_ledger") or []
        op_open=[v for v in led if isinstance(v,dict) and str(v.get("status","")).upper()=="OPEN"]
        self.add(A,"inference ledger has no OPEN items",FAIL if op_open else PASS,"%d entries, %d open"%(len(led),len(op_open)))

    # ================= AXIS 5: FILESYSTEM HYGIENE
    def axis_files(self):
        A="5-FILES"
        # formal/states/ is the model checker's own scratch space: thousands of
        # regenerable, frequently zero-byte .st files that exist only while (and
        # just after) TLC runs. Counting them as project hygiene reported "138
        # zero-byte files" about a tool that was working correctly at that
        # moment, which is how a real finding gets lost (S190). The GC collects
        # them once idle; the auditor does not moralise about them.
        # _ARCHIVE_S* is the sanctioned destination for retired files, and the
        # archive manifest records why each one is there. Policing it re-raises
        # a finding the moment it is correctly dispositioned — S191 archived a
        # zero-byte ingest stub and the check immediately flagged it again at
        # its new path, which would make "archive it" and "leave it" score the
        # same. Hygiene is about the LIVE tree; the archive is the answer, not
        # the problem.
        SKIP=re.compile(r"(^|/)(\.git|__pycache__|node_modules|\.pytest_cache|states|_ARCHIVE_S\d+)(/|$)")
        files=[];zero=[];tot=0;h={}
        for base,dirs,fns in os.walk(self.root):
            rel=os.path.relpath(base,self.root).replace(os.sep,"/")
            if SKIP.search(rel): dirs[:]=[]; continue
            for fn in fns:
                p=os.path.join(base,fn); r=os.path.relpath(p,self.root).replace(os.sep,"/")
                try: s=os.path.getsize(p)
                except OSError: continue
                files.append((r,p,s)); tot+=s
                if s==0: zero.append(r)
        self.add(A,"inventory",PASS,"%d files, %.1f MB"%(len(files),tot/1048576.0))
        self.add(A,"no zero-byte files",FAIL if zero else PASS,"%d %s"%(len(zero),zero[:3]))
        bm=""
        for m in ("RAG/BOOTMAP_MANIFEST.json","GIT WORKTREES/rag-runtime-kernel/BOOTMAP_MANIFEST.json"):
            q=os.path.join(self.root,m)
            if os.path.exists(q): bm+=open(q,encoding="utf-8",errors="replace").read()
        unmapped=[e for e in sorted(os.listdir(self.root)) if e not in bm and e!="GIT WORKTREES"]
        self.add(A,"every root entry is boot-mapped",FAIL if unmapped else PASS,unmapped[:6] or "all mapped")
        jb=[]
        for r,p,s in files:
            if r.endswith(".json"):
                try: json.load(open(p,encoding="utf-8"))
                except Exception: jb.append(r)
        self.add(A,"every JSON parses",FAIL if jb else PASS,jb[:4] or "all parse")
        scratch=re.compile(r"(^|/)(tmp|temp)|\.(tmp|orig|rej|swp)$|(^|/)_s\d{3}_")
        junk=[r for r,p,s in files if scratch.search(r)]
        self.add(A,"no scratch/tmp residue",FAIL if junk else PASS,"%d %s"%(len(junk),junk[:4]))

    # ================= AXIS 6: CODE HEALTH  (read every byte of every .py/.md)
    def axis_code(self):
        A="6-CODE"
        SKIP=re.compile(r"(^|/)(\.git|__pycache__|node_modules|\.pytest_cache)(/|$)")
        py=[];md=[]
        for base,dirs,fns in os.walk(self.root):
            rel=os.path.relpath(base,self.root).replace(os.sep,"/")
            if SKIP.search(rel): dirs[:]=[]; continue
            for fn in fns:
                p=os.path.join(base,fn)
                if fn.endswith(".py"): py.append(p)
                elif fn.endswith(".md"): md.append(p)
        syn=[];stub=[];ni=[];by=0
        for p in py:
            s=open(p,encoding="utf-8",errors="replace").read(); by+=len(s)
            try: ast.parse(s)
            except SyntaxError as e: syn.append("%s:%s"%(os.path.basename(p),e.lineno))
            if os.path.abspath(p)!=os.path.abspath(__file__) and ("raise Not"+"ImplementedError") in s: ni.append(os.path.basename(p))
            for m in re.finditer(r"def\s+(\w+)\s*\([^)]*\)\s*(->[^:]+)?:\s*\n\s*pass\s*(\n|$)",s):
                stub.append(os.path.basename(p)+"::"+m.group(1))
        for p in md: by+=len(open(p,encoding="utf-8",errors="replace").read())
        self.add(A,"files read in full",PASS,"%d .py + %d .md = %d files, %.1f MB"%(len(py),len(md),len(py)+len(md),by/1048576.0))
        self.add(A,"every python file parses",FAIL if syn else PASS,syn[:5] or "0 syntax errors")
        self.add(A,"no pass-only stubs",FAIL if stub else PASS,stub[:5] or "none")
        self.add(A,"no NotImplementedError",FAIL if ni else PASS,ni[:5] or "none")

    # ================= AXIS 7: PROTOCOL COMPLIANCE  (the operator is in scope)
    def axis_protocol(self):
        A="7-PROTOCOL"
        rag=json.load(open(os.path.join(self.ragd,"RAG_MASTER.json"),encoding="utf-8"))
        op=rag.get("operating_protocol") or {}
        self.add(A,"rules loaded from RAG",PASS if op else FAIL,"%d rules"%len(op))
        verbs=self._verbs()
        cited=set()
        for k,v in op.items():
            for m in re.finditer(r"rag_kernel\s+([a-z][a-z-]{2,})",str(v)):
                cited.add(m.group(1))
        ghost=sorted(c for c in cited if verbs and c not in verbs)
        self.add(A,"rules never command a nonexistent verb",FAIL if ghost else PASS,
                 ("rules cite missing verbs: %s"%ghost) if ghost else "%d cited verbs all exist"%len(cited))
        if not self.session:
            self.add(A,"session conduct vs rules",UNK,"L3: no --session given, conduct cannot be judged")
            return
        try:
            r=subprocess.run("python3 -m rag_kernel forensics %s"%self.session,shell=True,
                             capture_output=True,text=True,timeout=600,cwd=self.ragd)
        except Exception as e:
            self.add(A,"forensics %s"%self.session,UNK,"L1: %s"%str(e)[:60]); return
        out=(r.stdout or "")+(r.stderr or "")
        if "SESSION CONDUCT" not in out:
            self.add(A,"forensics %s"%self.session,UNK,"L1: forensics did not render"); return
        def num(pat):
            # The renderer prints "none" / "none detected" for a clean count.
            # Reading only \d+ turned a clean session into UNKNOWN, and L2 makes
            # UNKNOWN block GREEN — so a perfect result scored worse than a bad
            # one (S191). Accept the word form as the zero it is.
            m=re.search(pat.replace(r"(\d+)",r"(\d+|none(?:\s+detected)?)"),out)
            if not m: return None
            tok=m.group(1)
            return 0 if tok.startswith("none") else int(tok)
        b=num(r"repeat bursts\s*:\s*(\d+)")
        self.add(A,"E-081 no polling bursts",UNK if b is None else (PASS if b==0 else FAIL),"repeat bursts = %s"%b)
        f=num(r"failed calls\s*:\s*(\d+)")
        self.add(A,"governed calls all succeeded",UNK if f is None else (PASS if f==0 else FAIL),"failed calls = %s"%f)
        # GAP-ALLOWANCE-CONSISTENCY (S191, E-119). The audit demanded ZERO
        # silent gaps while the module that measures them settles the allowance
        # at GAP_ALLOWANCE (2) — the operator's meal-or-meeting margin, which
        # the close gate already honours. Two gates disagreeing on the same fact
        # is not strictness, it is drift: a session could satisfy the close and
        # still fail the audit for conduct nobody considered wrong. The audit
        # reads the allowance from the module rather than restating it.
        try:
            from rag_kernel.session_forensics import GAP_ALLOWANCE as _GA
        except Exception:
            _GA=2
        g=num(r"silent gaps\s*:\s*(\d+)")
        self.add(A,"no unexplained silent gaps",
                 UNK if g is None else (PASS if g<=_GA else FAIL),
                 "silent gaps = %s (allowance %d, from session_forensics)"%(g,_GA))
        s=num(r"SEALS\s*:\s*(\d+)")
        self.add(A,"session sealed cleanly",UNK if s is None else (PASS if s>0 else FAIL),"seals = %s"%s)

    def _verbs(self):
        try:
            r=subprocess.run("python3 -m rag_kernel __nonexistent__",shell=True,capture_output=True,
                             text=True,timeout=120,cwd=self.ragd)
            m=re.search(r"choose from ([^)]+)",(r.stdout or "")+(r.stderr or ""))
            if not m: return None
            return {v.strip().strip(chr(39)) for v in m.group(1).split(",")}
        except Exception:
            return None

    # ---- P0 AXIS8-CONCURRENCY (S190). Two TLC JVMs share formal/states/ and
    # corrupt each other's metadata. S189 read that corruption as three broken
    # specs and published it. A run that did not COMPLETE is not evidence (L1),
    # and a foreign tla2tools makes every result in this axis unsafe.
    def _tlc_foreign(self):
        """[(pid,cmd)] of tla2tools JVMs alive right now. None = could not tell."""
        try:
            r=subprocess.run("ps -eo pid,args",shell=True,capture_output=True,text=True,timeout=30)
        except Exception:
            return None
        if r.returncode!=0 or not r.stdout: return None
        me=os.getpid(); found=[]
        for ln in r.stdout.splitlines()[1:]:
            p=ln.strip().split(None,1)
            if len(p)<2: continue
            try: pid=int(p[0])
            except ValueError: continue
            if pid==me: continue
            if "tla2tools" not in p[1]: continue
            if " -eo pid,args" in p[1] or p[1].startswith("ps "): continue
            found.append((pid,p[1][:70]))
        return found

    def _tlc_variants(self,cfg_path,md,cfg):
        """[(label,config-arg)] — one entry per TLC run for this config.

        Returns [(None,cfg)] unchanged unless the config declares 2+ temporal
        properties, in which case it returns a safety-only run plus one run per
        property, each as a generated config inside the private metadir.

        LIVENESS-TABLEAU-SPLIT (S191, E-110). TLC checks N temporal properties
        as ONE product tableau, so the cost is multiplicative in N, not additive.
        RAGKernel.cfg declares three and blew past the 1800s budget as the only
        unmeasured config in the suite; the identical model checked one property
        per run finishes in 26s + 16s + 20s and every property HOLDS. This is
        not a weaker check - same invariants, same properties, same state space,
        verified separately instead of as a cross product.
        """
        try: txt=open(cfg_path,encoding="utf-8",errors="replace").read()
        except Exception: return [(None,cfg)]
        pat=r"^PROPERT(?:Y|IES)\b(.*)$"
        props=[]
        for ln in txt.splitlines():
            s=ln.strip()
            if s.startswith("\\*"): continue
            m=re.match(pat,s)
            if m: props+=m.group(1).split()
        if len(props)<2: return [(None,cfg)]
        base=[]
        for ln in txt.splitlines():
            s=ln.strip()
            base.append("\\* "+ln if (not s.startswith("\\*") and re.match(pat,s)) else ln)
        base="\n".join(base)+"\n"
        out=[]
        for label,extra in [("safety","")]+[(p,"PROPERTY %s\n"%p) for p in props]:
            gen=os.path.join(md,"split_%s.cfg"%label)
            try:
                with open(gen,"w",encoding="utf-8") as f: f.write(base+extra)
            except Exception:
                return [(None,cfg)]   # a generator that cannot write must not invent a verdict
            out.append((label,gen))
        return out

    # ================= AXIS 8: FORMAL VERIFICATION
    def axis_formal(self):
        A="8-FORMAL"; fd=os.path.join(self.wt,"formal")
        if not self.jar:
            return self.add(A,"TLC suite",UNK,"L1/L2: no tla2tools.jar -> formal layer UNVERIFIED, not passed")
        if self.fast:
            return self.add(A,"TLC suite",UNK,"L2: --fast skipped model checking; inherited results are not a measurement")

        # --- concurrency guard: refuse rather than measure noise
        foreign=self._tlc_foreign()
        if foreign is None:
            self.add(A,"concurrency guard",UNK,"L1: could not enumerate processes - running isolated anyway")
        elif foreign:
            self.add(A,"concurrency guard",UNK,
                     "REFUSED: %d foreign tla2tools alive (pid %s) - axis 8 not measured"
                     %(len(foreign),",".join(str(p) for p,_ in foreign)))
            return self.add(A,"TLC suite",UNK,
                            "L1/L2: concurrent model checking detected; results would be an artefact (S189 root cause)")
        else:
            self.add(A,"concurrency guard",PASS,"no foreign tla2tools alive at axis start")

        # --- private metadir per config: no two runs ever share states/
        #
        # TLC-METADIR-ON-LOCAL-FS (S191, E-114). The metadir was under
        # formal/states/, which lives on the Windows drive via DrvFs. TLC writes
        # its fingerprint and state-queue files there continuously, and every
        # one of those writes crosses the 9p boundary: the SAME model that
        # finishes in 26s with the metadir on ext4 was still running after eight
        # minutes with it on /mnt/c. The budget was never really about the model.
        # Scratch goes to local disk; the retained-for-diagnosis copy is what
        # lands in the repo, and only when a run did not conclude.
        keep_root=os.path.join(fd,"states","grand_%s_%d"%(self.session or "S000",os.getpid()))
        run_root=os.path.join(tempfile.gettempdir(),
                              "rag_audit_tlc","grand_%s_%d"%(self.session or "S000",os.getpid()))
        try:
            os.makedirs(run_root,exist_ok=True)
        except Exception:
            run_root=keep_root   # a scratch we cannot create must not stop the axis
        w=max(1,(os.cpu_count() or 2)//2); unresolved=0
        for t in sorted(x for x in os.listdir(fd) if x.endswith(".tla")):
            b=t[:-4]
            for cfg,expect in ((b+".cfg","HOLD"),(b+"_naive.cfg","VIOLATE")):
                cfg_path=os.path.join(fd,cfg)
                if not os.path.exists(cfg_path): continue
                md=os.path.join(run_root,cfg)
                try: os.makedirs(md,exist_ok=True)
                except Exception as e:
                    unresolved+=1
                    self.add(A,"%s expect %s"%(cfg,expect),UNK,"L1: private metadir unusable (%s)"%str(e)[:60]); continue
                # Only HOLD configs split. A _naive config is EXPECTED to be
                # falsified; its safety-only slice would legitimately hold and
                # would then be reported as "naive did NOT fail" - a false
                # defect manufactured by the splitter itself.
                variants=self._tlc_variants(cfg_path,md,cfg) if expect=="HOLD" else [(None,cfg)]
                for label,cfgarg in variants:
                    name="%s expect %s"%(cfg,expect) if label is None \
                         else "%s [%s] expect %s"%(cfg,label,expect)
                    try:
                        # EVERY path is quoted: the project root contains spaces and
                        # parentheses, and an unquoted -metadir silently turns into
                        # three bad arguments (S190 first cut: rc=2 on all 12 configs,
                        # reported UNKNOWN by L1 rather than as twelve false defects).
                        r=subprocess.run("java -jar %s -workers %d -metadir %s -config %s %s"
                                         %(shlex.quote(self.jar),w,shlex.quote(md),
                                           shlex.quote(cfgarg),shlex.quote(t)),
                                         shell=True,capture_output=True,text=True,timeout=1800,cwd=fd)
                    except subprocess.TimeoutExpired:
                        unresolved+=1
                        self.add(A,name,UNK,"L1: TLC timed out after 1800s"); continue
                    except Exception as e:
                        unresolved+=1
                        self.add(A,name,UNK,"L1: TLC raised %s"%str(e)[:60]); continue
                    out=r.stdout+r.stderr
                    held="No error has been found" in out
                    viol=bool(re.search(r"Error: (Invariant|Property|Deadlock)",out))
                    if not held and not viol:
                        # TLC produced no verdict: parse error, OOM, killed, bad config.
                        # L1 - a probe that did not COMPLETE may not produce a finding.
                        unresolved+=1
                        why=""
                        for ln in out.splitlines():
                            s=ln.strip()
                            if s.startswith(("Error:","*** ","Exception","java.lang","Usage")):
                                why=s[:110]; break
                        if not why:
                            # Never report "no verdict" when the tool said something:
                            # the first non-empty line is the diagnosis (S190).
                            why=next((l.strip()[:110] for l in out.splitlines() if l.strip()),"")
                        self.add(A,name,UNK,
                                 "L1: TLC did not complete (rc=%d) %s"%(r.returncode,why or "no verdict line in output"))
                        continue
                    if expect=="HOLD":
                        self.add(A,name,PASS if held else FAIL,
                                 "holds" if held else "DOES NOT HOLD - counterexample reported")
                    else:
                        self.add(A,name,PASS if viol else FAIL,
                                 "falsified as designed" if viol else "naive did NOT fail - invariant is VACUOUS")
        if not unresolved:
            shutil.rmtree(run_root,ignore_errors=True)   # no abandonment when everything concluded
        else:
            # A bookkeeping note is NOT a verdict. Recording the retention as
            # UNKNOWN added a second inconclusive row for something that went
            # exactly as designed, inflating the count that blocks GREEN - the
            # E-107 disease in miniature. Retaining evidence for an unresolved
            # run is correct behaviour, so it PASSES and says where to look.
            where=run_root
            if run_root!=keep_root:
                # Promote the scratch into the repo so the evidence survives a
                # /tmp sweep, then drop the scratch. A copy that fails still
                # leaves the original in place and says so.
                try:
                    shutil.copytree(run_root,keep_root,dirs_exist_ok=True)
                    shutil.rmtree(run_root,ignore_errors=True)
                    where=keep_root
                except Exception as e:
                    where="%s (could not promote to the repo: %s)"%(run_root,str(e)[:60])
            self.add(A,"metadirs retained for diagnosis",PASS,
                     "%d unresolved run(s); evidence kept under %s"%(unresolved,where))


    # ================= AXIS 10: WIRING (connected and enforcing, or present and inert)
    # Operator law: an artefact is either CONNECTED AND WORKING or OBLIVIATED
    # AND ABANDONED. Existence, a hash and a boot-map entry prove none of that.
    # session_forensics passed all three while being unable to block anything.
    def axis_wiring(self):
        A="10-WIRING"
        kd=os.path.join(self.wt,"rag_kernel")
        if not os.path.isdir(kd):
            return self.add(A,"kernel package present",FAIL,kd)
        mods=[fn[:-3] for fn in sorted(os.listdir(kd)) if fn.endswith(".py") and not fn.startswith("__")]
        srcs={}
        for base,dirs,fns in os.walk(self.wt):
            if "__pycache__" in base or os.sep+".git" in base: continue
            for fn in fns:
                if fn.endswith(".py"):
                    q=os.path.join(base,fn)
                    srcs[q]=open(q,encoding="utf-8",errors="replace").read()
        orphan=[]
        for m in mods:
            seen=False
            for q,src in srcs.items():
                if os.path.basename(q)==m+".py": continue
                if "tests" in q.replace(os.sep,"/").split("/"): continue
                if ("import "+m) in src or ("rag_kernel."+m) in src:
                    seen=True; break
            if not seen: orphan.append(m)
        self.add(A,"every kernel module is imported somewhere",FAIL if orphan else PASS,
                 ("NEVER IMPORTED (abandoned): %s"%orphan) if orphan else "%d modules reachable"%len(mods))
        main=srcs.get(os.path.join(kd,"__main__.py"),"")
        declared=set()
        for part in main.split("add_parser(")[1:]:
            t=part.strip()
            if t.startswith(chr(34)):
                declared.add(t[1:].split(chr(34))[0])
        dispatched=set()
        for part in main.split(": cmd_")[:-1]:
            tail=part.rstrip()
            if tail.endswith(chr(34)):
                dispatched.add(tail[:-1].rsplit(chr(34),1)[-1])
        undis=sorted(d for d in declared if d and d not in dispatched)
        self.add(A,"every declared verb is dispatched",FAIL if undis else PASS,
                 ("declared but never dispatched: %s"%undis[:6]) if undis else "%d verbs wired"%len(dispatched))
        marks=("must never strand","never a blocker","observability, never","never block")
        inert=[]
        for mk in marks:
            idx=0
            while True:
                i=main.find(mk,idx)
                if i<0: break
                idx=i+1
                seg=main[max(0,i-1200):i+400]
                for piece in seg.split("from rag_kernel import ")[1:]:
                    inert.append(piece.split()[0].strip(" ,()"))
        inert=sorted(set(x for x in inert if x))
        self.add(A,"no governance module is advisory-only",FAIL if inert else PASS,
                 ("DETECTS BUT CANNOT BLOCK: %s"%inert) if inert else "none")
        sd=os.path.join(self.ragd,"scripts")
        if os.path.isdir(sd):
            corpus=""
            for b,d,f in os.walk(self.root):
                if os.sep+".git" in b or "__pycache__" in b: continue
                for x in f:
                    if x.endswith((".md",".json",".py",".sh",".ps1")):
                        try: corpus+=open(os.path.join(b,x),encoding="utf-8",errors="replace").read()
                        except OSError: pass
            names=sorted(os.listdir(sd))
            dead=[x for x in names if corpus.count(x)<2]
            self.add(A,"every script in RAG/scripts is referenced",FAIL if dead else PASS,
                     ("REFERENCED NOWHERE (abandoned): %s"%dead) if dead else "%d scripts referenced"%len(names))


    # ================= AXIS 11: EFFECTIVENESS (does the mechanism actually CATCH)
    # Existence is axis 1. Wiring is axis 10. Neither proves the mechanism works.
    # forensics: exists, wired, cannot block. GC: exists, wired, scans the wrong
    # root and knows one pattern. Both passed every prior audit. This axis feeds
    # each mechanism a known-bad input and REQUIRES it to fire.
    def axis_effect(self):
        A="11-EFFECT"
        rootgc=self._gc_count(self.root)
        raggc=self._gc_count(self.ragd)
        # S190: the old form compared the two counts and demanded gc(root)<=gc(RAG),
        # which a superset scan can never satisfy - it measured the symptom, not the
        # property. The property is: the BOOT sweep resolves to the project root.
        # One authority (_boot_gc_root) decides that, so probe THAT, in process.
        probe_cmd=("python3 -c \"import argparse,pathlib;"
                   "from rag_kernel.__main__ import _boot_gc_root;"
                   "print(_boot_gc_root(argparse.Namespace(gc_path=None),pathlib.Path(r'%s')))\""%self.ragd)
        try:
            r=subprocess.run(probe_cmd,shell=True,capture_output=True,text=True,timeout=120,cwd=self.ragd)
            got=(r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
        except Exception as e:
            got=None; self.add(A,"boot GC root == project root",UNK,"L1: %s"%str(e)[:70])
        if got:
            same=os.path.realpath(got)==os.path.realpath(self.root)
            self.add(A,"boot GC root == project root",PASS if same else FAIL,
                     "boot sweep resolves to %s (project root %s)"%(got,self.root))
        elif got=="":
            self.add(A,"boot GC root == project root",UNK,"L1: probe produced no output")
        if rootgc is None or raggc is None:
            self.add(A,"GC sees more from the root than from RAG/",UNK,"L1: gc probe did not complete")
        else:
            self.add(A,"GC sees more from the root than from RAG/",PASS if rootgc>=raggc else FAIL,
                     "gc(root)=%d items vs gc(RAG)=%d - the root scan is the superset the boot now uses"%(rootgc,raggc))
        probe=os.path.join(self.root,"_gc_effectiveness_probe")
        try:
            os.makedirs(probe,exist_ok=True)
            junk=os.path.join(probe,"leftover.tmp")
            open(junk,"w").write("x"*10)
            empty=os.path.join(probe,"zero.log")
            open(empty,"w").close()
            r=subprocess.run("python3 -m rag_kernel gc --dry-run --path \"%s\""%self.root,
                             shell=True,capture_output=True,text=True,timeout=300,cwd=self.ragd)
            out=r.stdout+r.stderr
            caught_tmp="leftover.tmp" in out or "_gc_effectiveness_probe" in out
            caught_zero="zero.log" in out
            self.add(A,"GC detects a planted .tmp leftover",PASS if caught_tmp else FAIL,
                     "planted leftover.tmp; GC %s"%("saw it" if caught_tmp else "DID NOT SEE IT"))
            self.add(A,"GC detects a planted zero-byte file",PASS if caught_zero else FAIL,
                     "planted zero.log; GC %s"%("saw it" if caught_zero else "DID NOT SEE IT"))
        except Exception as e:
            self.add(A,"GC injection probe",UNK,"L1: %s"%str(e)[:70])
        finally:
            try: shutil.rmtree(probe)
            except Exception: pass
        cen=os.path.join(self.ragd,"ABANDONMENT_CENSUS_S189.md")
        if os.path.exists(cen):
            # CENSUS-IS-A-SET-NOT-A-COUNT (S191, E-120). This compared today's
            # live GC COUNT against a COUNT frozen in an S189 document. The two
            # measure different populations, so the check got worse as the work
            # got better: S190 archived 16 of the census's 18 files, which drove
            # the live count DOWN and the "disagreement" UP. Sixteen resolved
            # items were being reported as a broken collector.
            #
            # The real invariant is per-file and survives progress: every file
            # the census called abandoned must now be GONE, or still visible to
            # the collector. A file that is neither is the actual blind spot.
            txt=open(cen,encoding="utf-8",errors="replace").read()
            part=txt.split("## GENUINELY ABANDONED")
            listed=re.findall(r"^\|\s`([^`]+)`",part[1],re.M) if len(part)>1 else []
            if not listed:
                self.add(A,"GC agrees with the abandonment census",UNK,"census unreadable")
            else:
                gcout=""
                try:
                    rg=subprocess.run("python3 -m rag_kernel gc --dry-run --path \"%s\""%self.root,
                                      shell=True,capture_output=True,text=True,timeout=300,cwd=self.ragd)
                    gcout=rg.stdout+rg.stderr
                except Exception: gcout=""
                # A census-listed file has THREE legitimate dispositions, not
                # two: gone, still visible to the collector, or re-asserted as
                # governed by the boot-map. The census was a heuristic judgement
                # made once; the boot-map is the project's standing claim about
                # what belongs. Both S191 survivors are boot-mapped — one is a
                # referenced script (axis 10 proves it), the other is the census
                # report itself — so "invisible to gc" is correct behaviour, not
                # blindness. Only a file that is in none of the three states is
                # genuinely lost.
                # The manifest stores {"files":[{"path":...},...]} relative to
                # its own root, so membership is tested on the path AND on the
                # basename — the census writes project-relative paths and the
                # two roots do not always agree.
                mapped=set()
                try:
                    bm=json.load(open(os.path.join(self.ragd,"BOOTMAP_MANIFEST.json"),encoding="utf-8"))
                    for row in (bm.get("files") or []):
                        p=row.get("path") if isinstance(row,dict) else row
                        if isinstance(p,str):
                            mapped.add(p); mapped.add(os.path.basename(p))
                except Exception: mapped=set()
                remaining=[f for f in listed if os.path.exists(os.path.join(self.root,f))]
                blind=[f for f in remaining
                       if os.path.basename(f) not in gcout and f not in gcout
                       and f not in mapped and os.path.basename(f) not in mapped]
                self.add(A,"GC agrees with the abandonment census",
                         FAIL if blind else PASS,
                         ("%d census file(s) on disk, unseen by gc AND unmapped: %s"%(len(blind),blind[:4]))
                         if blind else
                         "%d of %d census files resolved; %d remain, each gc-visible or boot-mapped"
                         %(len(listed)-len(remaining),len(listed),len(remaining)))
        else:
            self.add(A,"GC agrees with the abandonment census",UNK,"census not generated yet")
        r=subprocess.run("python3 -m rag_kernel resolve --help",shell=True,capture_output=True,text=True,cwd=self.ragd)
        h=(r.stdout or "")+(r.stderr or "")
        self.add(A,"resolve requires evidence",PASS if ("--artifact" in h or "--cite" in h) else FAIL,
                 "resolve accepts a DONE claim with no artifact flag" if "--artifact" not in h else "gated")

    def _gc_count(self,path):
        try:
            r=subprocess.run("python3 -m rag_kernel gc --dry-run --path \"%s\""%path,shell=True,
                             capture_output=True,text=True,timeout=300,cwd=self.ragd)
        except Exception:
            return None
        m=re.search(r"Total:\s*(\d+)\s*items",r.stdout+r.stderr)
        return int(m.group(1)) if m else None


    # ================= AXIS 10: WIRING  (connected and enforcing, or present and inert)
    # The operator law: every artefact is either CONNECTED AND WORKING, or it is
    # OBLIVIATED AND ABANDONED. Existence, a hash and a boot-map entry prove none
    # of that. forensics passed all three while being unable to block anything.
    def axis_wiring(self):
        A="10-WIRING"
        kd=os.path.join(self.wt,"rag_kernel")
        if not os.path.isdir(kd):
            return self.add(A,"kernel package present",FAIL,kd)
        mods={}
        for fn in sorted(os.listdir(kd)):
            if fn.endswith(".py") and not fn.startswith("__"):
                mods[fn[:-3]]=open(os.path.join(kd,fn),encoding="utf-8",errors="replace").read()
        allsrc={}
        for base,dirs,fns in os.walk(self.wt):
            if "__pycache__" in base or "/.git" in base: continue
            for fn in fns:
                if fn.endswith(".py"):
                    allsrc[os.path.join(base,fn)]=open(os.path.join(base,fn),encoding="utf-8",errors="replace").read()
        # Reachability, not import-graph membership. A module reached by a
        # documented convention is WIRED: `guardgen` is run as
        # `python -m rag_kernel.guardgen`, is named in the provenance header of
        # the file it generates, and is therefore no more abandoned than
        # __main__ is (S190). Nothing imports an entrypoint; that is what makes
        # it an entrypoint.
        docs=""
        for base,dirs,fns in os.walk(self.wt):
            if "__pycache__" in base or "/.git" in base.replace(os.sep,"/"): continue
            for fn in fns:
                if fn.endswith((".md",".toml",".cfg",".ini",".txt")):
                    try: docs+=open(os.path.join(base,fn),encoding="utf-8",errors="replace").read()
                    except OSError: pass
        orphan=[]
        for m in mods:
            hits=0
            for path,src in allsrc.items():
                if os.path.basename(path)==m+".py": continue
                if "/tests/" in path.replace(os.sep,"/"): continue
                if re.search(r"\b(import|from)\s+[\w.]*\b%s\b"%re.escape(m),src): hits+=1
                if re.search(r"-m\s+rag_kernel\.%s\b"%re.escape(m),src): hits+=1
            if re.search(r"(-m\s+rag_kernel\.%s\b|rag_kernel\.%s\b)"%(re.escape(m),re.escape(m)),docs):
                hits+=1
            if hits==0: orphan.append(m)
        self.add(A,"every kernel module is imported somewhere",FAIL if orphan else PASS,
                 ("never imported outside itself/tests: %s"%orphan) if orphan else "%d modules all reachable"%len(mods))
        main=allsrc.get(os.path.join(kd,"__main__.py"),"")
        # A verb is DECLARED by add_parser and by nothing else. The second
        # pattern this used - any string literal followed by ", help=" - matched
        # option metavars and choice lists, and reported auto/close/command/
        # first/list/origin as undispatched verbs for as long as the axis has
        # existed. A false positive that is explained away every session is a
        # check nobody reads (S190, P4).
        # ...and only TOP-LEVEL verbs. `session close` / `decisions list` are
        # sub-subcommands dispatched by their parent verb's own handler, not by
        # the cmd_ map, so keying on the top-level `subparsers` object is what
        # makes this check mean "reachable from the CLI root".
        declared=set(re.findall(r'\bsubparsers\.add_parser\(\s*"([a-z][a-z-]+)"',main))
        dispatched=set(re.findall(r'"([a-z][a-z-]+)"\s*:\s*cmd_',main))
        undispatched=sorted(d for d in declared if d not in dispatched and len(d)>3)
        self.add(A,"every declared verb is dispatched",FAIL if undispatched else PASS,
                 undispatched[:6] or "%d verbs wired"%len(dispatched))
        advisory=[]
        for name,src in (("__main__.py",main),):
            for m in re.finditer(r"except[^\n]*:\s*#?[^\n]*\n\s*print\(f?\"\s*WARN",src):
                seg=src[max(0,m.start()-1200):m.start()]
                who=re.findall(r"from rag_kernel import (\w+)",seg)
                if who: advisory.append(who[-1])
        advisory=sorted(set(advisory))
        self.add(A,"no governance module is advisory-only",FAIL if advisory else PASS,
                 ("detects but cannot block: %s"%advisory) if advisory else "none")
        never=[]
        for m in re.finditer(r"never (?:a )?block|must never strand|never a blocker|observability, never",main):
            seg=main[max(0,m.start()-900):m.start()+120]
            who=re.findall(r"from rag_kernel import (\w+)",seg)
            never.extend(who)
        never=sorted(set(never))
        self.add(A,"no gate declared non-blocking by design",FAIL if never else PASS,
                 ("explicitly cannot fail the close: %s"%never) if never else "none")
        sd=os.path.join(self.ragd,"scripts")
        if os.path.isdir(sd):
            corpus=""
            for b,d,f in os.walk(self.root):
                if "/.git" in b or "__pycache__" in b: continue
                for x in f:
                    if x.endswith((".md",".json",".py",".sh",".ps1")):
                        try: corpus+=open(os.path.join(b,x),encoding="utf-8",errors="replace").read()
                        except OSError: pass
            dead=[x for x in sorted(os.listdir(sd)) if corpus.count(x)<2]
            self.add(A,"every script in RAG/scripts is referenced",FAIL if dead else PASS,
                     ("referenced nowhere: %s"%dead) if dead else "%d scripts referenced"%len(os.listdir(sd)))

    # ================= AXIS 9: SELF-AUDIT OF THE AUDITOR
    def axis_self(self):
        A="9-SELF"
        ran={r[0] for r in self.rows}
        missing=sorted(set(AXES)-ran)
        self.add(A,"all axes executed",FAIL if missing else PASS,missing or "8 of 8")
        unk=[r[1] for r in self.rows if r[2]==UNK]
        self.add(A,"no inconclusive probes",FAIL if unk else PASS,
                 ("%d UNKNOWN: %s"%(len(unk),unk[:5])) if unk else "every probe completed")
        self.add(A,"operator in scope (axis 7 judged conduct)",
                 PASS if any(r[0]=="7-PROTOCOL" and "conduct" not in r[1] and r[2]!=UNK for r in self.rows) else UNK,
                 "session=%s"%(self.session or "not supplied"))

    # ================= RENDER
    def counts(self):
        c=collections.Counter(r[2] for r in self.rows)
        return c[PASS],c[FAIL],c[UNK]

    def render(self):
        w=max(len(r[1]) for r in self.rows)
        L=["="*104,
           "GRAND AUDIT   root=%s   session=%s   elapsed=%.1fs"%(os.path.basename(self.root),self.session or "-",time.time()-self.t0),
           "="*104]
        cur=None
        for axis,name,st,ev in self.rows:
            if axis!=cur: L+=["","--- AXIS %s"%axis]; cur=axis
            L.append("  [%s] %-*s  %s"%({PASS:" ok ",FAIL:"FAIL",UNK:"????"}[st],w,name,ev))
        p,f,u=self.counts()
        L+=["","="*104,
            "RESULT: %d PASS   %d FAIL   %d UNKNOWN   of %d checks"%(p,f,u,len(self.rows))]
        if f==0 and u==0:
            L.append("VERDICT: GREEN - system and operator both verified this run")
        else:
            L.append("VERDICT: NOT GREEN")
            if f: L.append("  %d FAIL - defects requiring rectification"%f)
            if u: L.append("  %d UNKNOWN - L2: an unfinished probe is NOT a pass and blocks GREEN"%u)
        L+=["="*104,"",
            "FAIL detail:"]
        for axis,name,st,ev in self.rows:
            if st==FAIL: L.append("  [%s] %s :: %s"%(axis,name,ev))
        L.append("")
        L.append("UNKNOWN detail:")
        for axis,name,st,ev in self.rows:
            if st==UNK: L.append("  [%s] %s :: %s"%(axis,name,ev))
        return chr(10).join(L)


def rag_ledger(rag):
    v=rag.get("inference_ledger")
    return v if isinstance(v,list) else []


def main():
    ap=argparse.ArgumentParser(description="GRAND AUDIT - full compliance due diligence, one command.")
    ap.add_argument("--root",default="/mnt/c/Users/pakhol/Desktop/GitHub Project (RAG Runtime Kernel)")
    ap.add_argument("--session",help="session id whose conduct axis 7 judges (via forensics)")
    ap.add_argument("--fast",action="store_true",help="skip TLC; axis 8 becomes UNKNOWN, verdict cannot be GREEN")
    ap.add_argument("--out",help="also write the report to this path")
    # GRAND-AUDIT-AT-BOOT (S190, P2): the boot needs axis 1 and only axis 1 -
    # the transports must be proven THIS session before anything below them is
    # believed. Running all eleven axes at every boot would tax the operator
    # minutes per session, so the gate selects.
    ap.add_argument("--only",default=None,
                    help="comma-separated axis numbers to run, e.g. --only 1 (boot gate)")
    a=ap.parse_args()
    # FORENSICS-CALLER-ATTRIBUTION (S191, E-111). Every kernel invocation this
    # auditor makes is a MACHINE call and inherits this stamp, so axis 7 stops
    # charging the auditor's own gc/audit probes to the agent it is judging.
    # Set before any child is spawned; children inherit os.environ.
    os.environ["RAG_KERNEL_CALLER"]="auditor"
    G=Grand(a.root,a.session,a.fast)
    PLAN=[("1",G.axis_tools),("2",G.axis_gates),("3",G.axis_claims),
          ("4",G.axis_continuity),("5",G.axis_files),("6",G.axis_code),
          ("7",G.axis_protocol),("8",G.axis_formal),("10",G.axis_wiring),
          ("11",G.axis_effect),("9",G.axis_self)]
    want=None
    if a.only:
        want={x.strip().split("-")[0] for x in a.only.split(",") if x.strip()}
        # Axis 1 always runs: it is the audit's first law (nothing below a broken
        # transport is trustworthy) AND its literal precondition - it is what
        # locates tla2tools.jar for axis 8. A selection that skipped it reported
        # "no jar" and called the formal layer unverified (S190).
        want.add("1")
    for num,fn in PLAN:
        if want is None or num in want:
            fn()
    txt=G.render()
    print(txt)
    if a.out:
        open(a.out,"w",encoding="utf-8").write(txt+chr(10))
    p,f,u=G.counts()
    sys.exit(0 if (f==0 and u==0) else 1)


if __name__=="__main__":
    main()
