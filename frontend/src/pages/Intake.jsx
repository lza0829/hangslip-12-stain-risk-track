import { useEffect, useState } from "react";
import { api } from "../api";

const EMPTY_ITEM = { garment: "衬衣", qty: 1, risk: false };

export default function Intake() {
  const [stores, setStores] = useState([]);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);
  const [form, setForm] = useState({
    ticket_no: "",
    store_code: "",
    pickup_code: "",
    customer_name: "",
    due_date: "",
    note: "",
  });
  const [items, setItems] = useState([{ ...EMPTY_ITEM }]);

  useEffect(() => {
    api.门店().then((rows) => {
      setStores(rows.filter((s) => s.active));
      if (rows[0]) setForm((f) => ({ ...f, store_code: rows[0].code }));
    });
  }, []);

  function setItem(i, patch) {
    setItems((list) => list.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  }

  async function submit(e) {
    e.preventDefault();
    setMsg("");
    try {
      const clean = items
        .filter((it) => it.garment.trim() && Number(it.qty) > 0)
        .map((it) => ({ garment: it.garment.trim(), qty: Number(it.qty), remark: "", risk: !!it.risk }));
      if (!clean.length) throw new Error("至少一件衣物");
      const row = await api.开票({ ...form, items: clean });
      const mixed = clean.some((i) => i.risk) && clean.some((i) => !i.risk);
      const pureRisk = clean.every((i) => i.risk);
      setOk(true);
      setMsg(
        `已开票 ${row.ticket_no}，状态 ${row.status}` +
          (mixed ? "；含染色风险件，进待取前须先在工序轨拆票分轨" : "") +
          (pureRisk ? "；纯风险票，清险确认后方可进待取" : "")
      );
      setForm((f) => ({ ...f, ticket_no: "", pickup_code: "", customer_name: "", note: "" }));
      setItems([{ ...EMPTY_ITEM }]);
    } catch (err) {
      setOk(false);
      setMsg(err.message);
    }
  }

  return (
    <section className="panel">
      <h3>收衣开票</h3>
      <p className={`msg ${ok ? "ok" : ""}`}>{msg}</p>
      <form onSubmit={submit}>
        <div className="row">
          <input
            placeholder="票号"
            value={form.ticket_no}
            onChange={(e) => setForm({ ...form, ticket_no: e.target.value })}
            required
          />
          <select
            value={form.store_code}
            onChange={(e) => setForm({ ...form, store_code: e.target.value })}
          >
            {stores.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
            ))}
          </select>
          <input
            placeholder="取衣暗码"
            value={form.pickup_code}
            onChange={(e) => setForm({ ...form, pickup_code: e.target.value })}
            required
          />
          <input
            placeholder="顾客称呼"
            value={form.customer_name}
            onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
            required
          />
          <input
            type="date"
            value={form.due_date}
            onChange={(e) => setForm({ ...form, due_date: e.target.value })}
          />
          <input
            placeholder="备注"
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </div>
        {items.map((it, i) => (
          <div className="row" key={i}>
            <input
              placeholder="衣物"
              value={it.garment}
              onChange={(e) => setItem(i, { garment: e.target.value })}
            />
            <input
              type="number"
              min="1"
              value={it.qty}
              onChange={(e) => setItem(i, { qty: e.target.value })}
            />
            <label className="risk-check">
              <input
                type="checkbox"
                checked={it.risk}
                onChange={(e) => setItem(i, { risk: e.target.checked })}
              />
              染色风险
            </label>
            <button
              type="button"
              className="ghost"
              onClick={() => setItems((list) => list.filter((_, idx) => idx !== i))}
              disabled={items.length === 1}
            >
              移除
            </button>
            {i === items.length - 1 && (
              <button
                type="button"
                className="ghost"
                onClick={() => setItems((list) => [...list, { ...EMPTY_ITEM, garment: "" }])}
              >
                加一件
              </button>
            )}
          </div>
        ))}
        <div className="row">
          <button type="submit">开票贴号</button>
        </div>
      </form>
    </section>
  );
}
