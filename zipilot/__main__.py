"""Allow running zipilot as ``python -m zipilot``."""

import sys

from zipilot.cli import main

sys.exit(main())
