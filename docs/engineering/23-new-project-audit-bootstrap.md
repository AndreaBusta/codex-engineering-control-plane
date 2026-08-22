# New-project audit bootstrap
Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`. This runbook prepares evidence for an unidentified next project without installing Control Plane or changing a consumer. Its source-owned four-file pack and outputs are `authorizes=false`.
## Boundary
The source pack is exactly `templates/new-project/AGENTS.md`, its two `.codex/{project-policy,resource-registry}.toml` files, and the pack `README.md`. The README stays source-owned; only the other three may later become target authority after external customization. The consumer README is preserved. There is no copier, installer, target transaction, hook, lock, runtime, Git workflow, remote action, canary selection, or adoption authority.
## 1. Bind the selected source
Select an integrated Control Plane commit from reviewed evidence and use a clean detached source at its exact SHA. The contract is `FULLY_MATERIALIZED_LOCAL_ONLY`: File Provider or materialization `UNKNOWN` stops before the audit, and zero-mutation is not claimed for unsupported roots. Export `CONTROL_PLANE_SOURCE` as its literal physical path and `CONTROL_PLANE_SOURCE_SHA` as that exact integrated SHA, then run this verifier.
<!-- BEGIN SOURCE_BINDING -->
```bash
set -eu
CONTROL_PLANE="$CONTROL_PLANE_SOURCE/scripts/control-plane"
verify_control_plane_context() {
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin HOME=/var/empty \
    XDG_CONFIG_HOME=/var/empty GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0 \
    CONTROL_PLANE_SOURCE="$CONTROL_PLANE_SOURCE" \
    CONTROL_PLANE_SOURCE_SHA="$CONTROL_PLANE_SOURCE_SHA" \
    VERIFY_TARGET="$1" TARGET_REPO="${TARGET_REPO-}" TASK_ENVELOPE="${TASK_ENVELOPE-}" \
    TASK_ENVELOPE_SHA256="${TASK_ENVELOPE_SHA256-}" TARGET_AGENTS_SHA256="${TARGET_AGENTS_SHA256-}" \
    TARGET_POLICY_SHA256="${TARGET_POLICY_SHA256-}" TARGET_REGISTRY_SHA256="${TARGET_REGISTRY_SHA256-}" \
    /usr/bin/python3 -I -S -B - <<'PY'
from hashlib import sha256; import os,re,selectors,signal,stat,subprocess,time; from pathlib import Path
M=1024*1024; MAX_TREE=MAX_FILE=MAX_TASK=M; MAX_TOTAL=16*M; MAX_FILES=4096; MAX_PATH=4096
MAX_META=MAX_FILES*16; MAX_ANCESTORS=64; UF_DATALESS=0x40000000; END=time.monotonic()+30; ACTIVE=None; EUID=os.geteuid(); CHAINS={}
GIT_ENV={"LC_ALL":"C","PATH":"/usr/bin:/bin","HOME":"/var/empty","XDG_CONFIG_HOME":"/var/empty","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null","GIT_OPTIONAL_LOCKS":"0","GIT_TERMINAL_PROMPT":"0","GIT_NO_REPLACE_OBJECTS":"1","GIT_NO_LAZY_FETCH":"1"}
GIT=["/usr/bin/git","--no-pager","-c","core.hooksPath=/dev/null","-c","core.fsmonitor=false","-c","core.untrackedCache=false","-c","color.ui=false","-c","core.pager=cat"]
def stop(p):
    for effect in (signal.SIGTERM,signal.SIGKILL):
        try: os.killpg(p.pid,effect)
        except ProcessLookupError: pass
        try: p.wait(timeout=.25); return
        except subprocess.TimeoutExpired: pass
    raise SystemExit("E_SOURCE_TREE_MISMATCH")
def fail(*_):
    global ACTIVE; p,ACTIVE=ACTIVE,None
    if p is not None: stop(p)
    raise SystemExit("E_SOURCE_TREE_MISMATCH")
def need(ok): return ok or fail()
def fully_materialized(item): return not bool(getattr(item,"st_flags",0)&UF_DATALESS)
def safe(item):
    if not fully_materialized(item): raise SystemExit("E_AUDIT_STABLE_PAUSE_MATERIALIZATION")
    if item.st_uid!=EUID: raise SystemExit("E_AUDIT_UNSAFE_PERMISSIONS")
    if stat.S_IMODE(item.st_mode)&0o022: raise SystemExit("E_AUDIT_UNSAFE_PERMISSIONS")
signal.signal(signal.SIGALRM,fail); signal.setitimer(signal.ITIMER_REAL,30)
def git(args,cap,allowed=(0,),input_bytes=None):
    global ACTIVE
    process=subprocess.Popen(GIT+args,env=GIT_ENV,stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True,start_new_session=True); ACTIVE=process
    try:
        if input_bytes is not None:
            try: output,_=process.communicate(input=input_bytes,timeout=END-time.monotonic())
            except (OSError,subprocess.TimeoutExpired): fail()
        else:
            selector=selectors.DefaultSelector(); output=bytearray()
            try:
                selector.register(process.stdout,selectors.EVENT_READ)
                while selector.get_map():
                    remaining=END-time.monotonic()
                    if remaining<=0: fail()
                    for key,_ in selector.select(min(remaining,.05)):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk: selector.unregister(key.fileobj); continue
                        if len(output)+len(chunk)>cap: fail()
                        output.extend(chunk)
                try: process.wait(timeout=END-time.monotonic())
                except subprocess.TimeoutExpired: fail()
                output=bytes(output)
            finally: selector.close(); process.stdout and process.stdout.close()
        rc=process.returncode; ACTIVE=None; need(len(output)<=cap and rc in allowed); return rc,output
    except BaseException:
        if ACTIVE is process: ACTIVE=None; stop(process)
        raise
def identity(s): return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def chain_identity(s): return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)
def sticky_protects(item,child_item): return bool(item.st_mode&stat.S_ISVTX) and item.st_uid in (0,EUID) and child_item.st_uid==EUID
def bind_chain(path,final_directory):
    need(path.is_absolute() and len(path.parts)<=MAX_ANCESTORS); current=Path(path.anchor); item=current.lstat(); records=[]
    for part in path.parts[1:]:
        need(stat.S_ISDIR(item.st_mode) and not stat.S_ISLNK(item.st_mode) and fully_materialized(item)); child=current/part; child_item=child.lstat()
        if stat.S_IMODE(item.st_mode)&0o022: need(sticky_protects(item,child_item))
        records.append((str(current),chain_identity(item))); current,item=child,child_item
    need(not stat.S_ISLNK(item.st_mode) and fully_materialized(item)); records.append((str(current),chain_identity(item)))
    need(stat.S_ISDIR(item.st_mode) if final_directory else stat.S_ISREG(item.st_mode)); CHAINS[str(path)]=records; return item
def revalidate_chains():
    for records in CHAINS.values():
        for path,bound_identity in records:
            item=Path(path).lstat(); need(fully_materialized(item) and not stat.S_ISLNK(item.st_mode) and chain_identity(item)==bound_identity)
def read_small(path,maximum=8192):
    before=path.lstat(); safe(before)
    need(stat.S_ISREG(before.st_mode) and before.st_size<=maximum)
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: opened=os.fstat(descriptor); safe(opened); payload=os.read(descriptor,maximum+1); after_open=os.fstat(descriptor); safe(after_open)
    finally: os.close(descriptor)
    after=path.lstat(); safe(after)
    need(len(payload)==before.st_size and identity(before)==identity(opened)==identity(after_open)==identity(after)); return payload
def scan_metadata(root,skip_git=False,allow_symlinks=True):
    stack=[root]; count=0
    while stack:
        directory=stack.pop(); named=directory.lstat(); safe(named)
        need(stat.S_ISDIR(named.st_mode))
        with os.scandir(directory) as entries:
            for entry in entries:
                count+=1; need(count<=MAX_META); item=entry.stat(follow_symlinks=False); safe(item)
                if skip_git and entry.name==".git" and directory!=root: fail()
                if skip_git and entry.name==".git": continue
                if stat.S_ISDIR(item.st_mode): stack.append(Path(entry.path))
                elif not (stat.S_ISREG(item.st_mode) or (allow_symlinks and stat.S_ISLNK(item.st_mode))): fail()
def metadata_root(pointer,raw):
    text=os.fsdecode(raw).strip(); need(bool(text))
    candidate=Path(text); candidate=candidate if candidate.is_absolute() else pointer.parent/candidate
    normalized=Path(os.path.abspath(os.path.normpath(candidate))); item=bind_chain(normalized,True); safe(item)
    resolved=normalized.resolve(strict=True); need(resolved==normalized); return resolved
def forbidden_config(payload):
    if payload.startswith(b"\xef\xbb\xbf"): return True
    for raw in re.split(rb"[\r\n]+",payload):
        line=raw.lstrip(); key=line.split(b"=",1)[0].strip().lower()
        if line and not line.startswith((b"#",b";")) and (re.search(rb'(?i)\[\s*(?:include(?:if)?|filter)(?=[\s.\]"\'])',line) or key.startswith(b"filter.")): return True
    return False
def reject_external_object_store(gitdir,common):
    objects=common/"objects"; objects_item=objects.lstat(); need(not stat.S_ISLNK(objects_item.st_mode)); safe(objects_item); bind_chain(objects,True)
    info=objects/"info"
    try: info_item=info.lstat()
    except FileNotFoundError: pass
    else:
        need(not stat.S_ISLNK(info_item.st_mode) and stat.S_ISDIR(info_item.st_mode)); safe(info_item); bind_chain(info,True)
        for name in ("alternates","http-alternates"):
            try: (info/name).lstat()
            except FileNotFoundError: pass
            else: fail()
    for config in (common/"config",gitdir/"config.worktree"):
        try: config.lstat()
        except FileNotFoundError: continue
        payload=read_small(config,MAX_FILE)
        if forbidden_config(payload): fail()
    try: (common/"modules").lstat()
    except FileNotFoundError: pass
    else: fail()
def verify_index(bound,head,status_required):
    _,tags=git(bound+["ls-files","-v","-z"],MAX_TREE); tag_rows=[row for row in tags.split(b"\0") if row]; need(len(tag_rows)<=MAX_FILES and all(row.startswith(b"H ") for row in tag_rows))
    _,stages=git(bound+["ls-files","-s","-z"],MAX_TREE); stage_rows=[row for row in stages.split(b"\0") if row]; need(len(stage_rows)==len(tag_rows) and len(stage_rows)<=MAX_FILES and all(not row.startswith(b"160000 ") for row in stage_rows))
    index_rc,_=git(bound+["diff-index","--cached","--quiet",head,"--"],256,(0,1)); need(index_rc==0)
    if status_required: _,dirty=git(bound+["-c","status.showUntrackedFiles=all","-c","core.fileMode=true","status","--porcelain=v1","-z","--untracked-files=all"],MAX_TREE); need(not dirty)
def verify_authority(bound,approved):
    paths=list(approved); _,raw_index=git(bound+["ls-files","-s","-z","--",*paths],MAX_TREE); _,raw_head=git(bound+["ls-tree","-rz","--full-tree","HEAD","--",*paths],MAX_TREE)
    def entries(raw,pattern):
        matches=[re.fullmatch(pattern,row) for row in raw.split(b"\0") if row]; need(all(matches)); result={os.fsdecode(match.group(3)):(match.group(1),match.group(2)) for match in matches}
        need(len(result)==len(matches) and set(result)<=set(approved)); return result
    indexed=entries(raw_index,rb"(100644|100755) ([0-9a-f]{40}) 0\t(.+)"); headed=entries(raw_head,rb"(100644|100755) blob ([0-9a-f]{40})\t(.+)"); need(indexed==headed and set(headed)==set(approved))
    for path,(_,oid) in headed.items(): _,blob=git(bound+["cat-file","blob",oid.decode()],M); need(sha256(blob).hexdigest()==approved[path])
def task_metadata_preflight(task_path):
    item=bind_chain(task_path,False); safe(item); return item
def task_envelope_bytes(path):
    before=path.lstat(); safe(before)
    if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_TASK: fail()
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try: opened=os.fstat(descriptor); safe(opened); payload=os.read(descriptor,MAX_TASK+1); after_open=os.fstat(descriptor); safe(after_open)
    finally: os.close(descriptor)
    after=path.lstat(); safe(after)
    if len(payload)!=before.st_size or identity(before)!=identity(opened) or identity(opened)!=identity(after_open) or identity(opened)!=identity(after): fail()
    return payload
def target_metadata_preflight(literal):
    paths=(literal,literal/".git",literal/".codex",literal/"AGENTS.md",literal/"README.md",literal/".codex/project-policy.toml",literal/".codex/resource-registry.toml")
    items=[]
    for path in paths: item=path.lstat(); safe(item); items.append(item)
    need(all(stat.S_ISDIR(items[i].st_mode) for i in (0,2)) and all(stat.S_ISREG(items[i].st_mode) for i in (3,4,5,6)))
    bind_chain(literal,True); bind_chain(literal/".codex",True); scan_metadata(literal,skip_git=True)
    for path in paths[3:]: bind_chain(path,False)
    dotgit=literal/".git"
    if stat.S_ISDIR(items[1].st_mode): bind_chain(dotgit,True); gitdir=dotgit.resolve(strict=True)
    elif stat.S_ISREG(items[1].st_mode):
        bind_chain(dotgit,False); payload=read_small(dotgit); need(payload.startswith(b"gitdir: ")); gitdir=metadata_root(dotgit,payload[8:])
    else: fail()
    pointer=gitdir/"commondir"
    try: pointer_item=pointer.lstat()
    except FileNotFoundError: common=gitdir
    else: safe(pointer_item); common=metadata_root(pointer,read_small(pointer))
    reject_external_object_store(gitdir,common); scan_metadata(gitdir,allow_symlinks=False); common!=gitdir and scan_metadata(common,allow_symlinks=False); return gitdir,common
def validate_target_and_task():
    names=(("AGENTS.md","TARGET_AGENTS_SHA256"),(".codex/project-policy.toml","TARGET_POLICY_SHA256"),(".codex/resource-registry.toml","TARGET_REGISTRY_SHA256"))
    approved={name:os.environ.get(variable,"") for name,variable in names}; need(all(re.fullmatch(r"[0-9a-f]{64}",value) for value in approved.values()))
    text=os.environ.get("TARGET_REPO",""); literal=Path(text); need(bool(text) and literal.is_absolute())
    task_text=os.environ.get("TASK_ENVELOPE",""); task_digest=os.environ.get("TASK_ENVELOPE_SHA256",""); task_path=Path(task_text)
    need(bool(task_text) and task_path.is_absolute() and bool(re.fullmatch(r"[0-9a-f]{64}",task_digest))); task_metadata_preflight(task_path)
    target_gitdir,target_common=target_metadata_preflight(literal); physical = literal.resolve(strict=True); need(literal==physical)
    _,target_raw_gitdir=git(["-C",str(literal),"rev-parse","--absolute-git-dir"],8192); need(Path(os.fsdecode(target_raw_gitdir.rstrip(b"\n"))).resolve(strict=True)==target_gitdir); target_bound=["--git-dir="+str(target_gitdir),"--work-tree="+str(literal)]
    _,target_raw_common=git(target_bound+["rev-parse","--path-format=absolute","--git-common-dir"],8192); need(Path(os.fsdecode(target_raw_common.rstrip(b"\n"))).resolve(strict=True)==target_common); _,target_raw_top=git(["-C",str(literal),"rev-parse","--show-toplevel"],8192); need(Path(os.fsdecode(target_raw_top.rstrip(b"\n"))).resolve(strict=True)==literal)
    verify_index(target_bound,"HEAD",True); verify_authority(target_bound,approved)
    task_physical=task_path.resolve(strict=True); need(task_path==task_physical); need(sha256(task_envelope_bytes(task_path)).hexdigest()==task_digest)
    dirs=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC; leaf=os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC; root_fd=codex_fd=None
    try:
        root_named=literal.lstat(); root_fd=os.open(literal,dirs); root_open=os.fstat(root_fd)
        codex_named=os.stat(".codex",dir_fd=root_fd,follow_symlinks=False); codex_fd=os.open(".codex",dirs,dir_fd=root_fd); codex_open=os.fstat(codex_fd)
        for item in (root_named,root_open,codex_named,codex_open): safe(item)
        need(all(stat.S_ISDIR(item.st_mode) for item in (root_named,root_open,codex_named,codex_open))); need(identity(root_named)==identity(root_open) and identity(codex_named)==identity(codex_open))
        for name,_ in names:
            leaf_name=Path(name).name; parent=root_fd if name=="AGENTS.md" else codex_fd; before=os.stat(leaf_name,dir_fd=parent,follow_symlinks=False); descriptor=os.open(leaf_name,leaf,dir_fd=parent)
            try: opened=os.fstat(descriptor); safe(opened); payload=os.read(descriptor,M+1); after_open=os.fstat(descriptor); safe(after_open)
            finally: os.close(descriptor)
            after=os.stat(leaf_name,dir_fd=parent,follow_symlinks=False); safe(before); safe(after)
            need(stat.S_ISREG(opened.st_mode) and identity(before)==identity(opened)==identity(after_open)==identity(after))
            need(len(payload)<=M and b"__PROJECT_NAME__" not in payload and sha256(payload).hexdigest()==approved[name])
        need(identity(os.fstat(root_fd))==identity(root_open)==identity(os.stat(literal,follow_symlinks=False)))
        need(identity(os.fstat(codex_fd))==identity(codex_open)==identity(os.stat(".codex",dir_fd=root_fd,follow_symlinks=False)))
    finally:
        if codex_fd is not None: os.close(codex_fd)
        if root_fd is not None: os.close(root_fd)
def source_metadata_preflight(source):
    root_item=bind_chain(source,True); dotgit=source/".git"; dotgit_item=dotgit.lstat(); safe(root_item); safe(dotgit_item)
    scan_metadata(source,skip_git=True); pointer=source/".git"
    if stat.S_ISDIR(dotgit_item.st_mode): bind_chain(pointer,True); gitdir=pointer.resolve(strict=True)
    elif stat.S_ISREG(dotgit_item.st_mode):
        bind_chain(pointer,False); payload=read_small(pointer); need(payload.startswith(b"gitdir: ")); gitdir=metadata_root(pointer,payload[8:])
    else: fail()
    safe(gitdir.lstat()); common_pointer=gitdir/"commondir"
    try: common_item=common_pointer.lstat()
    except FileNotFoundError: common=gitdir
    except OSError: fail()
    else: safe(common_item); common=metadata_root(common_pointer,read_small(common_pointer))
    reject_external_object_store(gitdir,common); scan_metadata(gitdir,allow_symlinks=False); common!=gitdir and scan_metadata(common,allow_symlinks=False)
    return gitdir,common
source_text=os.environ.get("CONTROL_PLANE_SOURCE",""); sha=os.environ.get("CONTROL_PLANE_SOURCE_SHA",""); need(bool(source_text) and bool(re.fullmatch(r"[0-9a-f]{40}",sha)))
source=Path(source_text); need(source.is_absolute()); source_metadata_gitdir,source_metadata_common=source_metadata_preflight(source)
physical = source.resolve(strict=True)
need(source==physical and source.is_dir())
verify_target=os.environ.get("VERIFY_TARGET",""); need(verify_target in ("0","1"))
if verify_target=="1": validate_target_and_task()
_, raw_gitdir = git(["-C",str(source),"--work-tree="+str(source),"rev-parse","--absolute-git-dir"],8192)
gitdir=Path(os.fsdecode(raw_gitdir.rstrip(b"\n"))).resolve(strict=True)
need(gitdir==source_metadata_gitdir)
bound=["--git-dir="+str(gitdir),"--work-tree="+str(source)]
_,raw_common=git(bound+["rev-parse","--path-format=absolute","--git-common-dir"],8192)
common=Path(os.fsdecode(raw_common.rstrip(b"\n"))).resolve(strict=True)
need(common==source_metadata_common)
_,raw_top=git(bound+["rev-parse","--show-toplevel"],8192)
top=Path(os.fsdecode(raw_top.rstrip(b"\n"))).resolve(strict=True)
need(top==source)
_,raw_head=git(bound+["rev-parse","--verify","HEAD^{commit}"],256); need(raw_head.decode("ascii","strict").strip()==sha)
symbolic_rc,_=git(bound+["symbolic-ref","-q","HEAD"],256,(0,1)); need(symbolic_rc==1)
verify_index(bound,sha,False)
_,tree=git(bound+["ls-tree","-rz","--full-tree",sha],MAX_TREE)
records=[item for item in tree.split(b"\0") if item]; need(bool(records) and len(records)<=MAX_FILES)
expected={}
for record in records:
    metadata,separator,raw_path=record.partition(b"\t"); match=re.fullmatch(rb"(100644|100755|120000) blob ([0-9a-f]{40})",metadata); need(bool(separator and match and raw_path) and len(raw_path)<=MAX_PATH)
    relative=Path(os.fsdecode(raw_path)); need(not relative.is_absolute() and not any(part in ("",".","..") for part in relative.parts)); key=relative.as_posix(); need(key not in expected)
    expected[key]=(match.group(1).decode(),match.group(2).decode())
oids=sorted({oid for _,oid in expected.values()}); request=b"".join(oid.encode()+b"\n" for oid in oids)
_,size_rows=git(bound+["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],MAX_TREE,input_bytes=request)
rows=size_rows.splitlines(); need(len(rows)==len(oids))
sizes={}; expected_total=0
for requested,row in zip(oids,rows):
    fields=row.split(); need(len(fields)==3 and fields[0].decode()==requested and fields[1]==b"blob")
    need(fields[2].isdigit()); size=int(fields[2])
    expected_total+=size; need(0<=size<=MAX_FILE and expected_total<=MAX_TOTAL); sizes[requested]=size
batch_cap=sum(size+len(oid)+len(str(size))+8 for oid,size in sizes.items())
_,batch=git(bound+["cat-file","--batch"],batch_cap,input_bytes=request); blobs={}; offset=0
for requested in oids:
    end=batch.find(b"\n",offset); header=batch[offset:end].split(); need(end>=0 and len(header)==3 and header[0].decode()==requested and header[1]==b"blob")
    need(header[2].isdigit()); size=int(header[2])
    start,finish=end+1,end+1+size; need(size==sizes[requested] and finish<len(batch) and batch[finish:finish+1]==b"\n")
    blobs[requested]=batch[start:finish]; offset=finish+1
need(offset==len(batch))
observed_total=0
for key,(mode,oid) in expected.items():
    candidate=source.joinpath(*Path(key).parts); before=candidate.lstat(); safe(before)
    if mode=="120000":
        need(stat.S_ISLNK(before.st_mode))
        live=os.fsencode(os.readlink(candidate)); after=candidate.lstat(); safe(after)
        need(identity(before)==identity(after))
    else:
        need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==int(mode[-3:],8))
        descriptor=os.open(candidate,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            opened=os.fstat(descriptor); safe(opened); chunks=[]; size=0
            while True:
                chunk=os.read(descriptor,min(65536,MAX_FILE+1-size))
                if not chunk: break
                chunks.append(chunk); size+=len(chunk)
                if size>MAX_FILE: fail()
            after_open=os.fstat(descriptor); safe(after_open)
        finally: os.close(descriptor)
        after=candidate.lstat(); safe(after)
        need(identity(before)==identity(opened)==identity(after_open)==identity(after)); live=b"".join(chunks)
    observed_total+=len(live); need(observed_total<=MAX_TOTAL and live==blobs[oid])
stack=[source]; walked=0; observed_paths = set()
while stack:
    directory=stack.pop()
    with os.scandir(directory) as entries:
        for entry in entries:
            walked+=1; need(walked<=MAX_FILES*4); relative=Path(entry.path).relative_to(source); key=relative.as_posix()
            if relative.parts==(".git",): continue
            item=entry.stat(follow_symlinks=False); safe(item)
            if stat.S_ISDIR(item.st_mode): stack.append(Path(entry.path))
            elif key not in expected or key in observed_paths: fail()
            else: observed_paths.add(key)
if observed_paths != set(expected): fail()
revalidate_chains()
PY
}
verify_control_plane_source() { verify_control_plane_context 0; [ -x "$CONTROL_PLANE" ]; }
verify_control_plane_target_and_task() { verify_control_plane_context 1; [ -x "$CONTROL_PLANE" ]; }
verify_control_plane_audit_context() {
  verify_control_plane_target_and_task
  verify_control_plane_source
  verify_control_plane_target_and_task
}
control_plane_audit() {
  verify_control_plane_audit_context
  if "$@"; then audit_rc=0; else audit_rc=$?; fi
  verify_control_plane_audit_context
  return "$audit_rc"
}
verify_control_plane_source
```
<!-- END SOURCE_BINDING -->
## 2. Inspect the target
Inspect root, Git state, README, instructions, policy, dependencies and provider protections before proposing bytes. Unobserved base, remote, CI, release, language or authority facts are `UNKNOWN`.
## 3. Customize outside the target
Outside the target, derive final project-specific bytes from only the template `AGENTS.md`, policy and registry. Replace `__PROJECT_NAME__`, reconcile observed facts, retain restrictive authority and real locators, and never stage generic bytes.
The project README is recommended only by the first-use `audit`/`research` T2 route. Store reviewed lowercase SHA-256 values externally and export `TARGET_{AGENTS,POLICY,REGISTRY}_SHA256` individually. Externally validate the exact TaskEnvelope and export its physical path plus `TASK_ENVELOPE_SHA256`; neither a movable envelope nor a digest alone is enough.
## 4. Deferred target transition
Adding reviewed files is `DEFERRED_TARGET_BOOTSTRAP`, only after target, scope, bytes, rollback, gates and authorization are known; no mutator is provided. Then bind `TARGET_REPO` to the literal physical root and `TASK_ENVELOPE` to that externally validated local-read T2 answer.
## 5. Exact read-only happy path
The five commands alone cannot prove customization. First run this exact guard; then all five must exit zero with JSON. The single wrapper revalidates source, target authority and TaskEnvelope immediately before and after every launcher call.
Source index must equal its selected HEAD; target index/status must be clean even when config or index flags hide changes, and the three reviewed authority digests must equal regular stage-0 entries in both target index and `HEAD`. Unsafe ancestors, submodules/nested repos, filter config, config includes, alternates or object-store redirects stop before target Git can run filters or status. Sticky ancestors are allowed only when they protect an EUID-owned child. Nothing writes or authorizes.
<!-- BEGIN CUSTOMIZATION_GUARD -->
```bash
set -eu
verify_control_plane_target_and_task
```
<!-- END CUSTOMIZATION_GUARD -->
<!-- BEGIN HAPPY_PATH -->
```bash
control_plane_audit "$CONTROL_PLANE" policy-check --policy "$TARGET_REPO/.codex/project-policy.toml" --json
control_plane_audit "$CONTROL_PLANE" registry-check --registry "$TARGET_REPO/.codex/resource-registry.toml" --policy "$TARGET_REPO/.codex/project-policy.toml" --json
control_plane_audit "$CONTROL_PLANE" inventory --repo "$TARGET_REPO" --registry "$TARGET_REPO/.codex/resource-registry.toml" --json
control_plane_audit "$CONTROL_PLANE" preflight --mode read --repo "$TARGET_REPO" --policy "$TARGET_REPO/.codex/project-policy.toml" --offline --json
control_plane_audit "$CONTROL_PLANE" route --repo "$TARGET_REPO" --task "$TASK_ENVELOPE" --policy "$TARGET_REPO/.codex/project-policy.toml" --registry "$TARGET_REPO/.codex/resource-registry.toml" --mode audit --json
```
<!-- END HAPPY_PATH -->
Require policy/registry `ok=true`; inventory-ready instructions and README with no `R_NOT_FOUND` even when route exits zero; every observed local preflight check true; and a T2 `decision_ready=true` route selecting instructions, recommending the consumer README and reporting `authorizes=false`.
Local remote/tracking refs do not prove provider freshness, which remains `UNKNOWN` without separate evidence. Snapshot bytes, HEAD, branch, index and status before/after; change stops.
## 6. Optional diagnostics
`doctor` and `survey` are separate diagnostics, never happy-path gates:
```bash
control_plane_audit "$CONTROL_PLANE" doctor --repo "$TARGET_REPO" --json
control_plane_audit "$CONTROL_PLANE" survey --repo "$TARGET_REPO" --json
```
## Stop conditions
Stop on binding, materialization, permissions, identity, guard, locator, local preflight, command, route or snapshot failure, or before overwriting authority.
The identified project owns the next decision; this front does not cross `DEFERRED_TARGET_BOOTSTRAP`.
