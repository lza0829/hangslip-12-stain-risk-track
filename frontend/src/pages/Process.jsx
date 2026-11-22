import { useEffect, useState } from "react";
import { api } from "../api";

const NEXT = {
  已收: ["水洗", "烘干", "熨烫", "待取"],
  水洗: ["烘干", "熨烫", "待取"],
  烘干: ["熨烫", "待取"],
  熨烫: ["待取"],
};

function riskOf(r) {
  const hasRisk = (r.items || []).some((i) => i.risk_flag);
  const hasNormal = (r.items || []).some((i) => !i.risk_flag);
  return { mixed: hasRisk && hasNormal, uncleared: r.risk_state === "未清", hasRisk };
}

export default function Process() {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);

  async function load() {
    const all = await api.挂牌列表();
    setRows(all.filter((r) => !["已取", "超期隔离", "丢失隔离"].includes(r.status)));
  }

  useEffect(() => {
    load().catch((e) => setMsg(e.message));
  }, []);

  async function run(promise, text) {
    setMsg("");
    try {
      const r = await promise;
      setOk(true);
      setMsg(typeof text === "function" ? text(r) : text);
      await load();
    } catch (e) {
      setOk(false);
      setMsg(e.message);
    }
  }

  const go = (no, s) => run(api.推进(no, { to_status: s, operator: "工序员" }), `${no} → ${s}`);
  const split = (no) =>
    run(api.拆票(no, { operator: "工序长" }), (r) => `已拆票分轨：风险件 → ${r.risk_ticket.ticket_no}`);
  const clear = (no) => run(api.清险(no, { operator: "质检", note: "染色风险已处理" }), `${no} 风险已清`);

  return (
    <section className="panel">
      <h3>工序轨</h3>
      <p className={`msg ${ok ? "ok" : ""}`}>{msg}</p>
      <table>
        <thead>
          <tr>
            <th>票号</th>
            <th>门店</th>
            <th>顾客</th>
            <th>衣物件</th>
            <th>现行</th>
            <th>风险</th>
            <th>承诺日</th>
            <th>推进 / 处置</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const { mixed, uncleared, hasRisk } = riskOf(r);
            const blocked = mixed || uncleared;
            return (
              <tr key={r.ticket_no}>
                <td>{r.ticket_no}</td>
                <td>{r.store_code}</td>
                <td>{r.customer_name}</td>
                <td>
                  {(r.items || []).map((i) => (
                    <span key={i.id} className={i.risk_flag ? "item risk" : "item"}>
                      {i.garment}×{i.qty}
                      {i.risk_flag ? "⚠" : ""}
                    </span>
                  ))}
                </td>
                <td>
                  <span className={`badge ${r.status}`}>{r.status}</span>
                </td>
                <td>
                  {mixed && <span className="badge 混票">混票</span>}{" "}
                  {hasRisk && !mixed && (
                    <span className={`badge 风险${r.risk_state}`}>风险{r.risk_state}</span>
                  )}
                  {!hasRisk && "—"}
                </td>
                <td>{r.due_date}</td>
                <td>
                  <div className="row">
                    {(NEXT[r.status] || [])
                      .filter((s) => !(s === "待取" && blocked))
                      .map((s) => (
                        <button key={s} type="button" className="ghost" onClick={() => go(r.ticket_no, s)}>
                          {s}
                        </button>
                      ))}
                    {mixed && (
                      <button type="button" onClick={() => split(r.ticket_no)}>
                        拆票分轨
                      </button>
                    )}
                    {!mixed && uncleared && (
                      <button type="button" onClick={() => clear(r.ticket_no)}>
                        清险确认
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
