from __future__ import annotations

from datetime import date, datetime, timedelta

from app.db import connect, init_db


def seed_if_empty() -> None:
    init_db()
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM stores").fetchone()["c"]
        if n:
            return
        stores = [
            ("店-河西", "河西干洗", 3),
            ("店-城东", "城东快洗", 2),
            ("店-南苑", "南苑精洗", 4),
        ]
        for code, name, racks in stores:
            conn.execute(
                "INSERT INTO stores(code, name, rack_count, active) VALUES (?,?,?,1)",
                (code, name, racks),
            )

        today = date.today()
        opened = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        # (票号, 门店, 暗码, 顾客, 状态, 承诺日, 备注, 风险态)
        samples = [
            ("挂-1001", "店-河西", "暗码甲七", "周小姐", "待取", today.isoformat(), "衬衣两件", "无"),
            ("挂-1002", "店-河西", "暗码乙三", "陈先生", "水洗", (today + timedelta(days=1)).isoformat(), "大衣", "无"),
            ("挂-1003", "店-城东", "暗码丙九", "王女士", "已收", (today + timedelta(days=2)).isoformat(), "西装套装", "无"),
            ("挂-1004", "店-南苑", "暗码丁二", "李先生", "熨烫", today.isoformat(), "连衣裙", "无"),
            ("挂-1005", "店-南苑", "暗码戊五", "赵小姐", "待取", (today - timedelta(days=2)).isoformat(), "羽绒服", "无"),
            ("挂-1006", "店-城东", "暗码己八", "孙先生", "已取", (today - timedelta(days=1)).isoformat(), "衬衫", "无"),
            ("挂-1007", "店-河西", "暗码庚一", "吴女士", "水洗", (today + timedelta(days=1)).isoformat(), "真丝裙易染，混票待拆", "未清"),
            ("挂-1008", "店-城东", "暗码辛四", "郑先生", "熨烫", today.isoformat(), "红毯礼服裙，待清险", "未清"),
            ("挂-1009", "店-南苑", "暗码壬六", "钱小姐", "待取", today.isoformat(), "串色已去渍复洗", "已清"),
        ]
        # (品名, 数量, 备注, 是否染色风险)
        items = {
            "挂-1001": [("衬衣", 2, "", 0)],
            "挂-1002": [("大衣", 1, "防水", 0)],
            "挂-1003": [("西装上衣", 1, "", 0), ("西装裤", 1, "", 0)],
            "挂-1004": [("连衣裙", 1, "轻熨", 0)],
            "挂-1005": [("羽绒服", 1, "充绒", 0)],
            "挂-1006": [("衬衫", 3, "", 0)],
            "挂-1007": [("真丝裙", 1, "深色易染", 1), ("衬衣", 2, "", 0)],
            "挂-1008": [("礼服裙", 1, "红毯串色", 1)],
            "挂-1009": [("羊绒衫", 1, "串色已处理", 1)],
        }
        for ticket_no, store, code, cust, status, due, note, risk_state in samples:
            closed = opened if status == "已取" else None
            conn.execute(
                """INSERT INTO hang_tickets
                   (ticket_no, store_code, pickup_code, customer_name, status, opened_at, due_date, closed_at, note, risk_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ticket_no, store, code, cust, status, opened, due, closed, note, risk_state),
            )
            for garment, qty, remark, risk in items[ticket_no]:
                conn.execute(
                    "INSERT INTO ticket_items(ticket_no, garment, qty, remark, risk_flag) VALUES (?,?,?,?,?)",
                    (ticket_no, garment, qty, remark, risk),
                )
            conn.execute(
                """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
                   VALUES (?,?,?,?,?,?)""",
                (ticket_no, None, status if status != "已取" else "待取", "开店员", opened, "种子"),
            )
            if risk_state == "已清":
                conn.execute(
                    """INSERT INTO risk_confirmations(ticket_no, operator, confirmed_at, note)
                       VALUES (?,?,?,?)""",
                    (ticket_no, "开店员", opened, "去渍复洗，串色已除"),
                )
                conn.execute(
                    """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
                       VALUES (?,?,?,?,?,?)""",
                    (ticket_no, status, status, "开店员", opened, "染色风险清险确认：去渍复洗"),
                )
            if status == "已取":
                conn.execute(
                    """INSERT INTO pickups(ticket_no, pickup_code, picked_at, operator)
                       VALUES (?,?,?,?)""",
                    (ticket_no, code, opened, "开店员"),
                )
                conn.execute(
                    """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
                       VALUES (?,?,?,?,?,?)""",
                    (ticket_no, "待取", "已取", "开店员", opened, "种子取衣"),
                )

        conn.execute(
            "INSERT INTO day_gates(day, sealed, sealed_at, note) VALUES (?,?,?,?)",
            (today.isoformat(), 0, None, ""),
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('当前门店', ?)",
            ("店-河西",),
        )
        conn.execute(
            "INSERT INTO watermarks(key, value, updated_at) VALUES ('在途票', 0, ?)",
            (opened,),
        )
        # refresh watermark
        openish = conn.execute(
            "SELECT COUNT(*) AS c FROM hang_tickets WHERE status NOT IN ('已取','丢失隔离')"
        ).fetchone()["c"]
        conn.execute(
            "UPDATE watermarks SET value=?, updated_at=? WHERE key='在途票'",
            (openish, opened),
        )
        conn.commit()
    finally:
        conn.close()
