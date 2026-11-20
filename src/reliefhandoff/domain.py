from datetime import datetime
from .intake import ingest

PICKING_POLICIES=("fifo","lifo")

def _time(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError("timezone_required")
    return t

def _opt_time(value):
    return _time(value) if value else None

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
            counted_at=_opt_time(a.get("counted_at"))
            old=db.execute("SELECT * FROM arrival_count WHERE id=?",(a["id"],)).fetchone()
            values=(a["shipment"],a["counted"],a["seal"],a.get("counted_at"))
            if old and tuple(old[k] for k in ("shipment_id","counted","seal","usable_at"))!=(a["shipment"],a["counted"],a["seal"],a.get("counted_at")):
                raise ValueError("arrival_identity_conflict")
            reason=("seal_broken" if a["seal"]!=shipment["seal"] else "short_count" if a["counted"]<shipment["expected"] else "usable")
            status="quarantined" if reason=="seal_broken" else "usable"
            if not old:
                released=a["counted"] if status=="usable" else 0
                # An intact package becomes usable when counted; a broken seal stays
                # unusable until arrival_reconcile completes the witnessed recount.
                usable_at=a.get("counted_at") if status=="usable" else None
                db.execute("INSERT INTO arrival_count(id,shipment_id,counted,released_count,seal,status,reason,usable_at,source_id)"
                           " VALUES (?,?,?,?,?,?,?,?,?)",
                           (a["id"],a["shipment"],a["counted"],released,a["seal"],status,reason,usable_at,source))
                result[status]+=1
        return result
    return ingest(path,"arrival",file,handler)

def arrival_reconcile(path,file):
    """A broken seal is released only after two witnesses verify physical count."""
    def handler(db,data,source):
        result={"reconciled":0}
        for item in data["reconciliations"]:
            reconciled_at=_opt_time(item.get("reconciled_at"))
            old=db.execute("SELECT * FROM arrival_reconciliation WHERE id=?",(item["id"],)).fetchone()
            values=(item["arrival"],item["witness1"],item["witness2"],item["verified_count"],item["photo_sha"],item["reason"],item.get("reconciled_at"))
            if old:
                if tuple(old[k] for k in ("arrival_id","witness1","witness2","verified_count","photo_sha","reason","reconciled_at"))!=values:
                    raise ValueError("arrival_reconciliation_conflict")
                continue
            arrival=db.execute("SELECT * FROM arrival_count WHERE id=?",(item["arrival"],)).fetchone()
            if (not arrival or arrival["status"]!="quarantined" or item["witness1"]==item["witness2"] or
                not item["witness1"] or not item["witness2"] or not item["photo_sha"] or
                item["verified_count"]<0 or item["verified_count"]>arrival["counted"] or not item["reason"].strip()):
                raise ValueError("broken_seal_evidence_insufficient")
            db.execute("INSERT INTO arrival_reconciliation(id,arrival_id,witness1,witness2,verified_count,photo_sha,reason,reconciled_at,source_id)"
                       " VALUES (?,?,?,?,?,?,?,?,?)",(item["id"],*values,source))
            # The verified count becomes usable at the moment the witnessed recount is recorded.
            db.execute("UPDATE arrival_count SET released_count=?,status='usable',reason='witnessed_recount',usable_at=? WHERE id=?",
                       (item["verified_count"],item.get("reconciled_at"),item["arrival"]))
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

def picking_import(path,file):
    """Freeze the on-site picking contract (fifo/lifo) before receipts are issued."""
    def handler(db,data,source):
        result={"contracts":0}
        for c in data["contracts"]:
            if c["picking_policy"] not in PICKING_POLICIES:raise ValueError("invalid_picking_policy")
            _time(c["selected_at"])
            key=(c["site"],c["item"])
            old=db.execute("SELECT * FROM picking_contract WHERE site=? AND item=?",key).fetchone()
            # A contract cannot be (re)chosen after the site has already issued the item:
            # existing receipts keep the policy frozen on them.
            issued=db.execute("SELECT 1 FROM delivery_receipt WHERE site=? AND item=? AND status IN ('complete','partial') LIMIT 1",
                              key).fetchone()
            if issued and (not old or old["picking_policy"]!=c["picking_policy"]):
                raise ValueError("picking_contract_selected_after_delivery")
            if old:
                if old["picking_policy"]!=c["picking_policy"]:raise ValueError("picking_contract_conflict")
                # Same frozen policy re-asserted (possibly from a re-imported blob): no new contract.
                continue
            db.execute("INSERT INTO picking_contract(site,item,picking_policy,selected_at,source_id) VALUES (?,?,?,?,?)",
                       (c["site"],c["item"],c["picking_policy"],c["selected_at"],source))
            result["contracts"]+=1
        return result
    return ingest(path,"picking",file,handler)

def _allocated_to_batch(db,arrival_id):
    return db.execute("""SELECT COALESCE(SUM(ds.quantity),0) FROM delivery_source ds
                         JOIN delivery_receipt r ON r.id=ds.receipt_id
                         WHERE ds.shipment_id=(SELECT shipment_id FROM arrival_count WHERE id=?)
                           AND r.status IN ('complete','partial')""",(arrival_id,)).fetchone()[0]

def _legacy_delivered(db,site,item):
    """Units signed for by receipts that carry no frozen source registration."""
    return db.execute("""SELECT COALESCE(SUM(r.quantity),0) FROM delivery_receipt r
                         WHERE r.site=? AND r.item=? AND r.status IN ('complete','partial')
                           AND NOT EXISTS (SELECT 1 FROM delivery_source ds WHERE ds.receipt_id=r.id)""",
                      (site,item)).fetchone()[0]

def _usable_batches(db,site,item,at):
    """Batches released before instant `at`, each with its still-allocatable count.

    Recalled batches are excluded (their remaining units are isolated). Batches
    without a recorded usable instant are legacy rows and stay orderable but
    cannot be time-gated.
    """
    rows=db.execute("""SELECT a.id,a.shipment_id,a.usable_at,a.released_count
                       FROM arrival_count a JOIN shipment s ON s.id=a.shipment_id
                       WHERE s.site=? AND s.item=? AND a.status='usable'""",(site,item)).fetchall()
    batches=[]
    for row in rows:
        if row["usable_at"] and _time(row["usable_at"])>at:continue
        remaining=row["released_count"]-_allocated_to_batch(db,row["id"])
        if remaining>0:batches.append({"id":row["id"],"shipment_id":row["shipment_id"],
                                       "usable_at":row["usable_at"],"remaining":remaining})
    return batches

def _sorted_batches(batches,policy):
    timed=[b for b in batches if b["usable_at"]]
    untimed=[b for b in batches if not b["usable_at"]]
    timed.sort(key=lambda b:(b["usable_at"],b["shipment_id"]),reverse=(policy=="lifo"))
    return timed+untimed

def _allocation_plan(db,site,item,count,at,policy):
    """Greedy per-batch allocation under the preselected picking contract."""
    plan=[];need=count
    for batch in _sorted_batches(_usable_batches(db,site,item,at),policy):
        if need==0:break
        take=min(need,batch["remaining"])
        if take:plan.append((batch["shipment_id"],take));need-=take
    if need:raise ValueError("insufficient_site_stock")
    return plan

def _site_available(db,site,item,at):
    """Allocatable units at instant `at`: per-batch remaining, recalled batches excluded.

    Signed legacy receipts without source registration can never be traced back to
    a batch; their units are conservatively deducted from the site's available pool.
    """
    batch_remaining=sum(b["remaining"] for b in _usable_batches(db,site,item,at))
    return max(batch_remaining-_legacy_delivered(db,site,item),0)

def delivery_import(path,file):
    def handler(db,data,source):
        result={"complete":0,"partial":0,"held":0}
        for r in data["receipts"]:
            at=_time(r["at"])
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
            available=_site_available(db,r["site"],r["item"],at)
            contract=db.execute("SELECT * FROM picking_contract WHERE site=? AND item=?",(r["site"],r["item"])).fetchone()
            reason=("ineligible_household" if not household or household["status"]!="eligible" else
                    "unsigned" if not r["signature"] else "over_entitlement" if r["count"]>remaining else
                    "picking_contract_not_selected" if not contract else
                    "insufficient_site_stock" if r["count"]>available else "partial" if r["count"]<remaining else "complete")
            status=reason if reason in {"partial","complete"} else "held"
            policy=contract["picking_policy"] if contract else None
            # Receipt, per-batch sources, chosen contract and evidence are frozen in
            # this one transaction; later imports never re-pick sources for it.
            db.execute("INSERT INTO delivery_receipt(id,household_id,site,item,quantity,delivered_at,signature,status,reason,picking_policy,source_id)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"],*values,status,reason,policy,source))
            if status in ("partial","complete"):
                plan=_allocation_plan(db,r["site"],r["item"],r["count"],at,policy)
                if sum(qty for _,qty in plan)!=r["count"]:raise ValueError("delivery_source_mismatch")
                for shipment_id,qty in plan:
                    db.execute("INSERT INTO delivery_source(id,receipt_id,shipment_id,quantity,source_id) VALUES (?,?,?,?,?)",
                               ("%s|%s"%(r["id"],shipment_id),r["id"],shipment_id,qty,source))
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

def recall_import(path,file):
    """Offline recall: isolate undelivered units and list the families holding the rest."""
    def handler(db,data,source):
        result={"recalls":0}
        for rc in data["recalls"]:
            _time(rc["recalled_at"])
            old=db.execute("SELECT * FROM recall WHERE id=?",(rc["id"],)).fetchone()
            values=(rc["shipment"],rc["recalled_at"],rc["reason"])
            if old:
                if tuple(old[k] for k in ("shipment_id","recalled_at","reason"))!=values:
                    raise ValueError("recall_identity_conflict")
                continue
            arrival=db.execute("SELECT * FROM arrival_count WHERE shipment_id=?",(rc["shipment"],)).fetchone()
            if not arrival:raise ValueError("recall_shipment_unknown")
            shipment=db.execute("SELECT * FROM shipment WHERE id=?",(rc["shipment"],)).fetchone()
            delivered=_allocated_to_batch(db,arrival["id"])
            # Legacy receipts have no frozen provenance; their units cannot be proven
            # to belong to another batch, so they cannot be isolated either.
            untracked=_legacy_delivered(db,shipment["site"],shipment["item"])
            isolated=max(arrival["released_count"]-delivered-untracked,0)
            db.execute("INSERT INTO recall(id,shipment_id,recalled_at,reason,isolated_count,source_id) VALUES (?,?,?,?,?,?)",
                       (rc["id"],*values,isolated,source))
            # Remaining, undelivered units of this batch alone are taken out of the
            # allocatable pool; other batches and already-signed source shares stay put.
            db.execute("UPDATE arrival_count SET status='recalled' WHERE id=?",(arrival["id"],))
            result["recalls"]+=1
        return result
    return ingest(path,"recall",file,handler)
