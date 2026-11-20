import argparse
import json
import tempfile
from pathlib import Path
from .domain import (arrival_import,household_import,delivery_import,arrival_reconcile,eligibility_review,
                     equipment_arrival,equipment_authority,equipment_issue,equipment_return,equipment_inspect,
                     equipment_merge)
from .report import report,report_json

COMMANDS={"arrival-import":arrival_import,"household-import":household_import,"delivery-import":delivery_import,
          "arrival-reconcile":arrival_reconcile,"eligibility-review":eligibility_review,
          "equipment-arrival":equipment_arrival,"equipment-authority":equipment_authority,
          "equipment-issue":equipment_issue,"equipment-return":equipment_return,
          "equipment-inspect":equipment_inspect,"equipment-merge":equipment_merge}

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

def equipment_demo(workdir=None,transfer=False):
    """P1 + F1 arrives on a trusted manifest, clinic A takes it, returns it next
    day, and shelter B applies for a loan. Only a prior loan (settled cleanly)
    leaves the site able to lend; a prior transfer leaves A the owner."""
    root=Path(workdir or tempfile.mkdtemp(prefix="reliefhandoff-equipment-"));root.mkdir(parents=True,exist_ok=True)
    db=root/"data.db";f=Path.cwd()/"fixtures"
    arrival_import(db,f/"equipment-arrival_normal.json")
    equipment_arrival(db,f/"equipment-arrival_normal.json")
    equipment_authority(db,f/"equipment-authority_normal.json")
    equipment_issue(db,f/("equipment-transfer_normal.json" if transfer else "equipment-loan_normal.json"))
    equipment_return(db,f/("equipment-return_transfer.json" if transfer else "equipment-return_normal.json"))
    before=report(db)["equipment_units"][0]
    b=equipment_issue(db,f/"equipment-issue-clinicB.json")
    unit=report(db)["equipment_units"][0]
    return {"db":str(db),"mode":"transfer" if transfer else "loan","clinicB_issue":b,
            "before_clinicB":{"owner":before["owner"],"custodian":before["custodian"],
                              "loan_settled":before["loan_settled"],"filter_status":before["filter_status"],
                              "lendable":before["lendable"],"lendable_reason":before["lendable_reason"]},
            "owner":unit["owner"],"custodian":unit["custodian"],
            "lendable":unit["lendable"],"lendable_reason":unit["lendable_reason"],
            "lendable_equipment":report(db)["lendable_equipment"]}

def main():
    parser=argparse.ArgumentParser()
    subs=parser.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        sub=subs.add_parser(name);sub.add_argument("--db",required=True);sub.add_argument("--file",required=True)
    d=subs.add_parser("demo");d.add_argument("--workdir")
    dd=subs.add_parser("demo-decisions");dd.add_argument("--workdir")
    eq=subs.add_parser("demo-equipment");eq.add_argument("--workdir");eq.add_argument("--mode",choices=["loan","transfer"],default="loan")
    r=subs.add_parser("report");r.add_argument("--db",required=True)
    args=parser.parse_args()
    if args.command=="demo":print(json.dumps(demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-decisions":print(json.dumps(decision_demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-equipment":print(json.dumps(equipment_demo(args.workdir,args.mode=="transfer"),ensure_ascii=False))
    elif args.command=="report":print(report_json(args.db))
    else:print(json.dumps(COMMANDS[args.command](args.db,args.file),ensure_ascii=False))
if __name__=="__main__":main()
