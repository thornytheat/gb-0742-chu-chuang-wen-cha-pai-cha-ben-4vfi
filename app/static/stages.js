import {drill, el, entries, fault, hour, jump, line, pick, put, q, stamp, state, table, words} from './paper.js';

const raw = (row) => row.raw.map((value) => JSON.stringify(value)).join(' · ');
const issue = (target, rows) => {
  if (!rows.length) target.replaceChildren(el('p','无隔离或迟报记录。','empty'));
  else target.replaceChildren(...rows.map((row) => {
    const ticket = el('div',null,'proof-slip');
    ticket.append(el('strong',`${line(row)} · ${row.code ?? row.flags?.join(' ') ?? '—'}`), el('p',row.note ?? row.fields?.memo ?? raw(row)));
    return ticket;
  }));
};
const matched = (slot) => Boolean(slot.upper && slot.lower);
const rowLabel = (value) => value?.split(':').at(-1) ?? '—';

async function intake({get, base, signal}) {
  const data = await get(`${base}/intake`);
  q('#intake-summary').replaceChildren(entries([['图档编码',data.encoding],['记录段',data.segments],
    ['物理行',data.physical],['数据记录',data.declared]]));
  q('#intake-isolation').replaceChildren(entries(Object.entries(data.isolation)),
    ...(!Object.keys(data.isolation).length ? [el('p','本档无隔离行。','empty')] : []));
  const filter = q('#reading-filter');
  const draw = () => {
    const rows = data.rows.filter((row) => filter.value === 'all' || (filter.value === 'late' ? row.flags?.includes('LATE_RECEIPT') : row.disposition === filter.value));
    table(q('#readings'),rows,[['行',line,'num'],['探头',(row) => row.fields?.probe,'mono'],
      ['测量',(row) => stamp(row.observed_utc),'mono'],['收报',(row) => stamp(row.received_utc),'mono'],
      ['温度 °C',(row) => row.celsius?.[0],'num'],['湿度 %RH',(row) => row.relative_humidity?.[0],'num'],
      ['状态',(row) => state(row.disposition)],['原因码',(row) => row.code ?? row.flags?.join(' '),'mono'],['原始字段',raw]]);
  };
  filter.addEventListener('change',draw,{signal});
  draw();
}

async function assignment({get, base, signal}) {
  const data = await get(`${base}/assignment`);
  const form = q('#lookup-form');
  const result = q('#lookup-result');
  const lookup = async () => {
    const button = q('button',form);
    button.disabled = true;
    try {
      const found = await get(`${base}/assignment/lookup?${new URLSearchParams(new FormData(form))}`);
      if (signal.aborted) return;
      const d = found.deployment;
      result.replaceChildren(d ? entries([['展柜',d.cabinet],['层位',words[d.level]],['部署号',d.id],['起',stamp(d.start)],['止',stamp(d.end)]]) : state(found.code));
    } catch (error) {
      if (!signal.aborted) fault(result,error);
    } finally {
      if (!signal.aborted) button.disabled = false;
    }
  };
  form.addEventListener('submit',(event) => { event.preventDefault(); lookup(); },{signal});
  lookup();
  table(q('#deployments'),data.windows,[['部署号','id','mono'],['探头','probe','mono'],['展柜','cabinet','mono'],
    ['层位',(item) => words[item.level]],['起',(item) => stamp(item.start),'mono'],['止',(item) => stamp(item.end),'mono']]);
  issue(q('#assignment-issues'),[...data.issues,...data.late.filter((row) => !data.issues.some((item) => item.id === row.id))]);
  for (const overlap of data.conflicts) q('#assignment-issues').append(entries([['重叠部署',overlap.deployments.join(' · ')],['起',stamp(overlap.start)],['止',stamp(overlap.end)]]));
}

async function pairing({get, base, cabinet}) {
  const data = await get(`${base}/pairing/${cabinet}`);
  const count = data.slots.filter(matched).length;
  q('#pair-summary').replaceChildren(entries([['容差',`${data.tolerance_seconds} s`],['应配',data.slots.length],['成对',count],['无配对',data.slots.length-count]]));
  table(q('#pairs'),data.slots,[['柜时',(slot) => stamp(slot.start),'mono'],['上层行',(slot) => rowLabel(slot.upper),'num'],
    ['下层行',(slot) => rowLabel(slot.lower),'num'],['时差 s','drift','num'],['证据',(slot) => state(slot.code ?? '已配对')]]);
  table(q('#unpaired'),data.unused,[['行',(row) => rowLabel(row.id),'num'],['原因',(row) => words[row.code]]]);
}

function specimenReading(row) {
  const slip = el('article',null,'proof-slip');
  slip.tabIndex = -1;
  slip.id = `proof-${row.deployment.level}`;
  slip.append(el('strong',`${words[row.deployment.level]} · ${row.id} · ${row.probe}`),
    el('p',`${stamp(row.observed_utc)} 测量 · ${stamp(row.received_utc)} 收报`),
    el('p',`${row.fields.temp} ${row.fields.temp_unit} ± ${row.fields.temp_sigma}  /  ${row.fields.rh} ${row.fields.rh_unit}`));
  if (row.flags.length) slip.append(el('p',row.flags.map((flag) => words[flag]).join('、')));
  if (row.fields.memo) slip.append(el('p',row.fields.memo));
  return slip;
}

function renderCut(spec, slot, byLevel) {
  put('#cut-label',spec.label);
  const target = q('#section-cut');
  const drawing = el('div',null,'cut-drawing');
  const frame = el('div',null,'case-frame');
  const ruler = el('div',null,'measure-ruler');
  const ceiling = Math.ceil((spec.upper_mm + 150) / 500) * 500;
  for (let tick=0; tick<=ceiling; tick+=500) {
    const index = el('span',tick);
    index.style.bottom = `${tick / ceiling * 100}%`;
    ruler.append(index);
  }
  frame.append(el('div',null,'case-floor'));
  for (const level of ['upper','lower']) {
    const row = byLevel[level];
    const mark = el('div',null,'location');
    mark.style.bottom = `${spec[`${level}_mm`] / ceiling * 100}%`;
    const marker = el('button',`${words[level]} · ${row?.probe ?? '缺证'}`);
    marker.type = 'button';
    marker.disabled = !row;
    marker.addEventListener('click', () => q(`#proof-${level}`).focus());
    mark.append(marker,el('span',row ? `${Number(row.celsius[0]).toFixed(2)} °C` : '—'));
    frame.append(mark);
  }
  drawing.append(ruler,frame);
  const caption = el('div',null,'cut-direction');
  caption.append(el('span',`层间距 ${slot.gap_mm} mm`),el('span',`配对时差 ${slot.drift == null ? '—' : `${slot.drift} s`}`));
  target.replaceChildren(drawing,caption);
}

function interval(label, unit, metric) {
  const block = el('div',null,'interval');
  const title = el('header');
  title.append(el('span',label),el('b',metric ? `${metric.value} ${unit}` : '—'));
  block.append(title);
  if (!metric) return block;
  const [low, high, limit] = [metric.low,metric.high,metric.limit].map(Number);
  const scale = Math.max(Math.abs(low),Math.abs(high),limit) * 1.1;
  const percent = (value) => 50 + 50 * value / scale;
  const track = el('div',null,'range-track');
  track.setAttribute('aria-hidden','true');
  for (const [left,right,css] of [[-limit,limit,'safe-band'],[low,high,metric.verdict === 'ABNORMAL' ? 'observed-band abnormal-band' : 'observed-band']]) {
    const band = el('span',null,css);
    band.style.left = `${percent(left)}%`;
    band.style.width = `${percent(right)-percent(left)}%`;
    track.append(band);
  }
  block.append(el('p',`[${metric.low}, ${metric.high}] · ±${metric.limit}`,'interval-label'),track,state(metric.verdict));
  return block;
}

async function gradient({get, base, cabinet, params, signal}) {
  const data = await get(`${base}/gradient/${cabinet}`);
  const filter = q('#grade-filter');
  const target = q('#grade-evidence');
  const load = drill(target,signal);
  const select = (slot) => {
    put('#grade-time',stamp(slot.start));
    renderCut(data.cabinet,slot,{});
    return load(() => get(`${base}/gradient/${cabinet}/evidence?${new URLSearchParams({at:slot.start})}`), (proof) => {
      const title = el('div',null,'proof-title');
      title.append(state(proof.slot.state));
      if (proof.slot.code) title.append(el('span',words[proof.slot.code] ?? proof.slot.code));
      target.replaceChildren(title, interval('温度梯度','°C/m',proof.slot.temperature), interval('湿度梯度','%RH/m',proof.slot.humidity),
        ...proof.rows.map(specimenReading));
      renderCut(proof.cabinet,proof.slot,Object.fromEntries(proof.rows.map((row) => [row.deployment.level,row])));
    });
  };
  const draw = () => {
    const slots = data.slots.filter((slot) => filter.value === 'all' || filter.value === slot.state);
    table(q('#grade-slots'),slots,[['柜时',(slot) => pick(hour(slot.start),() => select(slot),stamp(slot.start)),'mono'],
      ['判级',(slot) => state(slot.state)]]);
    const wanted = slots.findIndex((slot) => slot.start === params.get('at'));
    if (slots.length) q('#grade-slots').querySelectorAll('button')[Math.max(0,wanted)].click();
    else load(async () => null, () => {
      q('#section-cut').replaceChildren();
      put('#grade-time',null);
      target.replaceChildren(el('p','当前筛选无柜时。','empty'));
    });
  };
  filter.addEventListener('change',draw,{signal});
  draw();
}

async function episodes({get, base, cabinet, run, signal}) {
  const data = await get(`${base}/episodes/${cabinet}`);
  const target = q('#episode-detail');
  const load = drill(target,signal);
  const show = (id) => load(() => get(`${base}/episodes/${cabinet}/${id}`),(item) => {
    const links = el('div',null,'jump-list');
    for (const entry of item.evidence) {
      links.append(jump(`${hour(entry.start)} · 原行 ${rowLabel(entry.upper)}/${rowLabel(entry.lower)}`, '/', {run:run.id,cabinet,at:entry.start}));
    }
    target.replaceChildren(entries([['异常段',item.id],['时长',`${item.hours} h`],['指标',item.axes.map((axis) => words[axis]).join('、')]]),links);
  });
  q('#episode-summary').replaceChildren(entries([['总段数',data.length],['持续',data.filter((item) => item.kind === 'SUSTAINED').length],
    ['瞬时',data.filter((item) => item.kind === 'TRANSIENT').length],['持续门槛',`${run.sustained_hours} h`]]));
  table(q('#episodes'), data,[['段号',(row) => pick(row.id,() => show(row.id)),'mono'],['判级',(row) => state(row.kind)],
    ['起',(row) => stamp(row.start),'mono'],['止',(row) => stamp(row.end),'mono'],['时长 h','hours','num']]);
  if (data.length) q('#episodes button').click();
  else target.replaceChildren(el('p','此柜工作时段无异常。','empty'));
}

function audit({audit,run}) {
  const hours = audit.hours;
  const rows = audit.row_audit;
  q('#audit-hours').replaceChildren(entries([['应监测',hours.expected],['稳定',hours.stable],['异常',hours.abnormal],['缺证',hours.missing],
    ['柜时核验',hours.balanced?'守恒':'有差异'],['图档行账',rows.balanced?'守恒':'有差异']]),
    entries([['采信',rows.accepted],['重传',rows.duplicate],['隔离',rows.isolated],['空行',rows.blank],['档头',rows.header]]));
  table(q('#tickets'),audit.tickets,[['工单',(item) => item.slot ? jump(item.id,'/',{run:run.id,cabinet:item.cabinet,at:item.slot}) : item.id,'mono'],
    ['展柜','cabinet','mono'],['馆内时刻',(item) => stamp(item.at_utc),'mono'],['勾稽',(item) => state(item.code)],
    ['关联段','episode','mono'],['原行',(item) => item.evidence.map(rowLabel).join(' / '),'mono']],'本次无报警工单。');
  const present = audit.unreported.length ? audit.unreported.join('、') : '无';
  q('#unreported').replaceChildren(entries([['缺工单',present],['已核销',audit.ticket_counts.MATCHED ?? 0],
    ['工单差项',audit.tickets.length - (audit.ticket_counts.MATCHED ?? 0)],['柜时缺键',hours.missing_keys.length],
    ['柜时多键',hours.extra_keys.length],['柜时重键',hours.duplicate_keys.length]]));
}

function archive({history,run}) {
  const list = el('div',null,'history-list');
  for (const item of history) {
    const link = jump(`${item.shift} · ${item.id.slice(0,8)}`,'/archive',{run:item.id});
    if (item.id === run.id) link.setAttribute('aria-current','true');
    list.append(link);
  }
  q('#history').replaceChildren(list);
  q('#digests').classList.add('digest-list');
  q('#digests').replaceChildren(entries([['归卷号',run.id],['原档 SHA-256',run.hashes.file_sha256],
    ['契约 SHA-256',run.hashes.contract_sha256],['结果 SHA-256',run.result_sha256]]));
  q('#archive-spec').replaceChildren(entries([['输入',run.file],['规则',run.policy],['配准容差',`${run.tolerance_seconds} s`],
    ['持续门槛',`${run.sustained_hours} h`],['耗时',`${run.elapsed_ms} ms`],['归卷柜号',run.cabinets.map((item) => item.id).join('、')]]));
}

const reasonWords = (slot) => slot.reason ? words[slot.reason] ?? slot.reason : slot.paired ? '已配对' : '缺证';
const gapWords = {MISSING_UPPER:'缺上层',NO_COMPATIBLE_LOWER:'无兼容下层',LOWER_CONTENTION:'下层占用'};

function coverageDiff(target, comparison, episodes) {
  const present = el('div',null,'diff-group');
  present.append(el('h3','本版异常段'));
  present.append(episodes.length ? (() => { const list = el('ul',null,'diff-list');
    for (const item of episodes) list.append(el('li',`${item.id} · ${words[item.kind] ?? item.kind} · ${stamp(item.start)}—${stamp(item.end)} · ${item.axes.map((axis) => words[axis] ?? axis).join('、')}`));
    return list; })() : el('p','本版无异常段。','empty'));
  if (!comparison || comparison.from == null) {
    const gaps = comparison ? comparison.slots.still_gap : [];
    const first = el('div',null,'diff-group');
    first.append(el('h3','本版缺口'));
    first.append(gaps.length ? (() => { const list = el('ul',null,'diff-list');
      for (const row of gaps) list.append(el('li',`${row.cabinet} · ${stamp(row.start)} · ${gapWords[row.reason] ?? row.reason}`));
      return list; })() : el('p','无剩余缺口。','empty'));
    target.replaceChildren(present,el('p','首版结论，按当前收报截止点发布。','empty'),first);
    return;
  }
  const convert = comparison.slots.converted;
  const still = comparison.slots.still_gap;
  const episodeDiff = comparison.episodes;
  const blocks = [entries([['上一版',comparison.cutoff_before ? stamp(comparison.cutoff_before) : '—'],
    ['本版截止',comparison.cutoff_after ? stamp(comparison.cutoff_after) : '—'],
    ['可判级',`${comparison.gradable_before} → ${comparison.gradable_after}`],
    ['缺上层',`${comparison.gaps_before.MISSING_UPPER} → ${comparison.gaps_after.MISSING_UPPER}`],
    ['无兼容下层',`${comparison.gaps_before.NO_COMPATIBLE_LOWER} → ${comparison.gaps_after.NO_COMPATIBLE_LOWER}`],
    ['下层占用',`${comparison.gaps_before.LOWER_CONTENTION} → ${comparison.gaps_after.LOWER_CONTENTION}`]])];
  const group = (title, items, draw, empty) => {
    const box = el('div',null,'diff-group');
    box.append(el('h3',title));
    box.append(items.length ? draw(items) : el('p',empty,'empty'));
    return box;
  };
  blocks.push(group('缺口转为可判级',convert,(rows) => {
    const list = el('ul',null,'diff-list');
    for (const row of rows) list.append(el('li',`${row.cabinet} · ${stamp(row.start)} · ${gapWords[row.reason_before] ?? row.reason_before} → ${words[row.state] ?? row.state}`));
    return list;
  },'本版无新增可判级柜时。'));
  blocks.push(group('仍为缺口',still,(rows) => {
    const list = el('ul',null,'diff-list');
    for (const row of rows) list.append(el('li',`${row.cabinet} · ${stamp(row.start)} · ${gapWords[row.reason] ?? row.reason}`));
    return list;
  },'无剩余缺口。'));
  if (comparison.slots.regressed.length) blocks.push(group('退回缺口',comparison.slots.regressed,(rows) => {
    const list = el('ul',null,'diff-list');
    for (const row of rows) list.append(el('li',`${row.cabinet} · ${stamp(row.start)}`));
    return list;
  },''));
  if (comparison.slots.state_changes.length) blocks.push(group('判级变化',comparison.slots.state_changes,(rows) => {
    const list = el('ul',null,'diff-list');
    for (const row of rows) list.append(el('li',`${row.cabinet} · ${stamp(row.start)} · ${words[row.state_before] ?? row.state_before} → ${words[row.state_after] ?? row.state_after}`));
    return list;
  },''));
  blocks.push(group('异常段沿革',[...episodeDiff.retained.map((item) => `${item.id} · 保留 · ${stamp(item.start)}—${stamp(item.end)}`),
    ...episodeDiff.extended.map((item) => `${item.after.id} · 延长 · ${stamp(item.before.end)} → ${stamp(item.after.end)}`),
    ...episodeDiff.changed.map((item) => `${item.after.id} · 变更 · ${stamp(item.after.start)}—${stamp(item.after.end)}`),
    ...episodeDiff.added.map((item) => `${item.id} · 新增 · ${stamp(item.start)}—${stamp(item.end)}`),
    ...episodeDiff.dropped.map((item) => `${item.id} · 消失 · ${stamp(item.start)}—${stamp(item.end)}`)],(rows) => {
    const list = el('ul',null,'diff-list');
    for (const line of rows) list.append(el('li',line));
    return list;
  },'无异常段。'));
  blocks.unshift(present);
  target.replaceChildren(...blocks);
}

async function coverage({get, base, run}) {
  const data = await get(`${base}/coverage`);
  const snapshot = data.coverage;
  put('#coverage-cutoff',snapshot.cutoff ? `截止 ${stamp(snapshot.cutoff)}` : '未设截止点');
  q('#coverage-summary').replaceChildren(entries([['应监测',snapshot.expected],['已配对',snapshot.paired],
    ['可判级',snapshot.gradable],['缺证',snapshot.ungradable],['已配对但缺证',snapshot.paired_ungradable],
    ['截止后暂缓',snapshot.deferred],['归卷对齐',snapshot.alignment.aligned ? '同一批柜时' : '存在偏差']]));
  const slots = snapshot.slots.map((slot) => ({...slot,reason:reasonWords(slot)}));
  table(q('#coverage-slots'),slots,[['柜时',(slot) => stamp(slot.start),'mono'],['上层',(slot) => rowLabel(slot.upper),'num'],
    ['下层',(slot) => rowLabel(slot.lower),'num'],['判级',(slot) => state(slot.state)],
    ['覆盖',(slot) => slot.gradable ? '可判级' : slot.reason,'mono']]);
  const chain = el('div',null,'history-list');
  for (const item of data.versions) {
    const link = jump(`v${item.version} · ${item.sample} · ${item.gradable}/${item.expected}`,'/coverage',{run:item.id});
    if (item.id === run.id) link.setAttribute('aria-current','true');
    const row = el('div',null,'version-row');
    row.append(link,el('span',`截止 ${item.cutoff_utc ? stamp(item.cutoff_utc) : '—'} · 暂缓 ${item.deferred}`,'version-meta'));
    chain.append(row);
  }
  q('#coverage-versions').replaceChildren(chain);
  coverageDiff(q('#coverage-diff'),data.comparison,data.episodes);
}

export const stages = {intake, assignment, pairing, gradient, episodes, audit, coverage, archive};
