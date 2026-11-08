import argparse
import os
from pathlib import Path
import sys
from .app import ReaderApplication
from .branding import display_name
from gi.repository import Gio

parser = argparse.ArgumentParser(description=display_name())
parser.add_argument('--data-dir', help='Use a separate local library directory')
parser.add_argument('files', nargs='*', help='EPUB files to import')
args = parser.parse_args()
launch_directory = os.environ.get('EBOOK_READER_LAUNCH_DIRECTORY', os.getcwd())
data_dir = args.data_dir
if data_dir and not Path(data_dir).is_absolute():
    data_dir = Path(launch_directory)/data_dir
files = [Gio.File.new_for_commandline_arg_and_cwd(name, launch_directory).get_uri() for name in args.files]
application = ReaderApplication(data_dir)
raise SystemExit(application.run([sys.argv[0]]+files))
