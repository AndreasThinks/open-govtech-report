"""Independent, read-only no-agent monitor with change-only alerts."""
import argparse
import json
import subprocess
from pathlib import Path

REPO='AndreasThinks/open-govtech-report'


def change_report(state_path, issues):
    state_path=Path(state_path)
    issues=sorted(set(issues))
    try:
        previous=json.loads(state_path.read_text()).get('issues',[])
    except (FileNotFoundError,ValueError):
        previous=[]
    state_path.parent.mkdir(parents=True,exist_ok=True)
    temp=state_path.with_suffix('.tmp')
    temp.write_text(json.dumps({'issues':issues},sort_keys=True)+'\n')
    temp.replace(state_path)
    if issues==previous:
        return ''
    if issues:
        return 'Open GovTech Report needs attention:\n'+'\n'.join('- '+issue for issue in issues)
    if previous:
        return 'Open GovTech Report recovered: publication freshness, artifact verification and refresh scheduling checks pass.'
    return ''


def collect_issues(root):
    issues=[]
    try:
        result=subprocess.run([str(root/'.venv/bin/python'),str(root/'scripts/check_freshness.py')],cwd=root,capture_output=True,text=True,timeout=300)
        if result.returncode:
            issues.append('Published dataset failed freshness or artifact verification (it may be stale, incomplete, inconsistent or unreachable).')
    except (OSError,subprocess.TimeoutExpired):
        issues.append('Publication verification could not complete.')
    try:
        result=subprocess.run(['gh','api',f'repos/{REPO}/actions/workflows/scrape-and-publish.yml'],capture_output=True,text=True,timeout=30,check=True)
        state=json.loads(result.stdout)['state']
        if state!='active':
            issues.append('Scheduled GitHub refresh is not active: '+state+'.')
        result=subprocess.run(['gh','run','list','-R',REPO,'--workflow','scrape-and-publish.yml','--limit','1','--json','status,conclusion,url'],capture_output=True,text=True,timeout=30,check=True)
        runs=json.loads(result.stdout)
        if not runs:
            issues.append('No refresh runs could be found.')
        elif runs[0]['status']=='completed' and runs[0]['conclusion']!='success':
            issues.append('Latest refresh did not succeed: '+runs[0]['conclusion']+'. '+runs[0]['url'])
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        issues.append('GitHub schedule/run verification could not complete.')
    return issues


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--state',type=Path,default=Path.home()/'.hermes/state/govtech-publication-monitor.json')
    args=parser.parse_args(argv)
    report=change_report(args.state,collect_issues(args.root))
    if report:
        print(report)


if __name__=='__main__':
    main()
