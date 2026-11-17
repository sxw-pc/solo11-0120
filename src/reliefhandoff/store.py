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
"""
TABLES=["source_blob","shipment","arrival_count","arrival_reconciliation","household","entitlement","eligibility_review","delivery_receipt","shortage_case"]
TABLES.insert(1,"source_failure")
def connect(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
