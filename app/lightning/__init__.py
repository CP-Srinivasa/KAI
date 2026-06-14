"""Lightning (RaspiBlitz/lnd) integration — KAI as read-only client (Phase 1).

Default-off, shadow-first, fail-closed. See
KAI-mirror/kai_lightning_integration_plan_20260614.md for the full phased plan.
"""

from app.lightning.adapter import LightningNodeStatus, get_node_status
from app.lightning.client import LightningUnavailableError, LndInfo, LndRestClient

__all__ = [
    "LightningNodeStatus",
    "LightningUnavailableError",
    "LndInfo",
    "LndRestClient",
    "get_node_status",
]
