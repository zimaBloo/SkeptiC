"""Tasks that Inspect finds through the package entry point (see pyproject.toml).

This lets the panel run as `inspect eval skeptic/skeptic_panel`.
"""

from skeptic.panel import skeptic_panel

__all__ = ["skeptic_panel"]
