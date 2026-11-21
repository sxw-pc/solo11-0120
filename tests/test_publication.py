import json
import subprocess
import sys
from pathlib import Path

import pytest

from reliefhandoff.domain import arrival_import, delivery_import, household_import
from reliefhandoff.publication import (PROTECT_K, define_region, list_publications,
                                       propose_publication, public_candidate_households)
from reliefhandoff.report import (public_certify, public_ledger, public_pending_households,
                                  public_proposal, public_proposal_json, report, report_json)
from reliefhandoff.store import connect

T1 = "2026-07-01T12:00:00+08:00"
T2 = "2026-07-02T12:00:00+08:00"
ITEM = "water"


def stock(db, site, n, ship_id, count_id):
    """给站点准备 n 件可用 water，保证签收不会因库存被 held。"""
    arrival = {"shipments": [{"id": ship_id, "site": site, "item": ITEM, "expected": n, "seal": "seal-" + ship_id}],
               "counts": [{"id": count_id, "shipment": ship_id, "counted": n, "seal": "seal-" + ship_id}]}
    f = db.parent / ("arrival-" + ship_id + ".json")
    f.write_text(json.dumps(arrival), encoding="utf-8")
    arrival_import(db, f)


def households(db, ids):
    payload = {"disaster_zone": "flood-A", "asof": "2026-07-01T00:00:00+08:00",
               "item": ITEM, "units_per_member": 1,
               "households": [{"id": h, "zone": "flood-A", "members": 1,
                               "credential_until": "2027-01-01T00:00:00+08:00"} for h in ids]}
    f = db.parent / ("households-" + "-".join(ids) + ".json")
    f.write_text(json.dumps(payload), encoding="utf-8")
    household_import(db, f)


def receipts(db, rows):
    payload = {"receipts": [
        {"id": rid, "household": h, "site": site, "item": ITEM, "count": 1, "at": at, "signature": "sig-" + h}
        for rid, h, site, at in rows]}
    f = db.parent / ("receipts-" + rows[0][0] + ".json")
    f.write_text(json.dumps(payload), encoding="utf-8")
    return delivery_import(db, f)


def assert_no_household_leak(text):
    """任何对外 JSON 都不得带出家庭标识、单据号或签名。"""
    for token in ("sig-", "household_id", "signature", "R10", "R11", "H10", "H11", "H15"):
        assert token not in text


# --- 场景一：连续两期同站，单站逐日发布时新增 1 户必须暂缓 ---

def test_same_site_second_period_single_household_increment_is_withheld(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H%d" % i for i in range(1, 12)])
    receipts(db, [("R%d" % i, "H%d" % i, "HALL", T1) for i in range(1, 11)])
    first = propose_publication(db, "HALL", ITEM, T1, publish=True)
    assert first["decision"] == "published"
    assert first["public_household_count"] == 10
    assert first["public_statement"]["households_signed"] == 10

    # 次日仅新增一户（H11），候选 11 户。
    receipts(db, [("R11", "H11", "HALL", T2)])
    assert public_pending_households(db, "HALL", ITEM, T2) == sorted(["H%d" % i for i in range(1, 12)])
    trial = propose_publication(db, "HALL", ITEM, T2)  # 试发
    assert trial["decision"] == "withheld"
    assert trial["publishable"] is False
    assert trial["public_household_count"] is None  # 暂缓数字不进公开面
    assert any(b["kind"] == "difference_against_prior" and b["size"] == 1 for b in trial["blockers"])
    # 试发依据必须包含全部既往公开记录，而不只是同站上一期以外的空集
    assert [p["publication_id"] for p in trial["checked_against_prior_publications"]] == [first["publication_id"]]

    # 强制要求正式发布也不能让 11 户出门
    forced = propose_publication(db, "HALL", ITEM, T2, publish=True)
    assert forced["decision"] == "withheld"
    ledger = public_ledger(db)
    assert [r["decision"] for r in ledger] == ["published", "withheld"]


# --- 场景二：曾单独公开的第二站；两站合并区域 16 户可发，第二站也公开过则暂缓 ---

def test_region_merge_publishable_when_only_hall_was_public(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    stock(db, "SCHOOL", 20, "SS1", "AS1")
    households(db, ["H%d" % i for i in range(1, 17)])
    receipts(db, [("R%d" % i, "H%d" % i, "HALL", T1) for i in range(1, 11)])
    hall = propose_publication(db, "HALL", ITEM, T1, publish=True)
    assert hall["public_household_count"] == 10

    receipts(db, [("R%d" % i, "H%d" % i, "HALL", T2) for i in range(11, 12)])
    receipts(db, [("RS%d" % i, "H%d" % i, "SCHOOL", T2) for i in range(12, 17)])
    define_region(db, "ZONE-A", "两站合并", ["HALL", "SCHOOL"])

    # 粒度在核证前固定：区域候选 16 户，相对已公开的 HALL 10 户的差集是 6 户（>=3）
    region = propose_publication(db, "ZONE-A", ITEM, T2, publish=True)
    assert region["decision"] == "published"
    assert region["scope_kind"] == "region"
    assert region["public_household_count"] == 16
    assert region["public_statement"] == {"grain": "region:ZONE-A", "item": ITEM, "cutoff": T2,
                                          "households_signed": 16}

    # 同期继续单站逐日发布 HALL（11 户）仍必须暂缓：区域公告也进入核证范围
    hall2 = propose_publication(db, "HALL", ITEM, T2, publish=True)
    assert hall2["decision"] == "withheld"
    assert any(b["size"] == 1 for b in hall2["blockers"])


def test_region_merge_withheld_when_school_was_also_public(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    stock(db, "SCHOOL", 20, "SS1", "AS1")
    households(db, ["H%d" % i for i in range(1, 17)])
    receipts(db, [("R%d" % i, "H%d" % i, "HALL", T1) for i in range(1, 11)])
    receipts(db, [("RP%d" % i, "H%d" % i, "SCHOOL", T1) for i in range(12, 17)])
    propose_publication(db, "HALL", ITEM, T1, publish=True)
    propose_publication(db, "SCHOOL", ITEM, T1, publish=True)  # SCHOOL 五户此前也已公开

    receipts(db, [("R11", "H11", "HALL", T2)])
    define_region(db, "ZONE-A", "两站合并", ["HALL", "SCHOOL"])
    region = propose_publication(db, "ZONE-A", ITEM, T2, publish=True)
    assert region["decision"] == "withheld"
    assert region["public_household_count"] is None
    # 16 − 10(HALL) − 5(SCHOOL) = 1，韦恩原子段暴露单户
    assert any(b["kind"] == "venn_atom" and b["size"] == 1 for b in region["blockers"])


# --- 场景三：同户重复签收（多次补领）只算一户 ---

def test_same_household_repeat_pickups_count_once(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H1", "H2", "H3"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    # 同户在不同收据里再次补领
    receipts(db, [("R4", "H1", "HALL", T2), ("R5", "H1", "HALL", T2)])
    internal = report(db)
    assert len(internal["delivery_receipt"]) == 5
    assert public_candidate_households(connect(db), ["HALL"], ITEM, T2) == {"H1", "H2", "H3"}
    prop = propose_publication(db, "HALL", ITEM, T2, publish=True)
    assert prop["public_household_count"] == 3
    assert prop["decision"] == "published"


# --- 场景四：迟到签收只能进入下一次候选，不能改旧公告 ---

def test_late_receipt_cannot_alter_published_announcement(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H1", "H2", "H3", "H4"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    first = propose_publication(db, "HALL", ITEM, T1, publish=True)
    assert first["public_household_count"] == 3

    # 迟到材料：签收时刻在旧截止之后才进入系统（更正同理，只能进下一次候选）
    receipts(db, [("R4", "H4", "HALL", T2)])
    rerun = propose_publication(db, "HALL", ITEM, T1)  # 同一截止时刻重跑
    assert rerun["reused_proposal"] is True
    assert rerun["decision"] == "published"
    assert rerun["public_household_count"] == 3  # 旧公告数字不变
    assert rerun["published_at"] == first["published_at"]
    ledger = list_publications(db)
    assert ledger[0]["household_count"] == 3
    assert len(ledger[0]["trials"]) == 2  # 初次发布 + 重复试发均留痕

    # 新候选按新截止时刻立账；4 户相对 3 户差 1，必须暂缓
    nxt = propose_publication(db, "HALL", ITEM, T2, publish=True)
    assert nxt["decision"] == "withheld"
    assert any(b["size"] == 1 for b in nxt["blockers"])


# --- 场景五：delivery_import 真正改变待发布人群与核证结果，而非内部备注 ---

def test_new_delivery_changes_public_candidate_and_certification(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H1", "H2", "H3", "H4", "H5"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    before = public_certify(db, "HALL", ITEM, T2)
    assert before["candidate_count"] == 3
    receipts(db, [("R4", "H4", "HALL", T2), ("R5", "H5", "HALL", T2)])
    after = public_certify(db, "HALL", ITEM, T2)
    assert after["candidate_count"] == 5
    assert public_pending_households(db, "HALL", ITEM, T2) == ["H1", "H2", "H3", "H4", "H5"]
    prop = propose_publication(db, "HALL", ITEM, T2, publish=True)
    assert prop["decision"] == "published" and prop["public_household_count"] == 5


def test_held_receipts_never_enter_public_population(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 1, "SH1", "AH1")  # 库存只够 1 件
    households(db, ["H1", "H2", "H3"])
    result = receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    assert result["held"] == 2
    assert public_pending_households(db, "HALL", ITEM, T2) == ["H1"]


# --- 场景六：公开载荷无逐户清单；内部 report 仍含完整核账信息 ---

def test_public_outputs_contain_no_per_household_data(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H1", "H2", "H3"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    prop = public_proposal(db, "HALL", ITEM, T1, publish=True)
    text = public_proposal_json(db, "HALL", ITEM, T1)
    assert_no_household_leak(text)
    assert "public_statement" in json.loads(text)
    assert prop["sources"] and all(set(s) == {"id", "kind", "sha"} for s in prop["sources"])
    ledger_text = json.dumps(public_ledger(db), ensure_ascii=False)
    assert_no_household_leak(ledger_text)
    # 冻结人群只存在内部表里，不进入任何公开 JSON
    raw = connect(db)
    assert raw.execute("SELECT COUNT(*) FROM publication_household").fetchone()[0] == 3
    raw.close()
    # 内部核账报告保持原样，仍有家庭、签收与来源
    internal = json.loads(report_json(db))
    assert internal["delivery_receipt"][0]["signature"].startswith("sig-")
    assert internal["handoff_chains"][0]["household"] == "H1"


# --- 场景七：区域必须事先固定，不能看到结果后改分组 ---

def test_region_definition_is_fixed_and_scope_must_exist(tmp_path):
    db = tmp_path / "db.sqlite"
    define_region(db, "ZONE-A", "两站", ["HALL", "SCHOOL"])
    again = define_region(db, "ZONE-A", "两站", ["SCHOOL", "HALL"])  # 同集合幂等
    assert again["sites"] == ["HALL", "SCHOOL"]
    with pytest.raises(ValueError, match="region_already_fixed"):
        define_region(db, "ZONE-A", "改站点", ["HALL"])
    with pytest.raises(ValueError, match="region_requires_sites"):
        define_region(db, "ZONE-B", "空", [])
    # 未固定区域按单站处理，不会临时拼出多站分组
    prop = propose_publication(db, "UNKNOWN-SITE", ITEM, T1)
    assert prop["scope_kind"] == "site" and prop["sites"] == ["UNKNOWN-SITE"]


# --- 场景八：重复试发幂等且每次都对照全部历史公告 ---

def test_repeated_trials_are_idempotent_and_rechecked_against_full_history(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    stock(db, "SCHOOL", 20, "SS1", "AS1")
    households(db, ["H1", "H2", "H3", "H4", "H5", "H6"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])
    receipts(db, [("RS1", "H4", "SCHOOL", T1), ("RS2", "H5", "SCHOOL", T1), ("RS3", "H6", "SCHOOL", T1)])
    p1 = propose_publication(db, "HALL", ITEM, T1, publish=True)
    p2 = propose_publication(db, "SCHOOL", ITEM, T1, publish=True)

    # 区域候选就是两站并集；先在只有 HALL 公告时试发（SCHOOL 公告已存在，差集 SCHOOL 段=3 安全）
    define_region(db, "ZONE-A", "两站", ["HALL", "SCHOOL"])
    trial1 = propose_publication(db, "ZONE-A", ITEM, T1)
    assert trial1["publishable"] is True and trial1["decision"] == "withheld"  # 仅试发不公布
    assert len(trial1["checked_against_prior_publications"]) == 2

    # 重复试发：不新建提案、不复制冻结人群，但追加一条留痕
    trial2 = propose_publication(db, "ZONE-A", ITEM, T1)
    assert trial2["publication_id"] == trial1["publication_id"]
    raw = connect(db)
    assert raw.execute("SELECT COUNT(*) FROM publication").fetchone()[0] == 3
    assert raw.execute("SELECT COUNT(*) FROM publication_household WHERE publication_id=?",
                       (trial1["publication_id"],)).fetchone()[0] == 6
    trials = raw.execute("SELECT COUNT(*) FROM publication_trial WHERE publication_id=?",
                         (trial1["publication_id"],)).fetchone()[0]
    raw.close()
    assert trials == 2
    # 试发安全后才允许正式发布
    published = propose_publication(db, "ZONE-A", ITEM, T1, publish=True)
    assert published["decision"] == "published" and published["public_household_count"] == 6
    assert p1 and p2  # 历史公告仍在台账中


# --- 场景九：CLI 端到端 ---

def test_cli_region_define_propose_and_list(tmp_path):
    db = tmp_path / "db.sqlite"
    stock(db, "HALL", 20, "SH1", "AH1")
    households(db, ["H1", "H2", "H3"])
    receipts(db, [("R1", "H1", "HALL", T1), ("R2", "H2", "HALL", T1), ("R3", "H3", "HALL", T1)])

    def run(*args):
        out = subprocess.run([sys.executable, "-m", "reliefhandoff", *args],
                             cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    region = run("region-define", "--db", str(db), "--code", "ZONE-A", "--name", "两站", "--sites", "HALL,SCHOOL")
    assert region["sites"] == ["HALL", "SCHOOL"]
    prop = run("publication-propose", "--db", str(db), "--scope", "HALL", "--item", ITEM,
               "--cutoff", T1, "--publish")
    assert prop["decision"] == "published" and prop["public_household_count"] == 3
    assert_no_household_leak(json.dumps(prop, ensure_ascii=False))
    ledger = run("publication-list", "--db", str(db))
    assert ledger[0]["grain"] == "site:HALL"
    assert ledger[0]["trials"][0]["verdict"]["safe"] is True
