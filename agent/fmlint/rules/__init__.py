"""FMLint rules package — auto-imports all rule modules to trigger registration."""

from . import structure  # noqa: F401
from . import naming  # noqa: F401
from . import documentation  # noqa: F401
from . import references  # noqa: F401
from . import best_practices  # noqa: F401
from . import calculations  # noqa: F401
from . import live_eval  # noqa: F401
from . import sl_inventory  # noqa: F401  — SL fork additions (see SL_FORK.md)
