import json
from datetime import datetime
from .intake import ingest

def _time(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError("timezone_required")
    return t

def _conflict(code,eid,kind,detail):
    """A rejected conflicting/late document must still leave old evidence and
    freeze later dispositions, even though the business transaction rolls back."""
    exc=ValueError(code)
    def persist(db,failure_id,sha):
        owner=db.execute("SELECT id FROM equipment_asset WHERE id=?",(eid,)).fetchone() if eid else None
        if owner:
            db.execute("UPDATE equipment_asset SET disposition_frozen=1,freeze_reason=? WHERE id=?",(kind,eid))
        db.execute("INSERT INTO equipment_dispute(equipment_id,kind,detail,evidence_sha,failure_id) VALUES (?,?,?,?,?)",
                   (eid if owner else None,kind,detail,sha,failure_id))
    exc.persist_after_rollback=persist
    return exc

def _scopes(value):
    if isinstance(value,str):return {x.strip() for x in value.split(",") if x.strip()}
    return set(value or [])

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

REUSABLE_EQUIPMENT="purifier"
CLAIM_SCOPES={"loan","transfer"}
RETURN_OUTCOMES={"ok","good","damaged"}

def _equipment_row(db,eid):
    return db.execute("SELECT * FROM equipment_asset WHERE id=?",(eid,)).fetchone()

def _active_claim(db,eid):
    return db.execute("SELECT * FROM equipment_claim WHERE equipment_id=? AND status='active' ORDER BY effective_at DESC LIMIT 1",(eid,)).fetchone()

def _asset_lendable(db,asset):
    """Only the owning site may lend, after a settled loan and a clean check."""
    if not asset:return False,"unknown_equipment"
    if asset["reusable"]!=1:return False,"not_reusable"
    if asset["disposition_frozen"]:return False,"disposition_frozen"
    if asset["owner"]!=asset["site"]:return False,"owner_not_site"
    if asset["status"]!="available":return False,asset["status"]
    if not asset["filter_id"]:return False,"filter_missing"
    if asset["body_status"]!="ok":return False,"body_"+str(asset["body_status"])
    if asset["filter_status"]!="ok":return False,"filter_"+str(asset["filter_status"])
    if _active_claim(db,asset["id"]):return False,"active_claim"
    return True,""

def equipment_arrival(path,file):
    """Register reusable purifier bodies and detachable filters from a trusted
    arrival count. A sealed/reconciled count is required; the serial manifest
    must match the released physical count exactly."""
    def handler(db,data,source):
        result={"assets":0,"manifests":0}
        seen_body=set();seen_filter=set()
        for m in data["manifests"]:
            arrival=db.execute("SELECT * FROM arrival_count WHERE id=?",(m["arrival"],)).fetchone()
            if not arrival:
                raise ValueError("unknown_arrival")
            shipment=db.execute("SELECT * FROM shipment WHERE id=?",(arrival["shipment_id"],)).fetchone()
            if m.get("kind")!=REUSABLE_EQUIPMENT or m.get("reusable") is not True or not shipment or shipment["item"]!=REUSABLE_EQUIPMENT:
                raise ValueError("not_reusable_equipment")
            units=m.get("units") or []
            for u in units:
                if not u.get("equipment") or not u.get("filter") or u["equipment"]==u["filter"]:
                    raise ValueError("invalid_serial_manifest")
            bodies=[u["equipment"] for u in units];filters=[u["filter"] for u in units]
            if len(set(bodies))!=len(bodies) or len(set(filters))!=len(filters):
                raise ValueError("duplicate_serial_in_manifest")
            if seen_body & set(bodies) or seen_filter & set(filters):
                raise ValueError("duplicate_serial_in_manifest")
            seen_body.update(bodies);seen_filter.update(filters)
            serials=json.dumps([{"equipment":u["equipment"],"filter":u["filter"]} for u in units],
                               ensure_ascii=False,sort_keys=True)
            # Replaying the same manifest is idempotent before any new checks,
            # so a duplicate import never looks like a serial collision.
            old=db.execute("SELECT * FROM equipment_manifest WHERE id=?",(m["id"],)).fetchone()
            if old:
                if old["arrival_id"]!=m["arrival"] or old["serials"]!=serials:
                    prior=json.loads(old["serials"])
                    raise _conflict("equipment_manifest_conflict",prior[0]["equipment"] if prior else None,
                                    "manifest_conflict",
                                    "manifest %s re-declared with different serials"%m["id"])
                continue
            # Neither an unreleased (broken-seal) count nor a serial list that
            # disagrees with the physical count may establish sellable assets.
            if arrival["status"]!="usable" or arrival["released_count"]<=0:
                raise ValueError("equipment_arrival_not_released")
            if len(units)!=arrival["released_count"]:
                raise ValueError("serial_manifest_mismatch")
            if db.execute("SELECT 1 FROM equipment_manifest WHERE arrival_id=?",(m["arrival"],)).fetchone():
                existing=db.execute("SELECT serials FROM equipment_manifest WHERE arrival_id=?",(m["arrival"],)).fetchone()
                first=json.loads(existing["serials"])[0]["equipment"] if existing and json.loads(existing["serials"]) else None
                raise _conflict("arrival_already_manifested",first,"manifest_conflict",
                                "arrival %s already has a serial manifest"%m["arrival"])
            for u in units:
                if _equipment_row(db,u["equipment"]) or db.execute("SELECT 1 FROM equipment_asset WHERE filter_id=?",(u["filter"],)).fetchone():
                    raise _conflict("serial_identity_conflict",u["equipment"],"serial_conflict",
                                    "serial %s already registered"%u["equipment"])
            db.execute("INSERT INTO equipment_manifest VALUES (?,?,?,?,?)",
                       (m["id"],m["arrival"],shipment["id"],serials,source))
            for u in units:
                db.execute("INSERT INTO equipment_asset VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (u["equipment"],REUSABLE_EQUIPMENT,1,u["filter"],shipment["site"],shipment["site"],
                            shipment["site"],"available","ok","ok",1,0,"",m["id"],None,source))
            result["manifests"]+=1;result["assets"]+=len(units)
        return result
    return ingest(path,"equipment-arrival",file,handler)

def equipment_authority(path,file):
    """Pre-issued disposition authority: who may hand this asset to whom, for
    loan or ownership transfer, and while the document is valid."""
    def handler(db,data,source):
        result={"authorities":0}
        for a in data["authorities"]:
            scope=_scopes(a.get("scope"))
            _time(a["valid_from"]);_time(a["valid_until"])
            if (not _equipment_row(db,a["equipment"]) or not scope or not scope<=CLAIM_SCOPES or
                not a.get("issuer") or not a.get("party") or not a.get("document_sha") or
                _time(a["valid_from"])>=_time(a["valid_until"])):
                raise ValueError("authority_evidence_insufficient")
            old=db.execute("SELECT * FROM equipment_authority WHERE id=?",(a["id"],)).fetchone()
            values=(a["equipment"],a["issuer"],a["party"],",".join(sorted(scope)),
                    a["valid_from"],a["valid_until"],a["document_sha"])
            if old:
                if tuple(old[k] for k in ("equipment_id","issuer","party","scope","valid_from","valid_until","document_sha"))!=values:
                    raise _conflict("authority_identity_conflict",a["equipment"],"authority_conflict",
                                    "authority %s re-declared with different terms"%a["id"])
                continue
            db.execute("INSERT INTO equipment_authority VALUES (?,?,?,?,?,?,?,?,?)",
                       (a["id"],*values,source))
            result["authorities"]+=1
        return result
    return ingest(path,"equipment-authority",file,handler)

def _refuse(db,c,asset,reason,source):
    db.execute("INSERT INTO equipment_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (c["id"],c["equipment"],asset["filter_id"],c["holder"],c.get("site") or asset["site"],
                c["mode"],c["issuer"],c["authority"],c["issued_at"],c["effective_at"],c["document_sha"],
                "refused",reason,source))

def equipment_issue(path,file):
    """Issue a purifier as a time-limited loan or as an ownership transfer.
    The issuer must hold the current ownership right and must cite pre-issued,
    still-valid, signed authority material; the claim records who takes custody
    and from when. A claim that cannot be granted is recorded as refused; a late
    or contradictory document freezes the asset instead of overwriting fact."""
    def handler(db,data,source):
        result={"loan":0,"transfer":0,"refused":0}
        for c in data["issues"]:
            mode=c.get("mode")
            if mode not in CLAIM_SCOPES:raise ValueError("invalid_claim_mode")
            issued_at=_time(c["issued_at"]);effective_at=_time(c["effective_at"])
            if effective_at<issued_at:raise ValueError("invalid_effective_time")
            if not c.get("holder") or not c.get("issuer") or not c.get("document_sha") or len(c["document_sha"])<8:
                raise ValueError("claim_evidence_insufficient")
            asset=_equipment_row(db,c["equipment"])
            if not asset:raise ValueError("unknown_equipment")
            old=db.execute("SELECT * FROM equipment_claim WHERE id=?",(c["id"],)).fetchone()
            values=(c["equipment"],asset["filter_id"],c["holder"],c.get("site") or asset["site"],mode,
                    c["issuer"],c["authority"],c["issued_at"],c["effective_at"],c["document_sha"])
            if old:
                if tuple(old[k] for k in ("equipment_id","filter_id","holder","site","mode","issuer",
                                          "authority_id","issued_at","effective_at","document_sha"))!=values:
                    raise _conflict("claim_identity_conflict",c["equipment"],"claim_conflict",
                                    "claim %s re-declared with different terms"%c["id"])
                if old["status"]!="refused":
                    continue
                # A refused request is not a physical handover. The same signed
                # document is re-evaluated once the asset is clear again.
                db.execute("DELETE FROM equipment_claim WHERE id=?",(c["id"],))
            if asset["disposition_frozen"]:
                raise ValueError("equipment_frozen")
            if c.get("filter") and c["filter"]!=asset["filter_id"]:
                raise _conflict("filter_identity_conflict",c["equipment"],"filter_conflict",
                                "claim cites filter %s but asset carries %s"%(c["filter"],asset["filter_id"]))
            if asset["last_event_at"] and issued_at<_time(asset["last_event_at"]):
                raise _conflict("late_document_conflict",c["equipment"],"late_document",
                                "claim %s predates the last physical handover %s"%(c["id"],asset["last_event_at"]))
            # A third party asserting a right over an asset it does not own is a
            # contradictory ownership claim, not merely an unavailable request.
            if c["issuer"]!=asset["owner"] and c["issuer"]!=asset["site"]:
                raise _conflict("no_disposition_authority",c["equipment"],"ownership_claim_conflict",
                                "issuer %s claims rights over asset owned by %s"%(c["issuer"],asset["owner"]))
            if asset["owner"]!=asset["site"]:
                _refuse(db,c,asset,"owner_not_site",source);result["refused"]+=1;continue
            if asset["status"]=="on_loan":
                _refuse(db,c,asset,"equipment_on_loan",source);result["refused"]+=1;continue
            if asset["status"]=="pending_inspection" or asset["filter_status"]!="ok" or asset["body_status"]!="ok":
                _refuse(db,c,asset,"equipment_pending_inspection",source);result["refused"]+=1;continue
            if asset["status"]!="available":
                _refuse(db,c,asset,"equipment_not_available",source);result["refused"]+=1;continue
            auth=db.execute("SELECT * FROM equipment_authority WHERE id=?",(c["authority"],)).fetchone()
            if (not auth or auth["equipment_id"]!=c["equipment"] or auth["party"]!=c["holder"] or
                mode not in _scopes(auth["scope"]) or auth["issuer"]!=c["issuer"] or
                issued_at<_time(auth["valid_from"]) or issued_at>_time(auth["valid_until"])):
                _refuse(db,c,asset,"claim_evidence_insufficient",source);result["refused"]+=1;continue
            db.execute("INSERT INTO equipment_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (c["id"],*values,"active","issued:"+mode,source))
            if mode=="loan":
                db.execute("UPDATE equipment_asset SET custodian=?,status='on_loan',loan_settled=0,last_event_at=? WHERE id=?",
                           (c["holder"],c["effective_at"],c["equipment"]))
            else:
                db.execute("UPDATE equipment_asset SET owner=?,custodian=?,status='transferred',loan_settled=1,last_event_at=? WHERE id=?",
                           (c["holder"],c["holder"],c["effective_at"],c["equipment"]))
            result[mode]+=1
        return result
    return ingest(path,"equipment-issue",file,handler)

def equipment_return(path,file):
    """Return checks body, filter, witness and condition. A missing part or a
    damaged unit goes to pending inspection rather than back on the shelf.
    For a transferred unit the handover only moves custody, never ownership."""
    def handler(db,data,source):
        result={"clean":0,"missing_body":0,"missing_filter":0,"damaged":0}
        for r in data["returns"]:
            returned_at=_time(r["returned_at"])
            claim=db.execute("SELECT * FROM equipment_claim WHERE id=?",(r["claim"],)).fetchone()
            if not claim or claim["equipment_id"]!=r["equipment"]:raise ValueError("unknown_claim")
            asset=_equipment_row(db,r["equipment"])
            old=db.execute("SELECT * FROM equipment_return WHERE id=?",(r["id"],)).fetchone()
            values=(r["equipment"],r["claim"],asset["filter_id"] if asset else None,r["holder"],
                    r.get("site") or asset["site"],r["witness"],int(bool(r["body_present"])),
                    int(bool(r["filter_present"])),r["condition"],r["returned_at"],r["document_sha"])
            if old:
                if tuple(old[k] for k in ("equipment_id","claim_id","filter_id","holder","site","witness",
                                          "body_present","filter_present","condition","returned_at","document_sha"))!=values:
                    raise _conflict("return_identity_conflict",r["equipment"],"return_conflict",
                                    "return %s re-declared with different terms"%r["id"])
                continue
            if asset["disposition_frozen"]:raise ValueError("equipment_frozen")
            if claim["status"]!="active":raise ValueError("claim_not_active")
            if (r["holder"]!=claim["holder"] or not r.get("witness") or r["witness"]==r["holder"] or
                not r.get("document_sha") or len(r["document_sha"])<8 or
                r["condition"] not in RETURN_OUTCOMES):
                raise ValueError("return_evidence_insufficient")
            if r.get("site") and r["site"]!=asset["site"]:raise ValueError("return_site_mismatch")
            if r.get("filter") and r["filter"]!=asset["filter_id"]:
                raise _conflict("filter_identity_conflict",r["equipment"],"filter_conflict",
                                "return cites filter %s but asset carries %s"%(r["filter"],asset["filter_id"]))
            if returned_at<_time(claim["effective_at"]):
                raise _conflict("late_document_conflict",r["equipment"],"late_document",
                                "return %s predates the issue it settles"%r["id"])
            outcome=("missing_body" if not r["body_present"] else
                     "missing_filter" if not r["filter_present"] else
                     "damaged" if r["condition"]!="ok" else "clean")
            body_status=asset["body_status"];filter_status=asset["filter_status"]
            if outcome=="missing_body":body_status="missing";filter_status="missing"
            elif outcome=="missing_filter":filter_status="missing"
            elif outcome=="damaged":body_status="damaged";filter_status="damaged"
            else:body_status="ok";filter_status="ok"
            if outcome=="clean":
                status="in_custody" if claim["mode"]=="transfer" else "available"
            else:
                status="pending_inspection"
            db.execute("INSERT INTO equipment_return VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"],*values,outcome,source))
            db.execute("UPDATE equipment_claim SET status='returned',reason=? WHERE id=?",
                       ("custody_returned" if claim["mode"]=="transfer" else "loan_returned",r["claim"]))
            db.execute("""UPDATE equipment_asset SET custodian=?,status=?,body_status=?,filter_status=?,
                          loan_settled=1,last_event_at=? WHERE id=?""",
                       (r.get("site") or asset["site"],status,body_status,filter_status,r["returned_at"],r["equipment"]))
            result[outcome]+=1
        return result
    return ingest(path,"equipment-return",file,handler)

def equipment_merge(path,file):
    """Reconcile a filter physically missing at return only by recording a real
    replacement part with its own serial and witness; a bare handover sheet is
    never enough. The asset stays pending inspection until checked."""
    def handler(db,data,source):
        result={"merged":0,"refused":0}
        for m in data.get("merges",[]):
            claim=db.execute("SELECT * FROM equipment_claim WHERE id=?",(m["claim"],)).fetchone()
            if not claim or claim["equipment_id"]!=m["equipment"]:raise ValueError("unknown_claim")
            asset=_equipment_row(db,m["equipment"])
            if not asset:raise ValueError("unknown_equipment")
            if m["id"] and db.execute("SELECT 1 FROM equipment_dispute WHERE equipment_id=? AND kind='filter_replaced' AND detail LIKE ?",
                                      (m["equipment"],"%merge "+m["id"]+"%",)).fetchone():
                continue
            # A replacement filter is a new physical part with its own serial.
            # Without a serial this is just another sheet of paper over a hole.
            if (claim["status"]=="active" or not m.get("filter") or m["filter"]==asset["filter_id"] or
                db.execute("SELECT 1 FROM equipment_asset WHERE filter_id=?",(m["filter"],)).fetchone() or
                not m.get("document_sha") or len(m["document_sha"])<8 or
                not m.get("witness") or m["witness"]==claim["holder"]):
                db.execute("INSERT INTO equipment_dispute(equipment_id,kind,detail,evidence_sha,source_id) VALUES (?,?,?,?,?)",
                           (m["equipment"],"filter_merge_refused","merge %s lacks a real replacement serial"%m["id"],
                            str(m.get("document_sha") or ""),source))
                result["refused"]+=1;continue
            db.execute("UPDATE equipment_asset SET filter_id=?,filter_status='pending',status='pending_inspection' WHERE id=?",
                       (m["filter"],m["equipment"]))
            db.execute("INSERT INTO equipment_dispute(equipment_id,kind,detail,evidence_sha,source_id) VALUES (?,?,?,?,?)",
                       (m["equipment"],"filter_replaced","merge %s installed filter %s"%(m["id"],m["filter"]),
                        m["document_sha"],source))
            result["merged"]+=1
        return result
    return ingest(path,"equipment-merge",file,handler)

def equipment_inspect(path,file):
    """A witnessed inspection is the only way out of pending inspection.
    A filter recorded missing on return cannot be certified back into service
    by paperwork alone."""
    def handler(db,data,source):
        result={"passed":0,"failed":0}
        for i in data["inspections"]:
            inspected_at=_time(i["inspected_at"])
            asset=_equipment_row(db,i["equipment"])
            if not asset:raise ValueError("unknown_equipment")
            old=db.execute("SELECT * FROM equipment_inspection WHERE id=?",(i["id"],)).fetchone()
            values=(i["equipment"],asset["filter_id"],i["inspector"],i["witness"],
                    int(bool(i["body_ok"])),int(bool(i["filter_ok"])),i["condition"],i["inspected_at"],i["evidence_sha"])
            if old:
                if tuple(old[k] for k in ("equipment_id","filter_id","inspector","witness","body_ok",
                                          "filter_ok","condition","inspected_at","evidence_sha"))!=values:
                    raise _conflict("inspection_identity_conflict",i["equipment"],"inspection_conflict",
                                    "inspection %s re-declared with different terms"%i["id"])
                continue
            if asset["disposition_frozen"]:raise ValueError("equipment_frozen")
            last=db.execute("SELECT * FROM equipment_return WHERE equipment_id=? ORDER BY returned_at DESC LIMIT 1",
                            (i["equipment"],)).fetchone()
            if not last or asset["status"] not in ("returned","pending_inspection"):
                raise ValueError("inspection_not_applicable")
            if inspected_at<_time(last["returned_at"]):
                raise _conflict("late_document_conflict",i["equipment"],"late_document",
                                "inspection %s predates the return it examines"%i["id"])
            if (not i.get("inspector") or not i.get("witness") or i["inspector"]==i["witness"] or
                not i.get("evidence_sha") or len(i["evidence_sha"])<8 or i["condition"] not in RETURN_OUTCOMES):
                raise ValueError("inspection_evidence_insufficient")
            if asset["filter_status"]=="missing" and i["filter_ok"]:
                # Paper cannot replace a filter that was physically absent.
                raise ValueError("inspection_evidence_insufficient")
            passed=bool(i["body_ok"] and i["filter_ok"] and i["condition"]=="ok"
                        and asset["body_status"]!="missing" and asset["filter_status"]!="missing")
            db.execute("INSERT INTO equipment_inspection VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (i["id"],*values,source))
            if passed:
                status="available" if asset["owner"]==asset["site"] and asset["loan_settled"]==1 else "in_custody"
                db.execute("UPDATE equipment_asset SET body_status='ok',filter_status='ok',status=?,last_event_at=? WHERE id=?",
                           (status,i["inspected_at"],i["equipment"]))
                result["passed"]+=1
            else:
                # A failed check never launders a physically absent part into
                # present; only a real replacement can change that fact later.
                body=("missing" if asset["body_status"]=="missing" else "pending")
                filt=("missing" if asset["filter_status"]=="missing" else "pending")
                db.execute("UPDATE equipment_asset SET body_status=?,filter_status=?,status='pending_inspection',last_event_at=? WHERE id=?",
                           (body,filt,i["inspected_at"],i["equipment"]))
                result["failed"]+=1
        return result
    return ingest(path,"equipment-inspect",file,handler)
