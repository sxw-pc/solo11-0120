import json
from .store import connect,TABLES

def _receipt_sources(db):
    """Per-receipt frozen per-batch source shares plus the original evidence summary."""
    rows=[]
    for r in db.execute("SELECT * FROM delivery_receipt ORDER BY id"):
        sources=[dict(row) for row in db.execute("""
            SELECT ds.shipment_id,ds.quantity,ds.source_id AS allocation_source_id
            FROM delivery_source ds WHERE ds.receipt_id=? ORDER BY ds.shipment_id""",(r["id"],))]
        evidence=db.execute("SELECT sha FROM source_blob WHERE id=?",(r["source_id"],)).fetchone()
        sourced=sum(s["quantity"] for s in sources)
        rows.append({"receipt_id":r["id"],"household_id":r["household_id"],"site":r["site"],"item":r["item"],
                     "quantity":r["quantity"],"status":r["status"],"delivered_at":r["delivered_at"],
                     "picking_policy":r["picking_policy"],"source_known":bool(sources),
                     "sourced_quantity":sourced,
                     "sources":sources,"delivery_source_sha":evidence["sha"] if evidence else None})
    return rows

def _batch_availability(db):
    """Released, already allocated and still allocatable units per batch."""
    rows=[]
    for a in db.execute("""SELECT a.id,a.shipment_id,s.site,s.item,a.status,a.released_count,a.usable_at
                           FROM arrival_count a JOIN shipment s ON s.id=a.shipment_id
                           ORDER BY s.site,s.item,a.usable_at,a.shipment_id"""):
        allocated=db.execute("""SELECT COALESCE(SUM(ds.quantity),0) FROM delivery_source ds
                                JOIN delivery_receipt r ON r.id=ds.receipt_id
                                WHERE ds.shipment_id=? AND r.status IN ('complete','partial')""",
                             (a["shipment_id"],)).fetchone()[0]
        rows.append({"arrival_id":a["id"],"shipment_id":a["shipment_id"],"site":a["site"],"item":a["item"],
                     "status":a["status"],"usable_at":a["usable_at"],"released_count":a["released_count"],
                     "allocated_count":allocated,
                     "remaining_allocatable":max(a["released_count"]-allocated,0) if a["status"]=="usable" else 0})
    return rows

def _unknown_source_receipts(db):
    """Delivered receipts predating source registration: unknown, never backfilled."""
    return [dict(row) for row in db.execute("""
        SELECT r.id AS receipt_id,r.household_id,r.site,r.item,r.quantity,r.delivered_at
        FROM delivery_receipt r
        WHERE r.status IN ('complete','partial')
          AND NOT EXISTS (SELECT 1 FROM delivery_source ds WHERE ds.receipt_id=r.id)
        ORDER BY r.delivered_at,r.id""")]

def _recall_contacts(db):
    contacts=[]
    for rc in db.execute("SELECT * FROM recall ORDER BY id"):
        shipment=db.execute("SELECT * FROM shipment WHERE id=?",(rc["shipment_id"],)).fetchone()
        households=[dict(row) for row in db.execute("""
            SELECT r.household_id,r.id AS receipt_id,ds.quantity,r.delivered_at,r.signature
            FROM delivery_source ds JOIN delivery_receipt r ON r.id=ds.receipt_id
            WHERE ds.shipment_id=? AND r.status IN ('complete','partial')
            ORDER BY r.delivered_at,r.id""",(rc["shipment_id"],))]
        # Only receipts without any frozen source row are indeterminate; a receipt
        # sourced from another batch is provably unaffected and is not listed.
        indeterminate=[dict(row) for row in db.execute("""
            SELECT r.id AS receipt_id,r.household_id,r.quantity,r.delivered_at
            FROM delivery_receipt r
            WHERE r.site=? AND r.item=? AND r.status IN ('complete','partial')
              AND NOT EXISTS (SELECT 1 FROM delivery_source ds WHERE ds.receipt_id=r.id)
            ORDER BY r.delivered_at,r.id""",(shipment["site"],shipment["item"]))]
        contacts.append({"recall_id":rc["id"],"shipment_id":rc["shipment_id"],"site":shipment["site"],
                         "item":shipment["item"],"recalled_at":rc["recalled_at"],"reason":rc["reason"],
                         "isolated_count":rc["isolated_count"],
                         "contact_households":sorted({h["household_id"] for h in households}),
                         "delivered_shares":households,"indeterminate_legacy_receipts":indeterminate})
    return contacts

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
        data["receipt_sources"]=_receipt_sources(db)
        data["batch_availability"]=_batch_availability(db)
        data["recall_contacts"]=_recall_contacts(db)
        data["unknown_source_receipts"]=_unknown_source_receipts(db)
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))
