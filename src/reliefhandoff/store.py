import sqlite3
from pathlib import Path
SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_blob(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT,raw BLOB,result TEXT,UNIQUE(kind,sha));
CREATE TABLE IF NOT EXISTS source_failure(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT,raw BLOB,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS shipment(id TEXT PRIMARY KEY,site TEXT,item TEXT,expected INTEGER,seal TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS arrival_count(id TEXT PRIMARY KEY,shipment_id TEXT UNIQUE REFERENCES shipment(id),counted INTEGER,released_count INTEGER,seal TEXT,status TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS arrival_reconciliation(id TEXT PRIMARY KEY,arrival_id TEXT REFERENCES arrival_count(id),witness1 TEXT,witness2 TEXT,verified_count INTEGER,photo_sha TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS household(id TEXT PRIMARY KEY,zone TEXT,members INTEGER,credential_until TEXT,status TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS entitlement(household_id TEXT REFERENCES household(id),item TEXT,quantity INTEGER,PRIMARY KEY(household_id,item));
CREATE TABLE IF NOT EXISTS eligibility_review(id TEXT PRIMARY KEY,household_id TEXT REFERENCES household(id),decision TEXT,valid_until TEXT,reviewer TEXT,proof_sha TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS delivery_receipt(id TEXT PRIMARY KEY,household_id TEXT REFERENCES household(id),site TEXT,item TEXT,quantity INTEGER,delivered_at TEXT,signature TEXT,status TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS shortage_case(id INTEGER PRIMARY KEY,household_id TEXT REFERENCES household(id),item TEXT,receipt_id TEXT REFERENCES delivery_receipt(id),missing INTEGER,reason TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS equipment_manifest(id TEXT PRIMARY KEY,arrival_id TEXT UNIQUE REFERENCES arrival_count(id),shipment_id TEXT REFERENCES shipment(id),serials TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_asset(id TEXT PRIMARY KEY,kind TEXT,reusable INTEGER,filter_id TEXT UNIQUE,site TEXT,owner TEXT,custodian TEXT,status TEXT,body_status TEXT,filter_status TEXT,loan_settled INTEGER,disposition_frozen INTEGER DEFAULT 0,freeze_reason TEXT,manifest_id TEXT REFERENCES equipment_manifest(id),last_event_at TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_authority(id TEXT PRIMARY KEY,equipment_id TEXT REFERENCES equipment_asset(id),issuer TEXT,party TEXT,scope TEXT,valid_from TEXT,valid_until TEXT,document_sha TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_claim(id TEXT PRIMARY KEY,equipment_id TEXT REFERENCES equipment_asset(id),filter_id TEXT,holder TEXT,site TEXT,mode TEXT,issuer TEXT,authority_id TEXT REFERENCES equipment_authority(id),issued_at TEXT,effective_at TEXT,document_sha TEXT,status TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_return(id TEXT PRIMARY KEY,equipment_id TEXT REFERENCES equipment_asset(id),claim_id TEXT REFERENCES equipment_claim(id),filter_id TEXT,holder TEXT,site TEXT,witness TEXT,body_present INTEGER,filter_present INTEGER,condition TEXT,returned_at TEXT,document_sha TEXT,outcome TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_inspection(id TEXT PRIMARY KEY,equipment_id TEXT REFERENCES equipment_asset(id),filter_id TEXT,inspector TEXT,witness TEXT,body_ok INTEGER,filter_ok INTEGER,condition TEXT,inspected_at TEXT,evidence_sha TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS equipment_dispute(id INTEGER PRIMARY KEY,equipment_id TEXT REFERENCES equipment_asset(id),kind TEXT,detail TEXT,evidence_sha TEXT,failure_id INTEGER REFERENCES source_failure(id),created_at TEXT DEFAULT CURRENT_TIMESTAMP,source_id INTEGER REFERENCES source_blob(id));
"""
TABLES=["source_blob","shipment","arrival_count","arrival_reconciliation","household","entitlement","eligibility_review","delivery_receipt","shortage_case",
        "equipment_manifest","equipment_asset","equipment_authority","equipment_claim","equipment_return","equipment_inspection","equipment_dispute"]
TABLES.insert(1,"source_failure")
def connect(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
