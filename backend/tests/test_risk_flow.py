"""染色风险分轨口径的端到端校验（无 pytest 依赖，直接运行）：

    .venv/bin/python -I tests/test_risk_flow.py

口径（全局唯一）：强制分轨——
  * 含染色风险件的票不得与普通件混票推进到「待取」；
  * 纯风险票未清险不得进「待取」；
  * 拆票整行迁移，件数守恒；
  * 所有判定与写入在同一事务内。
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.db as db  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="hangslip-risk-"))
db.DB_PATH = _tmp / "test.db"
db.init_db()

from fastapi import HTTPException  # noqa: E402

from app.routers.dashboard import dashboard  # noqa: E402
from app.services import gate as gate_svc  # noqa: E402
from app.services import tickets as svc  # noqa: E402

PASS = []


def ok(name: str) -> None:
    PASS.append(name)
    print(f"  ✓ {name}")


def expect_err(fn, code: int, part: str = "") -> None:
    try:
        fn()
    except HTTPException as e:
        assert e.status_code == code, f"期望 {code}，实得 {e.status_code}: {e.detail}"
        if part:
            assert part in str(e.detail), f"期望错误含「{part}」，实得「{e.detail}」"
        return
    raise AssertionError(f"期望 HTTPException {code}（{part}），却成功")


def make(ticket_no: str, items: list[dict], **kw):
    payload = {
        "ticket_no": ticket_no,
        "store_code": "店-测",
        "pickup_code": kw.pop("pickup_code", "暗码测"),
        "customer_name": "测试顾客",
        "due_date": "",
        "note": "",
        "operator": "测试员",
        "items": items,
    }
    payload.update(kw)
    return svc.create_ticket(payload)


def qty_sum(ticket: dict) -> int:
    return sum(i["qty"] for i in ticket["items"])


def main() -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO stores(code, name, rack_count, active) VALUES ('店-测','测试店',2,1)"
        )

    print("— 开票与混票拦截 —")
    t = make("测-混1", [
        {"garment": "衬衣", "qty": 2, "risk": False},
        {"garment": "真丝裙", "qty": 1, "risk": True},
    ])
    assert t["risk_state"] == "未清"
    assert [i["risk_flag"] for i in t["items"]] == [0, 1]
    ok("开票落风险标记，票态=未清")

    expect_err(lambda: svc.advance("测-混1", "待取"), 409, "混票")
    ok("混票直进待取被拦")

    t = svc.advance("测-混1", "水洗")
    assert t["status"] == "水洗"
    ok("混票可沿工序推进（只拦待取）")

    expect_err(lambda: svc.clear_risk("测-混1"), 409, "拆票")
    ok("混票清险被拒（须先拆票）")

    print("— 拆票分轨与件数守恒 —")
    before = qty_sum(svc.get_ticket("测-混1"))
    r = svc.split_ticket("测-混1", operator="工序长")
    origin, risk_t = r["origin"], r["risk_ticket"]
    assert origin["ticket_no"] == "测-混1"
    assert risk_t["ticket_no"] == "测-混1-风"
    assert origin["risk_state"] == "无" and all(i["risk_flag"] == 0 for i in origin["items"])
    assert risk_t["risk_state"] == "未清" and all(i["risk_flag"] == 1 for i in risk_t["items"])
    assert risk_t["status"] == "水洗", "新票承接原工序态"
    assert risk_t["pickup_code"] == origin["pickup_code"]
    assert qty_sum(origin) + qty_sum(risk_t) == before == 3
    ok("拆票整行迁移，件数守恒（3=2+1）")

    notes_o = [e["note"] for e in svc.get_ticket("测-混1")["events"]]
    notes_r = [e["note"] for e in risk_t["events"]]
    assert any("拆出染色风险件" in n and "测-混1-风" in n for n in notes_o)
    assert any("拆票开立" in n and "测-混1" in n for n in notes_r)
    ok("双票均留拆票审计事件")

    expect_err(lambda: svc.split_ticket("测-混1"), 409, "无染色风险件可拆")
    ok("重复拆票被拒")

    t = svc.advance("测-混1", "待取")
    assert t["status"] == "待取"
    ok("拆后普通票直通待取（不误挡）")

    expect_err(lambda: svc.advance("测-混1-风", "待取"), 409, "未清")
    ok("风险票未清仍被拦")

    print("— 清险确认与取衣 —")
    t = svc.clear_risk("测-混1-风", operator="质检", note="去渍复洗")
    assert t["risk_state"] == "已清"
    assert len(t["confirmations"]) == 1
    assert t["confirmations"][0]["note"] == "去渍复洗"
    assert any("清险确认" in e["note"] for e in t["events"])
    ok("清险落可重放事实（confirmations + 事件）")

    expect_err(lambda: svc.clear_risk("测-混1-风"), 409, "勿重复确认")
    ok("重复清险被拒")

    t = svc.advance("测-混1-风", "待取")
    assert t["status"] == "待取"
    expect_err(lambda: svc.pickup("测-混1-风", "错码"), 403)
    t = svc.pickup("测-混1-风", "暗码测", operator="柜员")
    assert t["status"] == "已取"
    ok("清险后进待取、凭暗码核销")

    print("— 纯风险票 —")
    make("测-纯1", [{"garment": "礼服裙", "qty": 1, "risk": True}])
    expect_err(lambda: svc.split_ticket("测-纯1"), 409, "纯风险票")
    expect_err(lambda: svc.advance("测-纯1", "待取"), 409, "未清")
    svc.clear_risk("测-纯1")
    assert svc.advance("测-纯1", "待取")["status"] == "待取"
    ok("纯风险票：无需拆，清险即可待取")

    print("— 取衣防线（脏数据不得假装可取） —")
    make("测-脏1", [{"garment": "羊绒衫", "qty": 1, "risk": True}])
    with db.transaction() as conn:  # 模拟历史脏数据：未清却挂在待取
        conn.execute("UPDATE hang_tickets SET status='待取' WHERE ticket_no='测-脏1'")
    expect_err(lambda: svc.pickup("测-脏1", "暗码测"), 409, "未清")
    ok("未清风险票即使挂在待取也拒核销")

    print("— 并发：清险/拆票/推进各自只成功一次 —")
    make("测-并1", [{"garment": "真丝", "qty": 1, "risk": True}])
    results = run_threads([lambda: svc.clear_risk("测-并1") for _ in range(4)])
    assert len(results["ok"]) == 1 and len(results["err"]) == 3
    assert len(svc.get_ticket("测-并1")["confirmations"]) == 1
    ok("并发清险仅一次生效")

    make("测-并2", [
        {"garment": "衬衣", "qty": 2, "risk": False},
        {"garment": "真丝裙", "qty": 3, "risk": True},
    ])
    results = run_threads([lambda: svc.split_ticket("测-并2") for _ in range(4)])
    assert len(results["ok"]) == 1 and len(results["err"]) == 3
    total = qty_sum(svc.get_ticket("测-并2")) + qty_sum(svc.get_ticket("测-并2-风"))
    assert total == 5, f"件数不守恒: {total}"
    ok("并发拆票仅一次生效，件数守恒（5=2+3）")

    make("测-并3", [{"garment": "围巾", "qty": 1, "risk": True}])
    svc.clear_risk("测-并3")
    svc.advance("测-并3", "熨烫")
    results = run_threads([lambda: svc.advance("测-并3", "待取") for _ in range(4)])
    assert len(results["ok"]) == 1 and len(results["err"]) == 3
    assert svc.get_ticket("测-并3")["status"] == "待取"
    ok("并发推进待取仅一次生效（不跳过确认）")

    print("— 总览与口径 —")
    d = dashboard()
    assert d["风险未清"] >= 1 and "风险已清" in d
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='风险口径'").fetchone()
    assert row and "强制分轨" in row["value"]
    ok("总览带风险计数，口径落库唯一")

    print("— 日闸结案后全部写路径被封 —")
    gate_svc.seal_day(note="测试结案")
    expect_err(lambda: make("测-闸1", [{"garment": "衬衣", "qty": 1}]), 409, "已结案")
    expect_err(lambda: svc.advance("测-纯1", "水洗"), 409, "已结案")
    expect_err(lambda: svc.pickup("测-纯1", "暗码测"), 409, "已结案")
    expect_err(lambda: svc.split_ticket("测-并2-风"), 409, "已结案")
    expect_err(lambda: svc.clear_risk("测-脏1"), 409, "已结案")
    ok("日闸封账覆盖开票/推进/取衣/拆票/清险")

    print(f"\n全部通过：{len(PASS)} 项")


def run_threads(fns) -> dict:
    out: dict = {"ok": [], "err": []}
    lock = threading.Lock()

    def wrap(fn):
        try:
            fn()
            with lock:
                out["ok"].append(1)
        except HTTPException:
            with lock:
                out["err"].append(1)

    threads = [threading.Thread(target=wrap, args=(f,)) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


if __name__ == "__main__":
    main()
