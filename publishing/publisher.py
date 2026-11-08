"""Publish a reviewed allowlisted snapshot, using Git only in an OS temp dir."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

REPOSITORY = 'Nanashisan6400/saiun'
REMOTE = 'https://github.com/'+REPOSITORY+'.git'
RECORD = '.saiun-publication.json'
MARKER = 'Saiun-Publication: v1'
MAX_FILE = 16*1024*1024
MAX_TOTAL = 64*1024*1024
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'aws-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'google-api-key': re.compile(r'\bAIza[A-Za-z0-9_-]{30,}\b'),
    'slack-token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{15,}\b'),
    'credential-url': re.compile(r'https?://[^\s/<>\x22\x27]+:[^\s/<>\x22\x27]+@'),
    'secret-literal': re.compile(r'(?im)^\s*[\x22\x27]?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)[\x22\x27]?\s*[:=]\s*[\x22\x27][^\x22\x27\n]{8,}[\x22\x27]'),
}


class PublicationError(Exception):
    pass


def safe_path(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
        raise PublicationError('不正な公開パスです。')
    path=PurePosixPath(value)
    if path.is_absolute() or path.as_posix()!=value or any(p in ('.','..') or p.casefold()=='.git' for p in path.parts):
        raise PublicationError('公開パスに絶対パス・親参照・Git管理領域は使えません。')
    if any(ord(c)<32 or ord(c)==127 for c in value):
        raise PublicationError('公開パスに制御文字は使えません。')
    return path


def ensure_no_git(root):
    for directory,dirs,files in os.walk(root,followlinks=False):
        found=next((n for n in dirs+files if n.casefold()=='.git'),None)
        if found is not None:
            raise PublicationError('既存の .git を検出しました。変更・削除せず停止: '+str(Path(directory)/found))


def scan(data,path,binary=False):
    if binary:return
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise PublicationError('UTF-8ではない公開テキストです: '+path)
    if '\x00' in text:raise PublicationError('公開テキストにNULを検出: '+path)
    hits=[]
    for kind,pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append(f'{path}:{text.count(chr(10),0,match.start())+1} [{kind}]')
    if hits:
        raise PublicationError('秘密情報の疑いを検出。値は表示しません:\n'+'\n'.join(hits[:20]))


def read_file(root,relative):
    path=root.joinpath(*safe_path(relative).parts)
    for parent in [path,*path.parents]:
        if parent==root:break
        if parent.is_symlink():raise PublicationError('シンボリックリンクは公開しません: '+relative)
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise PublicationError('通常ファイル以外またはハードリンクを検出: '+relative)
    if info.st_size>MAX_FILE:raise PublicationError('公開ファイルの容量上限を超過: '+relative)
    data=path.read_bytes()
    if len(data)>MAX_FILE:raise PublicationError('読み込み中に容量上限を超過: '+relative)
    return data


def collect(root):
    root=Path(root).resolve()
    ensure_no_git(root)
    raw=read_file(root,'publishing/manifest.json')
    manifest=json.loads(raw)
    if not isinstance(manifest,dict) or manifest.get('schema')!=1 or manifest.get('repository')!=REPOSITORY:
        raise PublicationError('公開許可リストの形式または公開先が不正です。')
    items=manifest.get('files')
    if not isinstance(items,list) or not 0<len(items)<=1000:
        raise PublicationError('公開許可リストが不正です。')
    snapshot={};total=0
    banned={'.env','.gitignore','.gitattributes','.gitmodules','.netrc','.npmrc','.pypirc','credentials','hosts.yml'}
    allowed={'.py','.js','.css','.md','.txt','.json','.sh','.svg','.ttf'}
    for item in items:
        if not isinstance(item,dict) or set(item)-{'source','destination','sha256','executable'}:
            raise PublicationError('公開許可リストの項目が不正です。')
        if 'executable' in item and type(item['executable']) is not bool:
            raise PublicationError('実行属性はtrue/falseで指定してください。')
        src=str(safe_path(item['source']));dst=str(safe_path(item['destination']))
        if dst==RECORD or dst in snapshot:raise PublicationError('公開先パスが重複しています: '+dst)
        p=PurePosixPath(dst)
        if any(part.lower() in banned or part.startswith('.env') or part in ('__pycache__','results','.venv','node_modules','empty-git-template') for part in (*p.parts,*PurePosixPath(src).parts)):
            raise PublicationError('公開禁止ファイル/フォルダ: '+dst)
        source_path=PurePosixPath(src)
        if (p.suffix not in allowed and p.name!='LICENSE') or (source_path.suffix not in allowed and source_path.name!='LICENSE'):
            raise PublicationError('未許可の拡張子: '+dst)
        # The public layout preserves the package one directory below ubuntu/.
        if not (root/src).exists() and src.startswith('ubuntureader/') and not (root/'ubuntureader').exists() and (root/'ubuntu/ubuntureader').is_dir():
            candidate='ubuntu/'+src
            src=candidate if (root/candidate).exists() else dst
        data=read_file(root,src)
        binary=p.suffix=='.ttf'
        digest=hashlib.sha256(data).hexdigest()
        if binary and item.get('sha256')!=digest:
            raise PublicationError('許可した公式フォントのハッシュが一致しません: '+dst)
        scan(data,dst,binary)
        total+=len(data)
        if total>MAX_TOTAL:raise PublicationError('公開対象の総容量が上限を超えました。')
        snapshot[dst]={'data':data,'mode':'100755' if item.get('executable') else '100644','sha256':digest}
    # Ensure all relative Python imports and referenced core files are represented.
    required=['ubuntu/ubuntureader/stage0/'+n for n in ('sample_epub.py','probe.js','typography.js')]
    required+=['README.md','LICENSE','CONTRIBUTING.md','THIRD_PARTY_NOTICES.md','publish_saiun.py']
    if any(name not in snapshot for name in required):raise PublicationError('公開に必須のファイルが不足しています。')
    for path,item in snapshot.items():
        if not path.endswith('.py'):continue
        tree=ast.parse(item['data'],filename=path)
        for node in ast.walk(tree):
            if not isinstance(node,ast.ImportFrom) or not node.level:continue
            base=PurePosixPath(path).parent.parts
            if node.level>len(base):raise PublicationError('パッケージ外への相対import: '+path)
            base=base[:len(base)-node.level+1]
            modules=[node.module] if node.module else [alias.name for alias in node.names]
            for module in modules:
                target='/'.join((*base,*module.split('.')))
                if target+'.py' not in snapshot and target+'/__init__.py' not in snapshot:
                    raise PublicationError('公開対象に相対importの依存が不足: '+path+' -> '+target)
    return snapshot


class Publisher:
    def __init__(self,root,remote=REMOTE,output=print,confirm=input,name=None,email=None):
        self.root=Path(root).resolve();self.remote=remote
        self.output=output;self.confirm=confirm;self.name=name;self.email=email
        self.work=None

    def git(self,*args,check=True):
        env={k:v for k,v in os.environ.items() if not k.startswith(('GIT_CONFIG_','GIT_TRACE')) and k not in {
            'GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES','GIT_COMMON_DIR','GIT_NAMESPACE','GIT_EXTERNAL_DIFF','GIT_DIFF_OPTS','GIT_CONFIG'}}
        env.update(GIT_TERMINAL_PROMPT='0',GIT_PAGER='cat',LC_ALL='C')
        options=['-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','-c','core.attributesFile='+os.devnull,
                 '-c','color.ui=false','-c','diff.external=','-c','credential.interactive=false',
                 '-c','core.bare=false','-c','core.autocrlf=false','-c','core.safecrlf=false','-c','core.filemode=true',
                 '-c','core.worktree='+str(self.work),'-c','http.sslVerify=true']
        if self.name:options+=['-c','user.name='+self.name]
        if self.email:options+=['-c','user.email='+self.email]
        result=subprocess.run(['git',*options,*args],cwd=self.work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if len(result.stdout)>MAX_TOTAL:raise PublicationError('Gitの応答容量が上限を超えました。')
        if check and result.returncode:
            # Do not echo credential helper output or remote-controlled errors.
            raise PublicationError('Git操作に失敗しました: '+args[0]+'。認証・接続・権限・コミット名義を確認してください（認証値は表示しません）。')
        return result

    def remote_refs(self):
        text=self.git('ls-remote','--symref','origin').stdout.decode('utf-8')
        branch=None;refs={}
        for line in text.splitlines():
            if line.startswith('ref: refs/heads/') and line.endswith('\tHEAD'):
                branch=line.split('\t')[0][len('ref: refs/heads/'):]
            elif '\t' in line:
                oid,ref=line.split('\t',1)
                if ref.startswith('refs/heads/'):refs[ref[len('refs/heads/'):]]=oid
        if branch is None:
            if refs:raise PublicationError('既定ブランチを判定できません。GitHub側のHEAD設定を確認してください。')
            branch='main'
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*',branch):raise PublicationError('未対応のブランチ名です。')
        self.git('check-ref-format','refs/heads/'+branch)
        return branch,refs

    def verify_existing(self,head,snapshot):
        message=self.git('show','-s','--format=%B',head).stdout.decode('utf-8')
        if MARKER not in message.splitlines():
            raise PublicationError('GitHub側に公開スクリプトで確認できないコミットがあります。初回の既存README等も自動上書きせず停止します。')
        raw_tree=self.git('ls-tree','-r','-z',head).stdout
        tree={}
        for entry in raw_tree.split(b'\x00'):
            if not entry:continue
            meta,path=entry.split(b'\t',1)
            mode,kind,oid=meta.decode('ascii').split()
            path=path.decode('utf-8');safe_path(path)
            if mode not in ('100644','100755') or kind!='blob':raise PublicationError('リモートのリンク/サブモジュール等を検出。停止します。')
            tree[path]=(mode,oid)
        if RECORD not in tree:raise PublicationError('前回公開のファイル記録がありません。停止します。')
        record=json.loads(self.git('cat-file','blob',tree[RECORD][1]).stdout)
        if not isinstance(record,dict):raise PublicationError('前回公開の記録が不正です。')
        files=record.get('files')
        if not isinstance(record,dict) or record.get('schema')!=1 or record.get('repository')!=REPOSITORY or not isinstance(files,dict):
            raise PublicationError('前回公開のファイル記録が不正です。')
        if set(tree)!=(set(files)|{RECORD}):raise PublicationError('GitHub側だけのファイル/削除を検出。停止します。')
        remote_only=set(files)-set(snapshot)
        if remote_only:raise PublicationError('GitHub側だけに存在するファイルがあります。削除せず停止:\n'+'\n'.join(sorted(remote_only)))
        for path,expected in files.items():
            mode,oid=tree[path];raw=self.git('cat-file','blob',oid).stdout
            if len(raw)>MAX_FILE or expected!={'sha256':hashlib.sha256(raw).hexdigest(),'mode':mode}:
                raise PublicationError('前回公開後のリモート変更を検出。上書きせず停止: '+path)
            scan(raw,path,Path(path).suffix=='.ttf')

    def run(self,check_only=False,offline=False,message='Publish Saiun source snapshot'):
        for field,value in [('コミットメッセージ',message),('公開名義',self.name),('公開メール',self.email)]:
            if value is not None:scan(value.encode('utf-8'),field)
        snapshot=collect(self.root)
        self.output(f'公開先: {REPOSITORY}\n公開候補: {len(snapshot)}ファイル / {sum(len(x["data"]) for x in snapshot.values())/1024/1024:.2f} MiB')
        if offline:
            if not check_only:raise PublicationError('--offline は --check と組み合わせてください。')
            self.output('\n'.join(sorted(snapshot)))
            self.output('ローカル事前検査のみ完了。Git/認証/リモート検査は未実施です。')
            return 'checked-offline'
        temp_parent=Path(tempfile.gettempdir()).resolve()
        if temp_parent==self.root or self.root in temp_parent.parents:
            raise PublicationError('一時領域が開発フォルダ内です。OSの一時領域を使用してください。')
        with tempfile.TemporaryDirectory(prefix='saiun-publish-',dir=temp_parent) as temporary:
            self.work=Path(temporary)
            try:
                empty_template=self.work/'empty-git-template'
                empty_template.mkdir()
                self.git('init','--quiet','--template='+str(empty_template))
                rewrites=self.git('config','--get-regexp',r'^url\..*\.(insteadof|pushinsteadof)$',check=False)
                if rewrites.returncode==0:
                    raise PublicationError('GitのURL書換え設定を検出しました。別の公開先へ送らないため停止します。')
                self.git('remote','add','origin',self.remote)
                branch,refs=self.remote_refs();head=refs.get(branch)
                if head:
                    # Fetch all branch history, but never check out remote code or run it.
                    self.git('fetch','--quiet','--no-tags','origin','refs/heads/*:refs/remotes/origin/*')
                    fetched=self.git('rev-parse','refs/remotes/origin/'+branch).stdout.decode().strip()
                    if fetched!=head:raise PublicationError('取得中にリモートが変更されました。停止します。')
                    self.verify_existing(head,snapshot)
                    self.git('update-ref','refs/heads/'+branch,head)
                    self.git('symbolic-ref','HEAD','refs/heads/'+branch)
                    self.git('read-tree',head)
                else:self.git('symbolic-ref','HEAD','refs/heads/'+branch)
                for path,entry in snapshot.items():
                    destination=self.work/path;destination.parent.mkdir(parents=True,exist_ok=True)
                    destination.write_bytes(entry['data']);destination.chmod(int(entry['mode'],8)&0o777)
                record={'schema':1,'repository':REPOSITORY,'files':{p:{'sha256':e['sha256'],'mode':e['mode']} for p,e in sorted(snapshot.items())}}
                (self.work/RECORD).write_text(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
                self.git('add','--',*sorted(snapshot),RECORD)
                if self.git('diff','--cached','--quiet',check=False).returncode==0:
                    self.output('変更なし。Commit/Pushは行いません。');return 'unchanged'
                self.output('ブランチ: '+branch)
                self.output(self.git('diff','--cached','--stat','--no-ext-diff','--no-textconv','--no-renames').stdout.decode('utf-8'))
                self.output(self.git('diff','--cached','--no-ext-diff','--no-textconv','--no-renames').stdout.decode('utf-8',errors='replace'))
                if check_only:self.output('差分確認のみ完了。Commit/Pushは行いません。');return 'checked'
                if self.git('var','GIT_AUTHOR_IDENT',check=False).returncode or self.git('var','GIT_COMMITTER_IDENT',check=False).returncode:
                    raise PublicationError('コミット名義が未設定です。既存のGit設定、または --name と --email を指定してください。設定は開発フォルダへ保存しません。')
                self.output('コミットメッセージ: '+message)
                try:answer=self.confirm(f'{REPOSITORY} の {branch} へ公開する場合だけ「PUBLISH」と入力してください: ')
                except EOFError:answer=''
                if answer!='PUBLISH':self.output('公開を取り消しました。');return 'cancelled'
                # Re-read everything after review. Do not publish a moving source snapshot.
                newer=collect(self.root)
                if {p:(e['sha256'],e['mode']) for p,e in newer.items()}!={p:(e['sha256'],e['mode']) for p,e in snapshot.items()}:
                    raise PublicationError('確認中に公開対象が変更されました。Commit/Pushせず停止します。')
                branch_now,refs_now=self.remote_refs()
                if branch_now!=branch or refs_now.get(branch)!=head:
                    raise PublicationError('確認中にリモートブランチが変更されました。Commit/Pushせず停止します。')
                self.git('-c','commit.gpgsign=false','commit','--quiet','-m',message+'\n\n'+MARKER)
                # A concurrent update is rejected by Git's normal fast-forward check.
                self.git('push',self.remote,'HEAD:refs/heads/'+branch)
                self.output('公開完了。'+REPOSITORY+' / '+branch)
                return 'published'
            finally:
                self.work=None


def main(argv=None):
    parser=argparse.ArgumentParser(description='彩雲/Saiunの明示的なソース公開（開発フォルダではGit管理しません）')
    parser.add_argument('--check',action='store_true',help='検査と差分表示のみ。Commit/Pushしない')
    parser.add_argument('--offline',action='store_true',help='--checkと併用し、認証不要のローカル検査だけ行う')
    parser.add_argument('--message',default='Publish Saiun source snapshot')
    parser.add_argument('--name',help='今回のコミット名義。永続設定しない')
    parser.add_argument('--email',help='今回のコミットメール。永続設定しない')
    args=parser.parse_args(argv)
    def interrupted(signum,frame):raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,interrupted)
    try:
        if not args.offline and not shutil.which('git'):raise PublicationError('Gitが見つかりません。')
        Publisher(Path(__file__).resolve().parent.parent,name=args.name,email=args.email).run(args.check,args.offline,args.message)
        return 0
    except KeyboardInterrupt:
        print('公開処理を中断しました。一時領域を終了処理で削除しました。',file=sys.stderr);return 130
    except (PublicationError,OSError,ValueError,KeyError,TypeError) as error:
        # File/JSON errors may contain untrusted source values: only our messages are displayed.
        print(str(error) if isinstance(error,PublicationError) else '公開検査またはファイル処理に失敗しました。Push中の失敗時はGitHub側の結果も確認してください。',file=sys.stderr)
        return 1
    finally:signal.signal(signal.SIGTERM,previous)
