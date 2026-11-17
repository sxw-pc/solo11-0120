import json
from pathlib import Path
import pytest
from reliefhandoff.domain import arrival_import,arrival_reconcile,household_import,eligibility_review,delivery_import
from reliefhandoff.report import report
from reliefhandoff.__main__ import demo,decision_demo
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
