"""Local Tk window around the existing fetch_feed client; no new request logic."""
from __future__ import annotations
import argparse, datetime, json, math, os, queue, subprocess, sys, threading, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from product_display import plain, price_text

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT
BG='#f4f5f7';INK='#1c2434';MUTED='#647084';RED='#cf2738'

def enable_dpi():
    if os.name=='nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError,OSError):pass

def product_file(result):
    path=Path(result['productsFile']) if result.get('productsFile') else ROOT/result['evidence']/f"page-{result['page']}-products.json"
    resolved=path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()):raise ValueError('商品文件不在示例站点项目目录内')
    return resolved

class FeedWindow:
    def __init__(self,root):
        self.root=root;self.events=queue.Queue();self.process=None;self.running=False;self.cancel_requested=False
        self.products=[];self.run_dir=None;self._closing=False
        root.title('示例站点商品 · 本地工具');root.geometry('1100x780');root.minsize(900,650)
        root.configure(bg=BG)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background=BG,foreground=INK)
        style.configure('TButton',padding=(12,7),borderwidth=0)
        style.configure('Primary.TButton',background=RED,foreground='white')
        style.map('Primary.TButton',background=[('active','#b61c2d'),('disabled','#d8b7bc')])
        style.configure('Treeview',background='white',fieldbackground='white',rowheight=34,borderwidth=0)
        style.configure('Treeview.Heading',background='#e9edf2',font=('Microsoft YaHei UI',10,'bold'),padding=8)
        style.map('Treeview',background=[('selected','#fce8eb')],foreground=[('selected',INK)])
        header=tk.Frame(root,bg=BG);header.pack(fill='x',padx=26,pady=(22,12))
        tk.Label(header,text='示例站点商品',bg=BG,fg=INK,font=('Microsoft YaHei UI',24,'bold')).pack(anchor='w')
        tk.Label(header,text='选择页码，查看请求进度与返回商品。',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',10)).pack(anchor='w',pady=(4,0))
        controls=tk.Frame(root,bg='white',padx=16,pady=12);controls.pack(fill='x',padx=26)
        self.page=tk.StringVar(value='1');self.pages=tk.StringVar(value='1');self.delay=tk.StringVar(value='1.5');self.preview=tk.BooleanVar(value=False)
        tk.Label(controls,text='起始页',bg='white',fg=INK).pack(side='left')
        self.page_entry=ttk.Spinbox(controls,from_=1,to=40,textvariable=self.page,width=4);self.page_entry.pack(side='left',padx=(8,18))
        tk.Label(controls,text='页数',bg='white',fg=INK).pack(side='left')
        self.pages_entry=ttk.Spinbox(controls,from_=1,to=40,textvariable=self.pages,width=4);self.pages_entry.pack(side='left',padx=(8,18))
        tk.Label(controls,text='间隔秒',bg='white',fg=INK).pack(side='left')
        self.delay_entry=ttk.Spinbox(controls,from_=1.0,to=60.0,increment=0.5,textvariable=self.delay,width=5);self.delay_entry.pack(side='left',padx=(8,18))
        self.preview_check=ttk.Checkbutton(controls,text='本地预演',variable=self.preview);self.preview_check.pack(side='left')
        self.start_button=ttk.Button(controls,text='开始请求',style='Primary.TButton',command=self.start);self.start_button.pack(side='right')
        self.stop_button=ttk.Button(controls,text='停止',command=self.stop,state='disabled');self.stop_button.pack(side='right',padx=8)
        self.status=tk.StringVar(value='就绪');self.count=tk.StringVar(value='0 个商品')
        statusbar=tk.Frame(root,bg=BG);statusbar.pack(fill='x',padx=26,pady=(14,10))
        tk.Label(statusbar,textvariable=self.status,bg=BG,fg=MUTED).pack(side='left')
        tk.Label(statusbar,textvariable=self.count,bg=BG,fg=INK,font=('Microsoft YaHei UI',12,'bold')).pack(side='right')
        self.progress=ttk.Progressbar(root,mode='indeterminate');self.progress.pack(fill='x',padx=26,pady=(0,10))
        pane=ttk.Panedwindow(root,orient='vertical');pane.pack(fill='both',expand=True,padx=26)
        product_panel=ttk.Frame(pane);pane.add(product_panel,weight=3)
        self.tree=ttk.Treeview(product_panel,columns=('page','title','price','sku'),show='headings',selectmode='browse',height=6)
        for key,label,width in [('page','页码',55),('title','商品名称',620),('price','价格',105),('sku','SKU',170)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=45,anchor='w',stretch=key=='title')
        scroll=ttk.Scrollbar(product_panel,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.tag_configure('even',background='#fafbfc');self.tree.bind('<Double-1>',self.open_product)
        self.tree.bind('<<TreeviewSelect>>',self.select_product)
        log_panel=ttk.Frame(pane);pane.add(log_panel,weight=1)
        tk.Label(log_panel,text='运行日志',bg=BG,fg=INK,font=('Microsoft YaHei UI',10,'bold')).pack(anchor='w',pady=(8,5))
        self.log=tk.Text(log_panel,height=5,wrap='word',font=('Consolas',10),bg='#202632',fg='#dde4ef',relief='flat',padx=12,pady=10,state='disabled')
        self.log.pack(fill='both',expand=True)
        self.detail=tk.StringVar(value='双击商品可打开详情页。')
        tk.Label(root,textvariable=self.detail,bg=BG,fg=MUTED,wraplength=1020,anchor='w',justify='left').pack(fill='x',padx=26,pady=(10,4))
        footer=tk.Frame(root,bg=BG);footer.pack(fill='x',padx=26,pady=(4,18))
        ttk.Button(footer,text='打开结果目录',command=self.open_folder).pack(side='left')
        ttk.Button(footer,text='导出商品 JSON',command=self.export).pack(side='left',padx=8)
        ttk.Button(footer,text='查看已有结果',command=self.load_history).pack(side='left')
        tk.Label(footer,text='使用已保存会话 · 风控或会话失效会停止',bg=BG,fg=MUTED).pack(side='right')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(75,self.poll)

    def append(self,text):
        self.log.configure(state='normal');self.log.insert('end',text);self.log.see('end');self.log.configure(state='disabled')

    def start(self):
        if self.running:return
        try:
            page=int(self.page.get());pages=int(self.pages.get())
            delay=float(self.delay.get())
            if not(1<=page<=40 and 1<=pages<=40 and page+pages-1<=40 and math.isfinite(delay) and 1<=delay<=60):raise ValueError
        except ValueError:messagebox.showerror('参数无效','页码 1–40，单次页数 1–40，页间隔 1–60 秒。',parent=self.root);return
        self.products=[];self.tree.delete(*self.tree.get_children());self.count.set('0 个商品');self.run_dir=None
        self.log.configure(state='normal');self.log.delete('1.0','end');self.log.configure(state='disabled')
        self.running=True;self.cancel_requested=False;self.status.set('正在初始化…');self.start_button.configure(state='disabled');self.stop_button.configure(state='normal')
        for control in (self.page_entry,self.pages_entry,self.delay_entry,self.preview_check):control.configure(state='disabled')
        self.progress.start(12)
        args=[sys.executable,'-X','utf8','-B',str(ROOT/'src/fetch_feed.py'),'--page',str(page),'--pages',str(pages),'--max-pages','40','--delay',str(delay),'--json']
        if self.preview.get():args.append('--dry-run')
        threading.Thread(target=self.worker,args=(args,),daemon=True).start()

    def worker(self,args):
        try:
            proc=subprocess.Popen(args,cwd=WORKSPACE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.process=proc
            if self.cancel_requested or self._closing:
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def collect(stream,kind):
                product_preview=False
                for line in stream:
                    if kind=='log' and line.strip()=='本次商品':product_preview=True
                    if not product_preview:self.events.put((kind,line))
                stream.close()
            threads=[threading.Thread(target=collect,args=(proc.stdout,'result'),daemon=True),threading.Thread(target=collect,args=(proc.stderr,'log'),daemon=True)]
            for thread in threads:thread.start()
            code=proc.wait()
            for thread in threads:thread.join()
            self.events.put(('done',code))
        except Exception as exc:self.events.put(('worker_error',type(exc).__name__))

    def handle_result(self,line):
        try:result=json.loads(line)
        except ValueError:return
        if result.get('evidence'):
            candidate=(ROOT/result['evidence']).resolve()
            if candidate.is_relative_to(ROOT.resolve()):self.run_dir=candidate
        if result.get('dryRun'):self.status.set('本地预演完成，未发送请求');return
        if result.get('accepted'):
            try:values=json.loads(product_file(result).read_text(encoding='utf-8'))
            except (OSError,ValueError,KeyError) as exc:self.append('读取商品结果失败：'+type(exc).__name__+'\n');return
            self.add_products(values,result.get('page'))
            self.status.set(f"第 {result['page']} 页取得 {len(values)} 个商品")
        elif result.get('outerCode') in ('605','606',605,606):self.status.set(f"风控处置 {result['outerCode']}，未取得商品")
        elif result.get('errorType'):self.status.set('请求异常：'+result['errorType'])
        else:self.status.set('未取得商品，请查看日志')

    def add_products(self,values,page=None):
        for value in values:
            item=dict(value)
            if page is not None:item['page']=page
            index=len(self.products);self.products.append(item)
            title=item.get('title')or item.get('t')or item.get('name')or'未提供名称'
            self.tree.insert('', 'end',iid=str(index),values=(item.get('page','—'),plain(title),price_text(item.get('price',item.get('jp'))),plain(item.get('sku'))),tags=('even',)if index%2==0 else())
        self.count.set(f'{len(self.products)} 个商品')

    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='log':self.append(value)
                elif kind=='result':self.handle_result(value)
                elif kind=='done':
                    self.finish()
                    if value==0 and not self.preview.get():self.status.set(f'完成 · 共 {len(self.products)} 个商品')
                    elif value!=0 and self.status.get().startswith('正在'):self.status.set('请求已停止，请查看日志')
                elif kind=='worker_error':self.append('启动失败：'+value+'\n');self.status.set('无法启动请求进程');self.finish()
        except queue.Empty:pass
        if not self._closing:self.root.after(75,self.poll)

    def finish(self):
        self.running=False;self.process=None;self.progress.stop();self.start_button.configure(state='normal');self.stop_button.configure(state='disabled')
        for control in (self.page_entry,self.pages_entry,self.delay_entry,self.preview_check):control.configure(state='normal')

    def stop(self):
        self.cancel_requested=True
        proc=self.process
        if proc is None:
            if self.running:self.status.set('正在停止…')
            return
        if proc.poll()is None:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.status.set('已停止请求');self.append('\n用户已停止本次请求。\n')

    def selected(self):
        selection=self.tree.selection()
        return self.products[int(selection[0])] if selection else None

    def select_product(self,_event=None):
        item=self.selected()
        if item:self.detail.set(plain(item.get('title')or item.get('t')or item.get('name')or'未提供名称'))

    def open_product(self,_event=None):
        item=self.selected()
        if item and str(item.get('sku','')).isdigit():webbrowser.open('https://item.example.invalid/'+str(item['sku'])+'.html')

    def open_folder(self):os.startfile(str(self.run_dir or ROOT/'assets/live'))

    def export(self):
        if not self.products:messagebox.showinfo('暂无商品','请求成功后可导出商品列表。',parent=self.root);return
        name=filedialog.asksaveasfilename(parent=self.root,title='导出商品',defaultextension='.json',initialfile='示例站点商品.json',filetypes=[('JSON 文件','*.json')])
        if name:
            try:Path(name).write_text(json.dumps(self.products,ensure_ascii=False,indent=2),encoding='utf-8');self.status.set(f'已导出 {len(self.products)} 个商品')
            except OSError as exc:messagebox.showerror('导出失败',str(exc),parent=self.root)

    def load_history(self):
        if self.running:return
        name=filedialog.askopenfilename(parent=self.root,title='查看已有商品结果',initialdir=ROOT/'assets/live',filetypes=[('商品 JSON','*.json')])
        if name:
            try:
                data=json.loads(Path(name).read_text(encoding='utf-8'))
                if not isinstance(data,list) or any(not isinstance(v,dict)or'sku'not in v for v in data):raise ValueError('请选择商品列表 JSON')
                self.products=[];self.tree.delete(*self.tree.get_children());self.add_products(data);self.run_dir=Path(name).parent;self.status.set('查看已有结果 · '+Path(name).name)
            except (ValueError,OSError)as exc:messagebox.showerror('无法读取',str(exc),parent=self.root)

    def close(self):
        self._closing=True;self.stop();self.root.destroy()

def main():
    p=argparse.ArgumentParser();p.add_argument('--preview',action='store_true');p.add_argument('--self-check',action='store_true');args=p.parse_args()
    enable_dpi();root=tk.Tk();app=FeedWindow(root)
    if args.preview or args.self_check:
        values=json.loads((ROOT/'assets/live/products.json').read_text(encoding='utf-8'));app.add_products(values);app.status.set('已有验收结果预览 · 不是新请求');app.append('已有结果：第 1–3 页共 75 个商品。\n点击“开始请求”后会展示本次结果。\n')
    if args.self_check:
        def check():
            assert len(app.tree.get_children())==len(app.products)==75
            app.preview.set(True);app.start()
            started=datetime.datetime.now()
            def await_done():
                if app.running:
                    if (datetime.datetime.now()-started).total_seconds()>25:raise RuntimeError('UI dry run timed out')
                    root.after(150,await_done);return
                assert '本地预演完成' in app.status.get()
                assert len(app.products)==0 and app.run_dir and (app.run_dir/'progress.jsonl').exists()
                app.add_products(values);assert len(app.tree.get_children())==75
                print('UI controls, async dry run, status and product table verified',flush=True);app.close()
            root.after(150,await_done)
        root.after(200,check)
    root.mainloop()
if __name__=='__main__':main()
