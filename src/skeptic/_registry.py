"""Tasks that Inspect finds through the package entry point (see pyproject.toml).

This lets the panel run as `inspect eval skeptic/skeptic_panel`.
"""

from skeptic.panel import skeptic_panel
from skeptic.single import single_judge

__all__ = ["single_judge", "skeptic_panel"]
