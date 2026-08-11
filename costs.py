"""Parse a Simvoly billing/charges export into per-site platform costs.

Accepts the Management-Panel style export where each charge is three lines:
    dd/mm/yyyy
    $<amount>
    <subdomain> [project id: NNN] - Payment for <Plan> (monthly)

Refunds (negative amounts) net against payments for the same project, so a
site that was charged then refunded ends at its true net cost.
"""
import re

_AMOUNT = re.compile(r"^\$?\s*(-?[\d,]+(?:\.\d+)?)\s*$")
_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}\s*$")
_SITE = re.compile(
    r"\[project id:\s*(\d+)\]\s*-\s*-?\s*(?:Payment|Refund) for .+?\((?:monthly|yearly)\)",
    re.IGNORECASE,
)


def parse_charges(text):
    """Return (net_costs_by_project_id, matched_line_count).

    net_costs_by_project_id maps str(project_id) -> float (net $/period).
    Lines that are not site charges (reseller fees, add-ons) are ignored.
    """
    lines = [l.strip() for l in (text or "").splitlines()]
    net = {}
    matched = 0
    n = len(lines)
    i = 0
    while i < n:
        if _DATE.match(lines[i]):
            amount = lines[i + 1] if i + 1 < n else ""
            desc = lines[i + 2] if i + 2 < n else ""
            am = _AMOUNT.match(amount)
            sm = _SITE.search(desc)
            if am and sm:
                pid = sm.group(1)
                net[pid] = round(net.get(pid, 0.0) + float(am.group(1).replace(",", "")), 2)
                matched += 1
            i += 3
        else:
            i += 1
    return net, matched
