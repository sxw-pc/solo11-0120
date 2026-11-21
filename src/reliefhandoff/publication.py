"""公开统计入口。

公开版只输出按站点或事先固定区域、品类、截止时刻汇总的实际已签收家庭数：
同一户多次补领只算一户；原始单据、家庭标识与签名永远不进入公开结果。

每次试发都会与 *全部* 既往公开记录一起核证（跨站组合、跨期相减、同户多收据
都在检查范围内），任何可由公开数字推回的、少于 ``PROTECT_K`` 户的新增领取段
都会使提案暂缓。发布粒度与保护界限在核证前固定，不能看到结果后改分组试探。
"""
import json
from datetime import datetime

from .store import connect, now_iso

PROTECT_K = 3


def _time(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise ValueError("timezone_required")
    return t


def resolve_scope(db, scope):
    """把站点名或预先固定的区域代码解析成站点列表。

    区域必须在数据核证前经 ``define_region`` 固定，解析时按定义冻结；
    不允许在提案时临时拼分组。
    """
    row = db.execute("SELECT * FROM region_def WHERE code=?", (scope,)).fetchone()
    if row:
        return "region", row["code"], json.loads(row["sites_json"])
    return "site", scope, [scope]


def define_region(path, code, name, sites):
    """事先宣布并固定一个跨站区域；重复定义必须给出完全相同的站点集合。"""
    sites = list(dict.fromkeys(sites))
    if not sites:
        raise ValueError("region_requires_sites")
    db = connect(path)
    try:
        with db:
            old = db.execute("SELECT * FROM region_def WHERE code=?", (code,)).fetchone()
            payload = json.dumps(sites, ensure_ascii=False)
            if old:
                if set(json.loads(old["sites_json"])) != set(sites):
                    raise ValueError("region_already_fixed")
                return {"code": code, "name": old["name"], "sites": json.loads(old["sites_json"]),
                        "created_at": old["created_at"]}
            created = now_iso()
            db.execute("INSERT INTO region_def(code,name,sites_json,created_at) VALUES (?,?,?,?)",
                       (code, name, payload, created))
            return {"code": code, "name": name, "sites": sites, "created_at": created}
    finally:
        db.close()


def public_candidate_households(db, sites, item, cutoff):
    """待发布人群：截止时刻（含）前、在给定站点实际签收该品类的去重家庭。

    只计 complete/partial 的真实签收；held 单据不计。同一户多张收据只算一户，
    补领不会重复计数。
    """
    cutoff_dt = _time(cutoff)
    placeholders = ",".join("?" for _ in sites)
    rows = db.execute(
        "SELECT household_id, delivered_at FROM delivery_receipt "
        "WHERE item=? AND status IN ('complete','partial') AND site IN (%s)" % placeholders,
        (item, *sites)).fetchall()
    return {r["household_id"] for r in rows if _time(r["delivered_at"]) <= cutoff_dt}


def _source_summary(db, households, sites, item, cutoff):
    """数据来源版本：支撑本次数字的签收单与家庭档案各自来自哪些来源 blob。"""
    cutoff_dt = _time(cutoff)
    site_set = set(sites)
    ids = set()
    receipts = db.execute("SELECT source_id, delivered_at, household_id, site FROM delivery_receipt "
                          "WHERE item=? AND status IN ('complete','partial')", (item,)).fetchall()
    keep = set()
    for r in receipts:
        if (r["household_id"] in households and r["site"] in site_set
                and _time(r["delivered_at"]) <= cutoff_dt):
            keep.add(r["household_id"])
            ids.add(r["source_id"])
    for h in keep:
        row = db.execute("SELECT source_id FROM household WHERE id=?", (h,)).fetchone()
        if row:
            ids.add(row["source_id"])
    sources = [dict(r) for r in db.execute(
        "SELECT id, kind, sha FROM source_blob WHERE id IN (%s) ORDER BY id"
        % ",".join("?" for _ in ids), tuple(ids))] if ids else []
    return sources


def _published_rows(db, exclude_id=None):
    sql = "SELECT * FROM publication WHERE decision='published'"
    params = ()
    if exclude_id is not None:
        sql += " AND id<>?"
        params = (exclude_id,)
    return db.execute(sql + " ORDER BY id", params).fetchall()


def _frozen(db, publication_id):
    return {r["household_id"] for r in db.execute(
        "SELECT household_id FROM publication_household WHERE publication_id=?", (publication_id,))}


def certify(db, candidate, k=PROTECT_K, exclude_id=None):
    """与全部既往公开记录核证。

    对每条既往公告（冻结人群 P）检查两类可被公众推算出的新增段：
      * 差集 C−P：本期相对该公告的新增贡献；
      * 韦恩原子：本期与所有公告覆盖关系组合出的最小可分辨人群段
        （跨站组合、跨期相减、同户多收据都只能作用在这些原子段上）。
    任一段规模在 1..k-1 之间即可被推回小于 k 户的新增领取，必须暂缓。
    返回 (safe, blockers)，blocker 只含规模与相关公告 id，不含任何家庭标识。
    """
    prior = [dict(r, covered=_frozen(db, r["id"])) for r in _published_rows(db, exclude_id)]
    if not prior:
        return True, []
    blockers = []
    for p in prior:
        diff = len(candidate - p["covered"])
        if 0 < diff < k:
            blockers.append({"kind": "difference_against_prior", "size": diff,
                             "prior_publication_id": p["id"],
                             "prior_scope": "%s:%s" % (p["scope_kind"], p["scope_code"]),
                             "prior_count": p["household_count"]})
    indexed = [p for p in prior if candidate & p["covered"]]
    n = len(indexed)
    if n:
        for mask in range(1 << n):
            atom = set(candidate)
            for bit, p in enumerate(indexed):
                atom = (atom & p["covered"]) if (mask >> bit) & 1 else (atom - p["covered"])
            size = len(atom)
            if 0 < size < k:
                blockers.append({"kind": "venn_atom", "size": size,
                                 "prior_publication_ids": [indexed[bit]["id"]
                                                           for bit in range(n) if (mask >> bit) & 1]})
    return not blockers, blockers


def _record_trial(db, publication_id, prior_ids, safe, blockers):
    db.execute("INSERT INTO publication_trial(publication_id,checked_against_json,decision_json,ran_at) VALUES (?,?,?,?)",
               (publication_id, json.dumps(prior_ids),
                json.dumps({"safe": safe, "blockers": blockers}, ensure_ascii=False), now_iso()))


def propose_publication(path, scope, item, cutoff, publish=False, k=PROTECT_K):
    """生成（或重跑）一期公开统计提案。

    publish=False 仅试发：结果作为 candidate 保留，不构成公告，但同样要和全部
    既往公开记录核证并留痕。publish=True 且核证安全才正式发布；不安全则保留为
    暂缓提案。已发布公告不可改写，迟到签收/更正只能进入下一次候选。
    """
    db = connect(path)
    try:
        with db:
            scope_kind, scope_code, sites = resolve_scope(db, scope)
            existing = db.execute(
                "SELECT * FROM publication WHERE scope_kind=? AND scope_code=? AND item=? AND cutoff=?",
                (scope_kind, scope_code, item, cutoff)).fetchone()
            if existing:
                pid = existing["id"]
                candidate = _frozen(db, pid)
                safe, blockers = certify(db, candidate, k, exclude_id=pid)
                prior_ids = [r["id"] for r in _published_rows(db, exclude_id=pid)]
                _record_trial(db, pid, prior_ids, safe, blockers)
                db.execute("UPDATE publication SET basis_json=? WHERE id=?",
                           (json.dumps({"protect_k": k, "safe": safe, "blockers": blockers},
                                       ensure_ascii=False), pid))
                if existing["decision"] == "published":
                    # 已发出的公告不可改写；重复试发只追加留痕。
                    return _proposal_payload(db, existing, True, [], sites, reused=True)
                decision = "published" if publish and safe else "withheld"
                if publish and safe:
                    db.execute("UPDATE publication SET decision=?, published_at=? WHERE id=?",
                               (decision, now_iso(), pid))
                row = db.execute("SELECT * FROM publication WHERE id=?", (pid,)).fetchone()
                return _proposal_payload(db, row, safe, blockers, sites)
            candidate = public_candidate_households(db, sites, item, cutoff)
            sources = _source_summary(db, candidate, sites, item, cutoff)
            safe, blockers = certify(db, candidate, k)
            decision = "published" if publish and safe else "withheld"
            created = now_iso()
            pid = db.execute(
                "INSERT INTO publication(scope_kind,scope_code,sites_json,item,cutoff,decision,"
                "household_count,sources_json,basis_json,published_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scope_kind, scope_code, json.dumps(sites, ensure_ascii=False), item, cutoff, decision,
                 len(candidate), json.dumps(sources, ensure_ascii=False),
                 json.dumps({"protect_k": k, "safe": safe, "blockers": blockers}, ensure_ascii=False),
                 now_iso() if decision == "published" else None, created)).lastrowid
            db.executemany("INSERT INTO publication_household(publication_id,household_id) VALUES (?,?)",
                           [(pid, h) for h in sorted(candidate)])
            _record_trial(db, pid, [r["id"] for r in _published_rows(db)], safe, blockers)
            row = db.execute("SELECT * FROM publication WHERE id=?", (pid,)).fetchone()
            return _proposal_payload(db, row, safe, blockers, sites)
    finally:
        db.close()


def _proposal_payload(db, row, safe, blockers, sites, reused=False):
    """对外结构：只含粒度、数字与来源摘要，绝不含逐户清单或家庭标识。"""
    prior = [{"publication_id": r["id"], "scope": "%s:%s" % (r["scope_kind"], r["scope_code"]),
              "item": r["item"], "cutoff": r["cutoff"], "household_count": r["household_count"]}
             for r in _published_rows(db, exclude_id=row["id"])]
    payload = {
        "publication_id": row["id"],
        "decision": row["decision"],
        "publishable": safe,
        "scope_kind": row["scope_kind"],
        "scope_code": row["scope_code"],
        "grain": "%s:%s" % (row["scope_kind"], row["scope_code"]),
        "sites": sites,
        "item": row["item"],
        "cutoff": row["cutoff"],
        "protect_k": PROTECT_K,
        "checked_against_prior_publications": prior,
        "blockers": blockers,
        "sources": json.loads(row["sources_json"]),
        "reused_proposal": reused,
    }
    # 暂缓数字不进入公开面：内部提案可以看到候选规模，公开载荷只在可发布时给数。
    payload["public_household_count"] = row["household_count"] if safe else None
    if row["decision"] == "published":
        payload["published_at"] = row["published_at"]
        payload["public_statement"] = {
            "grain": payload["grain"], "item": row["item"], "cutoff": row["cutoff"],
            "households_signed": row["household_count"]}
    return payload


def list_publications(path, include_withheld=True):
    """公开公告台账（内部核对用视图，同样不输出家庭标识、单据或签名）。"""
    db = connect(path)
    try:
        rows = db.execute("SELECT * FROM publication ORDER BY id").fetchall()
        out = []
        for r in rows:
            if not include_withheld and r["decision"] != "published":
                continue
            trials = db.execute(
                "SELECT checked_against_json, decision_json, ran_at FROM publication_trial "
                "WHERE publication_id=? ORDER BY id", (r["id"],)).fetchall()
            out.append({
                "publication_id": r["id"],
                "decision": r["decision"],
                "grain": "%s:%s" % (r["scope_kind"], r["scope_code"]),
                "sites": json.loads(r["sites_json"]),
                "item": r["item"],
                "cutoff": r["cutoff"],
                "household_count": r["household_count"],
                "public_household_count": r["household_count"] if r["decision"] == "published" else None,
                "published_at": r["published_at"],
                "sources": json.loads(r["sources_json"]),
                "trials": [{"checked_against": json.loads(t["checked_against_json"]),
                            "verdict": json.loads(t["decision_json"]), "ran_at": t["ran_at"]}
                           for t in trials],
            })
        return out
    finally:
        db.close()
