"""All Git repositories here are local disposable fixtures, never GitHub."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from publishing.publisher import Publisher, PublicationError, collect, RECORD, MARKER

ROOT=Path(__file__).resolve().parents[2]


class ObservedPublisher(Publisher):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.calls=[];self.workspaces=set()
    def git(self,*args,**kwargs):
        self.calls.append(args);self.workspaces.add(self.work)
        return super().git(*args,**kwargs)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='saiun-publisher-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'development';self.root.mkdir()
        home=self.base/'home';home.mkdir()
        self.environment=patch.dict(os.environ,{'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config')})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.remote=self.base/'remote.git'
        self.git(self.base,'init','--bare','--quiet',str(self.remote))
        self.git(self.base,'--git-dir='+str(self.remote),'symbolic-ref','HEAD','refs/heads/main')
        mapping={'README.md':b'# Saiun\n','LICENSE':b'MIT License\n','CONTRIBUTING.md':b'No pull requests.\n',
                 'THIRD_PARTY_NOTICES.md':b'Fonts keep their license.\n','publish_saiun.py':b'print("publication")\n',
                 'ubuntu/ubuntureader/stage0/sample_epub.py':b'# source\n',
                 'ubuntu/ubuntureader/stage0/probe.js':b'// probe\n',
                 'ubuntu/ubuntureader/stage0/typography.js':b'// typography\n'}
        entries=[]
        for i,(dst,data) in enumerate(mapping.items()):
            src=f'input/{i}.txt';p=self.root/src;p.parent.mkdir(exist_ok=True);p.write_bytes(data)
            entries.append({'source':src,'destination':dst})
        folder=self.root/'publishing';folder.mkdir()
        self.manifest=folder/'manifest.json'
        self.manifest.write_text(json.dumps({'schema':1,'repository':'Nanashisan6400/saiun','files':entries}))
        self.output=[];self.publishers=[]

    def git(self,cwd,*args):
        result=subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-c','user.name=Test Author','-c','user.email=test@example.invalid',*args],cwd=cwd,capture_output=True)
        if result.returncode:raise AssertionError(result.stderr.decode())
        return result.stdout

    def publisher(self,answer='PUBLISH',**kwargs):
        p=ObservedPublisher(self.root,str(self.remote),output=self.output.append,confirm=lambda prompt:answer,name='Test Author',email='test@example.invalid',**kwargs)
        self.publishers.append(p);return p

    def tearDown(self):
        for publisher in self.publishers:
            self.assertIsNone(publisher.work)
            for workspace in publisher.workspaces:
                self.assertIsNotNone(workspace)
                self.assertFalse(workspace.exists(),str(workspace))
            self.assertFalse(any('--force' in call or '-f' in call for call in publisher.calls))

    def head(self):
        result=subprocess.run(['git','--git-dir='+str(self.remote),'rev-parse','--verify','refs/heads/main'],capture_output=True)
        return result.stdout.strip() if result.returncode==0 else None

    def remote_commit(self,changes,message='GitHub-side edit'):
        clone=self.base/'remote-editor'
        if clone.exists():shutil.rmtree(clone)
        self.git(self.base,'clone','--quiet','--branch','main',str(self.remote),str(clone))
        for name,data in changes.items():
            p=clone/name
            if data is None:p.unlink()
            else:p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.git(clone,'add','--all');self.git(clone,'commit','--quiet','-m',message)
        self.git(clone,'push','--quiet',str(self.remote),'HEAD:refs/heads/main')

    def test_first_publication_and_no_changes(self):
        before=(self.root/'input/0.txt').read_bytes()
        self.assertEqual(self.publisher().run(),'published')
        head=self.head();self.assertIsNotNone(head)
        self.assertEqual(self.publisher().run(),'unchanged')
        self.assertEqual(head,self.head())
        self.assertEqual(before,(self.root/'input/0.txt').read_bytes())
        self.assertFalse((self.root/'.git').exists())
        self.assertIn(RECORD,self.git(self.base,'--git-dir='+str(self.remote),'ls-tree','--name-only',head.decode()).decode())

    def test_local_update_and_remote_history_retained(self):
        self.publisher().run();before=self.head()
        (self.root/'input/0.txt').write_text('# Changed Saiun\n')
        self.assertEqual(self.publisher().run(),'published')
        self.assertNotEqual(before,self.head())
        parent=self.git(self.base,'--git-dir='+str(self.remote),'rev-parse','refs/heads/main^').strip()
        self.assertEqual(before,parent)

    def test_cancel_and_check_only_never_commit_or_push(self):
        for check,answer in [(False,'no'),(True,'PUBLISH')]:
            publisher=self.publisher(answer=answer)
            publisher.run(check_only=check)
            self.assertIsNone(self.head())
            self.assertFalse(any('commit' in call or call[0]=='push' for call in publisher.calls))

    def test_offline_check_does_not_use_git(self):
        publisher=self.publisher()
        self.assertEqual(publisher.run(check_only=True,offline=True),'checked-offline')
        self.assertEqual(publisher.calls,[])

    def test_existing_git_is_reported_and_preserved(self):
        metadata=self.root/'.git';metadata.mkdir();(metadata/'keep').write_text('keep')
        with self.assertRaises(PublicationError):self.publisher().run()
        self.assertEqual((metadata/'keep').read_text(),'keep')

    def test_secret_rejected_without_echoing_value(self):
        token='ghp_'+'A'*40
        (self.root/'input/0.txt').write_text(token)
        with self.assertRaises(PublicationError) as raised:self.publisher().run()
        self.assertIn('github-token',str(raised.exception))
        self.assertNotIn(token,str(raised.exception))
        self.assertIsNone(self.head())

    def test_unlisted_private_book_is_excluded(self):
        (self.root/'private.epub').write_bytes(b'private book')
        (self.root/'.env').write_text('private configuration')
        self.publisher().run()
        names=self.git(self.base,'--git-dir='+str(self.remote),'ls-tree','-r','--name-only','refs/heads/main').decode()
        self.assertNotIn('private.epub',names);self.assertNotIn('.env',names)

    def test_remote_add_edit_and_delete_stop(self):
        self.publisher().run()
        self.remote_commit({'remote-only.md':b'keep this\n'})
        head=self.head()
        with self.assertRaises(PublicationError):self.publisher().run()
        self.assertEqual(head,self.head())

    def test_remote_hash_change_even_with_copied_marker_stops(self):
        self.publisher().run()
        self.remote_commit({'README.md':b'remote modified\n'},'manual\n\n'+MARKER)
        head=self.head()
        with self.assertRaises(PublicationError):self.publisher().run()
        self.assertEqual(head,self.head())

    def test_remote_deleted_file_stops(self):
        self.publisher().run()
        self.remote_commit({'README.md':None},'manual\n\n'+MARKER)
        with self.assertRaises(PublicationError):self.publisher().run()

    def test_local_removal_never_deletes_remote_file(self):
        self.publisher().run();head=self.head()
        data=json.loads(self.manifest.read_text())
        data['files'].append({'source':'input/extra.txt','destination':'extra.txt'})
        (self.root/'input/extra.txt').write_text('original\n');self.manifest.write_text(json.dumps(data))
        self.publisher().run();head=self.head()
        data['files'].pop();self.manifest.write_text(json.dumps(data))
        with self.assertRaises(PublicationError):self.publisher().run()
        self.assertEqual(head,self.head())

    def test_unmarked_initial_repository_stops(self):
        edit=self.base/'first';edit.mkdir()
        self.git(edit,'init','--quiet','-b','main');(edit/'README.md').write_text('existing')
        self.git(edit,'add','README.md');self.git(edit,'commit','--quiet','-m','Existing README')
        self.git(edit,'push','--quiet',str(self.remote),'HEAD:refs/heads/main')
        head=self.head()
        with self.assertRaises(PublicationError):self.publisher().run()
        self.assertEqual(head,self.head())

    def test_source_changes_during_review_stop_before_commit(self):
        publisher=self.publisher()
        def confirm(prompt):
            (self.root/'input/0.txt').write_text('modified after review')
            return 'PUBLISH'
        publisher.confirm=confirm
        with self.assertRaises(PublicationError):publisher.run()
        self.assertIsNone(self.head())
        self.assertFalse(any('commit' in call for call in publisher.calls))

    def test_remote_changes_during_review_stop_before_commit(self):
        self.publisher().run();publisher=self.publisher()
        (self.root/'input/0.txt').write_text('local modification')
        def confirm(prompt):
            self.remote_commit({'README.md':b'remote concurrent edit'})
            return 'PUBLISH'
        publisher.confirm=confirm
        with self.assertRaises(PublicationError):publisher.run()
        self.assertFalse(any('commit' in call for call in publisher.calls))

    def test_remote_race_at_push_is_rejected(self):
        self.publisher().run();publisher=self.publisher()
        (self.root/'input/0.txt').write_text('local modification')
        normal=publisher.git;changed=[]
        def git(*args,**kwargs):
            if args[0]=='push' and not changed:
                changed.append(True);self.remote_commit({'README.md':b'remote race'})
            return normal(*args,**kwargs)
        publisher.git=git
        with self.assertRaises(PublicationError):publisher.run()
        self.assertTrue(changed)
        self.assertEqual(self.git(self.base,'--git-dir='+str(self.remote),'show','refs/heads/main:README.md'),b'remote race')

    def test_interruption_and_connection_failure_cleanup(self):
        publisher=self.publisher()
        def stop(prompt):raise KeyboardInterrupt
        publisher.confirm=stop
        with self.assertRaises(KeyboardInterrupt):publisher.run()
        self.assertIsNone(self.head())
        missing=self.publisher();missing.remote=str(self.base/'does-not-exist.git')
        with self.assertRaises(PublicationError):missing.run()

    def test_symlink_and_path_traversal_rejected(self):
        file=self.root/'input/0.txt';file.unlink();file.symlink_to(self.base/'outside.txt')
        (self.base/'outside.txt').write_text('outside')
        with self.assertRaises(PublicationError):self.publisher().run()
        file.unlink();file.write_text('safe')
        data=json.loads(self.manifest.read_text());data['files'][0]['destination']='../escape'
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(PublicationError):self.publisher().run()

    def test_crlf_bytes_survive_global_autocrlf(self):
        self.git(self.base,'config','--global','core.autocrlf','true')
        (self.root/'input/0.txt').write_bytes(b'line one\r\nline two\r\n')
        self.publisher().run()
        self.assertEqual(self.publisher().run(),'unchanged')
        raw=self.git(self.base,'--git-dir='+str(self.remote),'show','refs/heads/main:README.md')
        self.assertEqual(raw,b'line one\r\nline two\r\n')

    def test_global_hooks_and_templates_are_not_executed(self):
        import shlex
        hooks=self.base/'hooks';hooks.mkdir()
        sentinel=self.base/'unexpected-hook'
        hook=hooks/'post-commit'
        hook.write_text('#!/bin/sh\ntouch '+shlex.quote(str(sentinel))+'\n');hook.chmod(0o755)
        self.git(self.base,'config','--global','core.hooksPath',str(hooks))
        template=self.base/'template';(template/'hooks').mkdir(parents=True)
        shutil.copyfile(hook,template/'hooks/post-commit');(template/'hooks/post-commit').chmod(0o755)
        self.git(self.base,'config','--global','init.templateDir',str(template))
        self.publisher().run()
        self.assertFalse(sentinel.exists())

    def test_url_rewrite_stops_without_push(self):
        self.git(self.base,'config','--global','url.https://example.invalid/.insteadOf','https://github.com/')
        publisher=self.publisher()
        with self.assertRaises(PublicationError):publisher.run()
        self.assertFalse(any(call[0]=='push' for call in publisher.calls))

    def test_case_insensitive_git_path_is_rejected(self):
        data=json.loads(self.manifest.read_text());data['files'][0]['destination']='.GIT/config'
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(PublicationError):self.publisher().run()

    def test_eof_is_cancellation(self):
        publisher=self.publisher()
        def end(prompt):raise EOFError
        publisher.confirm=end
        self.assertEqual(publisher.run(),'cancelled')
        self.assertIsNone(self.head())

    def test_secret_in_commit_message_stops(self):
        publisher=self.publisher()
        with self.assertRaises(PublicationError):publisher.run(message='github_pat_'+'B'*40)
        self.assertEqual(publisher.calls,[])

    def test_missing_relative_import_stops_before_git(self):
        (self.root/'input/4.txt').write_text('from .missing_module import item\n')
        publisher=self.publisher()
        with self.assertRaises(PublicationError):publisher.run()
        self.assertEqual(publisher.calls,[])


class RealSourceTests(unittest.TestCase):
    def test_actual_allowlist_and_public_layout_tests(self):
        snapshot=collect(ROOT)
        self.assertGreater(len(snapshot),30)
        self.assertFalse(any(path.endswith(('.epub','.sqlite3','.png','.pyc')) for path in snapshot))
        with tempfile.TemporaryDirectory(prefix='saiun-export-test-') as directory:
            public=Path(directory)
            for path,item in snapshot.items():
                p=public/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(item['data'])
            recollected=collect(public)
            self.assertEqual({p:e['sha256'] for p,e in snapshot.items()},{p:e['sha256'] for p,e in recollected.items()})
            result=subprocess.run([os.sys.executable,'-m','unittest','ubuntureader.reader.test_library','ubuntureader.reader.test_fixed','ubuntureader.reader.test_progressive','ubuntureader.reader.test_seamless'],cwd=public/'ubuntu',capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr[-3000:])
            readme=(public/'README.md').read_text()
            self.assertIn('Pull Requestは受け付けません',readme)
            self.assertIn('Pull requests are not accepted',readme)


if __name__=='__main__':unittest.main()
