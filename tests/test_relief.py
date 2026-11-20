import json
from pathlib import Path
import pytest
from reliefhandoff.domain import (arrival_import,arrival_reconcile,household_import,eligibility_review,
                                  delivery_import,picking_import,recall_import)
from reliefhandoff.report import report
from reliefhandoff.__main__ import demo,decision_demo
F=Path(__file__).parents[1]/"fixtures"

def write(tmp_path,name,payload):
    file=tmp_path/name;file.write_text(json.dumps(payload),encoding="utf-8");return file

def baseline(db):
    arrival_import(db,F/"arrival-import_normal.json")
    household_import(db,F/"household-import_normal.json")
    picking_import(db,F/"picking-import_normal.json")

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
             "counts":[{"id":"A3","shipment":"S3","counted":3,"seal":"Z3","counted_at":"2026-07-01T10:00:00+08:00"}]}
    file=tmp_path/"short.json";file.write_text(json.dumps(payload),encoding="utf-8")
    arrival_import(db,file);household_import(db,F/"household-import_normal.json")
    picking_import(db,F/"picking-import_normal.json")
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


# ---------------------------------------------------------------------------
# Source registration, picking contracts and offline recall reconciliation
# ---------------------------------------------------------------------------

HOUSEHOLDS_TWO={"disaster_zone":"flood-A","asof":"2026-07-01T10:00:00+08:00","item":"water","units_per_member":2,
                "households":[{"id":"H1","zone":"flood-A","members":1,"credential_until":"2027-01-01T00:00:00+08:00"},
                              {"id":"H2","zone":"flood-A","members":1,"credential_until":"2027-01-01T00:00:00+08:00"}]}

TWO_BATCHES={"shipments":[{"id":"S1","site":"HALL","item":"water","expected":2,"seal":"Z1"},
                          {"id":"S2","site":"HALL","item":"water","expected":2,"seal":"Z2"}],
             "counts":[{"id":"A1","shipment":"S1","counted":2,"seal":"Z1","counted_at":"2026-07-01T10:00:00+08:00"},
                       {"id":"A2","shipment":"S2","counted":2,"seal":"Z2","counted_at":"2026-07-01T10:05:00+08:00"}]}

TWO_RECEIPTS={"receipts":[{"id":"R1","household":"H1","site":"HALL","item":"water","count":2,
                           "at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"},
                          {"id":"R2","household":"H2","site":"HALL","item":"water","count":2,
                           "at":"2026-07-01T11:10:00+08:00","signature":"sig-H2"}]}


def _setup_batches_households(tmp_path,policy):
    db=tmp_path/"db.sqlite"
    arrival_import(db,write(tmp_path,"arrival.json",TWO_BATCHES))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":policy,
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    return db


def test_fifo_picking_recall_s1_contacts_h1(tmp_path):
    db=_setup_batches_households(tmp_path,"fifo")
    assert delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))=={"complete":2,"partial":0,"held":0}
    state=report(db)
    src={r["receipt_id"]:{s["shipment_id"]:s["quantity"] for s in r["sources"]} for r in state["receipt_sources"]}
    assert src=={"R1":{"S1":2},"R2":{"S2":2}}
    # Per-batch remaining allocatable counts after handover.
    remaining={b["shipment_id"]:b["remaining_allocatable"] for b in state["batch_availability"]}
    assert remaining=={"S1":0,"S2":0}
    recall_import(db,write(tmp_path,"recall.json",
                           {"recalls":[{"id":"K1","shipment":"S1","recalled_at":"2026-07-01T13:00:00+08:00",
                                        "reason":"contaminated batch"}]}))
    contact=report(db)["recall_contacts"][0]
    assert contact["contact_households"]==["H1"]
    assert contact["isolated_count"]==0
    assert contact["delivered_shares"]==[{"household_id":"H1","receipt_id":"R1","quantity":2,
                                          "delivered_at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"}]
    # S2 water is not judged recalled.
    assert next(b for b in report(db)["batch_availability"] if b["shipment_id"]=="S2")["status"]=="usable"
    assert next(b for b in report(db)["batch_availability"] if b["shipment_id"]=="S1")["status"]=="recalled"


def test_lifo_picking_recall_s1_contacts_h2(tmp_path):
    db=_setup_batches_households(tmp_path,"lifo")
    delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))
    state=report(db)
    src={r["receipt_id"]:{s["shipment_id"]:s["quantity"] for s in r["sources"]} for r in state["receipt_sources"]}
    # Same total delivered, opposite per-batch provenance.
    assert src=={"R1":{"S2":2},"R2":{"S1":2}}
    recall_import(db,write(tmp_path,"recall.json",
                           {"recalls":[{"id":"K1","shipment":"S1","recalled_at":"2026-07-01T13:00:00+08:00",
                                        "reason":"contaminated batch"}]}))
    contact=report(db)["recall_contacts"][0]
    assert contact["contact_households"]==["H2"]
    assert contact["indeterminate_legacy_receipts"]==[]


def test_one_receipt_spans_two_batches_and_sources_sum_to_quantity(tmp_path):
    db=tmp_path/"db.sqlite"
    # S1 has 2 usable, S2 has 2; one household with entitlement 4 picks 3 at once.
    households={"disaster_zone":"flood-A","asof":"2026-07-01T10:00:00+08:00","item":"water","units_per_member":2,
                "households":[{"id":"H1","zone":"flood-A","members":2,"credential_until":"2027-01-01T00:00:00+08:00"}]}
    arrival_import(db,write(tmp_path,"arrival.json",TWO_BATCHES))
    household_import(db,write(tmp_path,"households.json",households))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"fifo",
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    receipt={"receipts":[{"id":"R1","household":"H1","site":"HALL","item":"water","count":3,
                          "at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"}]}
    delivery_import(db,write(tmp_path,"delivery.json",receipt))
    record=report(db)["receipt_sources"][0]
    assert {s["shipment_id"]:s["quantity"] for s in record["sources"]}=={"S1":2,"S2":1}
    assert record["sourced_quantity"]==record["quantity"]==3
    # The single physical unit left in S2 is still allocatable; S1 is exhausted.
    remaining={b["shipment_id"]:b["remaining_allocatable"] for b in report(db)["batch_availability"]}
    assert remaining=={"S1":0,"S2":1}


def test_broken_seal_reconciled_after_receipt_cannot_source_that_receipt(tmp_path):
    db=tmp_path/"db.sqlite"
    # S1 intact 2 units usable 10:00; S3 broken, only witnessed at 11:05.
    arrival={"shipments":[{"id":"S1","site":"HALL","item":"water","expected":2,"seal":"Z1"},
                          {"id":"S3","site":"HALL","item":"water","expected":2,"seal":"Z3"}],
             "counts":[{"id":"A1","shipment":"S1","counted":2,"seal":"Z1","counted_at":"2026-07-01T10:00:00+08:00"},
                       {"id":"A3","shipment":"S3","counted":2,"seal":"BROKEN","counted_at":"2026-07-01T10:10:00+08:00"}]}
    arrival_import(db,write(tmp_path,"arrival.json",arrival))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"fifo",
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    # H1's 11:00 receipt can only see S1.
    first={"receipts":[{"id":"R1","household":"H1","site":"HALL","item":"water","count":2,
                        "at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"}]}
    delivery_import(db,write(tmp_path,"first.json",first))
    assert {s["shipment_id"] for s in report(db)["receipt_sources"][0]["sources"]}=={"S1"}
    reconcile={"reconciliations":[{"id":"ARC1","arrival":"A3","witness1":"volunteer-1","witness2":"supervisor-1",
                                   "verified_count":2,"photo_sha":"photo-abc123","reason":"witnessed recount",
                                   "reconciled_at":"2026-07-01T11:05:00+08:00"}]}
    arrival_reconcile(db,write(tmp_path,"reconcile.json",reconcile))
    s3=next(b for b in report(db)["batch_availability"] if b["shipment_id"]=="S3")
    assert s3["status"]=="usable" and s3["usable_at"]=="2026-07-01T11:05:00+08:00"
    # H2 at 11:10 is now served entirely from the late-reconciled batch.
    second={"receipts":[{"id":"R2","household":"H2","site":"HALL","item":"water","count":2,
                         "at":"2026-07-01T11:10:00+08:00","signature":"sig-H2"}]}
    delivery_import(db,write(tmp_path,"second.json",second))
    assert {s["shipment_id"] for s in report(db)["receipt_sources"][1]["sources"]}=={"S3"}
    # The 11:00 receipt's frozen source is untouched by the later reconciliation.
    assert {s["shipment_id"] for s in report(db)["receipt_sources"][0]["sources"]}=={"S1"}


def test_recall_isolates_undelivered_units_and_keeps_other_batches_allocatable(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival={"shipments":[{"id":"S1","site":"HALL","item":"water","expected":2,"seal":"Z1"},
                          {"id":"S2","site":"HALL","item":"water","expected":2,"seal":"Z2"}],
             "counts":[{"id":"A1","shipment":"S1","counted":2,"seal":"Z1","counted_at":"2026-07-01T10:00:00+08:00"},
                       {"id":"A2","shipment":"S2","counted":2,"seal":"Z2","counted_at":"2026-07-01T10:05:00+08:00"}]}
    arrival_import(db,write(tmp_path,"arrival.json",arrival))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"fifo",
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    # H1 takes one only (partial pickup): one S1 unit remains undelivered.
    receipt={"receipts":[{"id":"R1","household":"H1","site":"HALL","item":"water","count":1,
                          "at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"}]}
    delivery_import(db,write(tmp_path,"delivery.json",receipt))
    recall_import(db,write(tmp_path,"recall.json",
                           {"recalls":[{"id":"K1","shipment":"S1","recalled_at":"2026-07-01T12:00:00+08:00",
                                        "reason":"contaminated batch"}]}))
    contact=report(db)["recall_contacts"][0]
    assert contact["isolated_count"]==1
    assert contact["contact_households"]==["H1"]
    batches={b["shipment_id"]:b for b in report(db)["batch_availability"]}
    assert batches["S1"]["status"]=="recalled" and batches["S1"]["remaining_allocatable"]==0
    assert batches["S2"]["status"]=="usable" and batches["S2"]["remaining_allocatable"]==2
    # A later receipt must skip the recalled batch and draw from S2.
    later={"receipts":[{"id":"R2","household":"H2","site":"HALL","item":"water","count":2,
                        "at":"2026-07-01T12:30:00+08:00","signature":"sig-H2"}]}
    delivery_import(db,write(tmp_path,"later.json",later))
    assert {s["shipment_id"] for s in report(db)["receipt_sources"][1]["sources"]}=={"S2"}


def test_replenishment_after_recall_serves_shortage_from_new_batch(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival_import(db,write(tmp_path,"arrival.json",TWO_BATCHES))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"fifo",
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))
    recall_import(db,write(tmp_path,"recall-s2.json",
                           {"recalls":[{"id":"K1","shipment":"S2","recalled_at":"2026-07-01T13:00:00+08:00",
                                        "reason":"damaged in storage"}]}))
    # A fresh batch arrives after the recall and feeds new demand.
    replenishment={"shipments":[{"id":"S4","site":"HALL","item":"water","expected":3,"seal":"Z4"}],
                   "counts":[{"id":"A4","shipment":"S4","counted":3,"seal":"Z4","counted_at":"2026-07-01T14:00:00+08:00"}]}
    arrival_import(db,write(tmp_path,"replenish.json",replenishment))
    households3={"disaster_zone":"flood-A","asof":"2026-07-01T10:00:00+08:00","item":"water","units_per_member":2,
                 "households":[{"id":"H3","zone":"flood-A","members":1,"credential_until":"2027-01-01T00:00:00+08:00"}]}
    household_import(db,write(tmp_path,"h3.json",households3))
    receipt={"receipts":[{"id":"R3","household":"H3","site":"HALL","item":"water","count":2,
                          "at":"2026-07-01T14:30:00+08:00","signature":"sig-H3"}]}
    delivery_import(db,write(tmp_path,"r3.json",receipt))
    assert {s["shipment_id"] for s in report(db)["receipt_sources"][2]["sources"]}=={"S4"}
    recall=report(db)["recall_contacts"][0]
    assert recall["contact_households"]==["H2"]  # frozen FIFO sources do not move


def test_duplicate_receipt_reimport_keeps_frozen_sources(tmp_path):
    db=_setup_batches_households(tmp_path,"fifo")
    delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))
    # Re-importing the identical blob is idempotent and must not re-pick sources.
    duplicate=write(tmp_path,"delivery-again.json",TWO_RECEIPTS)
    assert delivery_import(db,duplicate)=={"complete":2,"partial":0,"held":0}  # cached source-blob replay
    state=report(db)
    assert len(state["delivery_receipt"])==2
    assert {row["receipt_id"] for row in state["delivery_source"]}=={"R1","R2"}
    # A changed re-import of the same receipt id still conflicts and leaves no extra rows.
    changed=json.loads(json.dumps(TWO_RECEIPTS));changed["receipts"][0]["count"]=1
    with pytest.raises(ValueError,match="receipt_identity_conflict"):
        delivery_import(db,write(tmp_path,"changed.json",changed))
    assert len(report(db)["delivery_receipt"])==2


def test_delivery_without_picking_contract_is_held_and_records_no_sources(tmp_path):
    db=tmp_path/"db.sqlite"
    arrival_import(db,write(tmp_path,"arrival.json",TWO_BATCHES))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))
    receipts=report(db)["delivery_receipt"]
    assert all(r["reason"]=="picking_contract_not_selected" and r["status"]=="held" for r in receipts)
    assert report(db)["delivery_source"]==[]


def test_picking_contract_cannot_change_after_delivery(tmp_path):
    db=_setup_batches_households(tmp_path,"fifo")
    delivery_import(db,write(tmp_path,"delivery.json",TWO_RECEIPTS))
    with pytest.raises(ValueError,match="picking_contract"):
        picking_import(db,write(tmp_path,"lifo.json",
                                {"contracts":[{"site":"HALL","item":"water","picking_policy":"lifo",
                                               "selected_at":"2026-07-01T12:00:00+08:00"}]}))
    # Re-asserting the same frozen policy from another blob creates no second contract row.
    picking_import(db,write(tmp_path,"fifo-again.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"fifo",
                                           "selected_at":"2026-07-01T09:05:00+08:00"}]}))
    assert len(report(db)["picking_contract"])==1
    assert report(db)["delivery_receipt"][0]["picking_policy"]=="fifo"


def test_late_batch_available_after_receipt_is_not_backfilled_into_it(tmp_path):
    db=tmp_path/"db.sqlite"
    # S1 alone exists at first; S2 arrives and is counted only at 11:20.
    first_arrival={"shipments":[{"id":"S1","site":"HALL","item":"water","expected":2,"seal":"Z1"}],
                   "counts":[{"id":"A1","shipment":"S1","counted":2,"seal":"Z1","counted_at":"2026-07-01T10:00:00+08:00"}]}
    arrival_import(db,write(tmp_path,"arrival1.json",first_arrival))
    household_import(db,write(tmp_path,"households.json",HOUSEHOLDS_TWO))
    picking_import(db,write(tmp_path,"picking.json",
                            {"contracts":[{"site":"HALL","item":"water","picking_policy":"lifo",
                                           "selected_at":"2026-07-01T09:00:00+08:00"}]}))
    delivery_import(db,write(tmp_path,"r1.json",
                             {"receipts":[{"id":"R1","household":"H1","site":"HALL","item":"water","count":2,
                                           "at":"2026-07-01T11:00:00+08:00","signature":"sig-H1"}]}))
    late={"shipments":[{"id":"S2","site":"HALL","item":"water","expected":2,"seal":"Z2"}],
          "counts":[{"id":"A2","shipment":"S2","counted":2,"seal":"Z2","counted_at":"2026-07-01T11:20:00+08:00"}]}
    arrival_import(db,write(tmp_path,"arrival2.json",late))
    delivery_import(db,write(tmp_path,"r2.json",
                             {"receipts":[{"id":"R2","household":"H2","site":"HALL","item":"water","count":2,
                                           "at":"2026-07-01T11:30:00+08:00","signature":"sig-H2"}]}))
    # Even under LIFO, R1 cannot have been sourced from the not-yet-arrived S2.
    sources={r["receipt_id"]:{s["shipment_id"] for s in r["sources"]} for r in report(db)["receipt_sources"]}
    assert sources=={"R1":{"S1"},"R2":{"S2"}}
    recall_import(db,write(tmp_path,"recall.json",
                           {"recalls":[{"id":"K1","shipment":"S1","recalled_at":"2026-07-01T13:00:00+08:00",
                                        "reason":"contaminated batch"}]}))
    assert report(db)["recall_contacts"][0]["contact_households"]==["H1"]


def test_legacy_receipt_without_sources_is_unknown_and_never_backfilled(tmp_path):
    db=tmp_path/"db.sqlite"
    # Simulate a database written before source registration existed.
    from reliefhandoff.store import connect
    legacy=connect(db)
    legacy.execute("INSERT INTO source_blob(kind,sha,raw) VALUES ('household','legacy',x'')")
    sid=legacy.execute("SELECT id FROM source_blob WHERE sha='legacy'").fetchone()[0]
    legacy.execute("INSERT INTO household VALUES ('H9','flood-A',1,'2027-01-01T00:00:00+08:00','eligible',?)",(sid,))
    legacy.execute("INSERT INTO entitlement VALUES ('H9','water',2)")
    legacy.execute("INSERT INTO shipment VALUES ('S1','HALL','water',2,'Z1',?)",(sid,))
    legacy.execute("INSERT INTO arrival_count(id,shipment_id,counted,released_count,seal,status,reason,usable_at,source_id)"
                   " VALUES ('A1','S1',2,2,'Z1','usable','usable','2026-07-01T10:00:00+08:00',?)",(sid,))
    legacy.execute("INSERT INTO delivery_receipt(id,household_id,site,item,quantity,delivered_at,signature,status,reason,picking_policy,source_id)"
                   " VALUES ('R9','H9','HALL','water',2,'2026-07-01T11:00:00+08:00','sig-H9','complete','complete',NULL,?)",(sid,))
    legacy.commit();legacy.close()
    state=report(db)
    unknown=state["unknown_source_receipts"]
    assert unknown==[{"receipt_id":"R9","household_id":"H9","site":"HALL","item":"water","quantity":2,
                      "delivered_at":"2026-07-01T11:00:00+08:00"}]
    record=next(r for r in state["receipt_sources"] if r["receipt_id"]=="R9")
    assert record["source_known"] is False and record["sources"]==[] and record["sourced_quantity"]==0
    # A recall cannot manufacture provenance: the legacy receipt is listed as indeterminate,
    # not treated as a confirmed S1 contact.
    recall_import(db,write(tmp_path,"recall.json",
                           {"recalls":[{"id":"K1","shipment":"S1","recalled_at":"2026-07-01T13:00:00+08:00",
                                        "reason":"contaminated batch"}]}))
    contact=report(db)["recall_contacts"][0]
    assert contact["contact_households"]==[]
    assert [r["receipt_id"] for r in contact["indeterminate_legacy_receipts"]]==["R9"]
    assert contact["isolated_count"]==0  # both units were signed for, just without provenance


def test_receipt_carries_evidence_sha_and_frozen_policy(tmp_path):
    db=_setup_batches_households(tmp_path,"lifo")
    delivery_file=write(tmp_path,"delivery.json",TWO_RECEIPTS)
    delivery_import(db,delivery_file)
    import hashlib
    expected_sha=hashlib.sha256(delivery_file.read_bytes()).hexdigest()
    for record in report(db)["receipt_sources"]:
        assert record["delivery_source_sha"]==expected_sha
        assert record["picking_policy"]=="lifo"
        assert record["source_known"] is True
