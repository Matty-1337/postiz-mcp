"""
Postiz MCP Server — Social media scheduling for Delta Kinetics / CoreTAP.
Wraps the Postiz REST API deployed at Railway.
"""
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from enum import Enum

import httpx
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("postiz_mcp")

# ── Configuration ──
POSTIZ_URL = os.environ.get("POSTIZ_URL", "https://postiz-production-3ded.up.railway.app")
POSTIZ_API_KEY = os.environ.get("POSTIZ_API_KEY", "")
PORT = int(os.environ.get("PORT", "8000"))

# ── HTTP Client ──
def _get_headers():
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if POSTIZ_API_KEY:
        headers["Authorization"] = POSTIZ_API_KEY
    return headers

async def _api_request(method: str, path: str, data: dict = None, params: dict = None) -> dict:
    url = f"{POSTIZ_URL}/api/public/v1{path}" if not path.startswith("http") else path
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if method == "GET":
                resp = await client.get(url, headers=_get_headers(), params=params)
            elif method == "POST":
                resp = await client.post(url, headers=_get_headers(), json=data)
            elif method == "PUT":
                resp = await client.put(url, headers=_get_headers(), json=data)
            elif method == "DELETE":
                resp = await client.delete(url, headers=_get_headers())
            else:
                return {"error": f"Unsupported method: {method}"}

            resp.raise_for_status()

            if resp.status_code == 204:
                return {"status": "success"}

            try:
                return resp.json()
            except Exception:
                return {"status": "success", "text": resp.text}

        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text
            except Exception:
                pass
            return {"error": f"HTTP {e.response.status_code}", "detail": error_body}
        except httpx.TimeoutException:
            return {"error": "Request timed out. Postiz may be slow — try again."}
        except Exception as e:
            return {"error": f"Request failed: {str(e)}"}

def _format_response(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)

# ── MCP Server ──
mcp = FastMCP("postiz_mcp", host="0.0.0.0", port=PORT)

# ═══════════════════════════════════════════════════════════
# Tool: List connected social media integrations
# ═══════════════════════════════════════════════════════════

@mcp.tool(
    name="postiz_list_integrations",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_list_integrations() -> str:
    """List all connected social media accounts in Postiz.

    Returns connected platforms (Instagram, LinkedIn, X, etc.) with their
    account names and IDs. Use integration IDs when creating posts.
    """
    result = await _api_request("GET", "/integrations")
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Create and schedule a post
# ═══════════════════════════════════════════════════════════

class CreatePostInput(BaseModel):
    """Input for creating a scheduled social media post."""
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(
        ...,
        description="The post caption/text content. Include hashtags at the end.",
        min_length=1,
        max_length=2200
    )
    integration_id: str = Field(
        ...,
        description="The Postiz integration ID for the target platform. Get from postiz_list_integrations."
    )
    schedule_date: str = Field(
        ...,
        description="ISO 8601 datetime for when to publish. Example: '2026-03-17T12:00:00-05:00' for noon CT."
    )
    image_url: Optional[str] = Field(
        default=None,
        description="URL of an image to attach. Use OpenAI Image Gen MCP URLs or any public image URL."
    )
    type: Optional[str] = Field(
        default="post",
        description="Post type: 'post', 'reel', 'story', or 'carousel'"
    )

@mcp.tool(
    name="postiz_create_post",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def postiz_create_post(params: CreatePostInput) -> str:
    """Create and schedule a social media post in Postiz.

    Creates a post with the given content, attaches optional media,
    and schedules it for the specified date/time. The post will be
    automatically published by Postiz at the scheduled time.

    Workflow:
    1. Call postiz_list_integrations to get integration IDs
    2. Generate image via OpenAI Image Gen MCP if needed
    3. Call this tool with content, integration_id, schedule_date, and image_url
    """
    payload = {
        "content": [{"content": params.content}],
        "integration": params.integration_id,
        "date": params.schedule_date,
        "type": params.type or "post",
    }

    if params.image_url:
        payload["media"] = [{"url": params.image_url}]

    result = await _api_request("POST", "/posts", data=payload)
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: List scheduled/published posts
# ═══════════════════════════════════════════════════════════

class ListPostsInput(BaseModel):
    """Input for listing posts."""
    model_config = ConfigDict(str_strip_whitespace=True)

    start_date: Optional[str] = Field(
        default=None,
        description="Start date in ISO 8601 format. Defaults to 90 days ago. Example: '2026-01-01T00:00:00Z'"
    )
    end_date: Optional[str] = Field(
        default=None,
        description="End date in ISO 8601 format. Defaults to 90 days from now. Example: '2026-12-31T23:59:59Z'"
    )
    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'draft', 'scheduled', 'published', 'error'. Leave empty for all."
    )
    limit: Optional[int] = Field(
        default=20,
        description="Maximum number of posts to return.",
        ge=1, le=100
    )

@mcp.tool(
    name="postiz_list_posts",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_list_posts(params: ListPostsInput) -> str:
    """List scheduled and published posts in Postiz.

    Returns posts with their status, scheduled date, content preview,
    and platform. Use to check what's queued, what's been published,
    and identify gaps in the content calendar.
    """
    now = datetime.now(timezone.utc)
    start = params.start_date or (now - timedelta(days=90)).isoformat()
    end = params.end_date or (now + timedelta(days=90)).isoformat()

    query_params = {"startDate": start, "endDate": end}
    if params.status:
        query_params["status"] = params.status
    if params.limit:
        query_params["limit"] = params.limit

    result = await _api_request("GET", "/posts", params=query_params)
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Delete a post
# ═══════════════════════════════════════════════════════════

class DeletePostInput(BaseModel):
    """Input for deleting a post."""
    post_id: str = Field(..., description="The Postiz post ID to delete.")

@mcp.tool(
    name="postiz_delete_post",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_delete_post(params: DeletePostInput) -> str:
    """Delete a scheduled or draft post from Postiz.

    Permanently removes a post. Cannot delete already-published posts.
    """
    result = await _api_request("DELETE", f"/posts/{params.post_id}")
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Update a post
# ═══════════════════════════════════════════════════════════

class UpdatePostInput(BaseModel):
    """Input for updating an existing post."""
    model_config = ConfigDict(str_strip_whitespace=True)

    post_id: str = Field(..., description="The Postiz post ID to update.")
    content: Optional[str] = Field(default=None, description="New caption/text content.", max_length=2200)
    schedule_date: Optional[str] = Field(default=None, description="New scheduled date in ISO 8601 format.")
    image_url: Optional[str] = Field(default=None, description="New image URL to replace existing media.")

@mcp.tool(
    name="postiz_update_post",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_update_post(params: UpdatePostInput) -> str:
    """Update a scheduled or draft post in Postiz.

    Modify the content, schedule date, or media of an existing post.
    Only provided fields will be updated — omitted fields stay unchanged.
    """
    payload = {}
    if params.content:
        payload["content"] = params.content
    if params.schedule_date:
        payload["date"] = params.schedule_date
    if params.image_url:
        payload["media"] = [{"url": params.image_url}]

    result = await _api_request("PUT", f"/posts/{params.post_id}", data=payload)
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Get post analytics
# ═══════════════════════════════════════════════════════════

@mcp.tool(
    name="postiz_get_analytics",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_get_analytics() -> str:
    """Get social media analytics and engagement metrics from Postiz.

    Returns aggregate metrics across connected platforms including
    impressions, engagement, clicks, and follower growth.
    """
    result = await _api_request("GET", "/analytics")
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Upload media
# ═══════════════════════════════════════════════════════════

class UploadMediaInput(BaseModel):
    """Input for uploading media to Postiz."""
    url: str = Field(..., description="Public URL of the image/video to upload to Postiz media library.")
    name: Optional[str] = Field(default=None, description="Optional filename for the media.")

@mcp.tool(
    name="postiz_upload_media",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def postiz_upload_media(params: UploadMediaInput) -> str:
    """Upload media to Postiz media library from a URL.

    Downloads the image/video from the given URL and stores it in Postiz.
    Returns a media ID that can be used when creating posts.
    Use this for images generated by the OpenAI Image Gen MCP.
    """
    payload = {"url": params.url}
    if params.name:
        payload["name"] = params.name

    result = await _api_request("POST", "/media", data=payload)
    return _format_response(result)


# ═══════════════════════════════════════════════════════════
# Tool: Quick status check
# ═══════════════════════════════════════════════════════════

@mcp.tool(
    name="postiz_status",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def postiz_status() -> str:
    """Check Postiz server health and connection status.

    Verifies the Postiz instance is reachable and returns basic status
    information including connected integrations count.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{POSTIZ_URL}/api/public/v1/integrations", headers=_get_headers())
            if resp.status_code == 200:
                integrations = resp.json()
                count = len(integrations) if isinstance(integrations, list) else 0
                return json.dumps({
                    "status": "connected",
                    "postiz_url": POSTIZ_URL,
                    "integrations_count": count,
                    "server": "postiz_mcp"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "error",
                    "http_status": resp.status_code,
                    "detail": "Could not reach Postiz API"
                }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "unreachable",
            "error": str(e),
            "postiz_url": POSTIZ_URL
        }, indent=2)


# ── Run ──
if __name__ == "__main__":
    logger.info(f"Starting Postiz MCP Server on port {PORT}")
    logger.info(f"Postiz URL: {POSTIZ_URL}")
    mcp.run(transport="sse")
