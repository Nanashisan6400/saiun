#!/usr/bin/env python3
"""Start an explicit, temporary-workspace publication of Saiun."""
import sys
sys.dont_write_bytecode = True
from publishing.publisher import main

if __name__ == '__main__':
    raise SystemExit(main())
