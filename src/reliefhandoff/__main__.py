import argparse
import json
import tempfile
from pathlib import Path
from .domain import arrival_import,household_import,delivery_import,arrival_reconcile,eligibility_review
from .report import report,report_json,public_proposal,public_proposal_json,public_ledger_json
from .publication import define_region

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
    rd=subs.add_parser("region-define");rd.add_argument("--db",required=True);rd.add_argument("--code",required=True)
    rd.add_argument("--name",required=True);rd.add_argument("--sites",required=True,help="comma-separated site codes fixed before certification")
    pp=subs.add_parser("publication-propose");pp.add_argument("--db",required=True);pp.add_argument("--scope",required=True)
    pp.add_argument("--item",required=True);pp.add_argument("--cutoff",required=True)
    pp.add_argument("--publish",action="store_true",help="formally publish when certification is safe; default is trial run only")
    pl=subs.add_parser("publication-list");pl.add_argument("--db",required=True)
    args=parser.parse_args()
    if args.command=="demo":print(json.dumps(demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-decisions":print(json.dumps(decision_demo(args.workdir),ensure_ascii=False))
    elif args.command=="report":print(report_json(args.db))
    elif args.command=="region-define":
        print(json.dumps(define_region(args.db,args.code,args.name,[s.strip() for s in args.sites.split(",") if s.strip()]),
                         ensure_ascii=False))
    elif args.command=="publication-propose":print(public_proposal_json(args.db,args.scope,args.item,args.cutoff,publish=args.publish))
    elif args.command=="publication-list":print(public_ledger_json(args.db))
    else:print(json.dumps(COMMANDS[args.command](args.db,args.file),ensure_ascii=False))
if __name__=="__main__":main()
