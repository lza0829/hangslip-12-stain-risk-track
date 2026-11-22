from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.db import FORWARD, RISK_CLEARED, RISK_NONE, RISK_PENDING, connect, transaction
from app.services.gate import ensure_day_row, is_day_sealed, refresh_in_flight, today_str

TERMINAL = ("已取", "超期隔离", "丢失隔离")


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _items_of(conn, ticket_no: str) -> list[dict]:
    return [
        dict(i)
        for i in conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_no=? ORDER BY id",
            (ticket_no,),
        ).fetchall()
    ]


def _risk_profile(items: list[dict]) -> tuple[bool, bool]:
    """(含风险件, 含普通件)"""
    has_risk = any(i["risk_flag"] for i in items)
    has_normal = any(not i["risk_flag"] for i in items)
    return has_risk, has_normal


def _ensure_ready_gate(conn, ticket_no: str, risk_state: str) -> None:
    """进「待取」的唯一口径：强制分轨——混票先拆，风险未清先确认。同事务内判定。"""
    items = _items_of(conn, ticket_no)
    has_risk, has_normal = _risk_profile(items)
    if has_risk and has_normal:
        raise HTTPException(409, "含染色风险件与普通件混票，须先拆票分轨")
    if has_risk and risk_state == RISK_PENDING:
        raise HTTPException(409, "染色风险未清，须先清险确认方可进待取")


def list_tickets(status: str | None = None, store_code: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM hang_tickets WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if store_code:
            sql += " AND store_code=?"
            args.append(store_code)
        sql += " ORDER BY id DESC"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for r in rows:
            r["items"] = [
                dict(i)
                for i in conn.execute(
                    "SELECT * FROM ticket_items WHERE ticket_no=? ORDER BY id",
                    (r["ticket_no"],),
                ).fetchall()
            ]
        return rows
    finally:
        conn.close()


def get_ticket(ticket_no: str) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        data = dict(row)
        data["items"] = [
            dict(i)
            for i in conn.execute(
                "SELECT * FROM ticket_items WHERE ticket_no=? ORDER BY id",
                (ticket_no,),
            ).fetchall()
        ]
        data["events"] = [
            dict(e)
            for e in conn.execute(
                "SELECT * FROM process_events WHERE ticket_no=? ORDER BY id",
                (ticket_no,),
            ).fetchall()
        ]
        data["confirmations"] = [
            dict(c)
            for c in conn.execute(
                "SELECT * FROM risk_confirmations WHERE ticket_no=? ORDER BY id",
                (ticket_no,),
            ).fetchall()
        ]
        return data
    finally:
        conn.close()


def create_ticket(payload: dict) -> dict:
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止新开挂牌")
    ticket_no = (payload.get("ticket_no") or "").strip()
    store_code = (payload.get("store_code") or "").strip()
    pickup_code = (payload.get("pickup_code") or "").strip()
    customer_name = (payload.get("customer_name") or "").strip()
    due_date = (payload.get("due_date") or "").strip() or today_str()
    note = (payload.get("note") or "").strip()
    items = payload.get("items") or []
    if not ticket_no or not store_code or not pickup_code or not customer_name:
        raise HTTPException(400, "票号、门店、暗码、顾客名必填")
    if not items:
        raise HTTPException(400, "至少一件衣物")

    now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    with transaction() as conn:
        ensure_day_row(conn)
        store = conn.execute(
            "SELECT * FROM stores WHERE code=? AND active=1", (store_code,)
        ).fetchone()
        if not store:
            raise HTTPException(400, "门店不可用")
        exists = conn.execute(
            "SELECT 1 FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if exists:
            raise HTTPException(409, "票号已存在")
        risk_rows = sum(1 for it in items if it.get("risk"))
        risk_state = RISK_PENDING if risk_rows else RISK_NONE
        conn.execute(
            """INSERT INTO hang_tickets
               (ticket_no, store_code, pickup_code, customer_name, status, opened_at, due_date, note, risk_state)
               VALUES (?,?,?,?, '已收',?,?,?,?)""",
            (ticket_no, store_code, pickup_code, customer_name, now, due_date, note, risk_state),
        )
        for it in items:
            garment = (it.get("garment") or "").strip()
            qty = int(it.get("qty") or 0)
            if not garment or qty <= 0:
                raise HTTPException(400, "衣物件不合法")
            conn.execute(
                "INSERT INTO ticket_items(ticket_no, garment, qty, remark, risk_flag) VALUES (?,?,?,?,?)",
                (ticket_no, garment, qty, (it.get("remark") or "").strip(), 1 if it.get("risk") else 0),
            )
        open_note = "收衣开票" + (f"；含染色风险件 {risk_rows} 项" if risk_rows else "")
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, None, "已收", payload.get("operator") or "店员", now, open_note),
        )
        refresh_in_flight(conn)
    return get_ticket(ticket_no)


def advance(ticket_no: str, to_status: str, operator: str = "店员", note: str = "") -> dict:
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止改工序")
    to_status = (to_status or "").strip()
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        cur = row["status"]
        if cur in ("已取", "超期隔离", "丢失隔离"):
            raise HTTPException(409, f"当前态「{cur}」不可推进工序")
        allowed = FORWARD.get(cur, ())
        if to_status not in allowed:
            raise HTTPException(409, f"不可从「{cur}」到「{to_status}」")
        if to_status == "待取":
            _ensure_ready_gate(conn, ticket_no, row["risk_state"])
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        conn.execute(
            "UPDATE hang_tickets SET status=? WHERE ticket_no=?",
            (to_status, ticket_no),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, cur, to_status, operator or "店员", now, note or ""),
        )
        refresh_in_flight(conn)
    return get_ticket(ticket_no)


def split_ticket(ticket_no: str, operator: str = "店员", note: str = "") -> dict:
    """拆票分轨：把染色风险件整行迁入新票，原票只留普通件。件数守恒由整行迁移保证。"""
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止拆票分轨")
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] in TERMINAL:
            raise HTTPException(409, f"当前态「{row['status']}」不可拆票")
        items = _items_of(conn, ticket_no)
        risk_items = [i for i in items if i["risk_flag"]]
        normal_items = [i for i in items if not i["risk_flag"]]
        if not risk_items:
            raise HTTPException(409, "无染色风险件可拆")
        if not normal_items:
            raise HTTPException(409, "纯风险票无需拆票，直接清险确认即可")
        # 同事务内生成唯一新票号
        new_no = f"{ticket_no}-风"
        n = 2
        while conn.execute(
            "SELECT 1 FROM hang_tickets WHERE ticket_no=?", (new_no,)
        ).fetchone():
            new_no = f"{ticket_no}-风{n}"
            n += 1
        now = _now()
        moved_qty = sum(i["qty"] for i in risk_items)
        conn.execute(
            """INSERT INTO hang_tickets
               (ticket_no, store_code, pickup_code, customer_name, status, opened_at, due_date, note, risk_state)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                new_no,
                row["store_code"],
                row["pickup_code"],
                row["customer_name"],
                row["status"],
                now,
                row["due_date"],
                note or f"自 {ticket_no} 拆出染色风险件",
                RISK_PENDING,
            ),
        )
        conn.execute(
            f"UPDATE ticket_items SET ticket_no=? WHERE id IN ({','.join('?' * len(risk_items))})",
            (new_no, *[i["id"] for i in risk_items]),
        )
        conn.execute(
            "UPDATE hang_tickets SET risk_state=? WHERE ticket_no=?",
            (RISK_NONE, ticket_no),
        )
        detail = f"{len(risk_items)} 项/{moved_qty} 件"
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, row["status"], row["status"], operator or "店员", now,
             f"拆出染色风险件 {detail} → {new_no}"),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (new_no, None, row["status"], operator or "店员", now,
             f"自 {ticket_no} 拆票开立，承接染色风险件 {detail}"),
        )
        refresh_in_flight(conn)
    return {"origin": get_ticket(ticket_no), "risk_ticket": get_ticket(new_no)}


def clear_risk(ticket_no: str, operator: str = "店员", note: str = "") -> dict:
    """清险确认：写入可重放的风险已处理事实，并把票置为「已清」。"""
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止清险确认")
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] in ("已取", "丢失隔离"):
            raise HTTPException(409, f"当前态「{row['status']}」不可清险")
        items = _items_of(conn, ticket_no)
        has_risk, has_normal = _risk_profile(items)
        if not has_risk:
            raise HTTPException(409, "该票无染色风险件")
        if has_normal:
            raise HTTPException(409, "混票须先拆票分轨，再对风险票清险")
        if row["risk_state"] != RISK_PENDING:
            raise HTTPException(409, "无待清风险，勿重复确认")
        now = _now()
        conn.execute(
            """INSERT INTO risk_confirmations(ticket_no, operator, confirmed_at, note)
               VALUES (?,?,?,?)""",
            (ticket_no, operator or "店员", now, note or "染色风险处理确认"),
        )
        conn.execute(
            "UPDATE hang_tickets SET risk_state=? WHERE ticket_no=?",
            (RISK_CLEARED, ticket_no),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, row["status"], row["status"], operator or "店员", now,
             f"染色风险清险确认：{note or '已处理'}"),
        )
    return get_ticket(ticket_no)


def pickup(ticket_no: str, pickup_code: str, operator: str = "店员") -> dict:
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止取衣核销")
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] != "待取":
            raise HTTPException(409, f"仅「待取」可核销，当前「{row['status']}」")
        if (pickup_code or "").strip() != row["pickup_code"]:
            raise HTTPException(403, "取衣暗码不符")
        # 防线：风险未清/混票不得核销（正常流程到不了这里，防脏数据与并发漏检）
        _ensure_ready_gate(conn, ticket_no, row["risk_state"])
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        conn.execute(
            "UPDATE hang_tickets SET status='已取', closed_at=? WHERE ticket_no=?",
            (now, ticket_no),
        )
        conn.execute(
            """INSERT INTO pickups(ticket_no, pickup_code, picked_at, operator)
               VALUES (?,?,?,?)""",
            (ticket_no, row["pickup_code"], now, operator or "店员"),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, "待取", "已取", operator or "店员", now, "取衣核销"),
        )
        refresh_in_flight(conn)
    return get_ticket(ticket_no)


def quarantine(ticket_no: str, kind: str, reason: str, operator: str = "店员") -> dict:
    kind = (kind or "").strip()
    if kind not in ("超期隔离", "丢失隔离"):
        raise HTTPException(400, "隔离种类仅超期隔离或丢失隔离")
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] in ("已取", "丢失隔离"):
            raise HTTPException(409, f"当前态「{row['status']}」不可隔离")
        if kind == "超期隔离" and row["status"] not in ("待取", "超期隔离"):
            raise HTTPException(409, "超期隔离仅从待取进入")
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        prev = row["status"]
        conn.execute(
            "UPDATE hang_tickets SET status=? WHERE ticket_no=?",
            (kind, ticket_no),
        )
        conn.execute(
            """INSERT INTO quarantines(ticket_no, kind, reason, created_at, resolved)
               VALUES (?,?,?,?,0)""",
            (ticket_no, kind, reason or kind, now),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, prev, kind, operator or "店员", now, reason or ""),
        )
        refresh_in_flight(conn)
    return get_ticket(ticket_no)
