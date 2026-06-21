"""Platform adapter contracts for browser-driven marketplace operations."""

from typing import Any, Dict, List, Optional, Protocol


class PlatformOperationError(RuntimeError):
    """Base exception for expected platform integration states."""


class PlatformNotLoggedInError(PlatformOperationError):
    """Raised when the local browser session is not logged in."""


class PlatformNotReadyError(PlatformOperationError):
    """Raised when the real page flow/selectors are not ready for use."""


class ListingPlatform(Protocol):
    """Operations for a seller account on a listing platform."""

    def login(self) -> Dict[str, Any]:
        """Open the platform and persist a login session."""

    def read_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Read buyer messages without modifying remote state."""

    def send_reply(
        self,
        conversation_id: str,
        content: str,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a confirmed reply. Implementations must require approval."""

    def list_item(
        self,
        item: Dict[str, Any],
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Publish a listing. Implementations must require approval."""

    def delist_item(
        self,
        item_id: str,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delist an item. Implementations must require approval."""

    def ship_order(
        self,
        order_id: str,
        shipment: Dict[str, Any],
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit shipping details. Implementations must require approval."""


class CompetitorSource(Protocol):
    """Read-only competitor intelligence source."""

    def fetch_competitor(
        self,
        keyword: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch public competitor listing data."""
