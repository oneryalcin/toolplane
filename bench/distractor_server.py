"""Distractor stdio MCP server for the M-axis benchmark (#107 item 8).

Simulates the *other* servers in a realistic multi-server setup: plausible
tool surfaces (CRM, calendar, tickets, ...) whose definitions occupy client
context but are irrelevant to the benchmark task. Every tool returns an
inert empty result so an agent that strays gets nothing and moves on.

Profile is selected with DISTRACTOR_PROFILE; the MCP server name (and thus
the client-visible namespace) comes from the client config key, so one
profile can back several configured "workspaces".
"""

from __future__ import annotations

import inspect
import os
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

# profile -> list of (tool_name, description, [(param, type, description), ...])
PROFILES: dict[str, list[tuple[str, str, list[tuple[str, type, str]]]]] = {
    "crm": [
        (
            "search_contacts",
            "Search CRM contacts by free text across name, email, company and "
            "notes. Returns contact summaries ordered by relevance.",
            [
                ("query", str, "Free-text search over name, email, company."),
                ("limit", int, "Maximum number of results to return."),
            ],
        ),
        (
            "get_contact",
            "Fetch the full CRM record for one contact, including deal "
            "history, owner, lifecycle stage and custom fields.",
            [("contact_id", str, "The CRM contact identifier, e.g. CT-1042.")],
        ),
        (
            "list_deals",
            "List deals in the pipeline, optionally filtered by stage and "
            "owner. Returns deal id, amount, stage and close date.",
            [
                ("stage", str, "Pipeline stage name, e.g. 'negotiation'."),
                ("owner", str, "Email of the deal owner to filter by."),
            ],
        ),
        (
            "create_note",
            "Attach a timestamped note to a contact or deal record.",
            [
                ("record_id", str, "Contact or deal id the note attaches to."),
                ("body", str, "Markdown body of the note."),
            ],
        ),
        (
            "list_companies",
            "List company accounts with industry, employee count and open "
            "deal totals.",
            [("industry", str, "Industry to filter companies by.")],
        ),
        (
            "log_activity",
            "Log a call, email or meeting against a contact for reporting.",
            [
                ("contact_id", str, "Contact the activity involves."),
                ("kind", str, "Activity type: call, email or meeting."),
                ("summary", str, "One-line summary of the activity."),
            ],
        ),
        (
            "pipeline_report",
            "Aggregate pipeline value by stage for a date range.",
            [
                ("start_date", str, "ISO date the report window opens."),
                ("end_date", str, "ISO date the report window closes."),
            ],
        ),
    ],
    "calendar": [
        (
            "list_events",
            "List calendar events between two datetimes for the current "
            "user, including attendees and conferencing links.",
            [
                ("start", str, "ISO datetime lower bound."),
                ("end", str, "ISO datetime upper bound."),
            ],
        ),
        (
            "get_event",
            "Fetch one calendar event with full attendee response status.",
            [("event_id", str, "Calendar event identifier.")],
        ),
        (
            "create_event",
            "Create a calendar event and invite attendees.",
            [
                ("title", str, "Event title."),
                ("start", str, "ISO start datetime."),
                ("duration_minutes", int, "Length of the event in minutes."),
                ("attendees", str, "Comma-separated attendee emails."),
            ],
        ),
        (
            "find_free_slot",
            "Find the earliest slot where all attendees are free within a "
            "search window.",
            [
                ("attendees", str, "Comma-separated attendee emails."),
                ("duration_minutes", int, "Required slot length in minutes."),
                ("window_days", int, "How many days ahead to search."),
            ],
        ),
        (
            "respond_to_event",
            "Accept, decline or tentatively respond to an invitation.",
            [
                ("event_id", str, "Calendar event identifier."),
                ("response", str, "One of accepted, declined, tentative."),
            ],
        ),
        (
            "list_calendars",
            "List calendars the current user can read or write.",
            [("include_shared", bool, "Include calendars shared with the user.")],
        ),
    ],
    "tickets": [
        (
            "search_tickets",
            "Search support tickets by free text over subject and body.",
            [
                ("query", str, "Free-text search string."),
                ("status", str, "Filter: open, pending, solved or any."),
            ],
        ),
        (
            "get_ticket",
            "Fetch one ticket with its full comment thread and audit trail.",
            [("ticket_id", int, "Numeric ticket identifier.")],
        ),
        (
            "create_ticket",
            "Open a new support ticket on behalf of a requester.",
            [
                ("subject", str, "Ticket subject line."),
                ("body", str, "Initial ticket description."),
                ("priority", str, "One of low, normal, high, urgent."),
            ],
        ),
        (
            "add_comment",
            "Append a public or internal comment to a ticket.",
            [
                ("ticket_id", int, "Ticket to comment on."),
                ("body", str, "Comment body, markdown supported."),
                ("internal", bool, "True for an agent-only internal note."),
            ],
        ),
        (
            "assign_ticket",
            "Assign a ticket to an agent or group queue.",
            [
                ("ticket_id", int, "Ticket to assign."),
                ("assignee", str, "Agent email or group name."),
            ],
        ),
        (
            "ticket_metrics",
            "Report ticket volume and first-response times for a period.",
            [
                ("start_date", str, "ISO date the report window opens."),
                ("end_date", str, "ISO date the report window closes."),
            ],
        ),
    ],
    "wiki": [
        (
            "search_pages",
            "Full-text search over wiki pages, returning title, space and a "
            "highlighted snippet per hit.",
            [
                ("query", str, "Full-text search string."),
                ("space", str, "Wiki space key to restrict the search to."),
            ],
        ),
        (
            "get_page",
            "Fetch a wiki page body as markdown with metadata and labels.",
            [("page_id", str, "Wiki page identifier.")],
        ),
        (
            "create_page",
            "Create a wiki page under a parent in a given space.",
            [
                ("space", str, "Wiki space key."),
                ("title", str, "Page title."),
                ("body", str, "Markdown page body."),
            ],
        ),
        (
            "update_page",
            "Replace a wiki page body, bumping its version.",
            [
                ("page_id", str, "Page to update."),
                ("body", str, "New markdown body."),
            ],
        ),
        (
            "list_recent_changes",
            "List recently changed pages across the wiki with author and "
            "change summary.",
            [("limit", int, "Maximum number of changes to return.")],
        ),
    ],
    "payments": [
        (
            "list_charges",
            "List payment charges with amount, currency, status and "
            "customer, newest first.",
            [
                ("status", str, "Filter: succeeded, pending, failed or any."),
                ("limit", int, "Maximum number of charges to return."),
            ],
        ),
        (
            "get_charge",
            "Fetch one charge with card details, receipt URL and dispute "
            "status.",
            [("charge_id", str, "Charge identifier, e.g. ch_3Nx...")],
        ),
        (
            "create_refund",
            "Refund a charge fully or partially.",
            [
                ("charge_id", str, "Charge to refund."),
                ("amount_cents", int, "Amount in minor units; 0 = full refund."),
                ("reason", str, "Refund reason for the ledger."),
            ],
        ),
        (
            "list_subscriptions",
            "List active subscriptions with plan, status and renewal date.",
            [("customer_id", str, "Customer to list subscriptions for.")],
        ),
        (
            "revenue_report",
            "Aggregate gross and net revenue by day for a date range.",
            [
                ("start_date", str, "ISO date the report window opens."),
                ("end_date", str, "ISO date the report window closes."),
            ],
        ),
        (
            "get_customer",
            "Fetch a payments customer with default source and balance.",
            [("customer_id", str, "Customer identifier, e.g. cus_9xj...")],
        ),
    ],
    "analytics": [
        (
            "run_query",
            "Run a saved analytics query by name with parameter overrides "
            "and return rows as JSON.",
            [
                ("query_name", str, "Name of the saved query to run."),
                ("params_json", str, "JSON object of parameter overrides."),
            ],
        ),
        (
            "list_dashboards",
            "List analytics dashboards with owner and last-refresh time.",
            [("folder", str, "Dashboard folder to list, empty for all.")],
        ),
        (
            "get_metric",
            "Fetch a single metric time series between two dates.",
            [
                ("metric", str, "Metric key, e.g. weekly_active_users."),
                ("start_date", str, "ISO date lower bound."),
                ("end_date", str, "ISO date upper bound."),
                ("granularity", str, "One of day, week, month."),
            ],
        ),
        (
            "list_events_schema",
            "Describe the tracked product-event schema: event names and "
            "their property types.",
            [("prefix", str, "Only events whose name starts with this.")],
        ),
        (
            "funnel_report",
            "Compute a conversion funnel across an ordered list of events.",
            [
                ("events", str, "Comma-separated ordered event names."),
                ("window_days", int, "Conversion window in days."),
            ],
        ),
    ],
    "files": [
        (
            "search_files",
            "Search stored documents by name and content.",
            [
                ("query", str, "Search string over file names and content."),
                ("folder", str, "Folder path to restrict the search to."),
            ],
        ),
        (
            "get_file_metadata",
            "Fetch size, owner, modified time and sharing state for a file.",
            [("file_id", str, "File identifier.")],
        ),
        (
            "download_file",
            "Return a short-lived download URL for a file.",
            [("file_id", str, "File identifier.")],
        ),
        (
            "upload_file",
            "Upload a new file into a folder from a URL source.",
            [
                ("folder", str, "Destination folder path."),
                ("name", str, "File name including extension."),
                ("source_url", str, "URL to fetch the file content from."),
            ],
        ),
        (
            "share_file",
            "Grant a user or group access to a file.",
            [
                ("file_id", str, "File to share."),
                ("principal", str, "User email or group name."),
                ("role", str, "One of viewer, commenter, editor."),
            ],
        ),
        (
            "list_folder",
            "List the immediate children of a folder.",
            [("folder", str, "Folder path to list.")],
        ),
    ],
}


def _make_tool(name: str, description: str, params: list[tuple[str, type, str]]):
    annotations = {
        pname: Annotated[ptype, Field(description=pdesc)]
        for pname, ptype, pdesc in params
    }
    signature = inspect.Signature(
        [
            inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY, annotation=annotations[pname]
            )
            for pname, _, _ in params
        ]
    )

    def impl(**kwargs):
        return {"results": [], "note": "no matching records in this workspace"}

    impl.__name__ = name
    impl.__doc__ = description
    impl.__signature__ = signature
    impl.__annotations__ = annotations
    return impl


def build_server(profile: str) -> FastMCP:
    try:
        tools = PROFILES[profile]
    except KeyError:
        raise SystemExit(
            f"unknown DISTRACTOR_PROFILE {profile!r}; choose from {sorted(PROFILES)}"
        )
    mcp = FastMCP(profile)
    for name, description, params in tools:
        mcp.tool(_make_tool(name, description, params))
    return mcp


if __name__ == "__main__":
    build_server(os.environ.get("DISTRACTOR_PROFILE", "crm")).run()
