import {production,performances} from '../store.js';
import {esc} from '../utils/format.js';
import {dayTile,notify,empty} from '../components/shared.js';
import {send} from '../api.js';
import {EVAL_STATES,EVAL_WEEK_STATES,EVAL_STALE_REASONS} from '../constants.js';

function chart(r,p){
  const max=p.seats+10;
  const points=key=>r.backtest.map((v,i)=>`${65+i*90},${245-v[key]/max*200}`).join(' ');
  return `<svg viewBox="0 0 700 290" role="img" aria-label="最近七日按时间留出回测"><path d="M50 30V245H650" stroke="var(--line)" fill="none"/><path d="M50 ${245-p.seats/max*200}H650" stroke="var(--line)" stroke-dasharray="5 4"/><text x="50" y="23" font-size="10">${p.seats}席容量</text><polyline points="${points('actual')}" fill="none" stroke="var(--wine)" stroke-width="2"/><polyline points="${points('predicted')}" fill="none" stroke="var(--gold)" stroke-width="2"/>${r.backtest.map((v,i)=>`<circle cx="${65+i*90}" cy="${245-v.actual/max*200}" r="4" fill="var(--wine)"/><text x="${50+i*90}" y="270" font-size="10">${v.date.slice(5)}</text>`).join('')}</svg>`;
}

function methodName(s,key){return s.methods.find(m=>m.key===key)?.label||key}

function evalPanel(s){
  const p=production(s), data=s.evaluationData, isAdmin=s.user.role==='admin';
  const activeKey=data?.active_method_key||s.methodDefault;
  const evals=data?.evaluations||[];
  const options=s.methods.filter(m=>m.key!==activeKey).map(m=>`<option value="${esc(m.key)}">${esc(m.label)}</option>`).join('');
  const inFlight=evals.some(e=>['queued','running'].includes(e.state));
  const evalRows=evals.map(e=>{
    const diff=e.candidate_mae!=null&&e.baseline_mae!=null?(e.candidate_mae-e.baseline_mae):null;
    const diffText=diff==null?'—':(diff>0?`+${diff.toFixed(3)}`:`${diff.toFixed(3)}`);
    const diffCls=diff==null?'':(diff<0?'good':'bad');
    const staleBadge=e.stale?`<span class="status invalidated">已过期：${e.stale_reasons.map(x=>esc(EVAL_STALE_REASONS[x]||x)).join('、')}</span>`:'';
    const action=e.state==='failed'
      ? (isAdmin?`<button data-resume="${e.id}" ${inFlight?'disabled':''}>恢复评估</button>`:'')
      : (e.state==='completed'
        ? (isAdmin?`<button class="primary" data-activate="${e.id}" ${e.activatable?'':'disabled'}>${e.activatable?'启用候选方案':'过期不可启用'}</button>`:'')
        : '');
    return `<article class="evalcard ${esc(e.state)}">
      <div class="evalhead"><b>${esc(e.candidate_label)}</b><span class="status">${esc(EVAL_STATES[e.state]||e.state)}</span>${staleBadge}</div>
      <p class="muted">对照现用：${esc(e.baseline_label)} · 由 ${esc(e.created_by)} 发起 · ${esc((e.created_at||'').replace('T',' ').slice(0,16))}</p>
      <div class="strip"><div>完成周数<b>${e.completed_weeks} / ${e.total_weeks}</b></div><div>候选未封顶 MAE<b>${e.candidate_mae??'—'}</b></div><div>现用未封顶 MAE<b>${e.baseline_mae??'—'}</b></div><div>误差差（候选−现用）<b class="${diffCls}">${diffText}</b></div></div>
      <table><thead><tr><th>评估周（周一至周日）</th><th>训练截止</th><th>状态</th><th>候选 MAE</th><th>现用 MAE</th></tr></thead><tbody>${e.weeks.map(w=>`<tr><td class="mono">${w.week_start.slice(5)} ~ ${w.week_end.slice(5)}</td><td class="mono">${w.train_end.slice(5)}</td><td>${esc(EVAL_WEEK_STATES[w.state]||w.state)}</td><td class="mono">${w.candidate_mae??'—'}</td><td class="mono">${w.baseline_mae??'—'}</td></tr>`).join('')}</tbody></table>
      ${e.error_message?`<p class="muted">中断原因：${esc(e.error_message)}</p>`:''}
      <div class="actions">${action}</div>
    </article>`;
  }).join('');
  return `<section class="section evalblock">
    <div class="sectionhead"><h2>候选方案评估（最近三个完整周逐周回测）</h2><span class="mono">ACTIVE: ${esc(activeKey)}</span></div>
    <p class="panelnote">每周仅用该周以前的验票训练，比较<b>未封顶</b>原始需求误差；评估固定发起时刻的验票与现用方案，之后更正不改旧结论。全部三周完成且验票、现用方案均未变化时，经理才可启用；启用后新预测采用新版，已排队预测仍沿用入队方案。</p>
    <div class="inline-form">
      <select id="candidate-select" ${!isAdmin||inFlight?'disabled':''}>${options||'<option value="">暂无可选候选</option>'}</select>
      <button class="primary" id="eval-start" ${!isAdmin||inFlight?'disabled':''}>${inFlight?'评估进行中…':'发起候选评估'}</button>
    </div>
    <p class="muted">当前采用版本：<b>${esc(methodName(s,activeKey))}</b>（${esc(activeKey)}）。评估在独立队列执行，不打断正常预测；中断后可恢复，已完成的周不会重复计算。</p>
    ${evals.length?evalRows:empty('尚无候选方案评估。经理选择候选方法后发起，三周回测完成前不可启用。')}
  </section>`;
}

export function render(s){
  const p=production(s),r=p.forecast_evidence,future=performances(s).filter(x=>x.forecast!==null).slice(0,7);
  let chartHtml='';
  if(r?.backtest)chartHtml=`<div class="forecastchart">${chart(r,p)}</div><div class="legend"><span>实际验票</span><span>当时预测</span></div><div class="strip"><div>平均绝对误差<b>${r.backtest_mae} 人</b></div><div>回测训练天数<b>${r.train_days}</b></div><div>未参与拟合的最近天数<b>${r.holdout_days}</b></div><div>采用方案<b style="font-size:13px">${esc(r.method||'—')}</b></div></div><div class="calendar">${future.map(x=>dayTile(x,p.seats)).join('')}</div>`;
  return `<div class="pagehead"><div><div class="eyebrow">ATTENDANCE FORECAST / 03</div><h1>周末的热闹，提前安排。</h1><p class="subtitle">根据每周节奏预估未来七场上座，并给出引导员配置下限。</p></div><button id="forecast-run" class="primary" ${s.user.role==='viewer'||p.forecast_state==='calculating'?'disabled':''}>${p.forecast_state==='calculating'?'正在核对上座…':'预测未来七场'}</button></div>
  <div class="columns"><section class="section"><h2>最近七日的预测与实际</h2>${r?.backtest?chartHtml:empty(r?.error||'点击预测未来七场。请先补齐连续验票日历；系统会保留最近七天检验预测误差。')}${evalPanel(s)}</section>
  <aside class="section"><h2>容量与场务口径</h2><p class="panelnote">每 40 位预计观众至少配置 1 名引导员，向上取整。无观众时仍保留 1 名值守人员。</p><h3>${p.seats} 席是本场上限</h3><p class="muted">原始需求预测超过座位时，排班采用座位容量封顶；超出部分在下表提示，提醒票务核对。方案比较的误差不做封顶。</p>${r?.future?`<table><thead><tr><th>日期</th><th>原始需求</th><th>排班人数基数</th></tr></thead><tbody>${r.future.map(x=>`<tr><td class="mono">${x.date.slice(5)}</td><td class="mono">${x.raw}${x.raw>p.seats?' ↑':''}</td><td class="mono">${x.capped}</td></tr>`).join('')}</tbody></table>`:''}<p class="muted" style="margin-top:24px">预测保留逐日时间顺序，以更早的记录预测最后七天，再用完整历史预估下一周。新验票会使未确认点名单失效。</p></aside></div>`;
}

export function bind(s,reload){
  const run=document.querySelector('#forecast-run');
  if(run)run.onclick=async()=>{try{await send('/productions/'+s.productionId+'/forecast');notify('已开始核对历史节奏与未来上座');await reload()}catch(x){notify(x.message)}};
  const start=document.querySelector('#eval-start');
  if(start)start.onclick=async()=>{
    const sel=document.querySelector('#candidate-select');
    if(!sel?.value){notify('请先选择候选方案');return}
    try{await send('/productions/'+s.productionId+'/method-evaluations',{candidate_key:sel.value});notify('候选评估已入队，将逐周回测最近三个完整周');await reload()}catch(x){notify(x.message)}
  };
  document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=async()=>{try{await send('/productions/'+s.productionId+'/method-evaluations/resume/'+b.dataset.resume,{});notify('已恢复评估，已完成的周不会重复计算');await reload()}catch(x){notify(x.message)}});
  document.querySelectorAll('[data-activate]').forEach(b=>b.onclick=async()=>{try{await send('/productions/'+s.productionId+'/method-evaluations/'+b.dataset.activate+'/activate',{});notify('候选方案已启用，之后的新预测将采用新版');await reload()}catch(x){notify(x.message)}});
}
