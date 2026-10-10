# -*- coding: utf-8 -*-
"""验证码**取题器**（稳定拿图）—— 只做「取题」，不含提交。

依据（实测 `docs/api.md` §F/§J.3/§K/§Q）：
  · **取题请求不需要 `bx-et` / `bx-pp` / `x-pipu2`**（实测 11+3 个取题请求仅有常规头）
    ⇒ 「稳定拿图」**无需任何 jsvmp 还原**
  · 取题 URL（参数序实测 100% 稳定）：
      token, appKey, ua, x5secdata, language, pageVersion, _rand, v
  · 响应（图片**内联 base64**，无独立图片请求）：
      滑块 v2：{"data":{"encryptToken","imageData":"data:image/jpeg;base64,…","ques":"data:image/png;base64,…"}}
      拼图   ：{"data":{"encryptToken","answerArea":"data:image/jpeg;base64,…","puzzle":"data:image/png;base64,…",
                        "puzzleY":83,"tag":"plant_green"}}

依赖边界（如实标注）：
  · `ua`(bx-ua) 可来自 ①`local_signature_provider` 离线快照纯算（推荐）或 ②页面 oracle（`local_signature_oracle`）
  · `_rand` 用 `captcha_flow.gen_rand("",0)`（= `genRandomValues` 等价物，44 字符）
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent
for p in (str(_SRC), str(_SRC / "pure")):
    if p not in sys.path:
        sys.path.insert(0, p)

import captcha_flow as CF          # noqa: E402

IMG_FIELDS = ("imageData", "ques", "answerArea", "puzzle")
UA_DEFAULT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
def parse_data_url(s: str):
    """`data:image/<fmt>;base64,<b64>` → `(fmt, bytes)`；非 data URL 返回 `(None, None)`。"""
    m = re.match(r"data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", s or "", re.S)
    if not m:
        return None, None
    return m.group(1), base64.b64decode(m.group(2))


def build_fetch(cfg: CF.PunishConfig, *, ua: str, rng=None) -> dict:
    """组装取题请求（URL + 需要的头）。"""
    rand = CF.gen_rand("", 0)
    v = "0" + "".join(str((rng or __import__("random")).randint(0, 9)) for _ in range(17))
    url = cfg.question_url(ua=ua, rand=rand, v=v)
    if url.startswith("//"):                     # 协议相对 → 绝对（urllib 需要 scheme）
        url = "https:" + url
    headers = {"user-agent": UA_DEFAULT,
               "referer": "//" + str(cfg.raw.get("HOST", "")).replace(":443", "")
                          + str(cfg.raw.get("PATH", "")) + "/_____tmd_____/punish?x5secdata=" + cfg.secdata}
    return {"url": url, "headers": headers, "rand": rand, "v": v,
            "kind": cfg.question_kind()}


def fetch(cfg: CF.PunishConfig, *, ua: str, save_dir=None, timeout: float = 20.0,
          rng=None, send: bool = True) -> dict:
    """取题：返回 `{url, ok, code, images:{name:path}, raw_head, tag, encryptToken}`。"""
    req = build_fetch(cfg, ua=ua, rng=rng)
    out = {"url": req["url"], "kind": req["kind"], "ok": False,
           "headers_used": sorted(req["headers"]), "images": {}}
    if not send:
        out["dry_run"] = True
        return out
    import urllib.request
    r = urllib.request.Request(req["url"], headers=req["headers"])
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    out["http_status"] = 200
    out["raw_head"] = body[:160]
    try:
        d = json.loads(body)
    except Exception:
        out["error"] = "响应非 JSON"
        return out
    out["code"] = d.get("code")
    data = d.get("data") or {}
    out["encryptToken"] = data.get("encryptToken")
    for k in ("puzzleY", "tag", "ques", "answerArea"):
        if k in data:
            out[k if k != "ques" else "ques_present"] = data[k] if not isinstance(data[k], str) else True
    # 落图
    if save_dir:
        sd = pathlib.Path(save_dir)
        sd.mkdir(parents=True, exist_ok=True)
        for name in IMG_FIELDS:
            fmt, blob = parse_data_url(data.get(name))
            if blob:
                f = sd / f"{req['kind']}_{name}.{fmt.replace('jpeg', 'jpg')}"
                f.write_bytes(blob)
                out["images"][name] = str(f)
    out["ok"] = bool(out["images"]) or any(isinstance(data.get(n), str) and data.get(n).startswith("data:image") for n in IMG_FIELDS)
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="验证码取题器（稳定拿图；默认 dry-run）")
    ap.add_argument("--html", help="惩罚页 HTML（解析 _config_）")
    ap.add_argument("--ua", help="bx-ua（或用 --ua-snapshot 离线纯算）")
    ap.add_argument("--ua-snapshot", help="寄存器快照 JSON（离线纯算 ua）")
    ap.add_argument("--ni-profile", choices=["slide_get", "slide_validate"])
    ap.add_argument("--save-dir", help="图片保存目录")
    ap.add_argument("--send", action="store_true", help="**真发取题请求**（默认 dry-run）")
    ap.add_argument("--out", help="结果 JSON 落盘")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.html:
        ap.print_help()
        return 2

    cfg = CF.PunishConfig.from_file(args.html)
    ua = args.ua
    if not ua and args.ua_snapshot:
        import local_signature_provider as OFF
        snap = OFF.load_snapshot(args.ua_snapshot)
        ua = OFF.resign_token(snap, args.ni_profile) if args.ni_profile else OFF.mint(snap)
    res = fetch(cfg, ua=ua or "", save_dir=args.save_dir, send=args.send)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.send:
        print("\n[dry-run] 未发请求；加 --send 才会真发。", file=sys.stderr)
    return 0


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

    print("== captcha_fetch 自检（全程 dry-run）==")
    # data URL 解析
    fmt, blob = parse_data_url("data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n").decode())
    check("parse_data_url 解出 png", fmt == "png" and blob == b"\x89PNG\r\n", f"{fmt} {blob!r}")
    check("非 data URL 返回 (None,None)", parse_data_url("http://example.invalid/y.png") == (None, None))

    # 惩罚页
    root = pathlib.Path(__file__).resolve().parents[2]
    cap = root.parent / "_artifacts/local_capture"
    html = None
    for h in sorted((cap / "bodies").glob("*.html")) if (cap / "bodies").is_dir() else []:
        t = h.read_text(encoding="utf-8", errors="replace")
        if "punishpage.min.js" in t and "window._config_" in t:
            html = h
            break
    check("找到惩罚页 HTML", html is not None)
    if html is None:
        print(f"结果: PASS={ok} FAIL={fail}")
        return 0
    cfg = CF.PunishConfig.from_file(html)

    # 🔑 关键：取题请求**不含**任何风控头
    req = build_fetch(cfg, ua="234!TEST", rng=__import__("random").Random(7))
    hk = set(req["headers"])
    check("取题头**不含** bx-et/bx-pp/x-pipu2",
          not any(h.startswith(("bx-et", "bx-pp", "x-pipu")) for h in hk), f"{sorted(hk)}")
    check("取题头含 user-agent / referer", {"user-agent", "referer"} <= hk)
    check("URL 指向 scene_get", "scene_get?" in req["url"], req["url"][-30:])
    for k in ("token=", "appKey=", "ua=", "x5secdata=", "language=cn", "pageVersion=", "_rand=", "v="):
        check(f"取题 URL 含 {k}", k in req["url"])

    # 与真实抓包对拍（取题部分）
    jl = cap / "index.jsonl"
    if jl.is_file():
        import urllib.parse as up
        rows = [json.loads(l) for l in jl.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        n_real = sum(1 for r in rows if "scene_get" in (r.get("url") or ""))
        check(f"抓包中有 {n_real} 个真实取题请求（对拍基准）", n_real >= 5, str(n_real))
        # 用真实取题的参数重建 URL，应逐字节一致
        import itertools
        good = 0
        for r in rows:
            u = r.get("url") or ""
            if "scene_get" not in u:
                continue
            q = dict(up.parse_qsl(up.urlparse(u).query))
            if "ua" not in q:
                continue
            built = ("_____tmd_____/scene_get?" +
                     CF.build_query([("token", q["token"]), ("appKey", q["appKey"]), ("ua", q["ua"]),
                                     ("x5secdata", q["x5secdata"]), ("language", q.get("language", "cn")),
                                     ("pageVersion", q.get("pageVersion", "0.1.129")),
                                     ("_rand", q["_rand"]), ("v", q["v"])]))
            real = "_____tmd_____/scene_get?" + up.urlparse(u).query
            if built == real:
                good += 1
        check(f"取题 URL 重建逐字节一致（{good} 条）", good >= 5, str(good))
    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
