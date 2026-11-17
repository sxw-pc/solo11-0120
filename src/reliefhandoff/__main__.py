import argparse
import json
import tempfile
from pathlib import Path
from .domain import arrival_import,household_import,delivery_import,arrival_reconcile,eligibility_review
from .report import report,report_json

COMMANDS={"arrival-import":arrival_import,"household-import":household_import,"delivery-import":delivery_import,
          "arrival-reconcile":arrival_reconcile,"eligibility-review":eligibility_review}

def demo(workdir=None):
    root=Path(workdir or tempfile.mkdtemp(prefix="reliefhandoff-"))
    root.mkdir(parents=True,exist_ok=True)
    db=root/"data.db"
    fixtures=Path.cwd()/"fixtures"
    results={}
    for name in ("arrival-import","household-import","delivery-import"):
        func=COMMANDS[name]
        results[name]=func(db,fixtures/(name+"_normal.json"))
    return {"db":str(db),"results":results}

def decision_demo(workdir=None):
    root=Path(workdir or tempfile.mkdtemp(prefix="reliefhandoff-decisions-"));root.mkdir(parents=True,exist_ok=True)
    db=root/"data.db";f=Path.cwd()/"fixtures"
    arrival_import(db,f/"arrival-import_normal.json")
    household_import(db,f/"household-import_normal.json")
    delivery_import(db,f/"delivery-import_normal.json")
    partial=report(db)["shortage_case"][0]["status"]
    arrival_import(db,f/"arrival-import_conflict.json")
    arrival_reconcile(db,f/"arrival-reconcile_normal.json")
    household_import(db,f/"household-import_conflict.json")
    eligibility_review(db,f/"eligibility-review_normal.json")
    delivery_import(db,f/"delivery-import_followup.json")
    return {"db":str(db),"initial_shortage":partial,"final_shortage":report(db)["shortage_case"][0]["status"],
            "final_receipts":len(report(db)["delivery_receipt"])}

def main():
    parser=argparse.ArgumentParser()
    subs=parser.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        sub=subs.add_parser(name);sub.add_argument("--db",required=True);sub.add_argument("--file",required=True)
    d=subs.add_parser("demo");d.add_argument("--workdir")
    dd=subs.add_parser("demo-decisions");dd.add_argument("--workdir")
    r=subs.add_parser("report");r.add_argument("--db",required=True)
    args=parser.parse_args()
    if args.command=="demo":print(json.dumps(demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-decisions":print(json.dumps(decision_demo(args.workdir),ensure_ascii=False))
    elif args.command=="report":print(report_json(args.db))
    else:print(json.dumps(COMMANDS[args.command](args.db,args.file),ensure_ascii=False))
if __name__=="__main__":main()
