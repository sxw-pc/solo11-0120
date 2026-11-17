from datetime import datetime
from .intake import ingest

def _time(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError("timezone_required")
    return t

def arrival_import(path,file):
    def handler(db,data,source):
        result={"shipments":0,"usable":0,"quarantined":0}
        for s in data["shipments"]:
            if s["expected"]<0:raise ValueError("invalid_expected_count")
            old=db.execute("SELECT * FROM shipment WHERE id=?",(s["id"],)).fetchone()
            values=(s["site"],s["item"],s["expected"],s["seal"])
            if old and tuple(old[k] for k in ("site","item","expected","seal"))!=values:raise ValueError("shipment_identity_conflict")
            if not old:db.execute("INSERT INTO shipment VALUES (?,?,?,?,?,?)",(s["id"],*values,source));result["shipments"]+=1
        for a in data["counts"]:
            shipment=db.execute("SELECT * FROM shipment WHERE id=?",(a["shipment"],)).fetchone()
            if not shipment or a["counted"]<0:raise ValueError("invalid_arrival_count")
            old=db.execute("SELECT * FROM arrival_count WHERE id=?",(a["id"],)).fetchone()
            values=(a["shipment"],a["counted"],a["seal"])
            if old and tuple(old[k] for k in ("shipment_id","counted","seal"))!=values:raise ValueError("arrival_identity_conflict")
            reason=("seal_broken" if a["seal"]!=shipment["seal"] else "short_count" if a["counted"]<shipment["expected"] else "usable")
            status="quarantined" if reason=="seal_broken" else "usable"
            if not old:
                released=a["counted"] if status=="usable" else 0
                db.execute("INSERT INTO arrival_count VALUES (?,?,?,?,?,?,?,?)",
                           (a["id"],a["shipment"],a["counted"],released,a["seal"],status,reason,source))
                result[status]+=1
        return result
    return ingest(path,"arrival",file,handler)

def arrival_reconcile(path,file):
    """A broken seal is released only after two witnesses verify physical count."""
    def handler(db,data,source):
        result={"reconciled":0}
        for item in data["reconciliations"]:
            old=db.execute("SELECT * FROM arrival_reconciliation WHERE id=?",(item["id"],)).fetchone()
            values=(item["arrival"],item["witness1"],item["witness2"],item["verified_count"],item["photo_sha"],item["reason"])
            if old:
                if tuple(old[k] for k in ("arrival_id","witness1","witness2","verified_count","photo_sha","reason"))!=values:
                    raise ValueError("arrival_reconciliation_conflict")
                continue
            arrival=db.execute("SELECT * FROM arrival_count WHERE id=?",(item["arrival"],)).fetchone()
            if (not arrival or arrival["status"]!="quarantined" or item["witness1"]==item["witness2"] or
                not item["witness1"] or not item["witness2"] or not item["photo_sha"] or
                item["verified_count"]<0 or item["verified_count"]>arrival["counted"] or not item["reason"].strip()):
                raise ValueError("broken_seal_evidence_insufficient")
            db.execute("INSERT INTO arrival_reconciliation VALUES (?,?,?,?,?,?,?,?)",(item["id"],*values,source))
            db.execute("UPDATE arrival_count SET released_count=?,status='usable',reason='witnessed_recount' WHERE id=?",
                       (item["verified_count"],item["arrival"]))
            result["reconciled"]+=1
        return result
    return ingest(path,"arrival-reconcile",file,handler)

def household_import(path,file):
    def handler(db,data,source):
        result={"eligible":0,"held":0}
        for h in data["households"]:
            _time(h["credential_until"])
            if h["members"]<=0:raise ValueError("invalid_household_size")
            status="eligible" if h["zone"]==data["disaster_zone"] and _time(h["credential_until"])>=_time(data["asof"]) else "held"
            old=db.execute("SELECT * FROM household WHERE id=?",(h["id"],)).fetchone()
            values=(h["zone"],h["members"],h["credential_until"],status)
            if old and tuple(old[k] for k in ("zone","members","credential_until","status"))!=values:
                raise ValueError("household_identity_conflict")
            if not old:db.execute("INSERT INTO household VALUES (?,?,?,?,?,?)",(h["id"],*values,source))
            qty=h["members"]*int(data["units_per_member"])
            prior=db.execute("SELECT quantity FROM entitlement WHERE household_id=? AND item=?",(h["id"],data["item"])).fetchone()
            if prior and prior["quantity"]!=qty:raise ValueError("entitlement_identity_conflict")
            db.execute("INSERT OR IGNORE INTO entitlement VALUES (?,?,?)",(h["id"],data["item"],qty))
            result[status]+=1
        return result
    return ingest(path,"household",file,handler)

def eligibility_review(path,file):
    """A documented local review can restore a held household's entitlement."""
    def handler(db,data,source):
        result={"approved":0,"denied":0}
        for item in data["reviews"]:
            _time(item["valid_until"])
            old=db.execute("SELECT * FROM eligibility_review WHERE id=?",(item["id"],)).fetchone()
            values=(item["household"],item["decision"],item["valid_until"],item["reviewer"],item["proof_sha"])
            if old:
                if tuple(old[k] for k in ("household_id","decision","valid_until","reviewer","proof_sha"))!=values:
                    raise ValueError("eligibility_review_conflict")
                continue
            household=db.execute("SELECT * FROM household WHERE id=?",(item["household"],)).fetchone()
            if (not household or item["decision"] not in result or not item["reviewer"] or
                len(item["proof_sha"])<8 or _time(item["valid_until"])<=_time(household["credential_until"])):
                raise ValueError("eligibility_evidence_insufficient")
            db.execute("INSERT INTO eligibility_review VALUES (?,?,?,?,?,?,?)",(item["id"],*values,source))
            db.execute("UPDATE household SET credential_until=?,status=? WHERE id=?",
                       (item["valid_until"],"eligible" if item["decision"]=="approved" else "held",item["household"]))
            result[item["decision"]]+=1
        return result
    return ingest(path,"eligibility-review",file,handler)

def _usable(db,site,item):
    return sum(r["released_count"] for r in db.execute(
        "SELECT a.released_count FROM arrival_count a JOIN shipment s ON s.id=a.shipment_id WHERE a.status='usable' AND s.site=? AND s.item=?",
        (site,item)))

def _delivered(db,site,item):
    return db.execute("SELECT COALESCE(SUM(quantity),0) FROM delivery_receipt WHERE site=? AND item=? AND status IN ('complete','partial')",
                      (site,item)).fetchone()[0]

def delivery_import(path,file):
    def handler(db,data,source):
        result={"complete":0,"partial":0,"held":0}
        for r in data["receipts"]:
            _time(r["at"])
            if r["count"]<0:raise ValueError("invalid_delivery_count")
            old=db.execute("SELECT * FROM delivery_receipt WHERE id=?",(r["id"],)).fetchone()
            values=(r["household"],r["site"],r["item"],r["count"],r["at"],r["signature"])
            if old:
                if tuple(old[k] for k in ("household_id","site","item","quantity","delivered_at","signature"))!=values:
                    raise ValueError("receipt_identity_conflict")
                continue
            household=db.execute("SELECT * FROM household WHERE id=?",(r["household"],)).fetchone()
            entitlement=db.execute("SELECT quantity FROM entitlement WHERE household_id=? AND item=?",(r["household"],r["item"])).fetchone()
            already=db.execute("SELECT COALESCE(SUM(quantity),0) FROM delivery_receipt WHERE household_id=? AND item=? AND status IN ('complete','partial')",
                               (r["household"],r["item"])).fetchone()[0]
            remaining=(entitlement["quantity"]-already) if entitlement else 0
            available=_usable(db,r["site"],r["item"])-_delivered(db,r["site"],r["item"])
            reason=("ineligible_household" if not household or household["status"]!="eligible" else
                    "unsigned" if not r["signature"] else "over_entitlement" if r["count"]>remaining else
                    "insufficient_site_stock" if r["count"]>available else "partial" if r["count"]<remaining else "complete")
            status=reason if reason in {"partial","complete"} else "held"
            db.execute("INSERT INTO delivery_receipt VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (r["id"],*values,status,reason,source))
            if status=="partial":
                cause="site_supply_short" if available==r["count"] else "recipient_partial_pickup"
                db.execute("INSERT INTO shortage_case(household_id,item,receipt_id,missing,reason,status) VALUES (?,?,?,?,?,?)",
                           (r["household"],r["item"],r["id"],remaining-r["count"],cause,"open"))
            if status=="complete":
                db.execute("UPDATE shortage_case SET status='resolved' WHERE household_id=? AND item=? AND status='open'",
                           (r["household"],r["item"]))
            result[status]+=1
        return result
    return ingest(path,"delivery",file,handler)
