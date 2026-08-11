import json
from typing import Dict, Any, List, Optional

import requests

from config import SETTINGS

# Simvoly's API docs require every client to identify itself with a User-Agent.
# Without it, white-label CDN/WAF layers (e.g. Cloudflare) frequently answer the
# default "python-requests/x.y" agent with an HTML block/challenge page under a
# 200 status, which is what surfaced as "Simvoly returned a non-JSON response".
USER_AGENT = "Smart1SitesAdmin/3.0 (+https://smart1sites.com)"


class SimvolyError(RuntimeError):
    pass


def unwrap_data(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _summarize_response(response) -> str:
    """Build a short, secret-free description of a response for error messages."""
    content_type = response.headers.get("Content-Type", "unknown")
    snippet = (response.text or "")[:300].replace("\n", " ").strip()
    redirected = ""
    if getattr(response, "history", None):
        redirected = f" (redirected to {response.url})"
    return (
        f"HTTP {response.status_code} {content_type}{redirected} "
        f"from {response.url} — body starts: {snippet!r}"
    )


class SimvolyClient:
    """Official Simvoly Platform Management API client for Smart 1 Sites.

    Normal /api/v1 endpoints authenticate with X-CLIENT-KEY.
    The SSO /api/platform/session endpoint authenticates with Bearer.
    POST bodies are application/x-www-form-urlencoded per Simvoly's docs.
    Every request sends a User-Agent, which Simvoly's docs require.
    """

    def __init__(self):
        self.s = SETTINGS

    def _require_key(self):
        if not self.s.api_key and not self.s.mock_mode:
            raise SimvolyError("SIMVOLY_API_KEY is not configured.")

    def _url(self, path: str) -> str:
        return f"{self.s.api_base_url}/{path.lstrip('/')}"

    def _v1_headers(self) -> Dict[str, str]:
        self._require_key()
        return {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-CLIENT-KEY": self.s.api_key,
        }

    def _sso_headers(self) -> Dict[str, str]:
        self._require_key()
        return {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {self.s.api_key}",
        }

    def _request(self, method: str, path: str, *, params=None, form=None, sso=False):
        if self.s.mock_mode:
            return {"success": True, "mock": True}
        try:
            response = requests.request(
                method.upper(),
                self._url(path),
                headers=self._sso_headers() if sso else self._v1_headers(),
                params=params,
                data=form,
                timeout=self.s.timeout_seconds,
                verify=self.s.verify_ssl,
            )
        except requests.RequestException as exc:
            raise SimvolyError(f"Could not reach Simvoly: {exc}") from exc

        content_type = (response.headers.get("Content-Type") or "").lower()
        looks_like_json = "json" in content_type

        if not response.ok:
            # Give the operator the status, content type, final URL and a body
            # snippet so a 401 (bad key) is not confused with an HTML block page.
            hint = ""
            if response.status_code in (401, 403):
                hint = " — check that SIMVOLY_API_KEY is the valid Platform API key (X-CLIENT-KEY)."
            elif not looks_like_json:
                hint = (
                    " — an HTML/error page usually means SIMVOLY_API_BASE_URL is pointing "
                    "at the wrong host (it must be https://api.<white-label-domain>) or a "
                    "CDN/WAF blocked the request."
                )
            raise SimvolyError(f"Simvoly {_summarize_response(response)}{hint}")

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            # 2xx but not JSON: almost always a wrong base URL (SPA/login/landing
            # page returned with 200) or a WAF challenge page.
            raise SimvolyError(
                "Simvoly returned a non-JSON response. This is a 2xx response whose body "
                "is not JSON, which almost always means SIMVOLY_API_BASE_URL is pointing at "
                "the wrong host (it must be https://api.<white-label-domain>, e.g. "
                "https://api.smart1sites.com) or a CDN/WAF returned a challenge page. "
                f"Details: {_summarize_response(response)}"
            ) from exc

    # ---------- Read-only catalog / project endpoints ----------
    def list_plans(self) -> List[Dict[str, Any]]:
        if self.s.mock_mode:
            return mock_plans()
        payload = self._request("GET", "/api/v1/plans")
        data = unwrap_data(payload)
        if not isinstance(data, list):
            raise SimvolyError("Plan response did not contain a list in data.")
        return data

    def list_templates(self) -> List[Dict[str, Any]]:
        if self.s.mock_mode:
            return mock_templates()
        payload = self._request("GET", "/api/v1/templates")
        data = unwrap_data(payload)
        if not isinstance(data, list):
            raise SimvolyError("Template response did not contain a list.")
        return data

    def list_projects_for_customer(self, identifier_type: str, value: str):
        """Official endpoint is customer-scoped; use one documented identifier."""
        if identifier_type not in {"externalCustomerId", "userId", "customerEmail"}:
            raise SimvolyError("Unsupported project discovery identifier.")
        if not value:
            raise SimvolyError("Customer identifier is required.")
        if self.s.mock_mode:
            return mock_projects()
        payload = self._request("GET", "/api/v1/projects", params={identifier_type: value})
        data = unwrap_data(payload)
        if not isinstance(data, list):
            raise SimvolyError("Customer project response did not contain a list.")
        return data

    def get_project(self, project_id):
        if self.s.mock_mode:
            return mock_project_detail(project_id)
        payload = self._request("GET", f"/api/v1/projects/{project_id}")
        data = unwrap_data(payload)
        if not isinstance(data, dict):
            raise SimvolyError("Project detail response was not an object.")
        return data

    def list_project_websites(self, project_id):
        if self.s.mock_mode:
            return mock_project_websites(project_id)
        payload = self._request("GET", f"/api/v1/projects/{project_id}/websites")
        data = unwrap_data(payload)
        if not isinstance(data, list):
            raise SimvolyError("Project websites response did not contain a list.")
        return data

    def get_website(self, project_id, website_id):
        if self.s.mock_mode:
            items = mock_project_websites(project_id)
            return next((x for x in items if str(x.get("id")) == str(website_id)), {})
        payload = self._request(
            "GET", f"/api/v1/projects/{project_id}/websites/{website_id}"
        )
        data = unwrap_data(payload)
        if not isinstance(data, dict):
            raise SimvolyError("Website detail response was not an object.")
        return data

    def check_limits(self, website_id, plan_id):
        if self.s.mock_mode:
            return {"success": True, "message": "Mock limits check passed."}
        return self._request(
            "GET",
            f"/api/v1/website/{website_id}/check-limits",
            params={"planId": plan_id},
        )

    # ---------- Users ----------
    def create_user(self, name, email, password=None, locale="en"):
        self._require_write()
        form = {"name": name, "email": email, "locale": locale}
        if password:
            form["password"] = password
        return self._request("POST", "/api/v1/users", form=form)

    def search_user(self, email):
        if self.s.mock_mode:
            return {"id": 1, "name": "Mock User", "email": email, "locale": "en"}
        return self._request("POST", "/api/v1/users/search", form={"email": email})

    def get_user(self, user_id):
        if self.s.mock_mode:
            return {"id": user_id, "name": "Mock User", "email": "mock@example.com", "locale": "en"}
        return self._request("GET", f"/api/v1/users/{user_id}")

    def delete_user(self, user_id):
        self._require_write()
        return self._request("DELETE", f"/api/v1/users/{user_id}")

    # ---------- Provisioning ----------
    def create_project_website(self, values: Dict[str, Any]):
        self._require_write()
        form = {
            "templateId": values.get("template_id"),
            "funnelTemplateId": values.get("funnel_template_id"),
            "externalCustomerId": values.get("external_customer_id"),
            "userId": values.get("user_id"),
            "customerFirstName": values.get("first_name"),
            "customerLastName": values.get("last_name"),
            "customerEmail": values.get("email"),
            "customerSubdomain": values.get("subdomain"),
            "websiteName": values.get("site_name"),
            "brandColor": values.get("brand_color"),
            "secondaryColor1": values.get("secondary_color_1"),
            "secondaryColor2": values.get("secondary_color_2"),
            "secondaryColor3": values.get("secondary_color_3"),
            "secondaryColor4": values.get("secondary_color_4"),
            "personalizationTags": values.get("personalization_tags"),
        }
        form = {k: v for k, v in form.items() if v not in (None, "")}
        if not form.get("externalCustomerId") and not form.get("userId"):
            raise SimvolyError("Provide externalCustomerId or userId when creating a project.")
        if self.s.mock_mode:
            return {"success": True, "data": {"websiteId": 999001, "projectId": 999002, "subdomain": values.get("subdomain", "mock-site")}}
        return self._request("POST", "/api/v1/website", form=form)

    def add_website(self, project_id, values):
        self._require_write()
        form = {
            "projectId": project_id,
            "templateId": values.get("template_id"),
            "funnelTemplateId": values.get("funnel_template_id"),
            "customerSubdomain": values.get("subdomain"),
            "brandColor": values.get("brand_color"),
            "secondaryColor1": values.get("secondary_color_1"),
            "secondaryColor2": values.get("secondary_color_2"),
            "secondaryColor3": values.get("secondary_color_3"),
            "secondaryColor4": values.get("secondary_color_4"),
            "personalizationTags": values.get("personalization_tags"),
        }
        form = {k: v for k, v in form.items() if v not in (None, "")}
        return self._request("POST", "/api/v1/website/add", form=form)

    def assign_customer(self, values):
        self._require_write()
        form = {
            "projectId": values.get("project_id"),
            "websiteId": values.get("website_id"),
            "externalCustomerId": values.get("external_customer_id"),
            "userId": values.get("user_id"),
            "customerFirstName": values.get("first_name"),
            "customerLastName": values.get("last_name"),
            "customerEmail": values.get("email"),
        }
        form = {k: v for k, v in form.items() if v not in (None, "")}
        return self._request("POST", "/api/v1/website/assign", form=form)

    def unassign_customer(self, values):
        self._require_write()
        form = {
            "projectId": values.get("project_id"),
            "websiteId": values.get("website_id"),
            "externalCustomerId": values.get("external_customer_id"),
            "userId": values.get("user_id"),
        }
        form = {k: v for k, v in form.items() if v not in (None, "")}
        return self._request("POST", "/api/v1/website/unassign", form=form)

    # ---------- Lifecycle ----------
    def set_project_status(self, project_id, status, plan_id=None, period=None):
        self._require_write()
        if status not in {"ACTIVE", "EXPIRED"}:
            raise SimvolyError("Project status must be ACTIVE or EXPIRED.")
        form = {"status": status}
        if status == "ACTIVE":
            if not plan_id or not period:
                raise SimvolyError("planId and period are required to activate a project.")
            form.update({"planId": plan_id, "period": period})
        return self._request("POST", f"/api/v1/projects/{project_id}/set-status", form=form)

    def activate_project_for_period(self, project_id, plan_id, for_months, continue_as):
        self._require_write()
        return self._request(
            "POST",
            f"/api/v1/projects/{project_id}/activate",
            form={"planId": plan_id, "forMonths": for_months, "continueAs": continue_as},
        )

    def set_website_status(self, website_id, status, plan_id=None, period=None):
        self._require_write()
        if status not in {"ACTIVE", "EXPIRED", "DELETED"}:
            raise SimvolyError("Website status must be ACTIVE, EXPIRED, or DELETED.")
        form = {"status": status}
        if status == "ACTIVE":
            if not plan_id or not period:
                raise SimvolyError("planId and period are required to activate a website.")
            form.update({"planId": plan_id, "period": period})
        return self._request("POST", f"/api/v1/website/{website_id}/set-status", form=form)

    def set_addon(self, project_id, addon_type, value):
        self._require_write()
        return self._request(
            "POST",
            f"/api/v1/projects/{project_id}/set-addon",
            form={"type": addon_type, "value": value},
        )

    # ---------- Domain / personalization ----------
    def connect_domain(self, website_id, domain):
        self._require_write()
        return self._request(
            "POST", f"/api/v1/website/{website_id}/connect-domain", form={"domain": domain}
        )

    def disconnect_domain(self, website_id, domain):
        self._require_write()
        return self._request(
            "POST", f"/api/v1/website/{website_id}/disconnect-domain", form={"domain": domain}
        )

    def set_personalization_tags(self, website_id, tags, colors=None):
        self._require_write()
        colors = colors or {}
        form = {"tags": json.dumps(tags)}
        for api_name, local_name in [
            ("brandColor", "brand_color"),
            ("secondaryColor1", "secondary_color_1"),
            ("secondaryColor2", "secondary_color_2"),
            ("secondaryColor3", "secondary_color_3"),
            ("secondaryColor4", "secondary_color_4"),
        ]:
            if colors.get(local_name):
                form[api_name] = colors[local_name]
        return self._request(
            "POST", f"/api/v1/website/{website_id}/set-personalization-tags", form=form
        )

    # ---------- SSO ----------
    def start_building_session(self, *, project_id, website_id=None, user_email=None, user_id=None, external_customer_id=None, path=None):
        if self.s.mock_mode:
            return {"token": "mock", "accessUrl": "https://manage.smart1sites.com", "createdAt": 0, "expiresAt": 9999999999}
        form = {"projectId": project_id}
        if website_id:
            form["websiteId"] = website_id
        if user_email:
            form["userEmail"] = user_email
        elif user_id:
            form["userId"] = user_id
        elif external_customer_id:
            form["externalCustomerId"] = external_customer_id
        else:
            raise SimvolyError("SSO requires userEmail, userId, or externalCustomerId.")
        if path:
            form["path"] = path
        return self._request("POST", "/api/platform/session", form=form, sso=True)

    def _require_write(self):
        if self.s.mock_mode:
            return
        if not self.s.enable_write_actions:
            raise SimvolyError("Write actions are disabled. Set ENABLE_WRITE_ACTIONS=true after verifying read-only access.")
        self._require_key()


def mock_plans():
    return [
        {"id": 929, "name": "Landing Page - Self Service", "monthlyPrice": 22, "yearlyPrice": 204, "bgMonthlyPrice": 0, "bgYearlyPrice": 0, "basePlan": "MINI_EXTENDED", "hidden": True, "visible": True, "pages": 2, "storage": 1, "bandwidth": 2, "contributors": 1, "storeProducts": 0, "websites": 1},
        {"id": 24, "name": "Starter Site - Self Service", "monthlyPrice": 29, "yearlyPrice": 300, "bgMonthlyPrice": 20, "bgYearlyPrice": 180, "basePlan": "PERSONAL", "hidden": False, "visible": True, "pages": 5, "storage": 2, "bandwidth": 10, "contributors": 2, "storeProducts": 0, "websites": 1},
        {"id": 25, "name": "Business - Self Service", "monthlyPrice": 39, "yearlyPrice": 420, "bgMonthlyPrice": 30, "bgYearlyPrice": 290, "basePlan": "BUSINESS", "hidden": False, "visible": True, "pages": 200, "storage": 25, "bandwidth": 25, "contributors": 5, "storeProducts": 0, "websites": 1},
        {"id": 26, "name": "ECommerce Self Service", "monthlyPrice": 89, "yearlyPrice": 948, "bgMonthlyPrice": 40.83, "bgYearlyPrice": 440, "basePlan": "ECOMMERCE", "hidden": False, "visible": True, "pages": 25, "storage": 50, "bandwidth": 100, "contributors": 10, "storeProducts": 100, "websites": 1},
        {"id": 27, "name": "ECommerce Premium", "monthlyPrice": 129, "yearlyPrice": 1428, "bgMonthlyPrice": 57.5, "bgYearlyPrice": 640, "basePlan": "ECOMMERCE_PLUS", "hidden": False, "visible": True, "pages": 999, "storage": 999, "bandwidth": 999, "contributors": 30, "storeProducts": 999, "websites": 1},
    ]


def mock_templates():
    return [
        {"id": 101, "name": "Smart 1 Business Starter", "primaryCategories": "Website", "categories": "Business", "visible": True, "systemTemplate": False, "previewUrl": "demo.smart1sites.com", "thumb": ""},
        {"id": 102, "name": "Smart 1 Landing Page", "primaryCategories": "Landing Page", "categories": "Business", "visible": True, "systemTemplate": False, "previewUrl": "landing.smart1sites.com", "thumb": ""},
    ]


def mock_projects():
    return [
        {"id": 764630, "name": "Daily Gazette - Brothers That Just Do Gutters", "status": "ACTIVE", "plan": {"name": "Landing Page - Self Service", "id": 929}, "created": "07/13/2026", "billingPeriod": "MONTHLY", "lastBillingDate": "07/16/2026", "nextBillingDate": "08/16/2026", "recurringManualSubscription": True},
        {"id": 764551, "name": "TMRG - Dorr's Dorky Reptiles", "status": "ACTIVE", "plan": {"name": "Starter Site - Self Service", "id": 24}, "created": "07/12/2026", "billingPeriod": "MONTHLY", "lastBillingDate": "07/13/2026", "nextBillingDate": "08/13/2026", "recurringManualSubscription": True},
        {"id": 765533, "name": "Anna's Website", "status": "TRIAL", "plan": {"name": "Trial", "id": 0}, "created": "07/24/2026", "billingPeriod": "MONTHLY"},
        {"id": 763519, "name": "Nicholas's Website", "status": "EXPIRED", "plan": {"name": "Trial", "id": 0}, "created": "06/30/2026", "billingPeriod": "MONTHLY"},
    ]


def mock_project_detail(project_id):
    for p in mock_projects():
        if str(p["id"]) == str(project_id):
            return {"id": p["id"], "name": p["name"], "status": p["status"], "plan": p["plan"], "sites": mock_project_websites(project_id)}
    return {"id": project_id, "name": f"Project {project_id}", "status": "ACTIVE", "plan": {"id": 25, "name": "Business - Self Service"}, "sites": []}


def mock_project_websites(project_id):
    if str(project_id) == "764630":
        return [{"id": 1609104, "domain": "brothersguttersny.com", "name": "Daily Gazette - Brothers That Just Do Gutters", "subdomain": "adops-189", "hasTemplate": True, "type": "WEBSITE"}]
    return []
