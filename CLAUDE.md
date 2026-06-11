# CLAUDE.md

Guidance for AI agents (Claude Code and similar) working in this repository. Read this first; do not skim.

---

## What this project is

A Python service that turns local video files into ONVIF-compliant mock cameras. Each mock camera:

1. is transcoded once on upload to a normalised H.264/AAC mp4
2. is loop-streamed by FFmpeg in copy mode to a shared `mediamtx` RTSP server
3. exposes its own ONVIF Device + Media SOAP endpoints, so NVR platforms (UniFi Protect etc.) discover it as a real camera

Use cases: NVR / VMS testing, CI for video pipelines, dev fixtures.

---

## Quick mental model

```
HTTP (Flask, port 9999)        ←── Web UI / REST
     │
     ▼
camera_lifecycle.create_camera()
     │   ExitStack rollback at every step
     ├─► transcoder.transcode()           ←── ffmpeg_builder builds the cmd
     ├─► transcoder.generate_snapshot()
     ├─► port_allocator.allocate()       (or macvlan_manager.create_interface())
     └─► _start_camera_runtime()         ←── shared tail: create / batch / restore
           ├─► db.CameraRepository.upsert()      ←── SQLite: data/service.db
           ├─► process_supervisor.start_ffmpeg() ←── pushes to mediamtx
           ├─► onvif_endpoint.get_onvif_endpoint().start()
           │     ├─ SubprocessOnvifEndpoint (default): spawn onvif_server.py
           │     └─ DispatcherOnvifEndpoint (opt-in):  add to onvif_dispatcher
           └─► runtime_registry.put(RuntimeState)   ←── PIDs, in-memory only

Background threads:
  - log_cleanup_scheduler (24h)   — disk-side retention for ./logs
  - data_cleaner          (1h)    — orphan scan in ./data
  - watchdog              (15s)   — auto-restart dead subprocesses
```

---

## Module map

| Module | Responsibility | Don't put here |
|---|---|---|
| `app/app.py` | Flask routes + presentation (`_camera_to_dict`). Translates HTTP ↔ service calls. | Business logic |
| `app/config.py` | All env-driven constants. Single source of truth for env vars. | Hardcoded values |
| `app/constants.py` | Static validation ranges (resolution / fps / bitrate). | Anything env-driven |
| `app/exceptions.py` | Typed exceptions. `http_status` field maps to HTTP code in `app.py` errorhandler. | Logging side-effects |
| `app/schemas.py` | Pydantic request schemas. | Domain logic |
| `app/db.py` | SQLite repository + YAML migration. Holds the only Connection. | Camera lifecycle |
| `app/camera_lifecycle.py` | Orchestration. Uses `ExitStack` for atomic rollback; `_start_camera_runtime` is the shared create/batch/restore tail. | FFmpeg arg formatting, HTTP shapes |
| `app/runtime_registry.py` | In-memory `RuntimeState`/`RuntimeRegistry` (PIDs, live params). Thread-safe. | Persistence, orchestration |
| `app/transcoder.py` | High-level transcode + snapshot + size cap. | FFmpeg cmd strings |
| `app/ffmpeg_builder.py` | Pure functions that return FFmpeg argv lists. | Subprocess execution |
| `app/process_supervisor.py` | `Popen` / log-pipe thread / SIGTERM-SIGKILL-`waitpid` reap. | FFmpeg cmd args |
| `app/port_allocator.py` | Thread-safe port allocator with TOCTOU mitigation. | Macvlan logic |
| `app/macvlan_manager.py` | `ip link add` / dhclient / interface restore. Requires `NET_ADMIN`. | Anything non-Linux |
| `app/onvif_handlers.py` | Pure SOAP response builders. Take `OnvifContext`, return XML strings (XML-escaped). | Flask, env, sockets |
| `app/onvif_endpoint.py` | ONVIF mode strategy (`SubprocessOnvifEndpoint` / `DispatcherOnvifEndpoint`) + `extract_onvif_params`. Callers hold the strategy instead of branching on config or PID sentinels. | SOAP rendering, route registration |
| `app/onvif_http.py` | Shared SOAP route shell (`register_onvif_routes`) used by both ONVIF front-ends, parameterised by a context resolver. | Mode selection, business logic |
| `app/onvif_dispatcher.py` | Single in-process Flask served on N werkzeug ports. Opt-in. | SOAP rendering |
| `app/watchdog.py` | Liveness check + respawn dead FFmpeg / ONVIF. | Camera CRUD |
| `app/log_manager.py` | `RotatingFileHandler` per camera + `close_logger` cleanup. | Disk scanning |
| `app/log_cleanup_scheduler.py` | 24h sweep of `./logs`. Event-based, stoppable. | Anything other than logs |
| `app/data_cleaner.py` | 1h orphan scan of `./data` against SQLite. | Subprocess management |
| `app/startup.py` | Boots background services. Called from `run.py`. | Routes |
| `app/utils.py` | `get_server_ip()` and small helpers. | Anything substantial |
| `onvif_server.py` | Per-camera subprocess entry. Reads env, calls `onvif_handlers.dispatch_*`. | Anything new — keep it thin |
| `run.py` | Process entry. Registers shutdown handler. | Routes / logic |

---

## Persistence

- **Single source of truth:** `data/service.db` (SQLite, WAL mode).
- **Schema:** declared inline in `app/db.py`. Add new columns via an `ALTER TABLE` block in `_init_schema` (gated by `try/except` since `CREATE TABLE IF NOT EXISTS` doesn't add columns).
- **What we persist:** durable metadata only. **PIDs and runtime state stay in memory** (`RuntimeRegistry` in `runtime_registry.py`).
- **Legacy YAML migration:** `migrate_yaml_configs()` runs on every boot. It is idempotent. After successful import, the YAML file is deleted. `data/cameras/` is removed once empty. **Do not write new YAML configs.**

---

## Conventions

### Errors

- Raise a typed exception from `app/exceptions.py`. Never `raise Exception("...")`.
- The Flask `errorhandler` for `CameraServiceError` reads `e.http_status` and returns the right code. Add a new subclass to add a new mapping.
- Never use string matching like `if "save video" in str(e)` to decide HTTP code — that was the old anti-pattern.

### Subprocesses

- Always go through `process_supervisor.start_ffmpeg` / `start_onvif_subprocess`. Never call `subprocess.Popen` directly elsewhere.
- Always pair `start_*` with `stop_*` which calls `_terminate_and_reap` (SIGTERM → grace → SIGKILL → `waitpid`).
- On camera delete, call `release_camera_loggers(camera_id)` to close rotating-file handlers and drop the daemon log thread. Skipping this leaks FDs over time.

### Lifecycle

- Create/restore flows MUST use `ExitStack`. Each acquisition step pushes a rollback callback. On success call `stack.pop_all()` to disarm.
- Do not partial-cleanup manually — that was the previous pyramid-of-doom pattern.

### Pre-existing global state

The following are module-level singletons (intentional, to keep the call sites simple). All are reached via accessor functions; most are lazy with double-checked locking:

- `app.db._repo` via `get_repository()` — SQLite connection (lazy)
- `app.port_allocator._default` via `get_default_allocator()` — port allocator (lazy)
- `app.runtime_registry._registry` via `get_registry()` — runtime PID/state registry
- `app.camera_lifecycle._macvlan` via `_get_macvlan_manager()` — macvlan manager (lazy, double-checked locking; only used when `MACVLAN_ENABLED`)
- `app.onvif_endpoint._endpoint` via `get_onvif_endpoint()` — ONVIF mode strategy, selected once at import from config
- `app.onvif_dispatcher._dispatcher` via `get_dispatcher()` — only when `ONVIF_DISPATCHER_ENABLED`
- `app.log_cleanup_scheduler._scheduler`, `app.data_cleaner._scheduler` via each module's `get_scheduler()` — background loops (lazy)

In tests, monkey-patch these. There's no DI framework.

### Logging

- Use `logging` (`logger = logging.getLogger(__name__)`) **everywhere**. Do not `print`.
- `run.py` calls `logging.basicConfig` once at startup. Don't reconfigure root logging in other modules.
- Per-camera FFmpeg/ONVIF stdout is piped to `RotatingFileHandler` (3 MB × 4 backups) under `./logs/{ffmpeg,onvif}/`.
- Docker daemon stdout is rotated by `json-file` driver via `docker-compose.yml`'s `x-logging` anchor (`50m × 5`).

---

## Operational invariants — don't break these

1. **Camera ID = UUID4** (`str(uuid.uuid4())`). Used everywhere: filenames, log paths, ONVIF SerialNumber, macvlan interface name (`cam_<first8>`).
2. **`data/service.db` is authoritative.** If a file exists on disk but no DB row references it, the orphan scanner will delete it within an hour. Don't leave files on disk expecting them to persist without a DB row.
3. **PIDs are NOT persisted.** They live in `RuntimeRegistry`. On startup, `restore_cameras()` re-spawns subprocesses and registers fresh PIDs.
4. **Per-camera log file path is keyed by `camera_id[:8]`** (e.g. `logs/ffmpeg/ffmpeg_8c058bc4.log`). Two cameras with the same first-8 collide — UUID4 collision is essentially zero, but keep the slice if you change the naming.
5. **Each FFmpeg subprocess is in its own session** (`start_new_session=True`). ONVIF processes are killed via `killpg(getpgid(pid), SIG)`. Don't change this — it's how we cleanly kill child processes (e.g. waitress threads).
6. **macvlan requires root inside the container** (for `ip link add`). The Dockerfile runs as non-root `mockcam` by default; the `docker-compose.macvlan.yml` overlay sets `user: "0"` and `cap_add: NET_ADMIN`.

---

## Two ONVIF modes

| | Subprocess mode (default) | Dispatcher mode (`ONVIF_DISPATCHER_ENABLED=true`) |
|---|---|---|
| One Python interpreter per camera | yes — ~25 MB RSS each | no — single process |
| ONVIF restart on crash | watchdog respawns subprocess | werkzeug server-per-port; no subprocess to die |
| Port binding | per-port subprocess | per-port werkzeug thread + shared Flask |
| Compatibility | fully battle-tested | newer; same SOAP responses (shared `onvif_handlers.py` + `onvif_http.py` routes) |
| `onvif_pid` in the camera API dict | subprocess PID | `null` (in-process, nothing to track) |

The dispatcher should be preferred for high-camera-count deployments (e.g. 100+). It is opt-in to avoid changing default behaviour for existing users.

---

## How to run

```bash
# Local
uv sync                        # installs deps + dev group (pytest, ruff) from pyproject.toml
mediamtx mediamtx.yml &        # required: RTSP server on 8554
uv run python run.py           # service on 9999
# Note: requirements.txt is kept in sync with pyproject.toml — Docker installs from it.

# Docker (bridge mode)
docker compose up -d

# Docker (macvlan mode, Linux only)
sudo bash scripts/setup-macvlan-host.sh eth0  # one-time host setup
docker compose -f docker-compose.yml -f docker-compose.macvlan.yml up -d
```

`run.py` is the only supported entry point. `app/app.py`'s `if __name__ == '__main__'` block has been removed.

---

## How to test

Run the pytest suite (142 tests, in `tests/`; config in `pyproject.toml`):

```bash
uv run pytest          # full suite
uv run ruff check .    # lint (E/F/I/W, line length 100)
```

For changes the suite doesn't cover (subprocess spawning, real HTTP boot), use these smoke checks:

```bash
# Import everything
.venv/bin/python -c "import importlib; \
  [importlib.import_module(m) for m in [
    'app.config','app.exceptions','app.schemas','app.db','app.ffmpeg_builder',
    'app.transcoder','app.port_allocator','app.process_supervisor',
    'app.macvlan_manager','app.camera_lifecycle','app.runtime_registry',
    'app.watchdog','app.log_manager','app.log_cleanup_scheduler',
    'app.startup','app.utils','app.app','app.onvif_handlers',
    'app.onvif_endpoint','app.onvif_http','app.onvif_dispatcher',
    'app.data_cleaner','onvif_server']]; print('ok')"

# Boot service end-to-end (no real cameras)
SERVER_PORT=19999 WATCHDOG_ENABLED=false .venv/bin/python -c "
import threading, urllib.request, json, time
from app.app import app
threading.Thread(target=lambda: app.run(host='127.0.0.1', port=19999, debug=False, use_reloader=False), daemon=True).start()
time.sleep(1)
print(json.loads(urllib.request.urlopen('http://127.0.0.1:19999/health').read()))"

# Force a one-shot orphan scan
.venv/bin/python -c "from app.data_cleaner import scan_orphans; scan_orphans(grace_seconds=0, dry_run=True)"
```

When adding new code, add pytest coverage under `tests/` (at minimum a smoke test that imports the module and exercises the main entry point).

---

## How to add a feature — checklist

Adding a new camera property (e.g. `audio_codec`):

1. Add to `app/schemas.py` `VideoParams` with a validator
2. Add column to `_SCHEMA` in `app/db.py` (and the matching `INSERT` / `from_row`)
3. Add to `CameraRecord` dataclass
4. Plumb through `onvif_endpoint.extract_onvif_params` → `OnvifContext` → `onvif_handlers.*` if it affects SOAP responses
5. Update `app/ffmpeg_builder.py` if it affects FFmpeg args
6. Update `README.md` env-var section if user-configurable
7. Bump migration logic if old DB rows need backfill
8. Add pytest coverage under `tests/`

Adding a new env var:

1. Define in `app/config.py` with default
2. Document in `.env.example`
3. Document in `README.md` Configuration section
4. Surface in `docker-compose.yml` `environment:` block if relevant to Docker users

---

## Pitfalls (real bugs that bit us)

- **`ffmpeg_pid` NameError** in failure path: always init `ffmpeg_pid = None` before the first `start_ffmpeg` call. ExitStack now handles this, but if you write new orchestration, mind it.
- **ONVIF subprocesses become zombies** unless `waitpid` is called. `process_supervisor.stop_onvif` does this; do not bypass it.
- **Rotating logger FD leak**: every `LogManager.create_rotating_logger` must be paired with `LogManager.close_logger` on camera delete. `process_supervisor.release_camera_loggers` is the canonical helper.
- **`docker-compose stop` SIGKILLs** if `cleanup_all` takes longer than `stop_grace_period`. Current grace is 30 s; `cleanup_all` delegates to `process_supervisor.terminate_many`, which parallel-signals all subprocesses, so it should finish in ~1 s.
- **Docker daemon `json-file` log grows unbounded** without the `logging:` block — a production VM had it grow to 75 GB. The `x-logging` anchor in `docker-compose.yml` is non-optional.
- **`is_port_in_use(port)` calls `connect_ex`** with a 0.1 s timeout (`PORT_PROBE_TIMEOUT_SECONDS` in `config.py`). Don't raise it — allocating across the full 12000–13000 range under load is otherwise unbearable.

---

## Where things live on disk at runtime

```
data/
  service.db                # SQLite, authoritative
  videos/                   # *.mp4 (and *_sub.mp4 if sub_profile)
  snapshots/                # *.jpg
logs/
  ffmpeg/ffmpeg_<id8>.log   # rotated 3 MB × 4
  onvif/onvif_<id8>.log     # rotated 3 MB × 4
```

That's everything. There is no other state.

---

## Reference: full code review

See `REVIEW_REPORT.html` for the original audit + implementation status (`FIXED` / `PARTIAL` / `OPEN` tags on every finding, plus the per-layer disk retention matrix).
