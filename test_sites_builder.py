"""Focused checks for the Smart 1 Sites Builder.

Run with: python test_sites_builder.py
"""
import pathlib

from flask import Flask
import hub as hub_package
from hub import sites_builder as sb
from hub.sites_builder_routes import register

passed = failed = 0


def check(label, value):
    global passed, failed
    if value:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


print("\nSmart 1 Sites Builder\n---------------------")

restaurant = sb.recipe_for("family pizza restaurant", "wings and catering")
check("restaurant plans include the menu and online ordering",
      "Menu" in restaurant["pages"] and "Order Online" in restaurant["pages"])

home = sb.recipe_for("HVAC contractor", "heating and cooling repair")
check("home services plans include a service area",
      "Service Area" in home["pages"])

plan = sb.fallback_plan({
    "business_name": "Riverstone Heating",
    "business_type": "HVAC company",
    "city": "Columbus",
    "goal": "quotes",
    "description": "Fast, friendly help for homeowners.",
})
check("the concept is visibly personalized",
      plan["business_name"] == "Riverstone Heating" and
      "Columbus" in plan["headline"] and plan["cta"] == "Get My Free Quote")
check("the rules path never fabricates ratings or awards",
      not any(word in str(plan).lower() for word in
              ("five-star", "award-winning", "#1", "guaranteed")))

rows = [
    {"id": 1, "name": "Restaurant Table", "categories": "food restaurant",
     "visible": True, "custom": False, "thumbnail": "a", "preview_url": "p"},
    {"id": 2, "name": "Smart 1 HVAC Pro", "categories": "home services heating cooling",
     "visible": True, "custom": True, "thumbnail": "b", "preview_url": "p"},
]
ranked = sb.rank_templates(rows, {"business_type": "HVAC heating company",
                                  "description": "cooling repair", "goal": "quotes"})
check("the relevant design outranks an unrelated one", ranked[0]["id"] == 2)

root = pathlib.Path(__file__).parent
tools = (root / "hub/templates/tools.html").read_text(encoding="utf-8")
page = (root / "hub/templates/sites_builder.html").read_text(encoding="utf-8")
routes = (root / "hub/sites_builder_routes.py").read_text(encoding="utf-8")
check("Client Tools visibly offers the builder", "Smart 1 Sites Builder" in tools)
check("the tool produces a visual website rather than showing data",
      'id="sb-browser"' in page and "<pre" not in page.lower() and
      "structured data" not in page.lower())
check("the preview endpoint is protected by one blueprint-wide login gate",
      "@bp.before_request" in routes and "/api/sites-builder/preview" in routes)
check("the result offers a proposal handoff", "/sales/builder/" in page)
check("the customer-facing copy avoids developer terminology",
      not any(term in page.lower() for term in
              ("template id", "json response", "api key", "developer mode")))

app = Flask(__name__, template_folder=str(root / "hub/templates"))
app.secret_key = "sites-builder-test"
register(app)
original_user = hub_package.current_user
try:
    hub_package.current_user = lambda: "Test User"
    response = app.test_client().get("/tools/sites-builder")
    check("the authenticated Hub route renders the complete experience",
          response.status_code == 200 and
          b"Give your next lead a website" in response.data)
finally:
    hub_package.current_user = original_user

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
