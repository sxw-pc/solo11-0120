import json
from .store import connect,TABLES

def report(path):
    db=connect(path)
    try:
        data={name:[dict(row) for row in db.execute("SELECT * FROM "+name)] for name in TABLES}
        data["handoff_chains"]=[dict(row) for row in db.execute("""
            SELECT h.id AS household,h.status AS eligibility,e.item,e.quantity AS entitlement,
                   COALESCE((SELECT SUM(r.quantity) FROM delivery_receipt r WHERE r.household_id=h.id AND r.item=e.item AND r.status IN ('complete','partial')),0) AS delivered,
                   COALESCE((SELECT SUM(c.missing) FROM shortage_case c WHERE c.household_id=h.id AND c.item=e.item AND c.status='open'),0) AS open_shortage,
                   s.sha AS household_source_sha
            FROM household h JOIN entitlement e ON e.household_id=h.id
            JOIN source_blob s ON s.id=h.source_id ORDER BY h.id,e.item
        """)]
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))
