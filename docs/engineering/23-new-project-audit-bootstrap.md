# New-project source-driven audit bootstrap

## Estado y frontera

Este runbook gobierna el primer uso de Control Plane en un proyecto nuevo sin
instalar runtime, hooks, locks ni estado de tarea en el consumidor. El starter
es propiedad del proyecto; el ejecutable permanece en una fuente limpia
seleccionada y ligada a un SHA exacto. Crear la rama, copiar, personalizar y commitear son mutaciones
project-owned previas; solo la auditoría source-driven de la sección 5 es
read-only.

```text
external_consumer_adoption=PROHIBITED
consumer_adoption_commands=PROHIBITED
source-driven=AUDIT_ONLY
authorizes=false
copy_customize_commit=PROJECT_OWNED_MUTATION
section_5_source_driven_audit=READ_ONLY
```

No ejecutes `scripts/control-plane-adoption` contra el consumidor. `preview`,
`apply`, `verify` y `rollback` siguen siendo comandos de adopción prohibidos
fuera del harness. Este runbook tampoco autoriza instalación, canary, deploy,
release, publicación, cambios de CI, dependencias ni manejo de secretos.

El pack fuente contiene cuatro ficheros:

```text
templates/new-project/
├── AGENTS.md
├── README.md
└── .codex/
    ├── project-policy.toml
    └── resource-registry.toml
```

`templates/new-project/README.md` es la guía source-side y permanece en Control
Plane. Copia al consumidor solo `AGENTS.md` y los dos TOML de autoridad. Nunca
reemplaces el README propio del proyecto.

## 1. Fijar fuente y destino

Selecciona primero una fuente limpia (`selected clean source`). No fijes aquí un checkout “canónico” ni
presupongas que su rama está actualizada: el path y el SHA son dos entradas de
operador independientes y deben corresponder al mismo checkout seleccionado.

```bash
# Completa estas entradas de operador sin command substitutions.
export BOOTSTRAP_CONTROL_PLANE_SOURCE=/ruta/absoluta/a/la/fuente-seleccionada
export BOOTSTRAP_CONTROL_PLANE_SOURCE_SHA=REEMPLAZAR_CON_SHA_EXACTO_DE_40_HEX
export BOOTSTRAP_NEW_PROJECT_ROOT=/ruta/absoluta/al/proyecto-nuevo
export BOOTSTRAP_NEW_PROJECT_TASK=/ruta/absoluta/al/task-envelope.json
export BOOTSTRAP_NEW_PROJECT_BASE=main
export BOOTSTRAP_NEW_PROJECT_WORK_BRANCH=codex/control-plane-bootstrap-v1
export BOOTSTRAP_BASELINE_HEAD=REEMPLAZAR_CON_BASELINE_EXACTO_DE_40_HEX
export BOOTSTRAP_CONTROL_PLANE_AUTHORITY_STAGE=/ruta/temporal/authority-stage
export BOOTSTRAP_CONTROL_PLANE_GIT_AUTHOR_NAME='Nombre exacto del autor'
export BOOTSTRAP_CONTROL_PLANE_GIT_AUTHOR_EMAIL=autor@example.invalid
export BOOTSTRAP_CONTROL_PLANE_GIT_COMMITTER_NAME='Nombre exacto del committer'
export BOOTSTRAP_CONTROL_PLANE_GIT_COMMITTER_EMAIL=committer@example.invalid

# BEGIN CONTROL_PLANE_BOOTSTRAP_SETUP
export CONTROL_PLANE_SOURCE="${BOOTSTRAP_CONTROL_PLANE_SOURCE:?E_CONTROL_PLANE_SOURCE_REQUIRED}"
export CONTROL_PLANE_SOURCE_SHA="${BOOTSTRAP_CONTROL_PLANE_SOURCE_SHA:?E_CONTROL_PLANE_SOURCE_SHA_REQUIRED}"
export NEW_PROJECT_ROOT="${BOOTSTRAP_NEW_PROJECT_ROOT:?E_BOOTSTRAP_TARGET_REQUIRED}"
export NEW_PROJECT_TASK="${BOOTSTRAP_NEW_PROJECT_TASK:?E_BOOTSTRAP_TASK_REQUIRED}"
export NEW_PROJECT_BASE="${BOOTSTRAP_NEW_PROJECT_BASE:?E_BOOTSTRAP_BASE_REQUIRED}"
export NEW_PROJECT_WORK_BRANCH="${BOOTSTRAP_NEW_PROJECT_WORK_BRANCH:?E_BOOTSTRAP_WORK_BRANCH_REQUIRED}"
export BASELINE_HEAD="${BOOTSTRAP_BASELINE_HEAD:?E_BOOTSTRAP_BASELINE_REQUIRED}"
export CONTROL_PLANE_AUTHORITY_STAGE="${BOOTSTRAP_CONTROL_PLANE_AUTHORITY_STAGE:?E_BOOTSTRAP_AUTHORITY_STAGE_REQUIRED}"
export CONTROL_PLANE_GIT_AUTHOR_NAME="${BOOTSTRAP_CONTROL_PLANE_GIT_AUTHOR_NAME:?E_BOOTSTRAP_GIT_IDENTITY_REQUIRED}"
export CONTROL_PLANE_GIT_AUTHOR_EMAIL="${BOOTSTRAP_CONTROL_PLANE_GIT_AUTHOR_EMAIL:?E_BOOTSTRAP_GIT_IDENTITY_REQUIRED}"
export CONTROL_PLANE_GIT_COMMITTER_NAME="${BOOTSTRAP_CONTROL_PLANE_GIT_COMMITTER_NAME:?E_BOOTSTRAP_GIT_IDENTITY_REQUIRED}"
export CONTROL_PLANE_GIT_COMMITTER_EMAIL="${BOOTSTRAP_CONTROL_PLANE_GIT_COMMITTER_EMAIL:?E_BOOTSTRAP_GIT_IDENTITY_REQUIRED}"
# END CONTROL_PLANE_BOOTSTRAP_SETUP

# No derives CONTROL_PLANE_SOURCE_SHA del checkout dentro del bloque.

# BEGIN CONTROL_PLANE_SOURCE_BINDING
/usr/local/bin/python3 -I -S -B - <<'PY'
import hashlib
import os
import re
import stat
import subprocess
import sys

# Closed Git uses GIT_CONFIG_GLOBAL=/dev/null and core.hooksPath=/dev/null.
# Index hints are observed with ls-files -v -z; no filter-facing diff runs.
GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT = [
    "/usr/bin/git",
    "--no-pager",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "color.ui=false",
    "-c", "core.pager=cat",
    "-c", "user.useConfigOnly=true",
]
SUPPORTED_MODES = {b"100644", b"100755"}
MAXIMUM_METADATA_BYTES = 4 * 1_048_576
MAXIMUM_ENTRIES = 4096
MAXIMUM_FILE_BYTES = 2 * 1_048_576
MAXIMUM_TOTAL_BYTES = 32 * 1_048_576
MAXIMUM_PATH_BYTES = 4096


class BindingFailure(Exception):
    def __init__(self, code, detail=b""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:512]


def git(source, arguments):
    try:
        return subprocess.run(
            GIT + ["-C", source] + list(arguments),
            env=GIT_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BindingFailure(
            "E_CONTROL_PLANE_SOURCE_GIT",
            str(error).encode("utf-8", "replace"),
        )


def require(completed, code="E_CONTROL_PLANE_SOURCE_GIT"):
    if completed.returncode != 0:
        raise BindingFailure(code, completed.stderr)
    if len(completed.stdout) > MAXIMUM_METADATA_BYTES:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    return completed.stdout


def valid_path(path):
    if not path or len(path) > MAXIMUM_PATH_BYTES or path.startswith(b"/"):
        return False
    components = path.split(b"/")
    return all(component not in (b"", b".", b"..") for component in components)


def parse_index(source, object_length):
    raw = require(git(source, ["ls-files", "--stage", "-z"]))
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        if stage != b"0":
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_UNMERGED")
        if mode not in SUPPORTED_MODES:
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
        if (
            len(object_id) != object_length
            or re.fullmatch(b"[0-9a-f]+", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        entries[path] = (mode, object_id)

    tags_raw = require(git(source, ["ls-files", "-v", "-z"]))
    tags = [record for record in tags_raw.split(b"\0") if record]
    if len(tags) != len(entries):
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
    tagged_paths = set()
    for record in tags:
        if len(record) < 3 or record[:2] != b"H ":
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_FLAGS")
        path = record[2:]
        if not valid_path(path):
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        tagged_paths.add(path)
    if tagged_paths != set(entries):
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
    return entries


def parse_head(source, object_length):
    raw = require(git(source, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"]))
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ")
        except ValueError:
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_TREE_INVALID")
        if object_type != b"blob" or mode not in SUPPORTED_MODES:
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
        if (
            len(object_id) != object_length
            or re.fullmatch(b"[0-9a-f]+", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise BindingFailure("E_CONTROL_PLANE_SOURCE_TREE_INVALID")
        entries[path] = (mode, object_id)
    return entries


def read_regular_at(root_descriptor, path, expected_mode, total):
    components = path.split(b"/")
    directory = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise BindingFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
            executable = bool(before.st_mode & 0o111)
            if executable != (expected_mode == b"100755"):
                raise BindingFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
            if before.st_size < 0 or before.st_size > MAXIMUM_FILE_BYTES:
                raise BindingFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
            chunks = []
            observed = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                observed += len(chunk)
                total[0] += len(chunk)
                if (
                    observed > MAXIMUM_FILE_BYTES
                    or total[0] > MAXIMUM_TOTAL_BYTES
                ):
                    raise BindingFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(components[-1], dir_fd=directory, follow_symlinks=False)
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
            )
            identity_named = (
                named.st_dev,
                named.st_ino,
                named.st_mode,
                named.st_size,
            )
            if (
                identity_before != identity_after
                or identity_after != identity_named
                or observed != before.st_size
            ):
                raise BindingFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise BindingFailure(
            "E_CONTROL_PLANE_SOURCE_DIRTY",
            str(error).encode("utf-8", "replace"),
        )
    finally:
        os.close(directory)


def verify_raw_worktree(source, entries, algorithm):
    try:
        root_descriptor = os.open(
            source,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError as error:
        raise BindingFailure(
            "E_CONTROL_PLANE_SOURCE_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    total = [0]
    try:
        for path in sorted(entries):
            mode, expected_id = entries[path]
            payload = read_regular_at(root_descriptor, path, mode, total)
            header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
            observed_id = hashlib.new(algorithm, header + payload).hexdigest().encode()
            if observed_id != expected_id:
                raise BindingFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
    finally:
        os.close(root_descriptor)


try:
    source = os.environ.get("CONTROL_PLANE_SOURCE", "")
    source_sha = os.environ.get("CONTROL_PLANE_SOURCE_SHA", "")
    if not source:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_REQUIRED")
    if not source_sha:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_SHA_REQUIRED")
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_SHA_INVALID")

    root = require(
        git(source, ["rev-parse", "--show-toplevel"]),
        "E_CONTROL_PLANE_SOURCE_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    if root != source:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_INVALID")
    head = require(
        git(source, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_CONTROL_PLANE_SOURCE_INVALID",
    ).decode("ascii", "strict").strip()
    if head != source_sha:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_SHA_MISMATCH")
    algorithm = require(
        git(source, ["rev-parse", "--show-object-format"])
    ).decode("ascii", "strict").strip()
    if algorithm != "sha1":
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_OBJECT_FORMAT")
    object_length = 40

    index_entries = parse_index(source, object_length)
    head_entries = parse_head(source, object_length)
    if index_entries != head_entries:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
    verify_raw_worktree(source, index_entries, algorithm)

    untracked = require(
        git(source, ["ls-files", "--others", "--exclude-standard", "-z"])
    )
    if untracked:
        raise BindingFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
except (BindingFailure, UnicodeError, ValueError) as error:
    if isinstance(error, BindingFailure):
        code = error.code
        detail = error.detail
    else:
        code = "E_CONTROL_PLANE_SOURCE_INVALID"
        detail = str(error).encode("utf-8", "replace")[:512]
    sys.stderr.buffer.write(code.encode("ascii") + b"\n")
    if detail:
        sys.stderr.buffer.write(detail.rstrip(b"\n") + b"\n")
    raise SystemExit(1)

print("source_binding=PASS")
PY
# END CONTROL_PLANE_SOURCE_BINDING

/usr/bin/git -C "$NEW_PROJECT_ROOT" rev-parse --show-toplevel
test -f "$NEW_PROJECT_TASK"
```

`BOOTSTRAP_BASELINE_HEAD` es una entrada explícita elegida por el operador y
debe coincidir con el commit de baseline observado. No la derives mediante una
command substitution dentro del setup: un fallo del productor no puede quedar
oculto por una asignación.

Lee primero la guía desde el objeto de la fuente seleccionada, no desde el path
mutable del worktree:

```text
CONTROL_PLANE_SOURCE_SHA:templates/new-project/README.md
```

`CONTROL_PLANE_SOURCE_SHA` es obligatorio, contiene exactamente 40 caracteres
hexadecimales minúsculos y procede de la revisión que seleccionó la fuente; no
se obtiene asignando el `HEAD` que casualmente tenga el path. El bloque exige
ese `HEAD` exacto, que todos los tracked estén libres de los hints
`assume-unchanged` y `skip-worktree`, y cero cambios tracked, staged o
untracked. Para no ejecutar filtros hostiles, no usa `git diff` ni `status`:
compara entradas y OIDs del índice con el tree de `HEAD`, después lee cada
regular file de forma bounded, descriptor-relative y no-follow, calcula su blob
OID raw y lo compara con el índice. Modos symlink, Gitlink/submodule u otros no
soportados fallan con `E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE`. Git se ejecuta
con entorno global cerrado y hooks desactivados.
Repítelo
inmediatamente antes y después de la secuencia read-only de la sección 5. Un
path correcto no demuestra que el checkout esté limpio, actualizado o sea el
revisionado para el trabajo. Mismatch o suciedad es `UNKNOWN`: no corrijas refs
ni cambies de fuente o proyecto de forma implícita.

```text
selected_source=REQUIRED
selected_source_sha=REQUIRED
source_binding=PASS_BEFORE_EXTRACTION_BEFORE_AUDIT_AFTER_AUDIT
```

## 2. Fijar baseline y rama de trabajo

La base debe tener ya un commit consumer-owned y el repositorio debe estar
completamente limpio antes de introducir el bootstrap. Si `HEAD` no existe, el
resultado es `BLOCKED`: prepara y autoriza por separado un baseline propio del
consumidor —por ejemplo su README inicial—, commitéalo y vuelve a empezar. El
starter no crea ni se apropia del primer commit del proyecto.

Estas reglas todavía no están integradas en la base protegida del consumidor y
no pueden autoautorizar su propia rama o commit. Antes de integración, crear la
rama y commitear el bootstrap usa autoridad preexistente del proyecto o una
autorización exacta del operador.

```text
baseline_commit=REQUIRED
clean_worktree=REQUIRED
clean_index=REQUIRED
work_branch_before_precheck=REQUIRED
no_head=BLOCKED
bootstrap_authority=PREEXISTING_OR_EXACT_OPERATOR_AUTHORIZATION
```

Para el primer bootstrap sobre la base genérica `main`:

```bash
# BEGIN NEW_PROJECT_CREATE_WORK_BRANCH
/usr/local/bin/python3 -I -S -B - <<'PY'
import hashlib
import os
import re
import stat
import subprocess
import sys

GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT = [
    "/usr/bin/git",
    "--no-pager",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "color.ui=false",
    "-c", "core.pager=cat",
    "-c", "commit.gpgSign=false",
    "-c", "user.useConfigOnly=true",
]
SUPPORTED_MODES = {b"100644", b"100755"}
MAXIMUM_METADATA_BYTES = 4 * 1_048_576
MAXIMUM_ENTRIES = 4096
MAXIMUM_FILE_BYTES = 2 * 1_048_576
MAXIMUM_TOTAL_BYTES = 32 * 1_048_576
MAXIMUM_PATH_BYTES = 4096


class BranchFailure(Exception):
    def __init__(self, code, detail=b""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:512]


def git(root, arguments):
    try:
        return subprocess.run(
            GIT + ["-C", root] + list(arguments),
            env=GIT_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BranchFailure(
            "E_BOOTSTRAP_GIT",
            str(error).encode("utf-8", "replace"),
        )


def require(completed, code):
    if completed.returncode != 0:
        raise BranchFailure(code, completed.stderr)
    if len(completed.stdout) > MAXIMUM_METADATA_BYTES:
        raise BranchFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    return completed.stdout


def valid_path(path):
    if not path or len(path) > MAXIMUM_PATH_BYTES or path.startswith(b"/"):
        return False
    return all(component not in (b"", b".", b"..") for component in path.split(b"/"))


def parse_index(root):
    raw = require(
        git(root, ["ls-files", "--stage", "-z"]),
        "E_BOOTSTRAP_GIT",
    )
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise BranchFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        if stage != b"0":
            raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_UNMERGED")
        if mode not in SUPPORTED_MODES:
            raise BranchFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
        if (
            len(object_id) != 40
            or re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        entries[path] = (mode, object_id)

    tags_raw = require(
        git(root, ["ls-files", "-v", "-z"]),
        "E_BOOTSTRAP_GIT",
    )
    tags = [record for record in tags_raw.split(b"\0") if record]
    if len(tags) != len(entries):
        raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
    tagged_paths = set()
    for record in tags:
        if len(record) < 3 or record[:2] != b"H ":
            raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_FLAGS")
        path = record[2:]
        if not valid_path(path):
            raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        tagged_paths.add(path)
    if tagged_paths != set(entries):
        raise BranchFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
    return entries


def parse_head(root):
    raw = require(
        git(root, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"]),
        "E_BOOTSTRAP_GIT",
    )
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise BranchFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ")
        except ValueError:
            raise BranchFailure("E_BOOTSTRAP_TARGET_TREE_INVALID")
        if object_type != b"blob" or mode not in SUPPORTED_MODES:
            raise BranchFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
        if (
            len(object_id) != 40
            or re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise BranchFailure("E_BOOTSTRAP_TARGET_TREE_INVALID")
        entries[path] = (mode, object_id)
    return entries


def read_regular_at(root_descriptor, path, expected_mode, total):
    components = path.split(b"/")
    directory = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise BranchFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
            executable = bool(before.st_mode & 0o111)
            if executable != (expected_mode == b"100755"):
                raise BranchFailure("E_BOOTSTRAP_TARGET_DIRTY")
            if before.st_size < 0 or before.st_size > MAXIMUM_FILE_BYTES:
                raise BranchFailure("E_BOOTSTRAP_TARGET_BOUNDS")
            chunks = []
            observed = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                observed += len(chunk)
                total[0] += len(chunk)
                if (
                    observed > MAXIMUM_FILE_BYTES
                    or total[0] > MAXIMUM_TOTAL_BYTES
                ):
                    raise BranchFailure("E_BOOTSTRAP_TARGET_BOUNDS")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(components[-1], dir_fd=directory, follow_symlinks=False)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
            )
            named_identity = (
                named.st_dev,
                named.st_ino,
                named.st_mode,
                named.st_size,
            )
            if (
                before_identity != after_identity
                or after_identity != named_identity
                or observed != before.st_size
            ):
                raise BranchFailure("E_BOOTSTRAP_TARGET_DIRTY")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise BranchFailure(
            "E_BOOTSTRAP_TARGET_DIRTY",
            str(error).encode("utf-8", "replace"),
        )
    finally:
        os.close(directory)


def require_raw_clean(root):
    object_format = require(
        git(root, ["rev-parse", "--show-object-format"]),
        "E_BOOTSTRAP_GIT",
    ).decode("ascii", "strict").strip()
    if object_format != "sha1":
        raise BranchFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_OBJECT_FORMAT")
    index_entries = parse_index(root)
    head_entries = parse_head(root)
    if index_entries != head_entries:
        raise BranchFailure("E_BOOTSTRAP_TARGET_DIRTY")
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError as error:
        raise BranchFailure(
            "E_BOOTSTRAP_TARGET_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    total = [0]
    try:
        for path in sorted(index_entries):
            mode, expected_id = index_entries[path]
            payload = read_regular_at(root_descriptor, path, mode, total)
            header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
            observed_id = hashlib.sha1(header + payload).hexdigest().encode()
            if observed_id != expected_id:
                raise BranchFailure("E_BOOTSTRAP_TARGET_DIRTY")
    finally:
        os.close(root_descriptor)
    untracked = require(
        git(root, ["ls-files", "--others", "--exclude-standard", "-z"]),
        "E_BOOTSTRAP_GIT",
    )
    if untracked:
        raise BranchFailure("E_BOOTSTRAP_TARGET_DIRTY")


try:
    root = os.environ.get("NEW_PROJECT_ROOT", "")
    base = os.environ.get("NEW_PROJECT_BASE", "")
    work = os.environ.get("NEW_PROJECT_WORK_BRANCH", "")
    if not root:
        raise BranchFailure("E_BOOTSTRAP_TARGET_REQUIRED")
    if not base:
        raise BranchFailure("E_BOOTSTRAP_BASE_REQUIRED")
    if not work:
        raise BranchFailure("E_BOOTSTRAP_WORK_BRANCH_REQUIRED")

    observed_root = require(
        git(root, ["rev-parse", "--show-toplevel"]),
        "E_BOOTSTRAP_TARGET_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    if observed_root != root:
        raise BranchFailure("E_BOOTSTRAP_TARGET_INVALID")
    baseline = require(
        git(root, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_BOOTSTRAP_BASELINE_REQUIRED",
    ).decode("ascii", "strict").strip()
    current_branch = require(
        git(root, ["symbolic-ref", "--short", "HEAD"]),
        "E_BOOTSTRAP_BASE_BRANCH_MISMATCH",
    ).decode("utf-8", "strict").strip()
    if current_branch != base:
        raise BranchFailure("E_BOOTSTRAP_BASE_BRANCH_MISMATCH")
    if work == base:
        raise BranchFailure("E_BOOTSTRAP_WORK_BRANCH_INVALID")
    require(
        git(root, ["check-ref-format", "--branch", work]),
        "E_BOOTSTRAP_WORK_BRANCH_INVALID",
    )
    require_raw_clean(root)

    queried = git(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + work])
    if queried.returncode == 0:
        raise BranchFailure("E_BOOTSTRAP_WORK_BRANCH_EXISTS")
    if queried.returncode != 1:
        raise BranchFailure("E_BOOTSTRAP_BRANCH_QUERY", queried.stderr)
    ref_path_value = require(
        git(root, ["rev-parse", "--git-path", "refs/heads/" + work]),
        "E_BOOTSTRAP_BRANCH_QUERY",
    ).decode("utf-8", "strict").rstrip("\n")
    ref_path = ref_path_value
    if not os.path.isabs(ref_path):
        ref_path = os.path.join(root, ref_path)
    if os.path.lexists(ref_path):
        raise BranchFailure("E_BOOTSTRAP_BRANCH_QUERY")

    require(
        git(root, ["switch", "-c", work]),
        "E_BOOTSTRAP_BRANCH_CREATE",
    )
    observed_branch = require(
        git(root, ["symbolic-ref", "--short", "HEAD"]),
        "E_BOOTSTRAP_WORK_BRANCH_MISMATCH",
    ).decode("utf-8", "strict").strip()
    observed_head = require(
        git(root, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_BOOTSTRAP_BASELINE_DRIFT",
    ).decode("ascii", "strict").strip()
    if observed_branch != work:
        raise BranchFailure("E_BOOTSTRAP_WORK_BRANCH_MISMATCH")
    if observed_head != baseline:
        raise BranchFailure("E_BOOTSTRAP_BASELINE_DRIFT")
    require_raw_clean(root)
except (BranchFailure, UnicodeError, ValueError) as error:
    if isinstance(error, BranchFailure):
        code = error.code
        detail = error.detail
    else:
        code = "E_BOOTSTRAP_GIT"
        detail = str(error).encode("utf-8", "replace")[:512]
    sys.stderr.buffer.write(code.encode("ascii") + b"\n")
    if detail:
        sys.stderr.buffer.write(detail.rstrip(b"\n") + b"\n")
    raise SystemExit(1)

print("BASELINE_HEAD=" + baseline)
PY
# END NEW_PROJECT_CREATE_WORK_BRANCH
```

Una rama ya existente, un `HEAD` distinto, staging no vacío, cambio tracked o
untracked o una rama igual a la base es `BLOCKED`. Reobserva y reconcilia; no
reutilices ni muevas una rama incierta. Conserva el `BASELINE_HEAD` de 40
hexadecimales emitido por el bloque y expórtalo sin reinterpretarlo antes de la
sección 4. Los tests ejecutan este bloque contra repositorios temporales y
demuestran que HEAD ausente, dirty tracked/staged/untracked y rama equivocada
no crean rama, no stagean bytes y no crean commit.

La comprobación raw rechaza también todo tag de índice distinto del normal con
`E_BOOTSTRAP_TARGET_INDEX_FLAGS` y todo modo que no sea regular `100644` o
`100755` con `E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE`. No intenta dar soporte
implícito a symlinks o Gitlinks y nunca invoca clean filters para decidir si el
target está limpio.

## 3. Copiar solo las tres autoridades

Antes de cualquier escritura, comprueba conjuntamente los tres destinos con
semántica no-follow equivalente a `os.path.lexists`. Un fichero, directorio,
symlink o dangling symlink existente produce
`E_BOOTSTRAP_AUTHORITY_EXISTS`. En ese caso no escribas nada, no sobrescribas,
no auto-mergees y abre una reconciliación semántica revisada por separado.

El siguiente precheck es exclusivamente read-only. Valida que el root del
destino sea un directorio real, que `.codex` esté ausente o sea un directorio
real, que ninguna autoridad exista —incluidos dangling symlinks— y que las tres
fuentes sean los blobs de como máximo 1 MiB extraídos del objeto Git fijado. No
lee los templates del worktree vivo. Primero crea un staging temporal privado
desde los tres objetos exactos; si el binding o cualquier `cat-file blob`
falla, no publica el directorio final:

```bash
# BEGIN CONTROL_PLANE_STAGE_AUTHORITY_BLOBS
/usr/local/bin/python3 -I -S -B - <<'PY'
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT = [
    "/usr/bin/git",
    "--no-pager",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "color.ui=false",
    "-c", "core.pager=cat",
    "-c", "user.useConfigOnly=true",
]
RELATIVE_PATHS = (
    "AGENTS.md",
    ".codex/project-policy.toml",
    ".codex/resource-registry.toml",
)
SUPPORTED_MODES = {b"100644", b"100755"}
MAXIMUM_METADATA_BYTES = 4 * 1_048_576
MAXIMUM_ENTRIES = 4096
MAXIMUM_FILE_BYTES = 2 * 1_048_576
MAXIMUM_TOTAL_BYTES = 32 * 1_048_576
MAXIMUM_PATH_BYTES = 4096


class StageFailure(Exception):
    def __init__(self, code, detail=b""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:512]


def git(source, arguments):
    try:
        return subprocess.run(
            GIT + ["-C", source] + list(arguments),
            env=GIT_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StageFailure(
            "E_CONTROL_PLANE_SOURCE_GIT",
            str(error).encode("utf-8", "replace"),
        )


def require(completed, code="E_CONTROL_PLANE_SOURCE_GIT"):
    if completed.returncode != 0:
        raise StageFailure(code, completed.stderr)
    if len(completed.stdout) > MAXIMUM_METADATA_BYTES:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    return completed.stdout


def valid_path(path):
    if not path or len(path) > MAXIMUM_PATH_BYTES or path.startswith(b"/"):
        return False
    return all(component not in (b"", b".", b"..") for component in path.split(b"/"))


def parse_index(source):
    raw = require(git(source, ["ls-files", "--stage", "-z"]))
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        if stage != b"0":
            raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_UNMERGED")
        if mode not in SUPPORTED_MODES:
            raise StageFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
        if (
            re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        entries[path] = (mode, object_id)
    tags_raw = require(git(source, ["ls-files", "-v", "-z"]))
    tags = [record for record in tags_raw.split(b"\0") if record]
    if len(tags) != len(entries):
        raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
    tagged_paths = set()
    for record in tags:
        if len(record) < 3 or record[:2] != b"H ":
            raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_FLAGS")
        path = record[2:]
        if not valid_path(path):
            raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
        tagged_paths.add(path)
    if tagged_paths != set(entries):
        raise StageFailure("E_CONTROL_PLANE_SOURCE_INDEX_INVALID")
    return entries


def parse_head(source):
    raw = require(git(source, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"]))
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ")
        except ValueError:
            raise StageFailure("E_CONTROL_PLANE_SOURCE_TREE_INVALID")
        if object_type != b"blob" or mode not in SUPPORTED_MODES:
            raise StageFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
        if (
            re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise StageFailure("E_CONTROL_PLANE_SOURCE_TREE_INVALID")
        entries[path] = (mode, object_id)
    return entries


def read_regular_at(root_descriptor, path, expected_mode, total):
    components = path.split(b"/")
    directory = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise StageFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_MODE")
            executable = bool(before.st_mode & 0o111)
            if executable != (expected_mode == b"100755"):
                raise StageFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
            if before.st_size < 0 or before.st_size > MAXIMUM_FILE_BYTES:
                raise StageFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
            chunks = []
            observed = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                observed += len(chunk)
                total[0] += len(chunk)
                if (
                    observed > MAXIMUM_FILE_BYTES
                    or total[0] > MAXIMUM_TOTAL_BYTES
                ):
                    raise StageFailure("E_CONTROL_PLANE_SOURCE_BOUNDS")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(components[-1], dir_fd=directory, follow_symlinks=False)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
            )
            named_identity = (
                named.st_dev,
                named.st_ino,
                named.st_mode,
                named.st_size,
            )
            if (
                before_identity != after_identity
                or after_identity != named_identity
                or observed != before.st_size
            ):
                raise StageFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise StageFailure(
            "E_CONTROL_PLANE_SOURCE_DIRTY",
            str(error).encode("utf-8", "replace"),
        )
    finally:
        os.close(directory)


def bind(source, source_sha):
    root = require(
        git(source, ["rev-parse", "--show-toplevel"]),
        "E_CONTROL_PLANE_SOURCE_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    if root != source:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_INVALID")
    head = require(
        git(source, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_CONTROL_PLANE_SOURCE_INVALID",
    ).decode("ascii", "strict").strip()
    if head != source_sha:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_SHA_MISMATCH")
    object_format = require(
        git(source, ["rev-parse", "--show-object-format"])
    ).decode("ascii", "strict").strip()
    if object_format != "sha1":
        raise StageFailure("E_CONTROL_PLANE_SOURCE_UNSUPPORTED_OBJECT_FORMAT")
    index_entries = parse_index(source)
    if index_entries != parse_head(source):
        raise StageFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
    try:
        root_descriptor = os.open(
            source,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError as error:
        raise StageFailure(
            "E_CONTROL_PLANE_SOURCE_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    total = [0]
    try:
        for path in sorted(index_entries):
            mode, expected_id = index_entries[path]
            payload = read_regular_at(root_descriptor, path, mode, total)
            header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
            observed_id = hashlib.sha1(header + payload).hexdigest().encode()
            if observed_id != expected_id:
                raise StageFailure("E_CONTROL_PLANE_SOURCE_DIRTY")
    finally:
        os.close(root_descriptor)
    if require(git(source, ["ls-files", "--others", "--exclude-standard", "-z"])):
        raise StageFailure("E_CONTROL_PLANE_SOURCE_DIRTY")


temporary = None
try:
    source = os.environ.get("CONTROL_PLANE_SOURCE", "")
    source_sha = os.environ.get("CONTROL_PLANE_SOURCE_SHA", "")
    stage_value = os.environ.get("CONTROL_PLANE_AUTHORITY_STAGE", "")
    if not source:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_REQUIRED")
    if not source_sha:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_SHA_REQUIRED")
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise StageFailure("E_CONTROL_PLANE_SOURCE_SHA_INVALID")
    if not stage_value:
        raise StageFailure("E_BOOTSTRAP_AUTHORITY_STAGE_REQUIRED")
    stage = Path(stage_value)
    if not stage.is_absolute() or os.path.lexists(stage):
        raise StageFailure("E_BOOTSTRAP_AUTHORITY_STAGE_EXISTS")
    parent = stage.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise StageFailure(
            "E_BOOTSTRAP_AUTHORITY_STAGE_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    if not stat.S_ISDIR(parent_metadata.st_mode) or parent.is_symlink():
        raise StageFailure("E_BOOTSTRAP_AUTHORITY_STAGE_INVALID")

    bind(source, source_sha)
    payloads = {}
    for relative in RELATIVE_PATHS:
        object_name = source_sha + ":templates/new-project/" + relative
        payload = require(
            git(source, ["cat-file", "blob", object_name]),
            "E_BOOTSTRAP_SOURCE_OBJECT_INVALID",
        )
        if not 0 < len(payload) <= MAXIMUM_FILE_BYTES:
            raise StageFailure("E_BOOTSTRAP_SOURCE_OBJECT_INVALID")
        payloads[relative] = payload

    temporary = Path(
        tempfile.mkdtemp(prefix=".control-plane-authority-", dir=str(parent))
    )
    temporary.chmod(0o700)
    for relative in RELATIVE_PATHS:
        destination = temporary / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(payloads[relative])
        destination.chmod(0o600)
    temporary.rename(stage)
    temporary = None
except (StageFailure, OSError, UnicodeError, ValueError) as error:
    if temporary is not None:
        shutil.rmtree(temporary, ignore_errors=True)
    if isinstance(error, StageFailure):
        code = error.code
        detail = error.detail
    else:
        code = "E_BOOTSTRAP_AUTHORITY_STAGE_INVALID"
        detail = str(error).encode("utf-8", "replace")[:512]
    sys.stderr.buffer.write(code.encode("ascii") + b"\n")
    if detail:
        sys.stderr.buffer.write(detail.rstrip(b"\n") + b"\n")
    raise SystemExit(1)

print("authority_stage=PASS")
PY
# END CONTROL_PLANE_STAGE_AUTHORITY_BLOBS
```

El bloque repite el binding inmediatamente antes de extraer y solo lee objetos
`CONTROL_PLANE_SOURCE_SHA:path`; una modificación posterior del worktree vivo
no puede cambiar esos bytes. El precheck siguiente es exclusivamente read-only
sobre el staging publicado y el destino; no crea directorios ni ficheros:

```bash
/usr/local/bin/python3 -I -S -B - "$CONTROL_PLANE_AUTHORITY_STAGE" "$NEW_PROJECT_ROOT" <<'PY'
from hashlib import sha256
import os
import stat
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
maximum_bytes = 1_048_576
relative_paths = (
    Path("AGENTS.md"),
    Path(".codex/project-policy.toml"),
    Path(".codex/resource-registry.toml"),
)
destinations = tuple((source / item, target / item) for item in relative_paths)

try:
    target_metadata = target.lstat()
except OSError:
    print("E_BOOTSTRAP_AUTHORITY_EXISTS", file=sys.stderr)
    raise SystemExit(1)
if not stat.S_ISDIR(target_metadata.st_mode) or stat.S_ISLNK(
    target_metadata.st_mode
):
    print("E_BOOTSTRAP_AUTHORITY_EXISTS", file=sys.stderr)
    raise SystemExit(1)

codex = target / ".codex"
if os.path.lexists(codex):
    codex_metadata = codex.lstat()
    if not stat.S_ISDIR(codex_metadata.st_mode) or stat.S_ISLNK(
        codex_metadata.st_mode
    ):
        print("E_BOOTSTRAP_AUTHORITY_EXISTS", file=sys.stderr)
        raise SystemExit(1)

if any(os.path.lexists(destination) for _, destination in destinations):
    print("E_BOOTSTRAP_AUTHORITY_EXISTS", file=sys.stderr)
    raise SystemExit(1)

for source_path, _ in destinations:
    before = source_path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum_bytes:
        print("E_BOOTSTRAP_SOURCE_INVALID", file=sys.stderr)
        raise SystemExit(1)
    payload = source_path.read_bytes()
    after = source_path.lstat()
    identity_before = (before.st_dev, before.st_ino, before.st_mode, before.st_size)
    identity_after = (after.st_dev, after.st_ino, after.st_mode, after.st_size)
    if identity_before != identity_after or len(payload) != before.st_size:
        print("E_BOOTSTRAP_SOURCE_INVALID", file=sys.stderr)
        raise SystemExit(1)
    relative = source_path.relative_to(source)
    print(f"source_sha256 {relative.as_posix()} {sha256(payload).hexdigest()}")

print("precheck=READ_ONLY")
PY
```

Después del precheck, un único writer Codex crea los tres ficheros en una sola
operación `apply_patch`. Esa operación contiene exactamente tres entradas
`*** Add File:` —`AGENTS.md`, `.codex/project-policy.toml` y
`.codex/resource-registry.toml`— con los bytes del staging ligado. No
uses `cp`, `install`, `rsync`, redirecciones, overwrite ni auto-merge. Si
`apply_patch` falla o informa que un destino apareció, emite Stable Pause y no
intentes limpieza incierta: la observación ya no demuestra qué bytes son
propios.

Reobserva el root, `.codex` y los tres destinos después de `apply_patch`, exige
que sean ficheros regulares no symlink y compara sus SHA-256 con las fuentes:

```bash
/usr/local/bin/python3 -I -S -B - "$CONTROL_PLANE_AUTHORITY_STAGE" "$NEW_PROJECT_ROOT" <<'PY'
from hashlib import sha256
import stat
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
relative_paths = (
    Path("AGENTS.md"),
    Path(".codex/project-policy.toml"),
    Path(".codex/resource-registry.toml"),
)

for parent in (target, target / ".codex"):
    metadata = parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SystemExit("E_BOOTSTRAP_AUTHORITY_EXISTS")

for relative in relative_paths:
    source_path = source / relative
    destination = target / relative
    source_metadata = source_path.lstat()
    destination_metadata = destination.lstat()
    if not stat.S_ISREG(source_metadata.st_mode):
        raise SystemExit("E_BOOTSTRAP_SOURCE_INVALID")
    if not stat.S_ISREG(destination_metadata.st_mode):
        raise SystemExit("E_BOOTSTRAP_AUTHORITY_EXISTS")
    source_payload = source_path.read_bytes()
    destination_payload = destination.read_bytes()
    source_digest = sha256(source_payload).hexdigest()
    destination_digest = sha256(destination_payload).hexdigest()
    if source_digest != destination_digest:
        raise SystemExit("E_BOOTSTRAP_AUTHORITY_MISMATCH")
    print(f"authority_sha256 {relative.as_posix()} {destination_digest}")
PY
```

El precheck y `apply_patch` no forman una transacción frente a otro proceso del
mismo UID. El contrato operativo exige single writer; si no puedes excluir un
writer same-UID entre ambas observaciones, el resultado es `UNKNOWN` y se
detiene antes de escribir.

La prueba de frontera es el postcheck raw anterior: los tres digests ligados
deben coincidir y el README del consumidor debe conservar sus bytes. No uses
`git status` ni `git diff` para esta comprobación porque pueden consultar
filtros definidos por el target. La presencia de cualquier otra autoridad
exige parar y revisar; no repitas la copia para forzarla.

## 4. Personalizar y commitear el bootstrap

Permanece en la rama no protegida fijada en la sección 2. Sustituye todas las apariciones de
`new-project` y revisa como mínimo `project_kind`, remote, base, estrategia de
integración, gates, recursos, comandos reales de test/build y evidencia de
release. La validez de esquema no prueba que esos valores describan el
proyecto real.

Ejecuta los gates propios que ya tenga el proyecto y después preserva las tres
autoridades como una unidad revisable. El bloque no ejecuta hooks ni filtros
`clean`: escribe cada blob desde los bytes crudos, lo enlaza al índice y lo
compara de nuevo antes de commitear:

```bash
# BEGIN NEW_PROJECT_COMMIT_BOOTSTRAP
/usr/local/bin/python3 -I -S -B - <<'PY'
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/var/empty",
    "XDG_CONFIG_HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT = [
    "/usr/bin/git",
    "--no-pager",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
    "-c", "color.ui=false",
    "-c", "core.pager=cat",
    "-c", "commit.gpgSign=false",
    "-c", "user.useConfigOnly=true",
]
AUTHORITY_PATHS = (
    "AGENTS.md",
    ".codex/project-policy.toml",
    ".codex/resource-registry.toml",
)
SUPPORTED_MODES = {b"100644", b"100755"}
MAXIMUM_METADATA_BYTES = 4 * 1_048_576
MAXIMUM_ENTRIES = 4096
MAXIMUM_FILE_BYTES = 2 * 1_048_576
MAXIMUM_TOTAL_BYTES = 32 * 1_048_576
MAXIMUM_INDEX_BYTES = 64 * 1_048_576
MAXIMUM_PATH_BYTES = 4096


class CommitFailure(Exception):
    def __init__(self, code, detail=b""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:512]


def git(root, arguments, environment=None):
    try:
        return subprocess.run(
            GIT + ["-C", root] + list(arguments),
            env=GIT_ENV if environment is None else environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommitFailure(
            "E_BOOTSTRAP_GIT",
            str(error).encode("utf-8", "replace"),
        )


def require(completed, code):
    if completed.returncode != 0:
        raise CommitFailure(code, completed.stderr)
    if len(completed.stdout) > MAXIMUM_METADATA_BYTES:
        raise CommitFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    return completed.stdout


def valid_path(path):
    if not path or len(path) > MAXIMUM_PATH_BYTES or path.startswith(b"/"):
        return False
    return all(component not in (b"", b".", b"..") for component in path.split(b"/"))


def parse_index(root, environment):
    raw = require(
        git(root, ["ls-files", "--stage", "-z"], environment),
        "E_BOOTSTRAP_GIT",
    )
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise CommitFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        if stage != b"0":
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_UNMERGED")
        if mode not in SUPPORTED_MODES:
            raise CommitFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
        if (
            re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        entries[path] = (mode, object_id)

    tags_raw = require(
        git(root, ["ls-files", "-v", "-z"], environment),
        "E_BOOTSTRAP_GIT",
    )
    tags = [record for record in tags_raw.split(b"\0") if record]
    if len(tags) != len(entries):
        raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
    tagged_paths = set()
    for record in tags:
        if len(record) < 3 or record[:2] != b"H ":
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_FLAGS")
        path = record[2:]
        if not valid_path(path):
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        tagged_paths.add(path)
    if tagged_paths != set(entries):
        raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
    return entries


def parse_tree(root, revision):
    raw = require(
        git(root, ["ls-tree", "-r", "-z", "--full-tree", revision]),
        "E_BOOTSTRAP_GIT",
    )
    records = [record for record in raw.split(b"\0") if record]
    if not records or len(records) > MAXIMUM_ENTRIES:
        raise CommitFailure("E_BOOTSTRAP_TARGET_BOUNDS")
    entries = {}
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ")
        except ValueError:
            raise CommitFailure("E_BOOTSTRAP_TARGET_TREE_INVALID")
        if object_type != b"blob" or mode not in SUPPORTED_MODES:
            raise CommitFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
        if (
            re.fullmatch(b"[0-9a-f]{40}", object_id) is None
            or not valid_path(path)
            or path in entries
        ):
            raise CommitFailure("E_BOOTSTRAP_TARGET_TREE_INVALID")
        entries[path] = (mode, object_id)
    return entries


def read_regular_at(root_descriptor, path, expected_mode, total):
    components = path.split(b"/")
    directory = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise CommitFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_MODE")
            executable = bool(before.st_mode & 0o111)
            if executable != (expected_mode == b"100755"):
                raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
            if before.st_size < 0 or before.st_size > MAXIMUM_FILE_BYTES:
                raise CommitFailure("E_BOOTSTRAP_TARGET_BOUNDS")
            chunks = []
            observed = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                observed += len(chunk)
                total[0] += len(chunk)
                if (
                    observed > MAXIMUM_FILE_BYTES
                    or total[0] > MAXIMUM_TOTAL_BYTES
                ):
                    raise CommitFailure("E_BOOTSTRAP_TARGET_BOUNDS")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(components[-1], dir_fd=directory, follow_symlinks=False)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
            )
            named_identity = (
                named.st_dev,
                named.st_ino,
                named.st_mode,
                named.st_size,
            )
            if (
                before_identity != after_identity
                or after_identity != named_identity
                or observed != before.st_size
            ):
                raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CommitFailure(
            "E_BOOTSTRAP_TARGET_DIRTY",
            str(error).encode("utf-8", "replace"),
        )
    finally:
        os.close(directory)


def verify_worktree(root, entries):
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError as error:
        raise CommitFailure(
            "E_BOOTSTRAP_TARGET_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    total = [0]
    try:
        for path in sorted(entries):
            mode, expected_id = entries[path]
            payload = read_regular_at(root_descriptor, path, mode, total)
            header = b"blob " + str(len(payload)).encode("ascii") + b"\0"
            observed_id = hashlib.sha1(header + payload).hexdigest().encode()
            if observed_id != expected_id:
                raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
    finally:
        os.close(root_descriptor)


def untracked_paths(root, environment):
    raw = require(
        git(
            root,
            ["ls-files", "--others", "--exclude-standard", "-z"],
            environment,
        ),
        "E_BOOTSTRAP_GIT",
    )
    paths = set()
    for path in (record for record in raw.split(b"\0") if record):
        if not valid_path(path) or path in paths:
            raise CommitFailure("E_BOOTSTRAP_TARGET_INDEX_INVALID")
        paths.add(path)
    return paths


def read_index_path(index_path):
    try:
        descriptor = os.open(
            index_path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or before.st_size < 0
                or before.st_size > MAXIMUM_INDEX_BYTES
            ):
                raise CommitFailure("E_BOOTSTRAP_INDEX_INVALID")
            chunks = []
            observed = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > MAXIMUM_INDEX_BYTES:
                    raise CommitFailure("E_BOOTSTRAP_INDEX_INVALID")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(index_path, follow_symlinks=False)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_uid,
                before.st_nlink,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_uid,
                after.st_nlink,
            )
            named_identity = (
                named.st_dev,
                named.st_ino,
                named.st_mode,
                named.st_size,
                named.st_uid,
                named.st_nlink,
            )
            if (
                before_identity != after_identity
                or after_identity != named_identity
                or observed != before.st_size
            ):
                raise CommitFailure("E_BOOTSTRAP_INDEX_DRIFT")
            return b"".join(chunks), stat.S_IMODE(before.st_mode)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CommitFailure(
            "E_BOOTSTRAP_INDEX_INVALID",
            str(error).encode("utf-8", "replace"),
        )


def write_all(descriptor, payload):
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short index write")
        offset += written


def create_index_file(parent, prefix, payload, mode):
    descriptor, value = tempfile.mkstemp(prefix=prefix, dir=str(parent))
    path = Path(value)
    try:
        os.fchmod(descriptor, mode)
        write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    os.close(descriptor)
    return path


def sync_parent(parent):
    descriptor = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exact_identity(environment, key_name, key_email, output):
    name = environment[key_name]
    email = environment[key_email]
    expected = (name + " <" + email + "> ").encode("utf-8")
    if not output.startswith(expected):
        raise CommitFailure("E_BOOTSTRAP_GIT_IDENTITY_INVALID")


root = ""
baseline = ""
index_path = None
index_before = b""
index_mode = 0
private_index = None
index_published = False
ref_advanced = False
try:
    root = os.environ.get("NEW_PROJECT_ROOT", "")
    base = os.environ.get("NEW_PROJECT_BASE", "")
    work = os.environ.get("NEW_PROJECT_WORK_BRANCH", "")
    baseline = os.environ.get("BASELINE_HEAD", "")
    if not root:
        raise CommitFailure("E_BOOTSTRAP_TARGET_REQUIRED")
    if not base:
        raise CommitFailure("E_BOOTSTRAP_BASE_REQUIRED")
    if not work:
        raise CommitFailure("E_BOOTSTRAP_WORK_BRANCH_REQUIRED")
    if not baseline:
        raise CommitFailure("E_BOOTSTRAP_BASELINE_REQUIRED")
    if re.fullmatch(r"[0-9a-f]{40}", baseline) is None:
        raise CommitFailure("E_BOOTSTRAP_BASELINE_INVALID")

    identity_keys = (
        "CONTROL_PLANE_GIT_AUTHOR_NAME",
        "CONTROL_PLANE_GIT_AUTHOR_EMAIL",
        "CONTROL_PLANE_GIT_COMMITTER_NAME",
        "CONTROL_PLANE_GIT_COMMITTER_EMAIL",
    )
    identity = {}
    for key in identity_keys:
        value = os.environ.get(key, "")
        if not value:
            raise CommitFailure("E_BOOTSTRAP_GIT_IDENTITY_REQUIRED")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise CommitFailure("E_BOOTSTRAP_GIT_IDENTITY_INVALID")
        identity[key] = value
    for key in (
        "CONTROL_PLANE_GIT_AUTHOR_NAME",
        "CONTROL_PLANE_GIT_COMMITTER_NAME",
    ):
        if "<" in identity[key] or ">" in identity[key]:
            raise CommitFailure("E_BOOTSTRAP_GIT_IDENTITY_INVALID")
    for key in (
        "CONTROL_PLANE_GIT_AUTHOR_EMAIL",
        "CONTROL_PLANE_GIT_COMMITTER_EMAIL",
    ):
        if (
            "<" in identity[key]
            or ">" in identity[key]
            or any(character.isspace() for character in identity[key])
        ):
            raise CommitFailure("E_BOOTSTRAP_GIT_IDENTITY_INVALID")

    commit_environment = dict(GIT_ENV)
    commit_environment.update(
        {
            "GIT_AUTHOR_NAME": identity["CONTROL_PLANE_GIT_AUTHOR_NAME"],
            "GIT_AUTHOR_EMAIL": identity["CONTROL_PLANE_GIT_AUTHOR_EMAIL"],
            "GIT_COMMITTER_NAME": identity["CONTROL_PLANE_GIT_COMMITTER_NAME"],
            "GIT_COMMITTER_EMAIL": identity["CONTROL_PLANE_GIT_COMMITTER_EMAIL"],
        }
    )
    author_ident = require(
        git(root, ["var", "GIT_AUTHOR_IDENT"], commit_environment),
        "E_BOOTSTRAP_GIT_IDENTITY_INVALID",
    )
    committer_ident = require(
        git(root, ["var", "GIT_COMMITTER_IDENT"], commit_environment),
        "E_BOOTSTRAP_GIT_IDENTITY_INVALID",
    )
    exact_identity(
        identity,
        "CONTROL_PLANE_GIT_AUTHOR_NAME",
        "CONTROL_PLANE_GIT_AUTHOR_EMAIL",
        author_ident,
    )
    exact_identity(
        identity,
        "CONTROL_PLANE_GIT_COMMITTER_NAME",
        "CONTROL_PLANE_GIT_COMMITTER_EMAIL",
        committer_ident,
    )

    observed_root = require(
        git(root, ["rev-parse", "--show-toplevel"]),
        "E_BOOTSTRAP_TARGET_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    if observed_root != root:
        raise CommitFailure("E_BOOTSTRAP_TARGET_INVALID")
    object_format = require(
        git(root, ["rev-parse", "--show-object-format"]),
        "E_BOOTSTRAP_GIT",
    ).decode("ascii", "strict").strip()
    if object_format != "sha1":
        raise CommitFailure("E_BOOTSTRAP_TARGET_UNSUPPORTED_OBJECT_FORMAT")
    current_head = require(
        git(root, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_BOOTSTRAP_BASELINE_REQUIRED",
    ).decode("ascii", "strict").strip()
    current_branch = require(
        git(root, ["symbolic-ref", "--short", "HEAD"]),
        "E_BOOTSTRAP_WORK_BRANCH_MISMATCH",
    ).decode("utf-8", "strict").strip()
    if current_branch != work:
        raise CommitFailure("E_BOOTSTRAP_WORK_BRANCH_MISMATCH")
    if work == base:
        raise CommitFailure("E_BOOTSTRAP_WORK_BRANCH_INVALID")
    if current_head != baseline:
        raise CommitFailure("E_BOOTSTRAP_BASELINE_DRIFT")
    base_head = require(
        git(root, ["rev-parse", "--verify", "refs/heads/" + base + "^{commit}"]),
        "E_BOOTSTRAP_BASELINE_DRIFT",
    ).decode("ascii", "strict").strip()
    if base_head != baseline:
        raise CommitFailure("E_BOOTSTRAP_BASELINE_DRIFT")

    baseline_entries = parse_index(root, GIT_ENV)
    if baseline_entries != parse_tree(root, "HEAD"):
        raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
    verify_worktree(root, baseline_entries)
    expected_authorities = {item.encode("utf-8") for item in AUTHORITY_PATHS}
    untracked_before = untracked_paths(root, GIT_ENV)
    if untracked_before != expected_authorities:
        if expected_authorities.issubset(untracked_before):
            raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
        raise CommitFailure("E_BOOTSTRAP_AUTHORITY_SET_MISMATCH")

    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except OSError as error:
        raise CommitFailure(
            "E_BOOTSTRAP_TARGET_INVALID",
            str(error).encode("utf-8", "replace"),
        )
    raw_payloads = {}
    authority_total = [0]
    try:
        for relative in AUTHORITY_PATHS:
            path = relative.encode("utf-8")
            payload = read_regular_at(
                root_descriptor,
                path,
                b"100644",
                authority_total,
            )
            if b"new-project" in payload:
                raise CommitFailure("E_BOOTSTRAP_CUSTOMIZATION_REQUIRED")
            raw_payloads[relative] = payload
    finally:
        os.close(root_descriptor)

    git_directory = require(
        git(root, ["rev-parse", "--absolute-git-dir"]),
        "E_BOOTSTRAP_INDEX_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    index_value = require(
        git(root, ["rev-parse", "--git-path", "index"]),
        "E_BOOTSTRAP_INDEX_INVALID",
    ).decode("utf-8", "strict").rstrip("\n")
    candidate_index = Path(index_value)
    if not candidate_index.is_absolute():
        candidate_index = Path(root) / candidate_index
    if candidate_index.parent.resolve(strict=True) != Path(git_directory).resolve(strict=True):
        raise CommitFailure("E_BOOTSTRAP_INDEX_INVALID")
    index_path = candidate_index
    index_before, index_mode = read_index_path(index_path)
    index_before_digest = hashlib.sha256(index_before).digest()
    private_index = create_index_file(
        index_path.parent,
        ".control-plane-private-index-",
        index_before,
        index_mode,
    )
    private_environment = dict(GIT_ENV)
    private_environment["GIT_INDEX_FILE"] = str(private_index)
    private_commit_environment = dict(commit_environment)
    private_commit_environment["GIT_INDEX_FILE"] = str(private_index)

    object_ids = {}
    for relative in AUTHORITY_PATHS:
        object_id = require(
            git(root, ["hash-object", "-w", "--no-filters", "--", relative]),
            "E_BOOTSTRAP_BLOB_WRITE",
        ).decode("ascii", "strict").strip()
        if re.fullmatch(r"[0-9a-f]{40}", object_id) is None:
            raise CommitFailure("E_BOOTSTRAP_BLOB_WRITE")
        object_payload = require(
            git(root, ["cat-file", "blob", object_id]),
            "E_BOOTSTRAP_AUTHORITY_BLOB_MISMATCH",
        )
        if object_payload != raw_payloads[relative]:
            raise CommitFailure("E_BOOTSTRAP_AUTHORITY_BLOB_MISMATCH")
        object_ids[relative] = object_id

    for relative in AUTHORITY_PATHS:
        require(
            git(
                root,
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    object_ids[relative],
                    relative,
                ],
                private_environment,
            ),
            "E_BOOTSTRAP_INDEX_WRITE",
        )

    expected_entries = dict(baseline_entries)
    for relative in AUTHORITY_PATHS:
        expected_entries[relative.encode("utf-8")] = (
            b"100644",
            object_ids[relative].encode("ascii"),
        )
    private_entries = parse_index(root, private_environment)
    if private_entries != expected_entries:
        raise CommitFailure("E_BOOTSTRAP_INDEX_MISMATCH")
    verify_worktree(root, private_entries)
    if untracked_paths(root, private_environment):
        raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")

    tree_id = require(
        git(root, ["write-tree"], private_environment),
        "E_BOOTSTRAP_COMMIT_FAILED",
    ).decode("ascii", "strict").strip()
    if parse_tree(root, tree_id) != expected_entries:
        raise CommitFailure("E_BOOTSTRAP_COMMIT_MISMATCH")
    commit_id = require(
        git(
            root,
            [
                "commit-tree",
                tree_id,
                "-p",
                baseline,
                "-m",
                "chore: add project audit governance",
            ],
            private_commit_environment,
        ),
        "E_BOOTSTRAP_COMMIT_FAILED",
    ).decode("ascii", "strict").strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit_id) is None:
        raise CommitFailure("E_BOOTSTRAP_COMMIT_FAILED")
    observed_parent = require(
        git(root, ["rev-parse", "--verify", commit_id + "^"]),
        "E_BOOTSTRAP_COMMIT_MISMATCH",
    ).decode("ascii", "strict").strip()
    if observed_parent != baseline or parse_tree(root, commit_id) != expected_entries:
        raise CommitFailure("E_BOOTSTRAP_COMMIT_MISMATCH")
    observed_identity = require(
        git(
            root,
            ["show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", commit_id],
        ),
        "E_BOOTSTRAP_COMMIT_MISMATCH",
    ).rstrip(b"\n").split(b"\0")
    expected_identity = [
        identity["CONTROL_PLANE_GIT_AUTHOR_NAME"].encode("utf-8"),
        identity["CONTROL_PLANE_GIT_AUTHOR_EMAIL"].encode("utf-8"),
        identity["CONTROL_PLANE_GIT_COMMITTER_NAME"].encode("utf-8"),
        identity["CONTROL_PLANE_GIT_COMMITTER_EMAIL"].encode("utf-8"),
    ]
    if observed_identity != expected_identity:
        raise CommitFailure("E_BOOTSTRAP_COMMIT_MISMATCH")

    current_index, current_mode = read_index_path(index_path)
    if (
        current_index != index_before
        or current_mode != index_mode
        or hashlib.sha256(current_index).digest() != index_before_digest
    ):
        raise CommitFailure("E_BOOTSTRAP_INDEX_DRIFT")
    private_bytes, private_mode = read_index_path(private_index)
    if private_mode != index_mode:
        raise CommitFailure("E_BOOTSTRAP_INDEX_MISMATCH")
    os.replace(private_index, index_path)
    private_index = None
    index_published = True
    sync_parent(index_path.parent)
    published_bytes, published_mode = read_index_path(index_path)
    if published_bytes != private_bytes or published_mode != index_mode:
        raise CommitFailure("E_BOOTSTRAP_INDEX_MISMATCH")

    require(
        git(
            root,
            [
                "update-ref",
                "-m",
                "commit: chore: add project audit governance",
                "refs/heads/" + work,
                commit_id,
                baseline,
            ],
        ),
        "E_BOOTSTRAP_COMMIT_FAILED",
    )
    ref_advanced = True

    final_head = require(
        git(root, ["rev-parse", "--verify", "HEAD^{commit}"]),
        "E_BOOTSTRAP_COMMIT_MISMATCH",
    ).decode("ascii", "strict").strip()
    final_parent = require(
        git(root, ["rev-parse", "--verify", "HEAD^"]),
        "E_BOOTSTRAP_BASELINE_DRIFT",
    ).decode("ascii", "strict").strip()
    final_branch = require(
        git(root, ["symbolic-ref", "--short", "HEAD"]),
        "E_BOOTSTRAP_WORK_BRANCH_MISMATCH",
    ).decode("utf-8", "strict").strip()
    final_base = require(
        git(root, ["rev-parse", "--verify", "refs/heads/" + base + "^{commit}"]),
        "E_BOOTSTRAP_BASELINE_DRIFT",
    ).decode("ascii", "strict").strip()
    if final_head != commit_id or final_parent != baseline:
        raise CommitFailure("E_BOOTSTRAP_COMMIT_MISMATCH")
    if final_branch != work:
        raise CommitFailure("E_BOOTSTRAP_WORK_BRANCH_MISMATCH")
    if final_base != baseline:
        raise CommitFailure("E_BOOTSTRAP_BASELINE_DRIFT")
    final_entries = parse_index(root, GIT_ENV)
    if final_entries != expected_entries or final_entries != parse_tree(root, "HEAD"):
        raise CommitFailure("E_BOOTSTRAP_COMMIT_MISMATCH")
    verify_worktree(root, final_entries)
    if untracked_paths(root, GIT_ENV):
        raise CommitFailure("E_BOOTSTRAP_TARGET_DIRTY")
except (CommitFailure, OSError, UnicodeError, ValueError) as error:
    if isinstance(error, CommitFailure):
        code = error.code
        detail = error.detail
    else:
        code = "E_BOOTSTRAP_COMMIT_FAILED"
        detail = str(error).encode("utf-8", "replace")[:512]
    if index_published and not ref_advanced and index_path is not None:
        restore_temporary = None
        try:
            restore_temporary = create_index_file(
                index_path.parent,
                ".control-plane-index-restore-",
                index_before,
                index_mode,
            )
            os.replace(restore_temporary, index_path)
            restore_temporary = None
            sync_parent(index_path.parent)
            restored_bytes, restored_mode = read_index_path(index_path)
            if (
                restored_bytes != index_before
                or restored_mode != index_mode
                or hashlib.sha256(restored_bytes).digest() != index_before_digest
            ):
                raise CommitFailure("E_BOOTSTRAP_INDEX_RESTORE_FAILED")
        except BaseException as restore_error:
            sys.stderr.write(
                "E_BOOTSTRAP_INDEX_RESTORE_FAILED "
                + "original="
                + code
                + "\n"
            )
            sys.stderr.write(str(restore_error)[:512] + "\n")
            raise SystemExit(125)
        sys.stderr.write(
            "E_BOOTSTRAP_INDEX_RESTORED_AFTER_FAILURE original=" + code + "\n"
        )
    if private_index is not None:
        try:
            private_index.unlink()
        except OSError as cleanup_error:
            sys.stderr.write(
                "E_BOOTSTRAP_PRIVATE_INDEX_RESIDUE "
                + str(cleanup_error)[:256]
                + "\n"
            )
    sys.stderr.write(code + "\n")
    if detail:
        sys.stderr.buffer.write(detail.rstrip(b"\n") + b"\n")
    raise SystemExit(1)
PY
# END NEW_PROJECT_COMMIT_BOOTSTRAP
```

No ejecutes estos comandos si la rama actual, su `HEAD` o el baseline ya no
coinciden con la sección 2, el destino tiene trabajo ajeno, el proyecto exige
otros gates o falta la autoridad previa/exacta para el commit. Esas condiciones
son `BLOCKED`, no permiso para sobrescribir ni omitir evidencia.

`switch` y la construcción del commit usan `core.hooksPath=/dev/null`. Antes de
mutar, el operador debe fijar por separado
`CONTROL_PLANE_GIT_AUTHOR_NAME`, `CONTROL_PLANE_GIT_AUTHOR_EMAIL`,
`CONTROL_PLANE_GIT_COMMITTER_NAME` y `CONTROL_PLANE_GIT_COMMITTER_EMAIL`.
El bloque cierra configuración global, activa `user.useConfigOnly=true`,
valida ambos idents mediante `git var` y exporta exactamente esos cuatro
valores a `commit-tree`; no infiere identidad local, global ni del host.

El commit se crea desde el árbol exacto del índice mediante `write-tree` y
`commit-tree`, y la rama avanza con un `update-ref` compare-and-swap; no se
invoca el porcelain que refrescaría el worktree o sus filtros. Los tres objetos
se crean con `hash-object -w --no-filters`, se stagean con
`update-index --cacheinfo` y se comparan byte a byte mediante `cat-file` contra
los ficheros personalizados antes del commit. El staging ocurre primero sobre
un índice privado. Solo sus bytes finales se publican atómicamente sobre el
índice real y, si la ref compare-and-swap falla, otro reemplazo atómico restaura
los bytes y el modo exactos capturados antes del staging. Una restauración
exitosa emite
`E_BOOTSTRAP_INDEX_RESTORED_AFTER_FAILURE`; si Git falla o el índice no coincide
con el snapshot previo, emite `E_BOOTSTRAP_INDEX_RESTORE_FAILED` y conserva esa
evidencia con un exit distinto. Los blobs escritos y todavía inalcanzables
pueden quedar para el GC normal del repositorio. Esta restauración no convierte
el proceso en una transacción contra otro writer del mismo UID: un cambio
después de la última comparación sigue siendo `UNKNOWN`.

## 5. Ejecutar la auditoría source-driven

Usa siempre el entrypoint de la fuente fijada y argumentos explícitos del
destino. Estos comandos observan; no instalan el runtime en el consumidor:

Inmediatamente antes de lanzar el primer comando, vuelve a ejecutar el bloque
`CONTROL_PLANE_SOURCE_BINDING`. Al terminar el último comando, ejecútalo otra
vez. No encadenes el launcher si cualquiera de las dos observaciones no imprime
exactamente `source_binding=PASS`.

```text
source_binding_checks=BEFORE_EXTRACTION_BEFORE_AUDIT_AFTER_AUDIT
```

```bash
"$CONTROL_PLANE_SOURCE/scripts/control-plane" policy-check \
  --policy "$NEW_PROJECT_ROOT/.codex/project-policy.toml" \
  --json

"$CONTROL_PLANE_SOURCE/scripts/control-plane" registry-check \
  --registry "$NEW_PROJECT_ROOT/.codex/resource-registry.toml" \
  --policy "$NEW_PROJECT_ROOT/.codex/project-policy.toml" \
  --json

"$CONTROL_PLANE_SOURCE/scripts/control-plane" inventory \
  --repo "$NEW_PROJECT_ROOT" \
  --registry "$NEW_PROJECT_ROOT/.codex/resource-registry.toml" \
  --json

"$CONTROL_PLANE_SOURCE/scripts/control-plane" doctor \
  --repo "$NEW_PROJECT_ROOT" \
  --json

"$CONTROL_PLANE_SOURCE/scripts/control-plane" preflight \
  --mode read \
  --offline \
  --repo "$NEW_PROJECT_ROOT" \
  --policy "$NEW_PROJECT_ROOT/.codex/project-policy.toml" \
  --json

"$CONTROL_PLANE_SOURCE/scripts/control-plane" route \
  --repo "$NEW_PROJECT_ROOT" \
  --task "$NEW_PROJECT_TASK" \
  --policy "$NEW_PROJECT_ROOT/.codex/project-policy.toml" \
  --registry "$NEW_PROJECT_ROOT/.codex/resource-registry.toml" \
  --mode audit \
  --json
```

En este bootstrap `doctor` debe salir con exit 1 únicamente porque falta
`.codex/control-plane.lock`: el error cerrado esperado es `L_PARSE`, mientras
`policy_valid=true`, `registry_valid=true` y `lock_valid=false`. Esto demuestra
`runtime_installation=NOT_PERFORMED`; no es permiso para copiar un lock. Otro
error, un lock presente o evidencia incompleta cambia el resultado a `BLOCKED`
o `UNKNOWN` y exige diagnóstico antes de continuar.

`route --mode audit` selecciona recursos y puede expresar una capacidad host
pendiente. Nunca crea capacidad nativa ni autoridad. Cada payload debe mantener
`authorizes=false`.

## 6. Survey solo con base local fijable

Survey no hace fetch ni prueba el servidor. Ejecútalo solo cuando la ref base
configurada exista localmente y resuelva a commit:

```bash
LOCAL_BASE_REF=origin/main
git -C "$NEW_PROJECT_ROOT" rev-parse --verify "$LOCAL_BASE_REF^{commit}"
"$CONTROL_PLANE_SOURCE/scripts/control-plane" survey \
  --repo "$NEW_PROJECT_ROOT" \
  --base "$LOCAL_BASE_REF" \
  --json
```

Una base local ausente o ambigua es `UNKNOWN`. Una ref remota local puede estar
obsoleta respecto del proveedor; `PASS`, `WARN` o `FAIL` de Survey son verdad
del clon seleccionado, nunca evidencia remota actual ni autorización para
limpiar, borrar, hacer push o fusionar.

## Matriz de resultado

| Resultado | Evidencia mínima | Acción coherente |
|---|---|---|
| `AUDIT_READY` | Policy y registry válidas; inventario y preflight read íntegros; route audit cerrado; los únicos rojos esperados de doctor son el lock ausente descrito arriba; el target no cambió durante la auditoría. | Conservar el informe local y usarlo para planificar. No afirmar instalación ni adopción. |
| `BLOCKED` | Existe una causa determinada: falta baseline, autoridad previa, configuración inválida, gate requerido ausente, lock inesperado o frontera del proyecto no satisfecha. | Detener la secuencia y resolver esa causa mediante un cambio project-owned revisado. No auto-mergear ni sobrescribir. |
| `UNKNOWN` | Falta evidencia íntegra: base local ausente o no fijable, ref/objeto ambiguo, timeout, almacenamiento no materializado, capacidad host no observable o estado remoto no consultado. | Preservar el estado, nombrar la evidencia ausente y reobservar por el canal exacto. No convertir UNKNOWN en PASS. |
| `ADOPTED` | Nunca es una conclusión válida de este runbook. | Rechazar la afirmación. Fuente auditada, starter copiado o preview de harness no son instalación, canary ni adopción. |

## Cierre y rollback

El cierre válido es `AUDIT_READY` con un commit project-owned de las tres
autoridades, artefactos locales `authorizes=false` y cero mutación atribuible a
los comandos de auditoría. No se crea receipt de adopción.

Antes del commit, elimina únicamente los tres ficheros recién creados si el
operador decide abandonar el bootstrap y ha comprobado que no contienen trabajo
ajeno. Después del commit, usa un revert revisado del commit exacto. No uses
`reset --hard`, `git clean`, borrado de rama, force push ni limpieza automática.

La siguiente transición posible —un canary o instalación sobre un consumidor
nombrado— requiere un ADR independiente y autorización nativa ligada al path y
SHA exactos. Hasta entonces:

```text
external_consumer_adoption=PROHIBITED
consumer_adoption_commands=PROHIBITED
authorizes=false
```
