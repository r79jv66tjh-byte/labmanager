"""Startet LabManager ohne Konsolenfenster (Doppelklick oder pythonw)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from labmanager.app import run

sys.exit(run(sys.argv[1:]))
