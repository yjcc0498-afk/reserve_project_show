"""Structured, credential-free progress shared by parent and iv8 child."""
from __future__ import annotations
import datetime, hashlib, json, os, sys, time
from pathlib import Path

PREFIX = '@RUNTIME_LOG '
CST = datetime.timezone(datetime.timedelta(hours=8))

def short_path(value):
    path=Path(value)
    try:return path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
    except ValueError:return str(path)

def byte_size(value):
    if value>=1024*1024:return f'{value/(1024*1024):.1f} MB'
    if value>=1024:return f'{value/1024:.1f} KB'
    return f'{value} B'

def terminal_colors():
    if os.environ.get('NO_COLOR') is not None or not sys.stderr.isatty():return False
    if os.name=='nt':
        try:
            import ctypes
            handle=ctypes.windll.kernel32.GetStdHandle(-12)
            mode=ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetConsoleMode(handle,ctypes.byref(mode)):return False
            if not ctypes.windll.kernel32.SetConsoleMode(handle,mode.value|4):return False
        except (AttributeError,OSError):return False
    return True

def value_info(value):
    text = str(value or '')
    return {'length': len(text), 'sha256_prefix': hashlib.sha256(text.encode()).hexdigest()[:12]}

def signature_info(request_signature):
    parts = request_signature.split(';')
    labels = ['格式化时间', '签名指纹', 'SDK appId', '算法 token', '主摘要',
              '签名版本', 'SDK 时间', '环境包', '第二摘要', '签名字段清单']
    result = []
    for index, part in enumerate(parts):
        item = {'segment': index + 1, 'name': labels[index] if index < len(labels) else '未解释分段',
                **value_info(part)}
        if index in (0, 2, 5, 6):
            item['value'] = part
        result.append(item)
    return result

class ProgressLog:
    def __init__(self, path: Path | None = None, quiet=False, child=False, verbose=False):
        self.path, self.quiet, self.child, self.verbose = path, quiet, child, verbose
        self.started = time.monotonic()
        self.color=not quiet and not child and terminal_colors()
        self.page=0
        self.requested_pages=0
        self.product_count=0
        self.last_result=None
        self.failed=False
        self.products=[]

    def event(self, stage, message, **details):
        item = {'time': datetime.datetime.now(CST).isoformat(timespec='milliseconds'),
                'stage': stage, 'message': message, 'details': details}
        if self.child:
            print(PREFIX + json.dumps(item, ensure_ascii=False), file=sys.stderr, flush=True)
        else:
            self.receive(item)

    def receive(self, item):
        item = dict(item, elapsed_seconds=round(time.monotonic() - self.started, 3))
        if self.path:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
        if item['stage']=='PRODUCT_RESULT' and item.get('details',{}).get('accepted'):
            file=item['details'].get('products_file')
            if file:
                try:
                    values=json.loads(Path(file).read_text(encoding='utf-8'))
                    self.products.extend(values)
                except (OSError,ValueError,TypeError):pass
        if not self.quiet:
            if self.verbose:
                print(f"[{item['time'][11:23]} +{item['elapsed_seconds']:.3f}s] "
                      f"[{item['stage']}] {item['message']}", file=sys.stderr, flush=True)
                for key, value in item.get('details', {}).items():
                    print(f"  {key}: {json.dumps(value, ensure_ascii=False)}", file=sys.stderr, flush=True)
            else:self.compact(item)
            if item['stage']=='FINISH' and self.products:
                from product_display import print_products
                print_products(self.products,sys.stderr)

    def line(self, item, label, message, tone='normal'):
        text=f"  {item['time'][11:19]}  {label}  {message}"
        if self.color:
            colors={'normal':'\033[36m','success':'\033[32m','warning':'\033[33m','muted':'\033[90m'}
            text=f"\033[90m  {item['time'][11:19]}\033[0m  {colors[tone]}{label}\033[0m  {message}"
        print(text,file=sys.stderr,flush=True)

    def compact(self,item):
        stage=item['stage'];d=item.get('details',{})
        if stage=='START':
            first=d.get('page',1);count=d.get('pages',1);self.requested_pages=count
            page_range=f'第 {first} 页'if count==1 else f'第 {first}–{first+count-1} 页'
            mode='本地预演'if d.get('dry_run')else'商品请求'
            print(f'\n  示例站点 · {mode} / {page_range}\n  '+ '─'*46,file=sys.stderr,flush=True)
            if 'delay_seconds' in d:
                self.line(item,'节奏',f"串行 1 个请求 · 间隔 {d['delay_seconds']:g} 秒 · 本次上限 {d.get('max_pages',3)} 页")
        elif stage=='PAGE_START':
            self.page=d['page']
            if self.requested_pages>1:print(f"\n  第 {self.page} 页",file=sys.stderr,flush=True)
        elif stage=='ENV_LOAD':
            self.line(item,'环境',f"iv8 {d.get('iv8','?')} · {d.get('platform','?')} · {d.get('hardware_concurrency','?')} 核")
        elif stage=='STATE_IMPORT':
            self.line(item,'会话',f"Cookie {len(d.get('cookie_names',[]))} 项 · 存储 {len(d.get('local_storage_keys',[]))} 项")
        elif stage=='CACHE_READ':
            entries=d.get('entries')
            if entries is None:self.line(item,'缓存','元信息未解析，使用 SDK 实际分支','warning')
            elif not entries:self.line(item,'缓存','无匹配算法缓存，等待 SDK 分支确认','warning')
            elif any(e.get('expired_by_host_clock')for e in entries):self.line(item,'缓存','有过期条目，等待 SDK 分支确认','warning')
            else:self.line(item,'缓存','算法缓存有效 · appId local-app')
        elif stage=='SIGN_RESULT':
            branch='正常算法'if d.get('normal_algorithm')else'降级算法'
            self.line(item,'签名',f"request_signature {d.get('version','?')} · {d.get('request_signature',{}).get('length',0)} 字符 · {branch} · {d.get('duration_ms',0):.0f} ms",'normal'if d.get('normal_algorithm')else'warning')
        elif stage=='HTTP_SEND':
            self.line(item,'请求',f"GET api.m.example.invalid · feed_page · 第 {d.get('page',self.page)} 页")
        elif stage=='HTTP_RESPONSE':
            self.line(item,'响应',f"HTTP {d.get('http_status','?')} · {d.get('duration_ms',0):.0f} ms · {byte_size(d.get('bytes',0))}")
        elif stage=='PRODUCT_RESULT':
            if d.get('accepted'):
                count=d.get('product_count',0);self.product_count+=count
                self.line(item,'成功',f"取得 {count} 个商品 · {d.get('price_count',0)} 条价格",'success')
                self.last_result=d.get('products_file')
            else:
                self.failed=True;self.last_result=d.get('summary_file')
                code=str(d.get('outer_code')or'')
                if code in ('605','606'):message=f'风控处置 {code} · 未取得商品 · 已停止'
                else:message=f'未取得商品 · 业务码 {code or "未提供"} · 已停止'
                self.line(item,'未通过',message,'warning')
        elif stage=='DRY_RUN':
            self.line(item,'预演','签名与请求准备完成 · 未发送 HTTP','success')
        elif stage=='PAGE_WAIT':
            self.line(item,'等待',f"{d.get('wait_seconds',0):g} 秒后继续下一页",'muted')
        elif stage=='ERROR':
            self.failed=True;self.line(item,'错误',f"{d.get('error_type','执行异常')} · 已停止，详情见 error.json",'warning')
        elif stage=='FINISH':
            print('  '+'─'*46,file=sys.stderr,flush=True)
            elapsed=item.get('elapsed_seconds',0)
            if d.get('dry_run'):message=f'本地预演完成 · 总耗时 {elapsed:.2f}s'
            elif self.failed:message=f"成功 {d.get('accepted_pages',0)}/{self.requested_pages} 页 · 总耗时 {elapsed:.2f}s"
            else:message=f"共 {self.product_count} 个商品 · 总耗时 {elapsed:.2f}s"
            print('  '+message,file=sys.stderr,flush=True)
            if self.last_result:print('  '+('诊断'if self.failed else'结果')+'  '+short_path(self.last_result),file=sys.stderr,flush=True)
            if self.path:print('  日志  '+short_path(self.path),file=sys.stderr,flush=True)
            print(file=sys.stderr,flush=True)
