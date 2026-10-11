"""The program shown on screen, in the real call shapes of the monty flat namespace."""

PROGRAM = r'''
import re
log = await git("log", format="%s", _=["v1.4..HEAD"])
ids = re.findall(r"TCK-\d+", log["stdout"])
pending = [tickets_get_ticket(id=i) for i in ids]
hits = []
for p in pending:
    ticket = await p
    if ticket["reported_by_customer"]:
        customer = await customers_get_customer(id=ticket["customer_id"])
        if await policy_sla_breach(ticket=ticket, customer=customer):
            hits.append({"customer": customer["name"], "ticket": ticket["id"]})
return {"commits": len(ids), "fixed_for": hits}
'''
