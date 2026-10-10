# -*- coding: utf-8 -*-
"""tmd 验证码协议客户端（纯 HTTP，无浏览器渲染）。

覆盖 `_____tmd_____` 惩罚链路的两步交互：**取题 → 提交**。

依据（实测台账 `docs/api.md` §F/§H/§J/§K/§L/§M）：
  · 参数顺序实测 100% 稳定：
      取题（scene_get / puzzle_get）:
        token, appKey, ua, x5secdata, language, pageVersion, _rand, v
      提交（scene_validate / puzzle_validate）:
        token, appKey, ua, umidToken, encryptToken, x5secdata, time, pageVersion,
        fireyeVersion, width, per, ppt, _rand, v
  · 值编码 = JS `encodeURIComponent`（`!`/`'`/`(`/`)`/`*`/`~`/`-`/`_`/`.` 不转义；`+`→`%2B`，`/`→`%2F`）
  · `ua` = `bx-ua`（可用 `local_signature_provider.py` 离线纯算）
  · `_rand` = `crypto.genRandomValues(seed, n)` → base64url（`n<=0` 默认 33 B ⇒ 44 字符；`n=120` ⇒ 160 字符）
  · `ppt` = PoW 计算耗时（ms）；`per`/`width`/`time` = 交互答案与度量

本模块**只组装请求**，不主动发起网络请求；发不发、发给谁由调用方决定。
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import time
import urllib.parse

# ---------------------------------------------------------------------------
# JS `encodeURIComponent` 语义
# ---------------------------------------------------------------------------
_JS_SAFE = "!'()*-._~"


def encode_uri_component(s: str) -> str:
    """等价于 JS `encodeURIComponent`（`!` 等不转义）。"""
    return urllib.parse.quote(str(s), safe=_JS_SAFE + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def build_query(pairs) -> str:
    """按给定顺序拼 query（键不编码，值走 encodeURIComponent）。"""
    return "&".join(f"{k}={encode_uri_component(v)}" for k, v in pairs)


# ---------------------------------------------------------------------------
# `_rand`（等价于窗口侧 polyfill_rand.js）
# ---------------------------------------------------------------------------
def b64url(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode().replace("+", "-").replace("/", "_").rstrip("=")


def gen_rand(seed: str = "", n: int = 0, entropy=None) -> str:
    """复刻 `crypto.genRandomValues(seed, n)`：base64url(n 字节)。

    `n<=0` 时取默认 33 字节（⇒ 44 字符，与实测 `genRandomValues("",0)` 一致）。
    `entropy` 可注入确定性字节源（用于自检/对拍）。
    """
    ln = n if isinstance(n, int) and n > 0 else 33
    if entropy is None:
        entropy = lambda k: __import__("os").urandom(k)  # noqa: E731
    b = bytearray(entropy(ln))
    if seed:
        sb = seed.encode()
        for i in range(min(len(sb), ln)):
            b[i] ^= sb[i]
    return b64url(bytes(b))


# ---------------------------------------------------------------------------
# 惩罚页 `_config_` 解析
# ---------------------------------------------------------------------------
class PunishConfig:
    """惩罚页 `window._config_`（服务端 HTML 内联）。"""

    FIELDS = ("renderTo", "NCTOKENSTR", "action", "HOST", "PATH", "FORMACTIOIN", "BXSTEP",
              "SECDATA", "NCAPPKEY", "nonce", "qrcode", "pp", "url", "crossSite",
              "language", "isUpgrade", "customImage", "logo", "logoLink", "copyright",
              "noCaptchaCallback", "languageConfig", "languageConfigJson", "noCaptchaLanguage")

    def __init__(self, raw: dict):
        self.raw = raw

    # -- 解析 --
    @classmethod
    def from_html(cls, html: str) -> "PunishConfig":
        return cls(_extract_config(html))

    @classmethod
    def from_file(cls, path) -> "PunishConfig":
        return cls.from_html(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))

    # -- 便捷访问 --
    @property
    def token(self) -> str:
        return str(self.raw.get("NCTOKENSTR", ""))

    @property
    def app_key(self) -> str:
        return str(self.raw.get("NCAPPKEY", ""))

    @property
    def secdata(self) -> str:
        return str(self.raw.get("SECDATA", ""))

    @property
    def action(self) -> str:
        return str(self.raw.get("action", ""))

    @property
    def nonce(self) -> str:
        return str(self.raw.get("nonce", ""))

    @property
    def path_prefix(self) -> str:
        """协议相对前缀 `//<host><path>`。

        `_config_.HOST` 实测为 `login.example.invalid:443`；浏览器会归一化掉默认端口
        （抓包 URL 为 `https://example.invalid/...`）⇒ 这里同样**剥离 `:443`**。
        """
        host = str(self.raw.get("HOST", ""))
        if host.endswith(":443"):
            host = host[:-4]
        path = str(self.raw.get("PATH", ""))
        return "//" + host + path

    @property
    def verify_path(self) -> str:
        return str(self.raw.get("FORMACTIOIN", ""))

    @property
    def pp(self) -> dict:
        v = self.raw.get("pp")
        return v if isinstance(v, dict) else {}

    def question_kind(self) -> str:
        """由 `action` 判定题目类型。"""
        a = self.action
        if "slide" in a:
            return "slider_v2"          # scene_get / scene_validate
        if "puzzle" in a:
            return "puzzle"             # puzzle_get / puzzle_validate
        return "unknown"

    # -- URL 构造 --
    def question_url(self, *, ua, rand, v, language="cn", page_version="0.1.129",
                     app_key=None, token=None, secdata=None, host=None) -> str:
        """取题 URL（实测参数序：token, appKey, ua, x5secdata, language, pageVersion, _rand, v）。"""
        kind = self.question_kind()
        leaf = "scene_get" if kind == "slider_v2" else "puzzle_get"
        base = host or self.path_prefix
        pairs = [
            ("token", token or self.token),
            ("appKey", app_key or self.app_key),
            ("ua", ua),
            ("x5secdata", secdata or self.secdata),
            ("language", language),
            ("pageVersion", page_version),
            ("_rand", rand),
            ("v", v),
        ]
        return f"{base}/_____tmd_____/{leaf}?{build_query(pairs)}"

    def validate_url(self, *, ua, umid_token, encrypt_token, rand, v, per, width=272,
                     elapsed_ms=None, ppt=None, fireye_version="1.234.37",
                     page_version="0.1.129", app_key=None, token=None, secdata=None,
                     host=None) -> str:
        """提交 URL（实测参数序：token, appKey, ua, umidToken, encryptToken, x5secdata, time,
        pageVersion, fireyeVersion, width, per, ppt, _rand, v）。"""
        kind = self.question_kind()
        leaf = "scene_validate" if kind == "slider_v2" else "puzzle_validate"
        base = host or self.path_prefix
        pairs = [
            ("token", token or self.token),
            ("appKey", app_key or self.app_key),
            ("ua", ua),
            ("umidToken", umid_token),
            ("encryptToken", encrypt_token),
            ("x5secdata", secdata or self.secdata),
            ("time", int(elapsed_ms if elapsed_ms is not None else 0)),
            ("pageVersion", page_version),
            ("fireyeVersion", fireye_version),
            ("width", int(width)),
            ("per", per),
            ("ppt", int(ppt if ppt is not None else 0)),
            ("_rand", rand),
            ("v", v),
        ]
        return f"{base}/_____tmd_____/{leaf}?{build_query(pairs)}"


def _extract_config(html: str) -> dict:
    """从惩罚页 HTML 提取 `window._config_ = {...}`（花括号配平）。"""
    m = re.search(r"window\._config_\s*=\s*\{", html)
    if not m:
        raise ValueError("未找到 window._config_")
    i = m.end() - 1
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                body = html[i:j + 1]
                break
    else:
        raise ValueError("_config_ 花括号未闭合")
    try:
        return json.loads(body)
    except Exception:
        # 容忍尾逗号 / 单引号
        body = re.sub(r",\s*([}\]])", r"\1", body)
        return json.loads(body.replace("'", '"'))


def parse_punish_url(login_resp_text: str) -> str | None:
    """从登录被拦响应里取 `data.url`（= 惩罚页 URL，服务端下发）。"""
    try:
        d = json.loads(login_resp_text)
    except Exception:
        m = re.search(r'"url"\s*:\s*"([^"]+)"', login_resp_text)
        return m.group(1) if m else None
    url = (d.get("data") or {}).get("url")
    return url


def parse_question_resp(text: str) -> dict:
    """解析取题响应（图片内联 base64）。"""
    d = json.loads(text)
    data = d.get("data") or {}
    out = {"code": d.get("code"), "encryptToken": data.get("encryptToken")}
    for k in ("imageData", "ques", "answerArea", "puzzle", "puzzleY", "tag"):
        if k in data:
            out[k] = data[k]
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="tmd 验证码协议客户端（只组装请求，不发请求）")
    ap.add_argument("--config-html", help="惩罚页 HTML 文件（解析 _config_）")
    ap.add_argument("--from-jsonl", metavar="FILE", help="从抓包 index.jsonl 重建并校验真实 URL")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.config_html:
        cfg = PunishConfig.from_file(args.config_html)
        print(json.dumps({k: (f"<{len(str(v))} 字符>" if isinstance(v, str) and len(str(v)) > 60 else v)
                          for k, v in cfg.raw.items()}, ensure_ascii=False, indent=1))
        print("题类型:", cfg.question_kind())
        return 0
    if args.from_jsonl:
        return _verify_against(args.from_jsonl)
    ap.print_help()
    return 2


def _selftest() -> int:
    ok = fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  [PASS] {name} {detail}")
        else:
            fail += 1
            print(f"  [FAIL] {name} {detail}")

    print("== captcha_flow 自检 ==")
    # 1) JS encodeURIComponent 语义
    check("encodeURIComponent('234!a+b/c d') 保持 ! 不转义",
          encode_uri_component("234!a+b/c d") == "234!a%2Bb%2Fc%20d",
          encode_uri_component("234!a+b/c d"))
    check("encodeURIComponent 不转义 ' ( ) * ~ - _ .",
          encode_uri_component("'()*~-._") == "'()*~-._")

    # 2) _rand 形态（与实测一致）
    r0 = gen_rand("", 0)
    r120 = gen_rand("x" * 40, 120)
    check("gen_rand('',0) == 44 字符（实测 loadSuccessRand）", len(r0) == 44, f"{len(r0)}")
    check("gen_rand(seed,120) == 160 字符（实测 _rand）", len(r120) == 160, f"{len(r120)}")
    check("gen_rand 输出为 base64url 字符集", re.fullmatch(r"[A-Za-z0-9_-]+", r0 + r120) is not None)
    check("seed 影响输出", gen_rand("a", 33, entropy=lambda k: bytes(k)) != gen_rand("b", 33, entropy=lambda k: bytes(k)))
    check("同参可复现（注入熵源）",
          gen_rand("s", 16, entropy=lambda k: b"\x01" * k) == gen_rand("s", 16, entropy=lambda k: b"\x01" * k))

    # 3) _config_ 解析（用真实抓到的惩罚页 HTML）
    root = pathlib.Path(__file__).resolve().parents[2]      # sites/local_study
    cap = root.parent / "_artifacts/local_capture"
    htmls = sorted((cap / "bodies").glob("*.html")) if (cap / "bodies").is_dir() else []
    pun = None
    for h in htmls:
        t = h.read_text(encoding="utf-8", errors="replace")
        if "punishpage.min.js" in t and "window._config_" in t:
            pun = t
            break
    if pun:
        cfg = PunishConfig.from_html(pun)
        check("_config_ 解析出 NCTOKENSTR(32)", len(cfg.token) == 32, f"{cfg.token[:8]}")
        check("_config_ 解析出 NCAPPKEY", len(cfg.app_key) >= 32, f"{len(cfg.app_key)}")
        check("_config_ 解析出 SECDATA", len(cfg.secdata) > 100, f"{len(cfg.secdata)}")
        check("_config_ 解析出 pp(PoW 配置)", bool(cfg.pp) and cfg.pp.get("f") == "md5", f"{cfg.pp.get('f')}")
        check("question_kind 判定为滑块 v2", cfg.question_kind() == "slider_v2", cfg.question_kind())
        check("verify_path 以 /verify/ 结尾", cfg.verify_path.endswith("/verify/"), cfg.verify_path)
    else:
        print("  [SKIP] 未找到惩罚页 HTML")

    # 4) 对拍真实抓包（最强证据）
    jl = cap / "index.jsonl"
    if jl.is_file():
        n = _verify_against(jl, quiet=True)
        check(f"真实抓包重建 {n} 条 URL 逐字节一致", n >= 10, f"{n} 条")
    else:
        print("  [SKIP] 无 index.jsonl")

    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


def _verify_against(jsonl_path, quiet=False) -> int:
    """用真实抓包重建 URL 并逐字节比对；返回一致条数。"""
    import urllib.parse as up
    rows = [json.loads(l) for l in pathlib.Path(jsonl_path).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    match = 0
    total = 0
    for r in rows:
        u = r.get("url") or ""
        if "scene_get" not in u and "scene_validate" not in u:
            continue
        total += 1
        q = dict(up.parse_qsl(up.urlparse(u).query))
        if "ua" not in q:
            total -= 1                     # 无 ua 的少量变体（早期/降级）不参与对拍
            continue
        if "scene_get" in u:
            pairs = [("token", q["token"]), ("appKey", q["appKey"]), ("ua", q["ua"]),
                     ("x5secdata", q["x5secdata"]), ("language", q.get("language", "cn")),
                     ("pageVersion", q.get("pageVersion", "0.1.129")),
                     ("_rand", q["_rand"]), ("v", q["v"])]
            leaf = "scene_get"
        else:
            pairs = [("token", q["token"]), ("appKey", q["appKey"]), ("ua", q["ua"]),
                     ("umidToken", q.get("umidToken", "")), ("encryptToken", q.get("encryptToken", "")),
                     ("x5secdata", q["x5secdata"]), ("time", q.get("time", "0")),
                     ("pageVersion", q.get("pageVersion", "0.1.129")),
                     ("fireyeVersion", q.get("fireyeVersion", "1.234.37")),
                     ("width", q.get("width", "272")), ("per", q.get("per", "")),
                     ("ppt", q.get("ppt", "0")), ("_rand", q["_rand"]), ("v", q["v"])]
            leaf = "scene_validate"
        built = f"_____tmd_____/{leaf}?" + build_query(pairs)
        got = up.urlparse(u).path.split("/_____tmd_____/")[-1]
        got = "_____tmd_____/" + got + "?" + up.urlparse(u).query
        if built == got:
            match += 1
        elif not quiet:
            print(f"  不匹配: {leaf}\n     built={built[:160]}\n     real ={got[:160]}")
    if not quiet:
        print(f"重建一致 {match}/{total}")
    return match


if __name__ == "__main__":
    raise SystemExit(main())
