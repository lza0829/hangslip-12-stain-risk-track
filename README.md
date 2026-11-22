# 衣票墙（hangslip）

收衣贴号 → 工序推进 → 取衣核销关账 → 超期/丢失隔离互证。

## 染色风险口径（全局唯一：强制分轨）

衣物件开票时可打「染色风险」标记（`ticket_items.risk_flag`），票级风险态
（`hang_tickets.risk_state`：无 / 未清 / 已清）由写路径同事务维护。全局只贯彻一套规则：

- **混票不得进待取**：同票既有风险件又有普通件时，可沿水洗/烘干/熨烫推进，
  但进「待取」一律 409，须先在工序轨「拆票分轨」。
- **拆票分轨**：`POST /api/挂牌/{票号}/拆票` 把风险件整行迁入新票（`{票号}-风`），
  原票只留普通件、风险态归「无」，可直接进待取；新票承接原工序态、风险态「未清」。
  整行迁移保证件数守恒，双票各留拆票事件可审计。
- **风险未清不得进待取、不得核销**：纯风险票须 `POST /api/挂牌/{票号}/清险`
  写入可重放的清险事实（`risk_confirmations` + 工序事件）后置「已清」，
  方可进待取、凭暗码核销；取衣核销同事务复查，脏数据也拒。
- 所有判定与写入在同一事务（`BEGIN IMMEDIATE`）内完成：并发清险/拆票/推进各自只生效一次。
- 总览含「风险未清/已清」计数；口径原文落库于 `settings['风险口径']`。

代取人登记不在本口径内，另立需求。

## 端点

- 前端 http://127.0.0.1:6200
- API http://127.0.0.1:11200

本地开发：

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11200

cd ..\frontend
npm install
npm run dev
```

后端口径校验（无需 pytest）：

```powershell
cd backend
.\.venv\Scripts\python -I tests\test_risk_flow.py
```
