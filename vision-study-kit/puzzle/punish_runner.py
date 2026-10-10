# -*- coding: utf-8 -*-
"""tmd 惩罚链路**端到端编排器**（默认 dry-run：只组装/打印，不发请求）。

链路（`docs/api.md` §F/§K/§L/§N/§Q）：
```
① 登录被拦 → 惩罚页 URL           captcha_flow.parse_punish_url
② GET 惩罚页 → window._config_     captcha_flow.PunishConfig.from_html
③ 取题 GET .../scene_get     cfg.question_url(ua, _rand, v)
④ 取题响应 → encryptToken + 图片   captcha_flow.parse_question_resp
⑤ 提交 GET .../scene_validate    cfg.validate_url(ua, umidToken, encryptToken,
                                                      per, width, time, ppt, _rand, v)
   请求头 bx-et / bx-pp            local_signature_oracle.sign_et / .pp_sign（须在惩罚页上下文）
⑥ 响应 + bx-x5sec                  → 业务接口放行 才是最终判据
```
**安全默认**：不加 `--send` 一律 dry-run（只打印将要发出的请求），不会向目标发起业务请求。

签名来源二选一：
  · `--ua-snapshot FILE`：用 `local_signature_provider` 从寄存器快照**离线纯算** `ua`（推荐）
  · 否则用 `local_signature_oracle` 向活页面取（含 `bx-et` / `umidToken`）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

_HERE = pathlib.Path(__file__).resolve().parent          # src/captcha
_SRC = _HERE.parent
for p in (str(_SRC), str(_SRC / "pure")):
    if p not in sys.path:
        sys.path.insert(0, p)

import captcha_flow as CF          # noqa: E402
try:
    import local_signature_oracle as TO      # noqa: E402
except Exception:
    TO = None
try:
    import local_signature_provider as OFF    # noqa: E402
except Exception:
    OFF = None


# ---------------------------------------------------------------------------
def gen_v(rng: random.Random | None = None) -> str:
    """`v` 参数：实测形态 = `0` + 16~17 位随机十进制（样本如 `06145506271885938`）。"""
    r = rng or random
    return "0" + "".join(str(r.randint(0, 9)) for _ in range(17))


def build_fetch_control(cfg: CF.PunishConfig, *, ua: str, seed: str = "", rng=None) -> dict:
    """组装 ③ 取题的动态参数。"""
    return {"ua": ua, "rand": CF.gen_rand(seed, 0), "v": gen_v(rng)}


def build_validate_control(cfg: CF.PunishConfig, *, ua: str, umid_token: str,
                           encrypt_token: str, per, width=272, elapsed_ms=0,
                           ppt=0, seed: str = "", rng=None) -> dict:
    """组装 ⑤ 提交的动态参数。"""
    return {"ua": ua, "umid_token": umid_token, "encrypt_token": encrypt_token,
            "per": per, "width": width, "elapsed_ms": elapsed_ms, "ppt": ppt,
            "rand": CF.gen_rand(seed, 0), "v": gen_v(rng)}


# ---------------------------------------------------------------------------
def _http_get(url: str, headers: dict | None = None, timeout: float = 20.0):
    """极简 GET（仅在 `--send` 下调用）。"""
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")


def run(*, html_path=None, config=None, per=None, width=272, elapsed_ms=0, ppt=0,
        ua=None, ua_snapshot=None, umid_token=None, encrypt_token=None,
        ni_profile=None, send=False, port=9223, timeout=20.0, out=None,
        rng=None) -> dict:
    """执行编排；`send=False` 时只组装并返回，不发请求。"""
    cfg = config or CF.PunishConfig.from_file(html_path)
    rep = {"kind": cfg.question_kind(), "action": cfg.action, "dry_run": not send, "steps": {}}

    # ---- ③ 取题 ----
    fc = build_fetch_control(cfg, ua=ua or "", rng=rng)
    q_url = cfg.question_url(ua=fc["ua"], rand=fc["rand"], v=fc["v"])
    rep["steps"]["question"] = {"url": q_url, "len": len(q_url)}
    question_resp = None
    if send:
        st, hd, body = _http_get(q_url, timeout=timeout)
        rep["steps"]["question"].update(status=st, body_head=body[:200])
        question_resp = body
    if question_resp:
        try:
            rep["steps"]["question"]["parsed"] = CF.parse_question_resp(question_resp)
            encrypt_token = encrypt_token or rep["steps"]["question"]["parsed"].get("encryptToken")
        except Exception as e:
            rep["steps"]["question"]["parse_err"] = str(e)

    # ---- ⑤ 提交 ----
    enc = encrypt_token or ""
    vc = build_validate_control(cfg, ua=ua or "", umid_token=umid_token or "",
                                encrypt_token=enc, per=per, width=width,
                                elapsed_ms=elapsed_ms, ppt=ppt, rng=rng)
    v_url = cfg.validate_url(**vc)
    rep["steps"]["validate"] = {"url": v_url, "len": len(v_url),
                                "params": {k: (len(v) if isinstance(v, str) else v) for k, v in vc.items()}}
    if send:
        headers = {}
        if TO is not None:
            try:
                et = TO.sign_et(v_url, port=port)
                if isinstance(et, str):
                    headers["bx-et"] = et
                pp = TO.pp_sign(port=port)
                if isinstance(pp, str):
                    headers["bx-pp"] = pp
            except Exception as e:
                rep["steps"]["validate"]["sign_err"] = str(e)
        st, hd, body = _http_get(v_url, headers=headers, timeout=timeout)
        rep["steps"]["validate"].update(status=st, headers_used=sorted(headers),
                                        body_head=body[:300],
                                        resp_headers={k: v for k, v in hd.items()
                                                      if k.lower().startswith(("bx-", "ufe-"))})
    if out:
        pathlib.Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="tmd 惩罚链路编排器（默认 dry-run，不发请求）")
    ap.add_argument("--html", help="惩罚页 HTML 文件")
    ap.add_argument("--per", help="答案比率（如 0.385）")
    ap.add_argument("--width", type=int, default=272)
    ap.add_argument("--time", type=int, default=0, help="操作耗时 ms")
    ap.add_argument("--ppt", type=int, default=0, help="PoW 耗时 ms")
    ap.add_argument("--ua", help="直接给 bx-ua")
    ap.add_argument("--ua-snapshot", help="寄存器快照 JSON（用离线纯算出 ua）")
    ap.add_argument("--ni-profile", choices=["slide_get", "slide_validate"], help="换调用点指纹后重签 ua")
    ap.add_argument("--umid-token", help="bx-umidtoken（缺则由 oracle 取）")
    ap.add_argument("--encrypt-token", help="取题响应的 encryptToken（dry-run 时可手填）")
    ap.add_argument("--send", action="store_true", help="**真发请求**（默认不开）")
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--out", help="把结果 JSON 写入文件")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.html:
        ap.print_help()
        return 2

    ua = args.ua
    if not ua and args.ua_snapshot and OFF is not None:
        snap = OFF.load_snapshot(args.ua_snapshot)
        ua = OFF.resign_token(snap, args.ni_profile) if args.ni_profile else OFF.mint(snap)
    umid = args.umid_token
    if not umid and TO is not None and args.send:
        try:
            umid = TO.umid_token(args.port)
        except Exception:
            umid = ""

    rep = run(html_path=args.html, per=args.per, width=args.width, elapsed_ms=args.time,
              ppt=args.ppt, ua=ua, umid_token=umid, encrypt_token=args.encrypt_token,
              send=args.send, port=args.port, out=args.out)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if not args.send:
        print("\n[dry-run] 未发送任何请求；加 --send 才会真发。", file=sys.stderr)
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

    print("== punish_runner 自检（全程 dry-run）==")
    root = pathlib.Path(__file__).resolve().parents[2]
    cap = root.parent / "_artifacts/local_capture"
    html = None
    for h in sorted((cap / "bodies").glob("*.html")) if (cap / "bodies").is_dir() else []:
        t = h.read_text(encoding="utf-8", errors="replace")
        if "punishpage.min.js" in t and "window._config_" in t:
            html = h
            break
    check("找到惩罚页 HTML", html is not None, str(html.name if html else None))
    if html is None:
        print(f"结果: PASS={ok} FAIL={fail}")
        return 0

    cfg = CF.PunishConfig.from_file(html)
    # dry-run 组装
    rep = run(html_path=html, per="0.385", elapsed_ms=1878, ua="234!TESTUA", umid_token="U" * 68,
              encrypt_token="E" * 100, rng=random.Random(1234))
    check("dry-run 不发送（dry_run=True）", rep["dry_run"] is True)
    q = rep["steps"]["question"]
    v = rep["steps"]["validate"]
    check("取题 URL 以 scene_get 结尾段", "scene_get?" in q["url"], q["url"][-40:])
    check("提交 URL 以 scene_validate 结尾段", "scene_validate?" in v["url"], v["url"][-40:])
    check("取题 URL 参数序正确",
          q["url"].split("?")[1].split("&")[0].startswith("token=") and "pageVersion=" in q["url"]
          and q["url"].rstrip().endswith("&v=" + q["url"].split("&v=")[1]) if "&v=" in q["url"] else False)
    for key in ("token=", "appKey=", "ua=", "umidToken=", "encryptToken=", "x5secdata=",
                "time=", "pageVersion=", "fireyeVersion=", "width=", "per=", "ppt=", "_rand=", "v="):
        check(f"提交 URL 含 {key}", key in v["url"])
    check("per/width/time/ppt 已写入",
          "per=0.385" in v["url"] and "width=272" in v["url"] and "time=1878" in v["url"] and "ppt=0" in v["url"])
    check("_rand 为 44 字符（gen_rand('',0)）", len(CF.gen_rand("", 0)) == 44)

    # v 形态
    vs = [gen_v(random.Random(i)) for i in range(200)]
    check("v 均为 18 字符且以 0 开头", all(len(x) == 18 and x[0] == "0" for x in vs), f"{vs[0]}")
    check("v 尾部为纯数字", all(x[1:].isdigit() for x in vs))

    # 与真实抓包对拍（复用 captcha_flow 的校验器）
    jl = cap / "index.jsonl"
    if jl.is_file():
        n = CF._verify_against(jl, quiet=True)
        check(f"captcha_flow 真实 URL 重建仍一致（{n} 条）", n >= 10, f"{n}")
    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
