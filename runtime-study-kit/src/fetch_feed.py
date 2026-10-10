"""Convenience entry for the verified, captured-session iv8 feed client."""
import argparse,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];B=R/'assets/local-session'
def main():
    p=argparse.ArgumentParser(description='Original SDK in iv8 → live HTTP feed. Requires the saved authorized browser session.');p.add_argument('--page',type=int,default=1);p.add_argument('--pages',type=int,default=1);p.add_argument('--max-pages',type=int,default=3,help='单次页数上限，最大 40');p.add_argument('--delay',type=float,default=1.5,help='页间等待秒数，最小 1.0');display=p.add_mutually_exclusive_group();display.add_argument('--quiet',action='store_true',help='Only print result JSON, keep progress.jsonl');display.add_argument('--verbose',action='store_true',help='Show detailed redacted stages');p.add_argument('--json',dest='json_output',action='store_true',help='Also print result JSON to stdout');p.add_argument('--dry-run',action='store_true',help='Show initialization/signing without sending HTTP');a=p.parse_args()
    for f in ['client-state.private.json','session.private.har']:
        if not(B/f).is_file():raise SystemExit('Missing captured session asset: '+f)
    args=[sys.executable,'-X','utf8','-B',str(R/'src/client.py'),'--page',str(a.page),'--pages',str(a.pages),'--max-pages',str(a.max_pages),'--delay',str(a.delay),'--concurrency','1','--transport','curl','--captured-headers','--captured-cookie','--state',str(B/'client-state.private.json'),'--har',str(B/'session.private.har')]
    if a.quiet:args.append('--quiet')
    if a.verbose:args.append('--verbose')
    if a.json_output:args.append('--json')
    if a.dry_run:args.append('--dry-run')
    raise SystemExit(subprocess.call(args))
if __name__=='__main__':main()
