#!/usr/bin/env python3
"""
UPS Watchdog — 飞牛NAS 非智能UPS断电保护（纯Python，零外部依赖）
一个进程同时跑看门狗守护 + Web 管理界面。
"""

import json, os, sys, time, signal, logging, subprocess, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────
APP_DIR  = Path(os.path.dirname(os.path.realpath(__file__))).parent
DATA_DIR = Path(os.environ.get("UPS_DATA_DIR", "/vol2/@appdata/ups-watchdog/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
STATUS_FILE = DATA_DIR / "status.json"
LOG_FILE    = DATA_DIR / "watchdog.log"
PID_FILE    = DATA_DIR / "app.pid"
WEB_DIR     = APP_DIR / "www"

TZ = timezone(timedelta(hours=8))
VERSION = "1.0.0"

# ── 日志 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ups-watchdog")

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

# ── 配置 ──────────────────────────────────────────────
DEFAULTS = {
    "targets": [],
    "ping_interval": 10,
    "ping_timeout": 3,
    "failure_threshold": 6,
    "shutdown_delay": 30,
    "all_must_fail": True,
}

def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception as e:
            log.warning("配置读取失败: %s", e)
    out = []
    for t in cfg.get("targets", []):
        if isinstance(t, str):
            out.append({"ip": t, "name": t, "enabled": True})
        elif isinstance(t, dict) and "ip" in t:
            out.append({"ip": t["ip"], "name": t.get("name", t["ip"]), "enabled": t.get("enabled", True)})
    cfg["targets"] = out
    return cfg

def save_config(cfg):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    os.replace(str(tmp), str(CONFIG_FILE))

# ── 状态 ──────────────────────────────────────────────
status_lock = threading.Lock()
status_data = {
    "running": True, "power_ok": True, "consecutive_fails": 0,
    "total_checks": 0, "total_fails": 0, "started_at": now_str(),
    "last_check": None, "last_success": None, "last_fail": None,
    "targets": [], "version": VERSION, "message": "初始化中",
}

def get_status():
    with status_lock:
        return dict(status_data)

def set_status(**kw):
    with status_lock:
        status_data.update(kw)
    _flush_status()

def _flush_status():
    try:
        tmp = STATUS_FILE.with_suffix(".tmp")
        with status_lock:
            tmp.write_text(json.dumps(status_data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(str(tmp), str(STATUS_FILE))
    except Exception:
        pass

# ── Ping ──────────────────────────────────────────────
def ping(ip, timeout=3):
    t0 = time.time()
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout), ip],
                           capture_output=True, text=True, timeout=timeout + 2)
        elapsed = round((time.time() - t0) * 1000)
        if r.returncode == 0:
            rtt = elapsed
            for line in r.stdout.splitlines():
                if "time=" in line:
                    try: rtt = float(line.split("time=")[1].split()[0])
                    except: pass
            return {"success": True, "rtt": rtt, "error": None}
        return {"success": False, "rtt": None, "error": "unreachable"}
    except subprocess.TimeoutExpired:
        return {"success": False, "rtt": None, "error": "timeout"}
    except Exception as e:
        return {"success": False, "rtt": None, "error": str(e)}

# ── 关机 ──────────────────────────────────────────────
def execute_shutdown(delay):
    log.warning("=" * 40 + " 安全关机 " + "=" * 40)
    log.warning("将在 %d 秒后关机", delay)
    set_status(power_ok=False, message="判定市电中断，正在安全关机...")
    try: subprocess.run(["sync"], timeout=10)
    except: pass
    time.sleep(min(delay, 10))
    log.warning("执行 shutdown ...")
    try: subprocess.run(["shutdown", "-h", "now"], timeout=30)
    except Exception as e:
        log.error("shutdown 失败: %s", e)

# ── 看门狗线程 ────────────────────────────────────────
stop_event = threading.Event()

def watchdog_loop():
    log.info("看门狗守护线程启动")
    fails = 0
    last_reload = 0
    check_count = 0
    while not stop_event.is_set():
        now = time.time()
        if now - last_reload > 30:
            cfg = load_config()
            last_reload = now

        targets = [t for t in cfg["targets"] if t.get("enabled", True)]
        if not targets:
            set_status(power_ok=True, last_check=now_str(), message="未配置监控目标")
            stop_event.wait(cfg["ping_interval"])
            continue

        results = []
        any_ok = False
        for t in targets:
            r = ping(t["ip"], cfg["ping_timeout"])
            results.append({"ip": t["ip"], "name": t["name"], **r, "checked_at": now_str()})
            if r["success"]: any_ok = True

        with status_lock:
            status_data["total_checks"] += 1
            status_data["last_check"] = now_str()
            status_data["targets"] = results

        check_count += 1

        if any_ok:
            fails = 0
            set_status(power_ok=True, consecutive_fails=0, last_success=now_str(), message="市电正常")
            # 每 5 次检测记录一次正常状态日志
            if check_count % 5 == 0:
                ok_targets = [f"{t['name']}({t['ip']})" for t in results if t.get('success')]
                log.info("检测正常 [%d次] 可达：%s", check_count, ", ".join(ok_targets) or "无")
        else:
            fails += 1
            set_status(consecutive_fails=fails, last_fail=now_str(),
                       message=f"网络异常 ({fails}/{cfg['failure_threshold']})")
            log.warning("所有目标不可达 %d/%d", fails, cfg["failure_threshold"])
            if fails >= cfg["failure_threshold"]:
                execute_shutdown(cfg["shutdown_delay"])
                return

        stop_event.wait(cfg["ping_interval"])

    set_status(running=False, message="守护进程已停止")
    log.info("看门狗守护线程退出")

# ══════════════════════════════════════════════════════
#  Web 服务器（纯 stdlib）
# ══════════════════════════════════════════════════════

class Handler(SimpleHTTPRequestHandler):
    """路由：静态文件 + JSON API"""

    def log_message(self, fmt, *a):
        pass  # 安静

    # ── 工具 ──
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def _static(self, path):
        """服务静态文件"""
        full = WEB_DIR / path
        if full.is_file():
            ct = "text/html; charset=utf-8"
            if path.endswith(".css"): ct = "text/css"
            elif path.endswith(".js"):  ct = "application/javascript"
            elif path.endswith(".png"): ct = "image/png"
            elif path.endswith(".svg"): ct = "image/svg+xml"
            data = full.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
            return True
        return False

    # ── GET ──
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")

        if path == "" or path == "/":
            if self._static("index.html"): return

        if path.startswith("/static/"):
            if self._static(path[8:]): return

        if path == "/api/status":
            return self._json(get_status())

        if path == "/api/config":
            return self._json(load_config())

        if path == "/api/logs":
            qs = parse_qs(urlparse(self.path).query)
            n = min(int(qs.get("lines", [100])[0]), 500)
            try:
                lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
                return self._json({"lines": lines[-n:], "total": len(lines)})
            except:
                return self._json({"lines": [], "total": 0})

        self.send_error(404)

    # ── POST ──
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/targets":
            ip = (body.get("ip") or "").strip()
            name = (body.get("name") or ip).strip()
            if not ip: return self._json({"error": "IP 不能为空"}, 400)
            cfg = load_config()
            if any(t["ip"] == ip for t in cfg["targets"]):
                return self._json({"error": f"{ip} 已存在"}, 409)
            cfg["targets"].append({"ip": ip, "name": name, "enabled": True})
            save_config(cfg)
            return self._json({"ok": True, "message": f"已添加 {ip}"})

        if path.startswith("/api/targets/") and path.endswith("/toggle"):
            ip = path.split("/")[3]
            cfg = load_config()
            for t in cfg["targets"]:
                if t["ip"] == ip:
                    t["enabled"] = not t.get("enabled", True)
                    save_config(cfg)
                    return self._json({"ok": True, "message": f"{ip} 已{'启用' if t['enabled'] else '禁用'}"})
            return self._json({"error": "未找到"}, 404)

        if path.startswith("/api/targets/") and path.endswith("/ping"):
            ip = path.split("/")[3]
            try:
                r = subprocess.run(["ping", "-c", "3", "-W", "2", ip],
                                   capture_output=True, text=True, timeout=15)
                return self._json({"ok": r.returncode == 0,
                                   "output": r.stdout.strip(),
                                   "error": r.stderr.strip() if r.returncode else None})
            except:
                return self._json({"ok": False, "error": "超时"})

        if path == "/api/logs/clear":
            LOG_FILE.write_text("")
            return self._json({"ok": True})

        if path == "/api/shutdown":
            if body.get("confirm") != "SHUTDOWN":
                return self._json({"error": "需确认"}, 400)
            threading.Thread(target=execute_shutdown, args=(10,), daemon=True).start()
            return self._json({"ok": True, "message": "关机指令已发送"})

        if path == "/api/shutdown/cancel":
            stop_event.set()
            return self._json({"ok": True, "message": "已取消"})

        self.send_error(404)

    # ── PUT ──
    def do_PUT(self):
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/config":
            cfg = load_config()
            for k in ("ping_interval", "ping_timeout", "failure_threshold", "shutdown_delay", "all_must_fail"):
                if k in body: cfg[k] = body[k]
            if "targets" in body: cfg["targets"] = body["targets"]
            save_config(cfg)
            return self._json({"ok": True, "message": "配置已保存"})

        self.send_error(404)

    # ── DELETE ──
    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/api/targets/"):
            ip = path.split("/")[3]
            cfg = load_config()
            before = len(cfg["targets"])
            cfg["targets"] = [t for t in cfg["targets"] if t["ip"] != ip]
            if len(cfg["targets"]) == before:
                return self._json({"error": "未找到"}, 404)
            save_config(cfg)
            return self._json({"ok": True, "message": f"已删除 {ip}"})

        self.send_error(404)


# ══════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════
def main():
    log.info("=" * 50)
    log.info("UPS 看门狗 v%s 启动", VERSION)
    log.info("应用目录: %s", APP_DIR)
    log.info("数据目录: %s", DATA_DIR)
    log.info("=" * 50)

    # 写 PID
    PID_FILE.write_text(str(os.getpid()))

    # 启动看门狗线程
    wd = threading.Thread(target=watchdog_loop, daemon=True)
    wd.start()

    # 启动 HTTP 服务器
    port = int(os.environ.get("WEB_PORT", 5080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    log.info("Web 管理界面: http://0.0.0.0:%d", port)

    def shutdown(sig, frame):
        log.info("收到信号 %s，正在退出...", signal.Signals(sig).name)
        stop_event.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        set_status(running=False, message="已停止")
        PID_FILE.unlink(missing_ok=True)
        log.info("UPS 看门狗已退出")

if __name__ == "__main__":
    main()
