"""`python -m robot_autonomy` runs the `robot-autonomy` command line."""

import sys

from .cli import main

sys.exit(main())
