import json
from pathlib import Path
import pytest
from reliefhandoff.domain import (arrival_import,arrival_reconcile,household_import,eligibility_review,delivery_import,
                                  equipment_arrival,equipment_authority,equipment_issue,equipment_return,
                                  equipment_inspect,equipment_merge)
from reliefhandoff.report import report
from reliefhandoff.__main__ import demo,decision_demo,equipment_demo
F=Path(__file__).parents[1]/"fixtures"

def baseline(db):
    arrival_import(db,F/"arrival-import_normal.json")
    household_import(db,F/"household-import_normal.json")

def test_offline_site_receipts_create_real_household_shortage(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    assert delivery_import(db,F/"delivery-import_normal.json")=={"complete":1,"partial":1,"held":0}
    state=report(db)
    assert state["shortage_case"][0]["missing"]==1
    assert state["shortage_case"][0]["reason"]=="site_supply_short"
    assert state["shortage_case"][0]["status"]=="open"
    assert sum(r["quantity"] for r in state["delivery_receipt"] if r["status"]!="held")==5

def test_broken_seal_has_no_usable_stock_until_dual_witness_recount(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival_import(db,F/"arrival-import_conflict.json")
    household_import(db,F/"household-import_normal.json")
    assert report(db)["arrival_count"][0]["released_count"]==0
    with pytest.raises(ValueError,match="broken_seal_evidence_insufficient"):
        arrival_reconcile(db,F/"arrival-reconcile_conflict.json")
    assert report(db)["source_failure"][-1]["error"]=="broken_seal_evidence_insufficient"
    assert arrival_reconcile(db,F/"arrival-reconcile_normal.json")["reconciled"]==1
    assert report(db)["arrival_count"][0]["released_count"]==3

def test_expired_family_review_and_replenishment_resolve_shortage(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    delivery_import(db,F/"delivery-import_normal.json")
    household_import(db,F/"household-import_conflict.json")
    assert next(h for h in report(db)["household"] if h["id"]=="H3")["status"]=="held"
    arrival_import(db,F/"arrival-import_conflict.json")
    arrival_reconcile(db,F/"arrival-reconcile_normal.json")
    assert eligibility_review(db,F/"eligibility-review_normal.json")["approved"]==1
    assert delivery_import(db,F/"delivery-import_followup.json")["complete"]==2
    assert report(db)["shortage_case"][0]["status"]=="resolved"

def test_duplicate_receipt_identity_conflict_does_not_double_deliver(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    delivery_import(db,F/"delivery-import_normal.json")
    with pytest.raises(ValueError,match="receipt_identity_conflict"):
        delivery_import(db,F/"delivery-import_conflict.json")
    assert len(report(db)["delivery_receipt"])==2
    assert report(db)["source_failure"][-1]["error"]=="receipt_identity_conflict"
    delivery_import(db,F/"delivery-import_normal.json")
    assert len(report(db)["delivery_receipt"])==2

def test_short_count_limits_physical_handover(tmp_path):
    db=tmp_path/"db.sqlite"
    payload={"shipments":[{"id":"S3","site":"HALL","item":"water","expected":5,"seal":"Z3"}],
             "counts":[{"id":"A3","shipment":"S3","counted":3,"seal":"Z3"}]}
    file=tmp_path/"short.json";file.write_text(json.dumps(payload),encoding="utf-8")
    arrival_import(db,file);household_import(db,F/"household-import_normal.json")
    assert report(db)["arrival_count"][0]["reason"]=="short_count"
    result=delivery_import(db,F/"delivery-import_normal.json")
    assert result["held"]==1 and result["partial"]==1
    assert next(r for r in report(db)["delivery_receipt"] if r["id"]=="R1")["reason"]=="insufficient_site_stock"

def test_unsigned_receipt_cannot_consume_site_count(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    payload={"receipts":[{"id":"RU","household":"H1","site":"HALL","item":"water","count":4,
                          "at":"2026-07-01T11:00:00+08:00","signature":""}]}
    file=tmp_path/"unsigned.json";file.write_text(json.dumps(payload),encoding="utf-8")
    assert delivery_import(db,file)["held"]==1
    assert report(db)["delivery_receipt"][0]["reason"]=="unsigned"

def test_demos(tmp_path):
    assert demo(tmp_path/"basic")["results"]["delivery-import"]["partial"]==1
    assert decision_demo(tmp_path/"decisions")["final_shortage"]=="resolved"

def test_recipient_partial_pickup_has_distinct_shortage_cause(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    payload={"receipts":[{"id":"R5","household":"H2","site":"HALL","item":"water","count":1,
                          "at":"2026-07-01T11:00:00+08:00","signature":"sig-H2"}]}
    file=tmp_path/"pickup.json";file.write_text(json.dumps(payload),encoding="utf-8")
    assert delivery_import(db,file)["partial"]==1
    assert report(db)["shortage_case"][0]["reason"]=="recipient_partial_pickup"

def test_household_identity_and_review_proof_cannot_be_rewritten(tmp_path):
    db=tmp_path/"db.sqlite";baseline(db)
    payload=json.loads((F/"household-import_normal.json").read_text())
    payload["households"][0]["members"]=3
    file=tmp_path/"changed.json";file.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError,match="household_identity_conflict"):household_import(db,file)
    household_import(db,F/"household-import_conflict.json")
    review=json.loads((F/"eligibility-review_normal.json").read_text())
    review["reviews"][0]["proof_sha"]="none"
    file=tmp_path/"review.json";file.write_text(json.dumps(review),encoding="utf-8")
    with pytest.raises(ValueError,match="eligibility_evidence_insufficient"):eligibility_review(db,file)
    assert next(h for h in report(db)["household"] if h["id"]=="H3")["status"]=="held"

def test_shipment_identity_conflict_does_not_add_physical_stock(tmp_path):
    db=tmp_path/"db.sqlite";arrival_import(db,F/"arrival-import_normal.json")
    payload=json.loads((F/"arrival-import_normal.json").read_text())
    payload["shipments"][0]["expected"]=99
    file=tmp_path/"changed.json";file.write_text(json.dumps(payload),encoding="utf-8")
    with pytest.raises(ValueError,match="shipment_identity_conflict"):arrival_import(db,file)
    assert len(report(db)["shipment"])==1
    assert report(db)["arrival_count"][0]["released_count"]==5

def equipment_baseline(db):
    arrival_import(db,F/"equipment-arrival_normal.json")
    assert equipment_arrival(db,F/"equipment-arrival_normal.json")=={"assets":1,"manifests":1}
    assert equipment_authority(db,F/"equipment-authority_normal.json")=={"authorities":2}

def unit(db):
    return report(db)["equipment_units"][0]

def test_equipment_serial_manifest_must_match_released_physical_count(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival_import(db,F/"equipment-arrival_mismatch.json")
    with pytest.raises(ValueError,match="serial_manifest_mismatch"):
        equipment_arrival(db,F/"equipment-arrival_mismatch.json")
    assert report(db)["equipment_asset"]==[]
    assert report(db)["source_failure"][-1]["error"]=="serial_manifest_mismatch"

def test_broken_seal_equipment_cannot_be_manifested_until_reconciled(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival_import(db,F/"equipment-arrival_broken_seal.json")
    with pytest.raises(ValueError,match="equipment_arrival_not_released"):
        equipment_arrival(db,F/"equipment-manifest_broken_seal.json")
    assert report(db)["equipment_asset"]==[]
    # A witnessed recount releases the physical units; only then can serials be
    # established, and the arrived equipment becomes lendable.
    arrival_reconcile(db,F/"equipment-reconcile_broken_seal.json")
    assert equipment_arrival(db,F/"equipment-manifest_broken_seal.json")=={"assets":2,"manifests":1}
    assert set(report(db)["lendable_equipment"])=={"P3","P4"}

def test_loan_return_then_site_can_lend_same_unit_to_shelter_b(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    assert equipment_issue(db,F/"equipment-loan_normal.json")["loan"]==1
    assert equipment_issue(db,F/"equipment-issue-clinicB.json")["refused"]==1
    assert report(db)["lendable_equipment"]==[]
    assert equipment_return(db,F/"equipment-return_normal.json")["clean"]==1
    assert unit(db)["loan_settled"] is True and unit(db)["custodian"]=="HALL"
    assert report(db)["lendable_equipment"]==["P1"]
    assert equipment_issue(db,F/"equipment-issue-clinicB_reapply.json")=={"loan":1,"transfer":0,"refused":0}
    assert unit(db)["custodian"]=="clinic-B" and unit(db)["owner"]=="HALL"

def test_transferred_unit_returned_to_storage_is_not_the_sites_to_lend(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    assert equipment_issue(db,F/"equipment-transfer_normal.json")["transfer"]==1
    assert unit(db)["owner"]=="clinic-A"
    assert equipment_return(db,F/"equipment-return_transfer.json")["clean"]==1
    # The next-day handover only moves custody. Ownership did not come back.
    assert unit(db)["owner"]=="clinic-A" and unit(db)["custodian"]=="HALL"
    assert unit(db)["lendable"] is False and unit(db)["lendable_reason"]=="owner_not_site"
    assert equipment_issue(db,F/"equipment-issue-clinicB.json")=={"loan":0,"transfer":0,"refused":1}
    claim=next(c for c in report(db)["equipment_claim"] if c["id"]=="CLB1")
    assert claim["status"]=="refused" and claim["reason"]=="owner_not_site"
    assert report(db)["lendable_equipment"]==[]

def test_loan_cannot_be_issued_twice_while_out_on_loan(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    # Re-importing the same signed document is idempotent, not a second loan.
    assert equipment_issue(db,F/"equipment-loan_normal.json")["loan"]==1
    # A distinct request while still out is refused, stock stays untouched.
    payload=json.loads((F/"equipment-loan_normal.json").read_text())
    payload["issues"][0]["id"]="CL1-duplicate-request"
    payload["issues"][0]["issued_at"]="2026-07-01T13:00:00+08:00"
    payload["issues"][0]["effective_at"]="2026-07-01T13:00:00+08:00"
    file=tmp_path/"dup.json";file.write_text(json.dumps(payload),encoding="utf-8")
    assert equipment_issue(db,file)=={"loan":0,"transfer":0,"refused":1}
    assert len([c for c in report(db)["equipment_claim"] if c["status"]=="active"])==1
    assert unit(db)["custodian"]=="clinic-A"

def test_missing_filter_return_blocks_lending_in_both_modes(tmp_path):
    for issue,ret in (("equipment-loan_normal.json","equipment-return_missing_filter.json"),
                      ("equipment-transfer_normal.json","equipment-return_missing_filter_transfer.json")):
        db=tmp_path/("db-"+issue);db.mkdir()
        db=db/"db.sqlite";equipment_baseline(db)
        equipment_issue(db,F/issue)
        assert equipment_return(db,F/ret)["missing_filter"]==1
        state=unit(db)
        assert state["status"]=="pending_inspection" and state["filter_status"]=="missing"
        assert state["lendable"] is False
        assert report(db)["lendable_equipment"]==[]
        assert equipment_issue(db,F/"equipment-issue-clinicB.json")["refused"]==1
        # A handover sheet that claims the filter is fine cannot restore it.
        with pytest.raises(ValueError,match="inspection_evidence_insufficient"):
            equipment_inspect(db,F/"equipment-inspect_missing_filter.json")
        assert unit(db)["lendable"] is False

def test_replacement_filter_serial_then_witnessed_check_restores_lendability(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    equipment_return(db,F/"equipment-return_missing_filter.json")
    merge={"merges":[{"id":"MG1","equipment":"P1","claim":"CL1","filter":"F9","witness":"supervisor-1",
                      "document_sha":"doc-merge-1"}]}
    file=tmp_path/"merge.json";file.write_text(json.dumps(merge),encoding="utf-8")
    assert equipment_merge(db,file)=={"merged":1,"refused":0}
    assert unit(db)["filter"]=="F9" and unit(db)["lendable"] is False
    inspect={"inspections":[{"id":"INS2","equipment":"P1","inspector":"technician-1","witness":"supervisor-1",
                             "body_ok":True,"filter_ok":True,"condition":"ok",
                             "inspected_at":"2026-07-02T11:00:00+08:00","evidence_sha":"inspect-photo-2"}]}
    file=tmp_path/"inspect.json";file.write_text(json.dumps(inspect),encoding="utf-8")
    assert equipment_inspect(db,file)=={"passed":1,"failed":0}
    assert unit(db)["lendable"] is True and unit(db)["filter"]=="F9"

def test_duplicate_import_is_idempotent(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    assert equipment_issue(db,F/"equipment-loan_normal.json")=={"loan":1,"transfer":0,"refused":0}
    assert equipment_issue(db,F/"equipment-loan_normal.json")=={"loan":1,"transfer":0,"refused":0}
    assert len(report(db)["equipment_claim"])==1
    assert len(report(db)["equipment_asset"])==1
    assert unit(db)["custodian"]=="clinic-A"

def test_conflicting_claim_keeps_old_evidence_and_freezes_disposition(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    before=unit(db)["custodian"]
    with pytest.raises(ValueError,match="claim_identity_conflict"):
        equipment_issue(db,F/"equipment-claim_conflict.json")
    state=unit(db)
    assert state["custodian"]==before and state["disposition_frozen"] is True
    assert state["freeze_reason"]=="claim_conflict"
    assert report(db)["equipment_dispute"][0]["kind"]=="claim_conflict"
    assert report(db)["equipment_dispute"][0]["evidence_sha"]==report(db)["source_failure"][-1]["sha"]
    # The already-issued loan is not rewritten; later dispositions are frozen.
    with pytest.raises(ValueError,match="equipment_frozen"):
        equipment_return(db,F/"equipment-return_normal.json")
    with pytest.raises(ValueError,match="equipment_frozen"):
        equipment_issue(db,F/"equipment-issue-clinicB.json")

def test_late_document_cannot_overwrite_completed_handover(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    with pytest.raises(ValueError,match="late_document_conflict"):
        equipment_issue(db,F/"equipment-claim_late.json")
    assert unit(db)["custodian"]=="clinic-A"
    assert unit(db)["disposition_frozen"] is True
    dispute=report(db)["equipment_dispute"][0]
    assert dispute["kind"]=="late_document" and "predates" in dispute["detail"]

def test_rival_ownership_claim_freezes_without_handing_over_the_unit(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    with pytest.raises(ValueError,match="no_disposition_authority"):
        equipment_issue(db,F/"equipment-claim_rival_owner.json")
    state=unit(db)
    assert state["owner"]=="HALL" and state["custodian"]=="HALL"
    assert state["disposition_frozen"] is True
    assert report(db)["equipment_dispute"][0]["kind"]=="ownership_claim_conflict"

def test_conflicting_manifest_and_authority_leave_old_rows_intact(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    with pytest.raises(ValueError,match="authority_identity_conflict"):
        equipment_authority(db,F/"equipment-authority_conflict.json")
    assert next(a for a in report(db)["equipment_authority"] if a["id"]=="AU1")["scope"]=="loan,transfer"
    assert unit(db)["disposition_frozen"] is True

def test_equipment_demo_loan_vs_transfer(tmp_path):
    loan=equipment_demo(tmp_path/"loan")
    assert loan["clinicB_issue"]=={"loan":1,"transfer":0,"refused":0}
    assert loan["before_clinicB"]["lendable"] is True
    assert loan["owner"]=="HALL" and loan["custodian"]=="clinic-B"
    transfer=equipment_demo(tmp_path/"transfer",transfer=True)
    assert transfer["clinicB_issue"]=={"loan":0,"transfer":0,"refused":1}
    assert transfer["before_clinicB"]["lendable"] is False
    assert transfer["owner"]=="clinic-A" and transfer["custodian"]=="HALL"

def test_duplicate_equipment_import_is_idempotent(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    assert equipment_arrival(db,F/"equipment-arrival_normal.json")=={"assets":1,"manifests":1}
    assert equipment_authority(db,F/"equipment-authority_normal.json")=={"authorities":2}
    assert len(report(db)["equipment_manifest"])==1
    assert len(report(db)["equipment_authority"])==2
    assert len(report(db)["equipment_asset"])==1
    assert report(db)["equipment_units"][0]["source_sha"]==report(db)["source_blob"][0]["sha"]

def test_conflicting_manifest_keeps_original_serials_and_freezes(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    with pytest.raises(ValueError,match="equipment_manifest_conflict"):
        equipment_arrival(db,F/"equipment-manifest_conflict.json")
    assert [a["id"] for a in report(db)["equipment_asset"]]==["P1"]
    assert report(db)["equipment_units"][0]["disposition_frozen"] is True
    assert report(db)["equipment_units"][0]["filter"]=="F1"
    dispute=report(db)["equipment_dispute"][0]
    assert dispute["kind"]=="manifest_conflict" and dispute["evidence_sha"]==report(db)["source_failure"][-1]["sha"]

def test_conflicting_return_does_not_rewrite_the_recorded_condition(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    equipment_return(db,F/"equipment-return_normal.json")
    with pytest.raises(ValueError,match="return_identity_conflict"):
        equipment_return(db,F/"equipment-return_conflict.json")
    ret=report(db)["equipment_return"][0]
    assert ret["condition"]=="ok" and ret["outcome"]=="clean"
    assert report(db)["equipment_units"][0]["disposition_frozen"] is True

def test_filter_merge_without_a_real_replacement_serial_is_refused(tmp_path):
    db=tmp_path/"db.sqlite";equipment_baseline(db)
    equipment_issue(db,F/"equipment-loan_normal.json")
    equipment_return(db,F/"equipment-return_missing_filter.json")
    merge={"merges":[{"id":"MGBAD","equipment":"P1","claim":"CL1","filter":"F1","witness":"supervisor-1",
                      "document_sha":"doc-merge-bad"}]}
    file=tmp_path/"bad.json";file.write_text(json.dumps(merge),encoding="utf-8")
    assert equipment_merge(db,file)=={"merged":0,"refused":1}
    assert unit(db)["filter_status"]=="missing" and unit(db)["lendable"] is False
    assert report(db)["lendable_equipment"]==[]
