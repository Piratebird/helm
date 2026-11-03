"""python -m helm.cli"""

from helm.cli import main
from helm.ui.ansi import T

try:
    main()
except KeyboardInterrupt:
    import sys

    print(f"\n{T.WARNING}[!] Process interrupted by user.{T.RST}", file=sys.stderr)
    sys.exit(130)
except Exception as e:
    import sys

    print(f"\n{T.ERROR}[!] An unexpected error occurred: {e}{T.RST}", file=sys.stderr)
    sys.exit(1)
