# Runtime Study Kit

隔离 JavaScript 运行环境研究代码：消息通道兼容补丁、离线传输、环境恢复、签名调用框架、顺序请求控制、脱敏进度日志和本地窗口。

这是公开脱敏版本。目标名称、域名、协议字段和应用标识已经替换为中性占位符，不能直接连接原始业务。原有本地项目仍保留完整实现。

## 文件

- `src/env/compat.js`：消息通道兼容补丁。
- `src/env/offline.js`：隔离传输与本地 fixture 回放。
- `src/env/dom_snapshot.js`、`trace.js`：环境恢复与调用观察。
- `src/client.py`：运行环境与 HTTP 客户端框架。
- `src/fetch_feed.py`、`src/ui.py`：命令行入口与本地窗口。
- `src/progress_log.py`：结构化脱敏日志。

## 本地环境

Python 3.10+，使用虚拟环境安装 `requirements.txt`。Windows 窗口使用 Python 自带 Tk。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src/client.py --help
```

## 私有依赖

公开版本不附带第三方站点脚本、真实会话、缓存、抓包、历史返回或训练/取证资产。需要在自己的授权环境提供私有脚本与会话，并恢复相应协议配置。

`assets/vendor/manifest.private.json` 是本地脚本 SHA-256 清单，包含 `hash-helper.js` 和 `runtime.js` 两个键。真实文件与清单均在忽略目录中。会话入口使用 `assets/local-session/`；离线探针、DOM 与 fixture 也依赖本地资产。

`--help` 与通用补丁可以独立检查；完整签名、真实 HTTP 请求以及依赖私有资产的窗口操作没有作为公开版本验收成功。离线回放命中也不代表业务服务接受。

## 发布边界

仅发布必要源代码与依赖说明，不附带历史 Git 记录。公开仓库的任何内容都可能被搜索、复制和自动分析；中性命名只能降低关键词匹配概率。
