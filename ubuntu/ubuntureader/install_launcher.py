#!/usr/bin/env python3
"""Install the reader's application and desktop launchers for the current user."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
APP_ID = 'local.ebookproject.Reader'


def exec_argument(value):
    # Desktop values and Exec arguments each have their own backslash escaping.
    quoted = '"'+value.replace('%','%%').replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
    return quoted.replace('\\','\\\\')


def main():
    data = Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    applications = data/'applications'
    icons = data/'icons/hicolor/scalable/apps'
    applications.mkdir(parents=True,exist_ok=True)
    icons.mkdir(parents=True,exist_ok=True)
    icon = icons/(APP_ID+'.svg')
    shutil.copyfile(ROOT/'assets/app-icon.svg',icon)
    launcher = applications/(APP_ID+'.desktop')
    launcher.write_text('\n'.join([
        '[Desktop Entry]',
        'Type=Application',
        'Name=Saiun',
        'Name[ja]=彩雲',
        'Keywords=EPUB;Reader;Saiun;彩雲;',
        'Comment=EPUBを本棚に取り込んで読む',
        'Exec='+exec_argument(str(ROOT/'run.sh'))+' %F',
        'Icon='+str(icon),
        'Terminal=false',
        'StartupNotify=true',
        'StartupWMClass='+APP_ID,
        'Categories=Office;Viewer;GTK;',
        'MimeType=application/epub+zip;',
        '',
    ]),encoding='utf-8')
    launcher.chmod(0o755)
    desktop = None
    if shutil.which('xdg-user-dir'):
        result = subprocess.run(['xdg-user-dir','DESKTOP'],capture_output=True,text=True,check=True)
        path = Path(result.stdout.strip())
        if path.is_absolute() and path != Path.home():
            path.mkdir(parents=True,exist_ok=True)
            desktop = path/launcher.name
            shutil.copyfile(launcher,desktop)
            desktop.chmod(0o755)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database',str(applications)],check=True)
    if desktop and shutil.which('gio'):
        environment = dict(os.environ)
        runtime = environment.setdefault('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid()))
        environment.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+runtime+'/bus')
        result = subprocess.run(['gio','set',str(desktop),'metadata::trusted','true'],env=environment,capture_output=True,text=True)
        if result.returncode:
            print('デスクトップのアイコンを右クリックし「起動を許可する」を選んでください。')
    print('アプリ一覧へ追加しました。',launcher)
    if desktop:
        print('デスクトップへ追加しました。',desktop)


if __name__ == '__main__':
    main()
