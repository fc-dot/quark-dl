#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夸克网盘下载器（分享版）—— 把分享链接里的文件下载到本地。

只有一条命令，参数都可以不写，不写会逐项提问：

    python quark.py "https://pan.quark.cn/s/xxxxx"
    python quark.py "https://pan.quark.cn/s/xxxxx" --only "顶层目录/子目录/文件名.zip"
    python quark.py "https://pan.quark.cn/s/xxxxx" -o "D:\\保存目录"
    python quark.py "https://pan.quark.cn/s/xxxxx" --list     只看有哪些文件

第一次运行会要求粘贴一次 Cookie，之后自动记住（存在本目录 cookie.txt）。
换账号：python quark.py --reset

依赖：pip install curl_cffi   （缺少会被夸克的反爬拦掉）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent          # src 目录
ROOT = HERE.parent                              # 程序根目录
DATA_DIR = ROOT / "data"                        # 配置与记忆都放这里
DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = DATA_DIR / "cookie.txt"
CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "downloads.json"

IMPERSONATIONS = ["chrome", "chrome124", "chrome120", "chrome110",
                  "edge101", "safari17_0", "firefox133"]
HOSTS = ["drive-pc.quark.cn", "drive.quark.cn", "drive-h.quark.cn"]
SHARE_HOSTS = ["drive-h.quark.cn", "drive-pc.quark.cn", "drive.quark.cn"]

CLIENT_UAS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
     "Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/108.0.5359.220 "
     "Electron/22.3.3 Safari/537.36"),
]
BROWSER_UAS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
]
OSS_UA = "aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit"

THREADS = 512
CHUNK_MB = 2
RETRIES = 5

COOKIE = ""

try:
    from curl_cffi import requests as _cffi
except Exception:                                        # noqa: BLE001
    _cffi = None


def _enable_vt():
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)
            return True
    except Exception:                                    # noqa: BLE001
        pass
    return False


HAVE_VT = _enable_vt()


def _spinner_frames():
    """缺口的圆圈（盲文旋转点）；控制台不支持就退回 ASCII。"""
    uni = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    try:
        uni.encode(sys.stdout.encoding or "utf-8")
        return uni
    except Exception:                                    # noqa: BLE001
        return "|/-\\"


RED, PURPLE, YELLOW, RESET = "\033[31m", "\033[35m", "\033[33m", "\033[0m"

ERR_PREFIX = ("✗", "×", "错误")
OK_PREFIX = ("✓", "完成：", "完成 ")
PURPLE_PREFIX = ("Cookie 已失效", "出错了", "下载中断", "重试没成功",
                 "网络中断", "下载没成功", "取不到直链")


def log(msg=""):
    s = "" if msg is None else str(msg)
    if s and sys.stdout.isatty() and HAVE_VT:
        t = s.lstrip()
        if t.startswith(ERR_PREFIX):
            s = RED + s + RESET
        elif t.startswith(PURPLE_PREFIX):
            s = PURPLE + s + RESET
        elif t.startswith(OK_PREFIX):
            s = YELLOW + s + RESET
    print(s, flush=True)


def ask(prompt, default=""):
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return default
        v = input(prompt).strip()
        return v or default
    except (EOFError, KeyboardInterrupt):
        return default


# ------------------------------------------------------------------ Cookie
def extract_cookie(text):
    """从粘贴内容里取出 Cookie，兼容各种复制格式：

    1) 纯 Cookie 字符串
    2) cmd 格式 cURL：  -b ^"..."^        （Edge/Chrome cmd）
    3) bash 格式 cURL： -b '...'           （旧版）
    4) bash 格式 cURL： -H 'cookie: ...'   （Chrome/Edge 新版，最常见）
    5) 单独一行：        cookie: ...
    被 ^ 续行符拆成多行也能还原。
    """
    t = (text or "")
    t = re.sub(r"\^\s*\r?\n", "", t)      # 去掉 cmd 续行符
    t = t.replace("\r\n", "\n")

    patterns = (
        r"-b\s+\^?[\"'](.+?)\^?[\"']",                        # -b "..."
        r"--cookie\s+\^?[\"'](.+?)\^?[\"']",                  # --cookie "..."
        r"-H\s+\^?[\"']cookie:\s*(.+?)\^?[\"']",             # -H "cookie: ..."
        r"--header\s+\^?[\"']cookie:\s*(.+?)\^?[\"']",       # --header "cookie: ..."
        r"^\s*cookie:\s*(.+?)\s*$",                             # 单独一行
    )
    for pat in patterns:
        m = re.search(pat, t, re.S | re.I | (re.M if pat.startswith("^") else 0))
        if m:
            v = m.group(1).replace("\n", "").replace("\r", "")
            v = v.strip().rstrip("^").strip()
            if v:
                return v

    # 兜底：当作直接粘的 Cookie
    return (t.replace("\n", "").replace("\r", "")
             .strip().strip('"').rstrip("^").strip())


def _looks_like_cookie(s):
    """粗判是不是 Cookie：至少有 a=b，且带 __pus 或 b-user-id 之类关键字段。"""
    if not s:
        return False
    if "=" not in s:
        return False
    return any(k in s for k in ("__pus", "__puus", "b-user-id", "__uid", "ctoken"))


def ask_cookie():
    """读 Cookie：支持单行 Cookie，也支持多行 cURL。

    关键：必须一直读到空行为止，否则多行 cURL 的剩余行会串到下一个提示。
    """
    log("把 Cookie 或整段 cURL 粘贴到这里，粘完再按一次回车（留空行）结束：")
    lines = []
    while True:
        try:
            ln = input()
        except (EOFError, KeyboardInterrupt):
            break
        if ln.strip() == "":
            if lines:
                break
            continue
        lines.append(ln)
    return extract_cookie("\n".join(lines))


def _skip_curl_junk(text):
    """如果误把 cURL 的残余行填进了别的提示，丢掉它们。"""
    t = (text or "").strip()
    while t.startswith("-") or t.startswith("curl ") or t.startswith("^"):
        t = ""
        try:
            t = input().strip()
        except (EOFError, KeyboardInterrupt):
            break
    return t


def judge_login(j):
    """True=已登录 / False=未登录 / None=无法判断。"""
    if not isinstance(j, dict):
        return None
    code = j.get("code")
    msg = (j.get("message") or "").lower()
    if code in (31001, 401, -6, 111) or "require login" in msg or "guest" in msg:
        return False
    if code in (0, None) and j.get("data") is not None:
        return True
    return None


def verify_login():
    j = api("GET", "/1/clouddrive/file/sort",
            {"pr": "ucpro", "fr": "pc", "uc_param_str": "", "pdir_fid": "0",
             "_page": 1, "_size": 1, "_fetch_total": 1})
    return judge_login(j)


def ensure_login():
    global COOKIE
    state = verify_login()
    if state is True:
        log("✓ 已登录")
        return
    if state is None:
        log("提示：暂时无法验证 Cookie（网络问题），继续尝试。")
        return
    log("")
    log("Cookie 已失效，需要重新填一个。")
    for _ in range(3):
        v = ask_cookie()
        if not v:
            break
        COOKIE = v
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(COOKIE, encoding="utf-8")
        if verify_login() is True:
            log("✓ Cookie 已更新，已登录")
            return
        log("这个 Cookie 还是不行，再试一次：")
    raise SystemExit("✗ Cookie 无效，退出。请到浏览器重新抓取后再运行。")


def prompt_new_cookie():
    """Cookie 过期时，让用户粘贴新的并保存。返回是否已更新。"""
    global COOKIE
    if not sys.stdin or not sys.stdin.isatty():
        return False
    log("")
    log("Cookie 过期了，需要重新填一个（详细步骤见 使用说明.md 第五节）：")
    log("  F12 → 网络 → 筛选器输入 clouddrive → F5 → 右键请求 → 以 cURL 复制 → 取 -b 后面的整段")
    v = ask_cookie() if sys.stdin and sys.stdin.isatty() else ""
    if not v:
        log("已跳过，仍是旧 Cookie。")
        return False
    COOKIE = v.strip().strip('"')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(COOKIE, encoding="utf-8")
    log("✓ 已更新 Cookie，正在重试 …")
    return True


def record_download(path):
    """记下已下载的完整路径，卸载脚本按这份清单清理。"""
    try:
        data = []
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8-sig")) or []
            if not isinstance(data, list):
                data = []
        p = str(path)
        data = [d for d in data if d != p]
        data.append(p)
        HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass


def load_saved_dir():
    """读取上次用的保存目录。"""
    try:
        j = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        d = (j or {}).get("save_dir") or ""
        return d.strip()
    except Exception:                                    # noqa: BLE001
        return ""


def save_dir_to_config(path):
    """记住这次的保存目录。"""
    try:
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")) or {}
            except Exception:                            # noqa: BLE001
                data = {}
        data["save_dir"] = str(path)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:                                    # noqa: BLE001
        pass


def choose_out_dir(cli_out):
    """决定保存目录：命令行 --out 优先；否则问是否沿用上次；都没有就问。"""
    if cli_out:
        save_dir_to_config(cli_out)
        return cli_out

    last = load_saved_dir()
    if last:
        ans = ask(f"上次保存到：{last}\n还用这个地址吗？（直接回车 = 是；输入新路径 = 更改）：")
        if ans:
            save_dir_to_config(ans)
            return ans
        return last

    first = ask("保存到哪个目录？（直接回车 = 当前目录）：", ".") or "."
    save_dir_to_config(first)
    log(f"已记住这个保存位置，下次可以直接回车沿用")
    return first


def load_cookie(force=False):
    global COOKIE
    if force:
        COOKIE_FILE.unlink(missing_ok=True)
    if COOKIE_FILE.exists():
        COOKIE = COOKIE_FILE.read_text(encoding="utf-8-sig").strip()
    if COOKIE:
        return
    log("")
    log("=" * 60)
    log("第一次使用，需要填入你的夸克 Cookie（相当于登录凭证）。")
    log("")
    log("  如不了解如何获取 Cookie，去同目录的「使用说明.md」里查看，")
    log("  第五节「第一次用怎么填 Cookie」有分步图文说明。")
    log("=" * 60)
    log("")
    v = ask_cookie()
    if not v:
        raise SystemExit("没有 Cookie，退出")
    COOKIE = v.strip().strip('"')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(COOKIE, encoding="utf-8")
    log(f"已保存到 {COOKIE_FILE}")


# ------------------------------------------------------------------ 传输层
class Transport:
    def __init__(self):
        self.good = None

    def _order(self):
        if self.good:
            return [self.good] + [i for i in IMPERSONATIONS if i != self.good]
        return list(IMPERSONATIONS)

    def request(self, method, url, headers, body=None, timeout=40, stream=False):
        if _cffi is None:
            raise SystemExit("缺少依赖 curl_cffi。请先双击「安装依赖.bat」，或执行：pip install curl_cffi")
        data = json.dumps(body) if body is not None else None
        last = None
        for imp in self._order():
            try:
                r = _cffi.request(method, url, headers=headers, data=data,
                                  impersonate=imp, timeout=timeout,
                                  allow_redirects=True, stream=stream)
                self.good = imp
                payload = r.iter_content(256 * 1024) if stream else r.content
                return r.status_code, payload, dict(r.headers)
            except Exception as e:                       # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
        return 0, str(last).encode(), {}


TR = Transport()


def headers_api(ua):
    return {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
        "Cookie": COOKIE,
        "cache-control": "no-cache",
    }


def as_json(raw):
    try:
        return json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes)
                          else raw)
    except Exception:                                    # noqa: BLE001
        return None


def find_links(obj):
    hits = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and v.startswith("http") and \
                        k.lower() in ("download_url", "dlink", "downloadurl"):
                    hits.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(obj)
    return hits


def api(method, path, params=None, body=None, ua=None, hosts=None):
    ua = ua or CLIENT_UAS[0]
    for host in (hosts or HOSTS):
        url = f"https://{host}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        st, raw, _h = TR.request(method, url, headers_api(ua), body=body)
        j = as_json(raw)
        if isinstance(j, dict):
            return j
    return None


# ------------------------------------------------------------------ 分享
def passcode_of(url):
    """从分享链接里取提取码：?pwd=xxxx 或 #pwd=xxxx"""
    m = re.search(r"[?&#]pwd=([0-9A-Za-z]+)", url or "")
    return m.group(1) if m else ""


def url_of_input(text):
    """从粘贴的内容里找出链接：支持整条命令、带引号、带参数。"""
    raw = (text or "").strip().replace("\t", " ")
    for tok in raw.split(" "):
        t = tok.strip().strip('"').strip("'")
        if t.lower().startswith(("http://", "https://")):
            return t
    return raw.split(" ")[0].strip().strip('"').strip("'") if raw else ""


def only_of_input(text):
    """从粘贴的内容里找 --only 后面的路径。"""
    m = re.search(r"--only\s*[= ]\s*"
                  r"(\"[^\"]+\"|'[^']+'|[^\s]+)", text or "", re.I)
    if not m:
        return None
    v = m.group(1).strip()
    return v.strip('"').strip("'")


def share_id_of(url):
    m = re.search(r"/s/([0-9A-Za-z]+)", url or "")
    return m.group(1) if m else (url or "").strip()


def share_token(pwd_id, passcode=""):
    j = api("POST", "/1/clouddrive/share/sharepage/token",
            {"pr": "ucpro", "fr": "pc"},
            body={"pwd_id": pwd_id, "passcode": passcode}, hosts=SHARE_HOSTS)
    if not j:
        return None, "接口无响应"
    st = (j.get("data") or {}).get("stoken")
    if not st:
        return None, (f"取 token 失败：{j.get('message') or j.get('code')}"
                      f"（提取码错或分享已失效）")
    return st, None


def share_walk(pwd_id, stoken, pdir_fid="0", prefix=""):
    j = api("GET", "/1/clouddrive/share/sharepage/detail",
            {"pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken,
             "pdir_fid": pdir_fid, "force": "0", "_page": 1, "_size": 100,
             "_fetch_banner": "0", "_fetch_share": "0", "_fetch_total": "1",
             "_sort": "file_type:asc,file_name:asc"}, hosts=SHARE_HOSTS)
    out = []
    for it in ((j or {}).get("data") or {}).get("list") or []:
        name = it.get("file_name")
        if it.get("dir"):
            out.extend(share_walk(pwd_id, stoken, it.get("fid"), f"{prefix}{name}/"))
        else:
            out.append((f"{prefix}{name}", it.get("fid"), it.get("size") or 0,
                        it.get("share_fid_token") or ""))
    return out


def share_save(pwd_id, stoken, to_fid, items=None):
    body = {"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0",
            "to_pdir_fid": to_fid, "scene": "link", "exclude_fids": []}
    if items:
        body["pdir_save_all"] = False
        body["fid_list"] = [f for f, _t in items]
        body["fid_token_list"] = [t for _f, t in items]
    else:
        body["pdir_save_all"] = True
    j = api("POST", "/1/clouddrive/share/sharepage/save",
            {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}, body=body,
            hosts=SHARE_HOSTS)
    if not j:
        return None, "接口无响应"
    info = f"code={j.get('code')} message={j.get('message') or '(无)'}"
    return (j.get("data") or {}).get("task_id"), info


def wait_task(task_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = api("GET", "/1/clouddrive/task",
                {"pr": "ucpro", "fr": "pc", "uc_param_str": "",
                 "task_id": task_id, "retry_index": 0})
        d = (j or {}).get("data") or {}
        if d.get("status") == 2:
            return d
        if d.get("status") == 3:
            return {"_failed": True, **d}
        time.sleep(2)
    return None


def list_dir(pdir_fid):
    j = api("GET", "/1/clouddrive/file/sort",
            {"pr": "ucpro", "fr": "pc", "uc_param_str": "", "pdir_fid": pdir_fid,
             "_page": 1, "_size": 200, "_fetch_total": 1,
             "_sort": "file_type:asc,file_name:asc"})
    return ((j or {}).get("data") or {}).get("list") or []


def root_dir_fid():
    """优先用「来自：分享」目录，找不到就用根目录。"""
    for it in list_dir("0"):
        if it.get("dir") and "分享" in (it.get("file_name") or ""):
            return it.get("fid")
    return "0"


def account_space():
    j = api("GET", "/1/clouddrive/member",
            {"pr": "ucpro", "fr": "pc", "uc_param_str": "",
             "fetch_subscribe": "true", "_ch": "home"},
            hosts=["drive-member-h.quark.cn", "drive-pc.quark.cn"])
    d = (j or {}).get("data") or {}
    try:
        return int(d.get("total_capacity") or 0), int(d.get("use_capacity") or 0)
    except (TypeError, ValueError):
        return 0, 0


# ------------------------------------------------------------------ 下载
def path_match(full, want):
    w = (want or "").replace(chr(92), "/").strip()
    if not w:
        return True
    full = full.replace(chr(92), "/")
    return full.startswith(w) if w.endswith("/") else full == w


def get_dlink(fid):
    for ua in CLIENT_UAS + BROWSER_UAS:
        j = api("POST", "/1/clouddrive/file/download",
                {"pr": "ucpro", "fr": "pc", "uc_param_str": ""},
                body={"fids": [fid]}, ua=ua)
        if not j or j.get("code") == 23018:
            continue
        links = find_links(j)
        if links:
            return links[0], {
                "User-Agent": ua, "Referer": "https://pan.quark.cn/",
                "Cookie": COOKIE, "x-oss-user-agent": OSS_UA, "Accept": "*/*"}
    return None, None


def probe(url, hdrs):
    st, _p, h = TR.request("GET", url, {**hdrs, "Range": "bytes=0-0"}, timeout=40)
    total = None
    cr = (h or {}).get("content-range") or (h or {}).get("Content-Range")
    if cr and "/" in cr:
        tail = cr.rsplit("/", 1)[-1]
        if tail.isdigit():
            total = int(tail)
    return st, total, st == 206


def download(fid, out, threads=THREADS, chunk_mb=CHUNK_MB):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    state_path = out.with_name(out.name + ".qstate.json")
    chunk = max(1, int(chunk_mb)) * 1024 * 1024

    state = {"last_msg": "", "hold": 0.0}

    def say(msg, hold=3.0):
        """单行提示：TTY 下原地覆盖，非 TTY 下同一句只打一次。"""
        state["hold"] = time.time() + hold
        try:
            if sys.stdout.isatty():
                sys.stdout.write("\r" + msg + " " * 10)
                sys.stdout.flush()
            elif msg != state["last_msg"]:
                state["last_msg"] = msg
                log("   " + msg)
        except Exception:                            # noqa: BLE001
            pass


    asked_cookie = False

    for attempt in range(3):
        dlink, hdrs = get_dlink(fid)
        if not dlink:
            say("取不到直链，可能是 Cookie 过期")
            if not asked_cookie:
                asked_cookie = True
                if prompt_new_cookie():
                    continue
            time.sleep(3)
            continue
        st, total, ranges = probe(dlink, hdrs)
        if st in (403, 410, 412) or not total:
            say(f"下载链接过期了（HTTP {st}），正在重新获取")
            continue

        segs = [(i * chunk, min((i + 1) * chunk, total) - 1)
                for i in range((total + chunk - 1) // chunk)]

        started_at = time.time()
        if state_path.exists():
            try:
                s0 = json.loads(state_path.read_text(encoding="utf-8"))
                started_at = float(s0.get("started_at") or started_at)
            except Exception:                            # noqa: BLE001
                pass

        done = set()
        if state_path.exists() and out.exists() and out.stat().st_size == total:
            try:
                s = json.loads(state_path.read_text(encoding="utf-8"))
                if s.get("size") == total and s.get("chunk") == chunk:
                    done = set(s.get("done", []))
            except Exception:                            # noqa: BLE001
                done = set()
        if not out.exists() or out.stat().st_size != total:
            with open(out, "wb") as fh:
                fh.truncate(total)
            done = set()

        lock = threading.Lock()
        prog = {"b": sum(e - s + 1 for i, (s, e) in enumerate(segs) if i in done)}
        expired = threading.Event()

        def save_state():
            try:
                tmp = state_path.with_name(state_path.name + ".tmp")
                tmp.write_text(json.dumps({
                    "size": total, "chunk": chunk, "done": sorted(done),
                    "live_bytes": prog["b"], "ts": time.time(),
                    "started_at": started_at, "url": dlink}), encoding="utf-8")
                os.replace(tmp, state_path)
            except Exception:                            # noqa: BLE001
                pass

        def worker(idx):
            s, e = segs[idx]
            for a in range(RETRIES):
                pos, contrib = s, 0
                try:
                    stx, payload, _h = TR.request(
                        "GET", dlink, {**hdrs, "Range": f"bytes={s}-{e}"},
                        stream=True, timeout=90)
                    if stx in (403, 410, 412):
                        expired.set()
                        raise RuntimeError(f"HTTP {stx}")
                    if stx not in (200, 206):
                        raise RuntimeError(f"HTTP {stx}")
                    with open(out, "r+b") as fh:
                        fh.seek(s)
                        for b in payload:
                            fh.write(b)
                            pos += len(b)
                            contrib += len(b)
                            with lock:
                                prog["b"] += len(b)
                    if pos != e + 1:
                        raise RuntimeError("块字节数不符")
                    with lock:
                        done.add(idx)
                        save_state()
                    return
                except Exception as exc:                 # noqa: BLE001
                    with lock:
                        prog["b"] -= contrib
                        done.discard(idx)
                    if expired.is_set():
                        return
                    if a == RETRIES - 1:
                        raise RuntimeError(str(exc))
                    time.sleep(min(2 ** a, 20))

        todo = [i for i in range(len(segs)) if i not in done]
        if not todo:
            log("  已完整，跳过")
            return True

        log(f"  {total / 1e6:.1f} MB，{len(segs)} 块，{threads} 线程，"
            f"已完成 {len(done)} 块")

        def panel(cur, cur_sp):
            pct = cur / total * 100 if total else 0
            eta = (total - cur) / (cur_sp * 1e6) / 60 if cur_sp > 0.05 else -1
            eta_txt = f"{eta:4.0f} 分钟" if eta >= 0 else "  --  "
            elapsed = max(time.time() - started_at, 1e-6)
            avg = cur / 1e6 / elapsed
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at))
            link = dlink if len(dlink) <= 96 else dlink[:96] + " …"
            return "\n".join([
                f"[下载中] {out.name}",
                f"  开始时间：{t}（已运行 {elapsed / 60:.1f} 分钟）",
                f"  下载链接：{link}",
                f"  进度：{cur / 1e6:.1f} / {total / 1e6:.1f} MB   {pct:.1f}%",
                f"  当前速率：{cur_sp:5.2f} MB/s    平均速率：{avg:5.2f} MB/s"
                f"    剩余 {eta_txt}",
                f"  位置：{out}",
            ])

        stop_evt = threading.Event()

        log(f"正在下载：{out.name}")
        log(f"  开始时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}")
        log(f"  下载链接：{dlink if len(dlink) <= 96 else dlink[:96] + ' …'}")
        log(f"  保存位置：{out}")
        log("")

        frames = _spinner_frames()
        def reporter():
            tty = sys.stdout.isatty()
            last, last_t = prog["b"], time.time()
            i = 0
            while not stop_evt.wait(0.2 if tty else 30.0):
                now = time.time()
                if now < state.get("hold", 0.0):
                    continue
                cur = prog["b"]
                sp = (cur - last) / 1e6 / max(now - last_t, 0.01)
                pct = cur / total * 100 if total else 0
                avg = cur / 1e6 / max(now - started_at, 1e-6)
                eta = (total - cur) / (sp * 1e6) / 60 if sp > 0.05 else -1
                eta_txt = f"{eta:.0f}分钟" if eta >= 0 else "--"
                spin = frames[i % len(frames)]
                i += 1
                line = (f"{spin} {pct:5.1f}%  {cur / 1e6:.1f}/{total / 1e6:.1f} MB  "
                        f"当前 {sp:.2f} MB/s  平均 {avg:.2f} MB/s  剩余 {eta_txt}")
                if tty:
                    sys.stdout.write("\r" + line + " " * 4)
                    sys.stdout.flush()
                else:
                    log("   " + line)
                last, last_t = cur, now
            if tty:
                sys.stdout.write("\r" + " " * 90 + "\r")
                sys.stdout.flush()

        th = threading.Thread(target=reporter, daemon=True)
        th.start()
        t0 = time.perf_counter()
        pool = ThreadPoolExecutor(min(threads, max(len(todo), 1)))
        failed = None
        futs = []
        try:
            futs = [pool.submit(worker, i) for i in todo]
            for f in futs:
                f.result()
        except Exception as exc:                         # noqa: BLE001
            failed = exc
            for f in futs:
                f.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
        finally:
            stop_evt.set()

        if failed is not None:
            if expired.is_set():
                log(f"  [直链] 中途过期，重新取链后继续（已完成 {len(done)} 块）")
                continue
            if attempt < 2:
                say(f"网络中断了，已下好 {len(done)}/{len(segs)} 块，3 秒后自动重试 …")
                time.sleep(3)
                continue
            say("重试没成功。重新运行同一条命令，会从断点继续。", hold=600)
            return False

        if out.stat().st_size == total and len(done) == len(segs):
            state_path.unlink(missing_ok=True)
            record_download(out)
            log(f"  ✓ 完成 {total / 1e6:.1f} MB，用时 "
                f"{(time.perf_counter() - t0) / 60:.1f} 分钟")
            return True
    return False


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(
        description="下载夸克网盘分享里的文件",
        epilog='例子：python quark.py "https://pan.quark.cn/s/xxxx" --only "目录/文件.zip"')
    ap.add_argument("url", nargs="?", help="分享链接")
    ap.add_argument("--only", default=None,
                    help="只下这个路径（从顶层目录写全；以 / 结尾表示整个目录）")
    ap.add_argument("-o", "--out", default=None, help="保存到哪个目录")
    ap.add_argument("--list", "--dry-run", dest="list", action="store_true",
                    help="只列出文件，不下载")
    ap.add_argument("--passcode", default="", help="提取码")
    ap.add_argument("-t", "--threads", type=int, default=THREADS, help="连接数（默认 512，512 最快）")
    args = ap.parse_args()

    log("提示：下载过程中请不要关闭这个窗口，关闭会中断下载"
        "（重新运行同一条命令可从断点继续）")
    log("")

    load_cookie()
    ensure_login()

    if not args.url:
        log("参数写在链接后面，例如：  \"分享链接\" --list")
        log("可携带的参数：")
        log("  --list / --dry-run   只列出文件，不下载（不转存、不占网盘空间）")
        log("  --only \"路径\"         只下载这一个文件；路径以 / 结尾表示整个目录")
        log("  -o \"目录\"            保存到指定目录（不写会问，并记住上次的）")
        log("  --passcode 1234      提取码（链接里带 ?pwd= 会自动识别）")
        log("  -t 128               连接数（默认 512，实测 512 最快；再高反而更慢）")
        log("")
    typed = args.url or ask("把分享链接粘贴到这里后回车（可直接带上面任一参数）：")
    if not typed:
        raise SystemExit("没有链接，退出")
    low = typed.lower().replace("--dry--run", "--dry-run").replace("--dryrun", "--dry-run")
    if "--dry-run" in low or "--list" in low:
        args.list = True
    if args.only is None:
        got = only_of_input(typed)
        if got:
            args.only = got
    url = url_of_input(typed)

    pwd_id = share_id_of(url)
    log(f"分享 ID：{pwd_id}")
    args.passcode = args.passcode or passcode_of(url)
    if args.passcode:
        log(f"提取码：{args.passcode}（来自链接）")
    stoken, err = share_token(pwd_id, args.passcode)
    if not stoken and prompt_new_cookie():
        stoken, err = share_token(pwd_id, args.passcode)
    if not stoken:
        raise SystemExit(f"✗ {err}")
    log("✓ 读取分享成功")

    all_files = share_walk(pwd_id, stoken)
    if not all_files:
        raise SystemExit("✗ 分享里没有文件（可能需要提取码）")

    files = all_files
    if args.only is None and not args.list:
        kw = ask("只下载指定路径？（直接回车 = 全部；格式：顶层目录/子目录/文件名）：")
        if kw:
            kw = re.sub(r"^\s*--only\s*[= ]?\s*", "", kw, flags=re.I).strip()
            args.only = kw.strip('"').strip("'")
    if args.only:
        files = [f for f in all_files if path_match(f[0], args.only)]
        if not files:
            raise SystemExit(f'✗ 没有匹配 "{args.only}" 的文件。'
                             f"加 --list 看完整路径（从顶层目录开始，一字不差）")

    total_size = sum(f[2] for f in files)
    log(f"选中 {len(files)} 个文件，合计 {total_size / 1e9:.2f} GB")
    for rel, _f, sz, _t in files:
        log(f"    {sz / 1e6:9.1f} MB  {rel}")
    if args.list:
        return 0

    args.out = choose_out_dir(args.out)
    log(f"保存目录：{Path(args.out).resolve()}")

    to_fid = root_dir_fid()
    cap_total, cap_used = account_space()
    if cap_total:
        free = cap_total - cap_used
        log(f"网盘容量：总 {cap_total / 1e9:.1f} GB，已用 {cap_used / 1e9:.1f} GB，"
            f"可用 {free / 1e9:.1f} GB")
        if total_size > free:
            raise SystemExit("✗ 网盘空间不足，转存必定失败。"
                             "用 --only 只挑需要的文件，或先清理网盘。")

    partial = [(f, t) for _r, f, _s, t in files] if args.only else None
    task, info = share_save(pwd_id, stoken, to_fid, items=partial)
    if not task:
        raise SystemExit(f"✗ 转存失败：{info}")
    log(f"✓ 已提交转存（{'仅选中文件' if partial else '整个分享'}），等待完成 …")
    d = wait_task(task)
    if not d or d.get("_failed"):
        raise SystemExit("✗ 转存未成功："
                         + json.dumps(d or {}, ensure_ascii=False)[:300])
    log("✓ 转存完成")

    tops = ((d.get("save_as") or {}).get("save_as_top_fids")) or []
    saved = [it for it in list_dir(to_fid) if it.get("fid") in tops]
    by_name = {}
    for it in saved + list_dir(to_fid):
        by_name.setdefault(it.get("file_name"), it.get("fid"))

    ok = fail = 0
    for i, (rel, f, sz, _t) in enumerate(files, 1):
        fid = by_name.get(Path(rel).name) or f
        dest = Path(args.out) / rel.replace("/", os.sep)
        if dest.exists() and sz and dest.stat().st_size == sz and \
                not dest.with_name(dest.name + ".qstate.json").exists():
            log(f"[{i}/{len(files)}] 已存在，跳过：{rel}")
            continue
        log(f"\n[{i}/{len(files)}] {rel}  {sz / 1e6:.1f} MB")
        if download(fid, dest, args.threads, CHUNK_MB):
            ok += 1
        else:
            fail += 1
    log(f"\n完成：成功 {ok}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        log("\n已中断。重新运行同一条命令就会从断点继续。")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:                             # noqa: BLE001
        msg = str(exc)
        if "Connection" in msg or "reset" in msg.lower() or "timed out" in msg:
            log("")
            log("  网络连接断了。重新运行同一条命令就能继续下载。")
        else:
            log("")
            log(f"  出错了：{msg}")
            log("  可以先重新运行一次；如果还不行，把这段提示发出来。")
        sys.exit(1)
