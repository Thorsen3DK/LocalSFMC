"""
SFMC REST API client for searching Content Builder assets.

Requires environment variables:
    SFMC_CLIENT_ID      — API integration client ID
    SFMC_CLIENT_SECRET  — API integration client secret
    SFMC_AUTH_BASE_URI  — Authentication base URI (e.g. https://YOUR_SUBDOMAIN.auth.marketingcloudapis.com)
    SFMC_REST_BASE_URI  — REST base URI (e.g. https://YOUR_SUBDOMAIN.rest.marketingcloudapis.com)
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests


class SFMCClient:
    """Thin wrapper around SFMC REST API for Content Builder asset search."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        auth_base_uri: Optional[str] = None,
        rest_base_uri: Optional[str] = None,
    ):
        self.client_id = client_id or os.environ.get("SFMC_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("SFMC_CLIENT_SECRET", "")
        self.auth_base_uri = (auth_base_uri or os.environ.get("SFMC_AUTH_BASE_URI", "")).rstrip("/")
        self.rest_base_uri = (rest_base_uri or os.environ.get("SFMC_REST_BASE_URI", "")).rstrip("/")

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Obtain or refresh an OAuth2 access token."""
        if self._access_token and time.time() < self._token_expires_at:
            return

        if not all([self.client_id, self.client_secret, self.auth_base_uri]):
            raise ValueError(
                "SFMC credentials not configured. Set SFMC_CLIENT_ID, "
                "SFMC_CLIENT_SECRET, and SFMC_AUTH_BASE_URI environment variables."
            )

        resp = requests.post(
            f"{self.auth_base_uri}/v2/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # Expire 60s early to avoid edge-case failures
        self._token_expires_at = time.time() + data.get("expires_in", 1080) - 60

    def _headers(self) -> Dict[str, str]:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # Asset Search
    # ------------------------------------------------------------------

    def search_assets(
        self,
        query: str,
        asset_types: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 50,
        sort_field: str = "modifiedDate",
        sort_direction: str = "DESC",
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Search Content Builder assets by a text query with exact substring matching.

        Paginates through all assets of the requested types 100 at a time,
        checking content locally for exact substring match. This approach
        avoids SFMC API 'like' operator issues with special characters
        (dots, slashes, etc.) that cause false negatives or timeouts.

        Args:
            query: The exact string to find in name, subject, or HTML body.
            asset_types: Filter by asset type names (e.g. ["htmlemail", "contentblock"]).
                         If None, searches both emails and content blocks.
            page: 1-based page number for the filtered results.
            page_size: Results per page.
            sort_field: Field to sort by (modifiedDate, createdDate, name).
            sort_direction: ASC or DESC.
            progress_callback: Optional callable(scanned, total, matches) called
                               after each batch is processed.

        Returns:
            Dict with 'items' (list of asset dicts) and 'totalCount'.
        """
        if not self.rest_base_uri:
            raise ValueError(
                "SFMC REST base URI not configured. Set SFMC_REST_BASE_URI."
            )

        # Asset type IDs: htmlemail=208, templatebasedemail=207, contentblock=220
        type_map = {
            "htmlemail": 208,
            "templatebasedemail": 207,
            "contentblock": 220,
        }

        if asset_types is not None:
            type_ids = [type_map[t] for t in asset_types if t in type_map]
        else:
            type_ids = []  # Empty means search ALL asset types

        query_lower = query.lower()

        # Use concurrent API calls to speed up the scan.
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        batch_size = 100
        max_retries = 3
        max_workers = 5  # Parallel API requests

        # Build asset type filter (or no filter if searching all types)
        type_filter: Optional[Dict[str, Any]] = None
        if type_ids:
            if len(type_ids) == 1:
                type_filter = {
                    "property": "assetType.id",
                    "simpleOperator": "equal",
                    "value": type_ids[0],
                }
            else:
                type_filter = {
                    "property": "assetType.id",
                    "simpleOperator": "in",
                    "value": type_ids,
                }

        def fetch_page(page_num: int) -> Dict[str, Any]:
            """Fetch a single page of assets with retry logic."""
            request_body: Dict[str, Any] = {
                "page": {
                    "page": page_num,
                    "pageSize": batch_size,
                },
                "sort": [
                    {"property": sort_field, "direction": sort_direction},
                ],
            }
            if type_filter:
                request_body["query"] = type_filter

            for attempt in range(max_retries):
                try:
                    resp = requests.post(
                        f"{self.rest_base_uri}/asset/v1/content/assets/query",
                        headers=self._headers(),
                        json=request_body,
                        timeout=120,
                    )
                    if not resp.ok:
                        try:
                            err_detail = resp.json()
                        except Exception:
                            err_detail = resp.text
                        raise RuntimeError(f"SFMC API returned {resp.status_code}: {err_detail}")
                    return resp.json()
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.ReadTimeout) as e:
                    if attempt < max_retries - 1:
                        _time.sleep(2 ** attempt)
                    else:
                        raise RuntimeError(
                            f"SFMC API connection failed after {max_retries} retries: {e}"
                        )
            return {"items": [], "count": 0}

        def check_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """Check if an item matches the search query."""
            asset_id = item.get("id")
            name = item.get("name", "")
            views = item.get("views") or {}
            subject = ""

            all_content_parts = [name]

            if "subjectline" in views:
                subject = views["subjectline"].get("content", "") or ""
                all_content_parts.append(subject)

            for view_key, view_val in views.items():
                if view_key == "subjectline":
                    continue
                if isinstance(view_val, dict):
                    content = view_val.get("content", "") or ""
                    if content:
                        all_content_parts.append(content)
                    meta = view_val.get("meta", {})
                    options = meta.get("options", {}) if isinstance(meta, dict) else {}
                    cbd = options.get("customBlockData", {}) if isinstance(options, dict) else {}
                    if isinstance(cbd, dict):
                        display_msg = cbd.get("display:message", "") or ""
                        if display_msg:
                            all_content_parts.append(display_msg)

            top_content = item.get("content", "") or ""
            if top_content:
                all_content_parts.append(top_content)

            searchable = "\n".join(all_content_parts).lower()
            if query_lower in searchable:
                return {
                    "id": asset_id,
                    "name": name,
                    "assetType": item.get("assetType", {}).get("name", ""),
                    "subject": subject,
                    "category": item.get("category", {}).get("name", ""),
                    "createdDate": item.get("createdDate", ""),
                    "modifiedDate": item.get("modifiedDate", ""),
                }
            return None

        # First, fetch page 1 to get the total count
        first_page_data = fetch_page(1)
        total_in_api = first_page_data.get("count", 0)
        total_pages = (total_in_api + batch_size - 1) // batch_size if total_in_api > 0 else 1

        # Process first page results
        matched_items: List[Dict[str, Any]] = []
        for item in first_page_data.get("items", []):
            match = check_item(item)
            if match:
                matched_items.append(match)

        scanned = min(batch_size, total_in_api)
        if progress_callback:
            progress_callback(scanned, total_in_api, len(matched_items))

        # Fetch remaining pages in parallel
        if total_pages > 1:
            lock = threading.Lock()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(fetch_page, p): p
                    for p in range(2, total_pages + 1)
                }

                for future in as_completed(futures):
                    page_data = future.result()
                    items = page_data.get("items", [])

                    for item in items:
                        match = check_item(item)
                        if match:
                            with lock:
                                matched_items.append(match)

                    with lock:
                        scanned = min(scanned + batch_size, total_in_api)
                        if progress_callback:
                            progress_callback(scanned, total_in_api, len(matched_items))

        # Return all matched results (frontend handles pagination)
        return {
            "items": matched_items,
            "totalCount": len(matched_items),
        }

    def _get_asset(self, asset_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single asset by ID to get its full content."""
        try:
            resp = requests.get(
                f"{self.rest_base_uri}/asset/v1/content/assets/{asset_id}",
                headers=self._headers(),
                timeout=15,
            )
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return None

    def is_configured(self) -> bool:
        """Check if the required SFMC credentials are set."""
        return all([self.client_id, self.client_secret, self.auth_base_uri, self.rest_base_uri])

    # ------------------------------------------------------------------
    # Journey Lookup
    # ------------------------------------------------------------------

    def get_journeys_for_assets(self, asset_ids: List[int]) -> Dict[int, List[Dict[str, str]]]:
        """
        Find which journeys reference the given asset IDs.

        Returns a dict mapping asset_id -> list of {name, status, id}.
        """
        if not asset_ids:
            return {}

        # Fetch journeys (paginate through all)
        journeys = self._fetch_all_journeys()

        # Build a mapping: asset_id -> journeys that use it
        asset_journeys: Dict[int, List[Dict[str, str]]] = {aid: [] for aid in asset_ids}
        asset_id_set = set(asset_ids)

        for journey in journeys:
            j_name = journey.get("name", "")
            j_status = journey.get("status", "")
            j_id = journey.get("id", "")

            # Look through activities for email asset references
            activities = journey.get("activities", [])
            for activity in activities:
                config_args = activity.get("configurationArguments", {}) or {}
                # Check all known locations where email/asset ID can be stored
                candidate_ids = set()

                # triggeredSend.emailId (classic journey email)
                ts = config_args.get("triggeredSend") or {}
                if ts.get("emailId"):
                    candidate_ids.add(str(ts["emailId"]))

                # Direct assetId field
                if config_args.get("assetId"):
                    candidate_ids.add(str(config_args["assetId"]))

                # Direct emailId field
                if config_args.get("emailId"):
                    candidate_ids.add(str(config_args["emailId"]))

                # Nested asset object (newer journey format)
                asset_obj = config_args.get("asset") or {}
                if asset_obj.get("id"):
                    candidate_ids.add(str(asset_obj["id"]))
                if asset_obj.get("itemId"):
                    candidate_ids.add(str(asset_obj["itemId"]))

                # Check outcomes for referenced assets
                outcomes = activity.get("outcomes", []) or []
                for outcome in outcomes:
                    om = outcome.get("metaData") or {}
                    if om.get("assetId"):
                        candidate_ids.add(str(om["assetId"]))

                for cid in candidate_ids:
                    try:
                        int_id = int(cid)
                    except (ValueError, TypeError):
                        continue
                    if int_id in asset_id_set:
                        asset_journeys[int_id].append({
                            "name": j_name,
                            "status": j_status,
                            "id": j_id,
                        })

        # Deduplicate (a journey might reference same email in multiple activities)
        for aid in asset_journeys:
            seen = set()
            deduped = []
            for j in asset_journeys[aid]:
                key = j["id"]
                if key not in seen:
                    seen.add(key)
                    deduped.append(j)
            asset_journeys[aid] = deduped

        return asset_journeys

    def _fetch_all_journeys(self) -> List[Dict[str, Any]]:
        """Fetch all journeys from Journey Builder API (paginated)."""
        all_journeys: List[Dict[str, Any]] = []
        page = 1
        page_size = 50

        while True:
            resp = requests.get(
                f"{self.rest_base_uri}/interaction/v1/interactions",
                headers=self._headers(),
                params={"$page": page, "$pageSize": page_size},
                timeout=30,
            )
            if not resp.ok:
                break
            data = resp.json()
            items = data.get("items", [])
            all_journeys.extend(items)
            total = data.get("count", 0)
            if page * page_size >= total or not items:
                break
            page += 1

        return all_journeys

    # ------------------------------------------------------------------
    # Content Block -> Email Mapping
    # ------------------------------------------------------------------

    def get_emails_for_content_blocks(
        self,
        content_blocks: List[Dict[str, Any]],
        progress_callback=None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        Find which emails reference the given content blocks.

        Scans all email assets for ContentBlockByName/ContentBlockById/ContentBlockByKey
        references matching the given content block names or IDs.

        Args:
            content_blocks: List of content block dicts with 'id' and 'name' keys.
            progress_callback: Optional callable(scanned, total) for progress updates.

        Returns:
            Dict mapping content_block_id -> list of email dicts {id, name, assetType}.
        """
        import re
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        if not content_blocks:
            return {}

        # Build lookup sets for matching
        cb_names_lower = {}  # lower(name) -> content_block_id
        cb_ids = {}  # str(id) -> content_block_id
        for cb in content_blocks:
            cb_names_lower[cb["name"].lower()] = cb["id"]
            cb_ids[str(cb["id"])] = cb["id"]

        # Pattern to match ContentBlockByName("..."), ContentBlockById(...), ContentBlockByKey("...")
        # Handles quoted strings (with spaces, backslashes for paths) and unquoted numeric IDs
        pattern_quoted = re.compile(
            r'ContentBlockBy(Name|Id|Key)\s*\(\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        pattern_unquoted = re.compile(
            r'ContentBlockBy(Name|Id|Key)\s*\(\s*([^"\',\)\s]+)',
            re.IGNORECASE,
        )

        # Fetch all email assets (types 208 and 207)
        batch_size = 100
        max_workers = 5
        email_type_filter = {
            "property": "assetType.id",
            "simpleOperator": "in",
            "value": [208, 207],
        }

        def fetch_email_page(page_num: int) -> Dict[str, Any]:
            request_body = {
                "page": {"page": page_num, "pageSize": batch_size},
                "query": email_type_filter,
            }
            for attempt in range(3):
                try:
                    resp = requests.post(
                        f"{self.rest_base_uri}/asset/v1/content/assets/query",
                        headers=self._headers(),
                        json=request_body,
                        timeout=120,
                    )
                    if resp.ok:
                        return resp.json()
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.ReadTimeout):
                    if attempt < 2:
                        import time as _t
                        _t.sleep(2 ** attempt)
            return {"items": [], "count": 0}

        # Get first page and total
        first_data = fetch_email_page(1)
        total_emails = first_data.get("count", 0)
        total_pages = (total_emails + batch_size - 1) // batch_size if total_emails > 0 else 1

        # Result mapping: content_block_id -> list of parent emails
        mapping: Dict[int, List[Dict[str, Any]]] = {cb["id"]: [] for cb in content_blocks}
        lock = threading.Lock()
        scanned = [0]

        def process_email_items(items: List[Dict[str, Any]]):
            for item in items:
                # Gather all content from the email
                all_content = []
                views = item.get("views") or {}
                for view_val in views.values():
                    if isinstance(view_val, dict):
                        c = view_val.get("content", "") or ""
                        if c:
                            all_content.append(c)
                top_content = item.get("content", "") or ""
                if top_content:
                    all_content.append(top_content)

                full_text = "\n".join(all_content)
                if not full_text:
                    continue

                # Find all ContentBlock references in this email
                matches = pattern_quoted.findall(full_text) + pattern_unquoted.findall(full_text)
                matched_cb_ids = set()
                for match_type, match_value in matches:
                    match_type_lower = match_type.lower()
                    match_value = match_value.strip()
                    if match_type_lower == "name":
                        # Try full path match first, then just the last segment
                        # SFMC paths use backslash: "Content Builder\folder\block"
                        cb_id = cb_names_lower.get(match_value.lower())
                        if not cb_id:
                            # Extract last segment of path
                            last_segment = match_value.rsplit("\\", 1)[-1].strip()
                            cb_id = cb_names_lower.get(last_segment.lower())
                        if cb_id:
                            matched_cb_ids.add(cb_id)
                    elif match_type_lower == "id":
                        cb_id = cb_ids.get(match_value)
                        if cb_id:
                            matched_cb_ids.add(cb_id)
                    elif match_type_lower == "key":
                        # Key might match name in some cases
                        cb_id = cb_names_lower.get(match_value.lower())
                        if not cb_id:
                            last_segment = match_value.rsplit("\\", 1)[-1].strip()
                            cb_id = cb_names_lower.get(last_segment.lower())
                        if cb_id:
                            matched_cb_ids.add(cb_id)

                if matched_cb_ids:
                    email_info = {
                        "id": item.get("id"),
                        "name": item.get("name", ""),
                        "assetType": item.get("assetType", {}).get("name", ""),
                    }
                    with lock:
                        for cb_id in matched_cb_ids:
                            if cb_id in mapping:
                                mapping[cb_id].append(email_info)

        # Process first page
        process_email_items(first_data.get("items", []))
        scanned[0] = min(batch_size, total_emails)
        if progress_callback:
            progress_callback(scanned[0], total_emails)

        # Fetch remaining pages in parallel
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(fetch_email_page, p): p
                    for p in range(2, total_pages + 1)
                }
                for future in as_completed(futures):
                    page_data = future.result()
                    process_email_items(page_data.get("items", []))
                    with lock:
                        scanned[0] = min(scanned[0] + batch_size, total_emails)
                        if progress_callback:
                            progress_callback(scanned[0], total_emails)

        return mapping
