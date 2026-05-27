"""
SFMC API client — handles authentication and content queries.

Uses the Marketing Cloud REST API (OAuth2 + Asset API).
"""

import time
from typing import Optional, Dict, Any, List

import requests
from flask import current_app


class SFMCClient:
    """Manages OAuth2 tokens and API requests to SFMC."""

    def __init__(self, client_id: str, client_secret: str, subdomain: str,
                 account_id: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.subdomain = subdomain
        self.account_id = account_id
        self.auth_base = f"https://{subdomain}.auth.marketingcloudapis.com"
        self.rest_base = f"https://{subdomain}.rest.marketingcloudapis.com"
        self._token: Optional[str] = None
        self._token_expires: float = 0

    def _authenticate(self) -> str:
        """Obtain or refresh an OAuth2 access token."""
        if self._token and time.time() < self._token_expires:
            return self._token

        payload: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.account_id:
            payload["account_id"] = self.account_id

        resp = requests.post(
            f"{self.auth_base}/v2/token",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        # Expire 60s early to avoid edge cases
        self._token_expires = time.time() + data.get("expires_in", 1080) - 60
        return self._token

    def _headers(self) -> Dict[str, str]:
        token = self._authenticate()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def search_content(self, query: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        """Search SFMC Content Builder assets for a text string.

        Uses the Asset API's advanced query endpoint to find assets
        whose content (HTML body, text, subject lines, etc.) contains
        the given query string.

        Returns the raw API response dict with 'items', 'count', 'page', 'pageSize'.
        """
        # Asset query payload — search in content and name
        request_body: Dict[str, Any] = {
            "page": {
                "page": page,
                "pageSize": page_size,
            },
            "query": {
                "leftOperand": {
                    "property": "content",
                    "simpleOperator": "like",
                    "value": f"%{query}%",
                },
                "logicalOperator": "OR",
                "rightOperand": {
                    "property": "name",
                    "simpleOperator": "like",
                    "value": f"%{query}%",
                },
            },
            "sort": [
                {"property": "modifiedDate", "direction": "DESC"}
            ],
            "fields": [
                "id", "name", "assetType", "category",
                "modifiedDate", "content", "views",
            ],
        }

        resp = requests.post(
            f"{self.rest_base}/asset/v1/content/assets/query",
            headers=self._headers(),
            json=request_body,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()


def get_sfmc_client() -> SFMCClient:
    """Build an SFMCClient from Flask app config / environment."""
    import os
    client_id = os.environ.get("SFMC_CLIENT_ID", current_app.config.get("SFMC_CLIENT_ID", ""))
    client_secret = os.environ.get("SFMC_CLIENT_SECRET", current_app.config.get("SFMC_CLIENT_SECRET", ""))
    subdomain = os.environ.get("SFMC_SUBDOMAIN", current_app.config.get("SFMC_SUBDOMAIN", ""))
    account_id = os.environ.get("SFMC_ACCOUNT_ID", current_app.config.get("SFMC_ACCOUNT_ID", ""))

    if not client_id or not client_secret or not subdomain:
        raise ValueError(
            "SFMC API-credentials mangler. Sæt SFMC_CLIENT_ID, SFMC_CLIENT_SECRET, "
            "og SFMC_SUBDOMAIN i .env-filen."
        )

    return SFMCClient(
        client_id=client_id,
        client_secret=client_secret,
        subdomain=subdomain,
        account_id=account_id or None,
    )
