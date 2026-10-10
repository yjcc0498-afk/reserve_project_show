"""Hard wall-clock limit around an iv8 child; only project venv is used."""
import subprocess,sys
from pathlib import Path
p=Path(__file__).with_name('iv8_probe.py')
try:
    r=subprocess.run([sys.executable,'-B',str(p),*sys.argv[1:]],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=35)
    print(r.stdout);print(r.stderr[-3500:],file=sys.stderr);raise SystemExit(r.returncode)
except subprocess.TimeoutExpired as e:
    stdout=e.stdout.decode('utf-8',errors='replace') if isinstance(e.stdout,bytes) else e.stdout or ''
    stderr=e.stderr.decode('utf-8',errors='replace') if isinstance(e.stderr,bytes) else e.stderr or ''
    report=p.parents[2]/'assets/iv8/last-timeout.txt'
    report.parent.mkdir(parents=True,exist_ok=True);report.write_text(stdout+'\n'+stderr,encoding='utf-8')
    print(stdout[-1800:]);print('IV8_HARD_TIMEOUT_35_SECONDS');raise SystemExit(124)
