"""沙箱执行器：在受限子进程中运行用户上传的 Python 代码（skill）。

安全护栏（多层纵深防御）：
1. 静态扫描：上传/执行前用 AST 检查禁用导入与危险调用（快速拦截明显恶意代码）。
2. 子进程隔离：独立 python 解释器进程，资源限额（CPU 时间 / 内存 / 进程数 / 超时）。
3. 权限降级：若以 root 运行（容器默认），降权到 nobody。
4. 导入护栏：自定义 meta path finder，禁止网络 / 执行类危险模块。
5. 文件护栏：仅允许在沙箱工作目录内写入；禁止读写敏感目录（/etc /proc /app …）。
6. 危险内建封禁：加载用户代码后封禁 eval / exec；os.system / popen / exec* / 危险 shutil 禁用。

说明：
- 本文件同时被生产（Linux 容器）与测试（开发机）导入。
- 资源限额 / 降权依赖 POSIX 的 resource / pwd，在 Windows 上自动跳过，
  但导入 / 文件 / 内建护栏在任何平台都会生效（它们运行在子进程的解释器里）。
- 真正的 OS 级网络隔离（--network none）需要容器层面支持；本沙箱在网络层面
  通过「禁止 socket / urllib / requests 等模块导入」实现纵深防御。
"""
import os
import sys
import json
import ast
import tempfile
import subprocess
import shutil
import uuid


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


# 资源限额（可用环境变量覆盖，便于运维调参）
DEFAULT_LIMITS = {
    "cpu_seconds": _env_int("SKILL_CPU_SEC", 25),
    "memory_mb": _env_int("SKILL_MEM_MB", 512),
    "nproc": _env_int("SKILL_NPROC", 32),
    "timeout": _env_int("SKILL_TIMEOUT", 25),
}

# 产物持久化根目录：沙箱生成的文件（docx/pptx/pdf 等）在临时目录被清理前移动到此，
# 供 /api/agent/download 对外提供下载。容器内 /app/data 由 docker-compose 挂载到宿主机
# ./data，故落盘文件在宿主机可见（默认 /app/data/artifacts）。
ARTIFACT_ROOT = os.environ.get("SKILL_ARTIFACT_ROOT") or (
    "/app/data/artifacts" if os.path.isdir("/app/data") else tempfile.gettempdir()
)
ARTIFACT_EXTS = (".pptx", ".docx", ".pdf", ".xlsx", ".txt", ".csv", ".zip",
                 ".json", ".md", ".html", ".png", ".jpg", ".jpeg", ".gif", ".py")
_INTERNAL_FILES = {"__result.json", "user_code.py", "args.json"}


def _collect_artifacts(sandbox_dir, target_root=None):
    """清理沙箱前，把用户生成的产物文件移动到持久目录，返回绝对路径列表。

    仅收集 ARTIFACT_EXTS 内的文件（避免误移沙箱内部文件），并跳过内部文件。
    target_root 指定落盘根目录（缺省 ARTIFACT_ROOT）；上层按 user_id/session_id 隔离后传入。
    """
    target_root = target_root or ARTIFACT_ROOT
    arts = []
    try:
        os.makedirs(target_root, exist_ok=True)
    except Exception:
        return arts
    try:
        names = os.listdir(sandbox_dir)
    except Exception:
        return arts
    for name in names:
        if name in _INTERNAL_FILES or name.startswith("skill_prelude_"):
            continue
        if not name.lower().endswith(ARTIFACT_EXTS):
            continue
        fp = os.path.join(sandbox_dir, name)
        if not os.path.isfile(fp):
            continue
        dst = os.path.join(target_root, "%s_%s" % (uuid.uuid4().hex[:8], name))
        try:
            shutil.move(fp, dst)
            arts.append(dst)
        except Exception:
            pass
    return arts

# 静态扫描 & 子进程双重使用的危险模块黑名单（网络 / 执行 / 系统底层）
BLOCKED_MODULES = {
    "socket", "ssl", "urllib", "urllib3", "requests", "http", "httpx", "aiohttp",
    "smtplib", "ftplib", "telnetlib", "paramiko", "pysftp", "websocket", "websockets",
    "grpc", "ctypes", "mmap", "pickle", "multiprocessing", "code", "codeop",
    "bdb", "trace", "pty",
}
# 注意：os / shutil 不在此黑名单（业务可能需要 os.path），其危险方法在运行时 hook 掉。

# 敏感目录：读写均禁止
SENSITIVE_DIRS = ("/etc", "/proc", "/root", "/app", "/app/data")


# ======================= 子进程引导脚本 =======================
# 该脚本由父进程以 sys.executable 在独立子进程中执行；用户代码放在 SANDBOX/user_code.py，
# 入参 JSON 放在 SANDBOX/args.json。脚本自身完成全部护栏后再 exec 用户代码。
PRELUDE = r'''
import sys, os, json, io, builtins, importlib.abc, traceback

SANDBOX = os.path.abspath(sys.argv[1])
ARGS_FILE = sys.argv[2]
RESULT_FILE = os.path.join(SANDBOX, "__result.json")

def _emit(obj):
    """将结构化结果写入文件，彻底隔离 stdout 污染。"""
    try:
        with _orig_open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        sys.stdout = real_stdout
    except Exception:
        pass

BLOCKED = {"socket","ssl","urllib","urllib3","requests","http","httpx","aiohttp",
           "smtplib","ftplib","telnetlib","paramiko","pysftp","websocket","websockets",
           "grpc","ctypes","mmap","pickle","multiprocessing","code","codeop",
           "bdb","trace","pty"}

def _forbidden(*a, **k):
    raise RuntimeError("该操作被沙箱禁止")

def _in_sbx(p):
    try:
        return os.path.abspath(os.path.expanduser(str(p))).startswith(SANDBOX + os.sep)
    except Exception:
        return False

# 强制 UTF-8 输出编码（防止中文/emoji 破坏 JSON）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 提前接管 stdout：覆盖「用户代码加载 + 执行」全阶段，防止顶层 print 污染
real_stdout = sys.stdout
buf = io.StringIO()
sys.stdout = buf

# 1) 裸读用户代码（护栏安装前，避免被 open 护栏拦截）
with open(os.path.join(SANDBOX, "user_code.py"), "r", encoding="utf-8") as f:
    USER_CODE = f.read()

# 2) 导入护栏
class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("模块 %s 被沙箱禁止" % name)
        return None
sys.meta_path.insert(0, _Blocker())
_orig_import = builtins.__import__
def _import(name, *a, **k):
    if name.split(".")[0] in BLOCKED:
        raise ImportError("模块 %s 被沙箱禁止" % name)
    return _orig_import(name, *a, **k)
builtins.__import__ = _import

# 3) os 危险函数护栏（先保存原函数，避免递归）
_orig_open = open
_orig_remove = os.remove
_orig_unlink = os.unlink
_orig_rmdir = os.rmdir
_orig_rename = os.rename
_orig_replace = os.replace
for fn in ("system","popen","execv","execve","execl","execlp","execle","execvp","execvpe",
           "spawnl","spawnle","spawnlp","spawnlpe","spawnv","spawnve","spawnvp","spawnvpe",
           "fork","forkpty","kill","startfile"):
    if hasattr(os, fn):
        setattr(os, fn, _forbidden)
_orig_mkdir = os.mkdir; _orig_makedirs = os.makedirs
def _mkdir(p, *a, **k):
    if not _in_sbx(p): raise RuntimeError("禁止在沙箱外创建目录: %s" % p)
    return _orig_mkdir(p, *a, **k)
def _makedirs(p, *a, **k):
    if not _in_sbx(p): raise RuntimeError("禁止在沙箱外创建目录: %s" % p)
    return _orig_makedirs(p, *a, **k)
os.mkdir = _mkdir; os.makedirs = _makedirs
def _rm(p, *a, **k):
    if not _in_sbx(p): raise RuntimeError("禁止在沙箱外删除: %s" % p)
    return _orig_remove(p, *a, **k)
os.remove = _rm; os.unlink = _rm; os.rmdir = _rm
def _mv(src, dst, *a, **k):
    if not (_in_sbx(src) and _in_sbx(dst)): raise RuntimeError("禁止移动文件到沙箱外")
    return _orig_rename(src, dst, *a, **k)
os.rename = _mv; os.replace = _mv
try:
    import shutil
    for fn in ("rmtree","move","copytree","copy","copy2","make_archive"):
        if hasattr(shutil, fn): setattr(shutil, fn, _forbidden)
except Exception:
    pass

# 4) 文件护栏：读允许（敏感目录除外），写仅限沙箱
# 产物目录（ARTIFACT_ROOT）例外放行读：run_temp_code 需读取已生成图片做后处理
import tempfile as _tf_mod
_ART = os.environ.get("SKILL_ARTIFACT_ROOT")
if not _ART:
    _ART = "/app/data/artifacts" if os.path.isdir("/app/data") else _tf_mod.gettempdir()
def _open(path, *a, **k):
    if isinstance(path, int):
        return _orig_open(path, *a, **k)
    p = os.path.abspath(os.path.expanduser(str(path)))
    mode = (k.get("mode") or (a[0] if len(a) >= 1 else "r"))
    write = any(ch in mode for ch in ("w","a","x","+"))
    if write and not _in_sbx(p):
        raise RuntimeError("禁止在沙箱外写入: %s" % path)
    if not write:
        # 例外：产物目录（已生成图片/文件）允许读取，供 run_temp_code 做后处理
        if not (p == _ART or p.startswith(_ART + os.sep)):
            for s in ("/etc","/proc","/root","/app","/app/data"):
                if p == s or p.startswith(s + os.sep):
                    raise RuntimeError("禁止读取敏感路径: %s" % path)
    return _orig_open(path, *a, **k)
builtins.open = _open

# 5) 加载用户代码（此时 eval/exec 尚未封禁，用于加载）
_safe_builtins = dict(vars(builtins))
_safe_builtins["open"] = _open
_safe_builtins["eval"] = _forbidden
_safe_builtins["exec"] = _forbidden
_safe_builtins["__import__"] = _import
ns = {"__name__": "__sandbox__", "__builtins__": _safe_builtins}
try:
    exec(compile(USER_CODE, "user_code.py", "exec"), ns)
except BaseException as e:
    _emit({"ok": False, "error": "代码加载失败: %s: %s" % (type(e).__name__, e),
           "stdout": buf.getvalue()[:2000],
           "traceback": traceback.format_exc()[-2000:]})
    sys.exit(0)

# 6) 加载完成后再次确保 eval / exec 封禁（防御性）
_safe_builtins["eval"] = _forbidden
_safe_builtins["exec"] = _forbidden

if "run" not in ns or not callable(ns["run"]):
    _emit({"ok": False, "error": "技能代码必须定义 run(args) 函数",
           "stdout": buf.getvalue()[:2000]})
    sys.exit(0)

try:
    with open(ARGS_FILE, "r", encoding="utf-8") as f:
        args = json.load(f)
except Exception as e:
    _emit({"ok": False, "error": "读取参数失败: %s: %s" % (type(e).__name__, e),
           "stdout": buf.getvalue()[:2000]})
    sys.exit(0)

try:
    result = ns["run"](args)
except BaseException as e:
    _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
           "stdout": buf.getvalue()[:2000],
           "traceback": traceback.format_exc()[-2000:]})
    sys.exit(0)

sys.stdout = real_stdout
user_out = buf.getvalue()
if result is None:
    result = user_out
if not isinstance(result, str):
    try:
        result = json.dumps(result, ensure_ascii=False)
    except Exception:
        result = str(result)
_emit({"ok": True, "result": result[:8000], "stdout": user_out[:2000]})
sys.exit(0)
'''


_PRELUDE_PATH = None
_PRELUDE_DIR = None


def _prelude_path(sandbox_dir=None):
    """返回 PRELUDE 脚本路径。优先写入沙箱目录（避免 /tmp 无写权限），否则用当前工作目录。

    注意：sandbox_dir 由 run_code 传入，确保文件落在有写权限的位置。
    文件权限设为 0o644，确保 setuid(nobody) 降权后子进程仍可读取。
    """
    global _PRELUDE_PATH, _PRELUDE_DIR
    # 沙箱目录变化时（或首次）重新生成
    if _PRELUDE_DIR != sandbox_dir or _PRELUDE_PATH is None or not os.path.exists(_PRELUDE_PATH):
        target_dir = sandbox_dir or os.getcwd()
        os.makedirs(target_dir, exist_ok=True)
        fd, _PRELUDE_PATH = tempfile.mkstemp(prefix="skill_prelude_", suffix=".py", dir=target_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(PRELUDE)
        # mkstemp 默认 0o600（仅 owner 可读），setuid 降权后 nobody 无法读取
        os.chmod(_PRELUDE_PATH, 0o644)
        _PRELUDE_DIR = sandbox_dir
    return _PRELUDE_PATH
    return _PRELUDE_PATH


# ======================= 静态扫描 =======================
def scan_code(code):
    """上传前静态检查。返回 (ok, reason)。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, "Python 语法错误：%s" % e
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in BLOCKED_MODULES:
                    bad.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] in BLOCKED_MODULES:
                bad.add(mod)
    if bad:
        return False, "检测到禁止导入的模块：" + ", ".join(sorted(bad)) + "（沙箱禁止网络/执行类模块）"
    # 轻量正则：显式危险调用
    for pat in ("os.system", "os.popen", "subprocess", "os.exec", "shutil.rmtree", "ctypes."):
        if pat in code:
            return False, "检测到禁止的调用：%s" % pat
    return True, ""


# ======================= 资源限额 / 降权 =======================
def _make_preexec(limits):
    def _preexec():
        # 资源限额（仅 POSIX 有效）
        try:
            import resource
            try:
                resource.setrlimit(resource.RLIMIT_CPU,
                                   (limits["cpu_seconds"], limits["cpu_seconds"] + 1))
            except Exception:
                pass
            try:
                mb = int(limits["memory_mb"]) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mb, mb))
            except Exception:
                pass
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (limits["nproc"], limits["nproc"]))
            except Exception:
                pass
        except Exception:
            pass
        # 权限降级到 nobody（仅 root 可降权）
        try:
            import pwd
            nobody = pwd.getpwnam("nobody")
            os.setgroups([])
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)
        except Exception:
            pass

    return _preexec


# ======================= 对外主入口 =======================
def run_code(code, args=None, limits=None, timeout=None, artifact_root=None):
    """在沙箱中执行用户代码。

    约定：用户代码需定义 `def run(args: dict) -> str:`。
    返回 dict：{ok, result, error, timed_out, stdout}
    artifact_root：产物落盘根目录（上层按 user_id/session_id 隔离后传入，缺省 ARTIFACT_ROOT）。
    """
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    # 注意：必须用可被 nobody 用户读取的目录（后续 preexec_fn 会 setuid 降权）
    # tempfile.mkdtemp 默认 0o700，降权后子进程无法读取；改用 0o755 确保可访问
    sandbox_dir = tempfile.mkdtemp(prefix="skill_sbx_")
    # 必须让降权后的 nobody 用户能在沙箱目录内写入产物（docx/pptx 等）；
    # 0o755 仅 other=rx 无写权限，会导致工具写文件时 Permission denied。
    os.chmod(sandbox_dir, 0o777)
    user_path = os.path.join(sandbox_dir, "user_code.py")
    args_path = os.path.join(sandbox_dir, "args.json")
    with open(user_path, "w", encoding="utf-8") as f:
        f.write(code)
    with open(args_path, "w", encoding="utf-8") as f:
        json.dump(args or {}, f, ensure_ascii=False)

    env = {
        "PYTHONPATH": sandbox_dir,
        "SKILL_ARTIFACT_ROOT": ARTIFACT_ROOT,
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "HOME": sandbox_dir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    prelude = _prelude_path(sandbox_dir)
    run_kwargs = dict(
        cwd=sandbox_dir, env=env, capture_output=True,
        timeout=timeout or limits["timeout"],
    )
    # preexec_fn（资源限额 / 降权）仅 POSIX 支持；Windows 跳过（护栏层仍生效）
    if os.name == "posix":
        run_kwargs["preexec_fn"] = _make_preexec(limits)
    try:
        proc = subprocess.run([sys.executable, prelude, sandbox_dir, args_path], **run_kwargs)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "ignore")
        err = (e.stderr or b"").decode("utf-8", "ignore")
        # 超时时也要尝试读结果文件（子进程可能在超时前已写入部分结果）
        result_file = os.path.join(sandbox_dir, "__result.json")
        rf_data = None
        if os.path.exists(result_file):
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    rf_data = json.load(f)
            except Exception:
                pass
        if rf_data:
            rf_data["timed_out"] = True
            if not rf_data.get("error"):
                rf_data["error"] = "执行超时（>%ss），已在沙箱终止" % (timeout or limits["timeout"])
        else:
            rf_data = {
                "ok": False, "timed_out": True,
                "error": "执行超时（>%ss），已在沙箱终止" % (timeout or limits["timeout"]),
                "stdout": out[:2000], "stderr": err[:2000],
            }
        rf_data["artifacts"] = _collect_artifacts(sandbox_dir, artifact_root)
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return rf_data

    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()

    # 优先从结果文件读取（彻底隔离 stdout 污染）
    result_file = os.path.join(sandbox_dir, "__result.json")
    data = None
    if os.path.exists(result_file):
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if data is None:
        # 兜底：从 stdout 解析（兼容旧版 PRELUDE 或文件写入失败）
        try:
            data = json.loads(out)
        except Exception:
            # 区分信号终止与结果损坏
            if proc.returncode is not None and proc.returncode < 0:
                sig = -proc.returncode
                hint = {24: "超出 CPU 时间限额(SIGXCPU)", 9: "被强制终止(SIGKILL，通常是内存超限)",
                        11: "段错误(SIGSEGV)"}.get(sig, "信号 %s" % sig)
                data = {"ok": False, "timed_out": (sig == 24),
                        "error": "沙箱进程被终止：%s" % hint,
                        "stdout": out[:2000], "stderr": err[:2000],
                        "returncode": proc.returncode}
            else:
                data = {
                    "ok": False, "error": "沙箱进程未产出合法结果（returncode=%s）。"
                                      "常见原因：代码顶层 print 污染输出 / run() 内调用 sys.exit() / "
                                      "超出 CPU(%ss) 或内存(%sMB) 限额被终止"
                                      % (proc.returncode, limits["cpu_seconds"], limits["memory_mb"]),
                    "stdout": out[:3000], "stderr": err[:2000], "returncode": proc.returncode,
                }

    # 确保清理在结果读取之后：先把用户生成的产物移动到持久目录，再删除沙箱
    data["artifacts"] = _collect_artifacts(sandbox_dir, artifact_root)
    shutil.rmtree(sandbox_dir, ignore_errors=True)

    data.setdefault("timed_out", False)
    data.setdefault("stdout", "")
    return data


def run_skill(code, args=None, limits=None, timeout=None):
    """便捷封装：直接返回给智能体工具用的字符串结果。"""
    res = run_code(code, args, limits=limits, timeout=timeout)
    if res.get("ok"):
        return res.get("result") or ""
    msg = res.get("error") or "技能执行失败"
    if res.get("timed_out"):
        msg = "技能执行超时（已在沙箱终止）"
    extra = res.get("stdout")
    if extra:
        msg += "\n--- 技能输出 ---\n" + extra[:1500]
    return "技能执行失败：" + msg
