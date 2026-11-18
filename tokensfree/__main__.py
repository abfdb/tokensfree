"""`python -m tokensfree` runs the loopback HTTP host.

The same entry point as `tokensfree.edge.host`; kept as a module so the command
does not depend on installing a console script.
"""
import sys

from tokensfree.edge.host import main

if __name__ == "__main__":
    sys.exit(main() or 0)
