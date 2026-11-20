import json
from .store import connect,TABLES
from .domain import _asset_lendable

def _equipment(db):
    """Per-machine disposition ledger: who owns it, who physically holds it,
    whether a loan is settled, filter condition, next lendability, and the
    original arrival evidence behind the serial."""
    units=[];lendable=[]
    for a in db.execute("""
            SELECT a.*,m.id AS manifest_id_,m.arrival_id,m.shipment_id,s.site AS arrival_site,
                   s.item AS arrival_item,src.sha AS source_sha
            FROM equipment_asset a
            LEFT JOIN equipment_manifest m ON m.id=a.manifest_id
            LEFT JOIN shipment s ON s.id=m.shipment_id
            LEFT JOIN source_blob src ON src.id=a.source_id
            ORDER BY a.id"""):
        claim=db.execute("SELECT * FROM equipment_claim WHERE equipment_id=? AND status='active' ORDER BY effective_at DESC LIMIT 1",
                         (a["id"],)).fetchone()
        last_claim=db.execute("SELECT * FROM equipment_claim WHERE equipment_id=? AND status!='refused' ORDER BY issued_at DESC,id DESC LIMIT 1",
                              (a["id"],)).fetchone()
        ret=db.execute("SELECT * FROM equipment_return WHERE equipment_id=? ORDER BY returned_at DESC,id DESC LIMIT 1",
                       (a["id"],)).fetchone()
        ok,reason=_asset_lendable(db,a)
        if ok:lendable.append(a["id"])
        units.append({
            "equipment":a["id"],"filter":a["filter_id"],"site":a["site"],
            "owner":a["owner"],"custodian":a["custodian"],"status":a["status"],
            "loan_settled":bool(a["loan_settled"]),"filter_status":a["filter_status"],
            "body_status":a["body_status"],"disposition_frozen":bool(a["disposition_frozen"]),
            "freeze_reason":a["freeze_reason"] or None,
            "last_disposition":last_claim["mode"] if last_claim else None,
            "active_claim":claim["id"] if claim else None,
            "active_claim_mode":claim["mode"] if claim else None,
            "active_issuer":claim["issuer"] if claim else None,
            "active_authority":claim["authority_id"] if claim else None,
            "last_return":ret["id"] if ret else None,
            "last_return_outcome":ret["outcome"] if ret else None,
            "lendable":ok,"lendable_reason":reason or None,
            "arrival":a["arrival_id"],"shipment":a["shipment_id"],"source_sha":a["source_sha"],
        })
    return units,lendable

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
        units,lendable=_equipment(db)
        data["equipment_units"]=units
        data["lendable_equipment"]=lendable
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))
