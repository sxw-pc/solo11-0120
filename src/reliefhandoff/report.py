import json
from .store import connect,TABLES
from .publication import (PROTECT_K, certify, define_region, list_publications,
                          propose_publication, public_candidate_households, resolve_scope)

def report(path):
    db=connect(path)
    try:
        data={name:[dict(row) for row in db.execute("SELECT * FROM "+name)] for name in TABLES}
        data["handoff_chains"]=[dict(row) for row in db.execute("""
            SELECT h.id AS household,h.status AS eligibility,e.item,e.quantity AS entitlement,
                   COALESCE((SELECT SUM(r.quantity) FROM delivery_receipt r WHERE r.household_id=h.id AND r.item=e.item AND r.status IN ('complete','partial')),0) AS delivered,
                   COALESCE((SELECT SUM(c.missing) FROM shortage_case c WHERE c.household_id=h.id AND c.item=e.item AND c.status='open'),0) AS open_shortage,
                   s.sha AS household_source_sha
            FROM household h JOIN entitlement e ON e.household_id=h.id
            JOIN source_blob s ON s.id=h.source_id ORDER BY h.id,e.item
        """)]
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))

def public_pending_households(path,scope,item,cutoff):
    """待发布人群（内部核证视图）：实际已签收去重家庭，供核证而非对外公布。"""
    db=connect(path)
    try:
        _,_,sites=resolve_scope(db,scope)
        return sorted(public_candidate_households(db,sites,item,cutoff))
    finally:db.close()

def public_certify(path,scope,item,cutoff,k=PROTECT_K):
    """对当前数据重新核证一个候选数字，返回安全与否及依据，不写公告。"""
    db=connect(path)
    try:
        _,_,sites=resolve_scope(db,scope)
        candidate=public_candidate_households(db,sites,item,cutoff)
        safe,blockers=certify(db,candidate,k)
        return {"scope":scope,"item":item,"cutoff":cutoff,"candidate_count":len(candidate),
                "publishable":safe,"protect_k":k,"blockers":blockers}
    finally:db.close()

def public_proposal(path,scope,item,cutoff,publish=False,k=PROTECT_K):
    """公开统计入口：试发或正式发布一期提案，返回不含任何逐户信息的决定。"""
    return propose_publication(path,scope,item,cutoff,publish=publish,k=k)

def public_proposal_json(path,scope,item,cutoff,publish=False,k=PROTECT_K):
    return json.dumps(public_proposal(path,scope,item,cutoff,publish=publish,k=k),ensure_ascii=False,indent=2)

def public_ledger(path):
    """公开公告台账：只有汇总数字、粒度、来源摘要与试发留痕，无逐户清单。"""
    return list_publications(path)

def public_ledger_json(path):
    return json.dumps(public_ledger(path),ensure_ascii=False,indent=2)
