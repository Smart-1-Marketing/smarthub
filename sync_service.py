from simvoly_client import SimvolyClient, mock_projects
from db import (
    init_db,
    start_sync,
    finish_sync,
    upsert_plan,
    upsert_template,
    upsert_project,
    replace_websites,
    known_project_ids,
)


def sync_catalog():
    init_db()
    run = start_sync()
    client = SimvolyClient()
    plan_count = template_count = 0
    try:
        plans = client.list_plans()
        for p in plans:
            upsert_plan(p)
        plan_count = len(plans)

        templates = client.list_templates()
        for t in templates:
            upsert_template(t)
        template_count = len(templates)

        project_count = 0
        if client.s.mock_mode:
            for p in mock_projects():
                upsert_project(p, source="MOCK")
                project_count += 1

        details = (
            "Catalog synced. The official Platform API does not document a reseller-wide project-list endpoint. "
            "Use Inventory Import or Discover Customer to seed existing projects, then refresh each project via the official API."
            if not client.s.mock_mode
            else "Mock catalog and four demo projects synced."
        )
        finish_sync(run, "SUCCESS", project_count, plan_count, template_count, details)
        return {
            "projects": project_count,
            "plans": plan_count,
            "templates": template_count,
            "details": details,
        }
    except Exception as exc:
        finish_sync(run, "FAILED", 0, plan_count, template_count, str(exc))
        raise


def sync_project(project_id):
    init_db()
    client = SimvolyClient()
    project = client.get_project(project_id)
    upsert_project(project, source="PLATFORM_API")

    # The documented project detail may include `sites`; also call the dedicated
    # websites endpoint because it is authoritative and works for multiple sites/funnels.
    websites = client.list_project_websites(project_id)
    replace_websites(project_id, websites)
    return {"project": project, "websites": len(websites)}


def discover_customer(identifier_type, value):
    init_db()
    client = SimvolyClient()
    projects = client.list_projects_for_customer(identifier_type, value)
    imported = 0
    refreshed = 0
    for p in projects:
        upsert_project(p, source="CUSTOMER_DISCOVERY")
        imported += 1
        try:
            sync_project(p.get("id"))
            refreshed += 1
        except Exception:
            # Keep the discovered summary even if one detail request fails.
            pass
    return {"imported": imported, "refreshed": refreshed}


def _extract_project_items(payload):
    """Accept management-panel list-projects responses, arrays of responses, or raw item arrays."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(payload.get("items"), list):
            return payload["items"]
        # A single project object.
        if payload.get("id") is not None and payload.get("name") is not None:
            return [payload]
        return []

    if isinstance(payload, list):
        # Could be raw project items or a list of page-response objects.
        out = []
        for item in payload:
            if isinstance(item, dict) and item.get("id") is not None and item.get("name") is not None:
                out.append(item)
            else:
                out.extend(_extract_project_items(item))
        return out
    return []


def import_inventory(payload):
    init_db()
    items = _extract_project_items(payload)
    unique = {}
    for p in items:
        if p.get("id") is not None:
            unique[str(p["id"])] = p
    for p in unique.values():
        upsert_project(p, source="INVENTORY_IMPORT")
    return len(unique)


def refresh_known_projects(limit=None):
    """Refresh project details with official API for IDs already in the local registry.

    This is intentionally separate from catalog sync because large portfolios can mean
    thousands of API requests (project + website list for each project).
    """
    ids = known_project_ids()
    if limit:
        ids = ids[: int(limit)]
    ok = failed = 0
    errors = []
    for pid in ids:
        try:
            sync_project(pid)
            ok += 1
        except Exception as exc:
            failed += 1
            if len(errors) < 10:
                errors.append(f"{pid}: {exc}")
    return {"ok": ok, "failed": failed, "errors": errors}
