import { api } from '../api.js';
import { ENUMS, provenanceTone, recordTone, statusTone, tagTone, wan } from '../config.js';
import { drawer, errorBox, errorMessage, esc, field, fmtTime, kv, table } from '../ui.js';

const state = { q: '', agencyId: '', status: '', risk: '', propertyId: '', recordKind: '', provenanceStatus: '' };

function pairCell(raw, norm) {
  return `<div class="pair"><span class="raw">${esc(raw)}</span><span class="arrow">→</span><span class="norm">${esc(norm)}</span></div>`;
}

function provenanceBlock(item) {
  const prov = item.provenance || {};
  if (prov.record_kind === 'repost') {
    const origin = prov.origin_agency_name || prov.origin_agency_raw || prov.origin_agency_code || '—';
    return kv([
      ['来源性质', `<span class="badge ${recordTone('repost')}">${esc(prov.record_kind_label)}</span>`],
      ['对齐状态', `<span class="badge ${provenanceTone(prov.status)}">${esc(prov.status_label)}</span>${prov.reason_label ? ` · ${esc(prov.reason_label)}` : ''}`],
      ['原发布中介', `${esc(origin)}${prov.origin_agency_code ? ` <span class="mono muted">${esc(prov.origin_agency_code)}</span>` : ''}`],
      ['原挂牌编号', prov.origin_lid ? `<span class="mono">${esc(prov.origin_lid)}</span>` : '<span class="muted">缺失</span>'],
      ['对齐到的直达记录', prov.source_lid ? `<span class="mono">${esc(prov.source_agency_name || '')} · ${esc(prov.source_lid)}</span>` : '<span class="muted">未对齐，留在来源台账</span>'],
      ['是否计入报价', prov.is_quote ? '是（该中介采用本条发布）' : prov.status === 'unresolved' ? '否（待补齐，不进入银行估价清单）' : '否（只作佐证）'],
    ]);
  }
  return kv([
    ['来源性质', `<span class="badge ${recordTone('direct')}">${esc(prov.record_kind_label || '直达发布')}</span>`],
    ['是否计入报价', prov.is_quote ? '是（该中介采用本条发布）' : '否（同中介重复发布或未入簇）'],
  ]);
}

function openListing(view, ctx, item) {
  const flags = item.parse_flags || [];
  const { root } = drawer({
    title: `${item.community_raw.trim()} · ${item.layout_raw}`,
    subtitle: `${item.lid} · ${item.agency_name} · ${fmtTime(item.captured_at)}`,
    body: `${kv([
      ['小区', `${pairCell(item.community_raw, item.norm.community)}`],
      ['户型', `${pairCell(item.layout_raw, item.norm.layout)}`],
      ['面积', `${pairCell(item.area_raw, item.norm.area === null ? '（无法解析）' : `${item.norm.area} ㎡`)}`],
      ['价格', `${pairCell(item.price_raw, item.norm.price ? wan(item.norm.price) : '（无法解析）')}`],
      ['楼层', `${pairCell(item.floor_raw, item.norm.floor ? `${item.norm.floor}/${item.norm.floor_total || '?'} · ${item.norm.floor_band}` : '（仅区间 / 缺失）')}`],
      ['朝向', `${pairCell(item.orientation_raw, item.norm.orientation || '—')}`],
      ['挂牌状态', `<span class="badge ${statusTone(item.status)}">${esc(item.status_label)}</span>`],
      ['挂牌天数 / 带看', `<span class="mono">${esc(item.listed_days)} 天 · ${esc(item.views)} 次</span>`],
      ['所属簇', item.property_id ? `<span class="mono">#${esc(item.property_id)} ${esc(item.property_community || '')} ${esc(item.property_layout || '')}</span>` : '未入簇'],
      ['风险标签', (item.risk_tags || []).length ? item.risk_tags.map((tag) => `<span class="badge ${tagTone(tag)}">${esc(ENUMS.riskTag[tag] || tag)}</span>`).join(' ') : '—'],
    ])}
    <h3 style="margin:14px 0 8px;font-size:0.95rem">来源链路</h3>
    ${provenanceBlock(item)}
    <h3 style="margin:14px 0 8px;font-size:0.95rem">归一标记</h3>
    <div class="chips">${
      flags.length ? flags.map((flag) => `<span class="chip flag">${esc(flag)}</span>`).join('') : '<span class="chip">无（字段规整）</span>'
    }</div>
    <h3 style="margin:14px 0 8px;font-size:0.95rem">标题与切词</h3>
    <p class="note">${esc(item.title)}</p>
    <div class="chips" style="margin-top:8px">${(item.norm.title_tokens || [])
      .slice(0, 18)
      .map((token) => `<span class="chip">${esc(token)}</span>`)
      .join('')}</div>`,
    footer: item.property_id
      ? `<button class="btn accent" data-cluster>查看归并簇 #${esc(item.property_id)}</button>`
      : item.provenance && item.provenance.status === 'unresolved'
        ? '<span class="mono muted">待补齐原发布：记录保留在来源台账，不进入估价清单</span>'
        : '<span class="mono muted">该挂牌尚未进入任何簇</span>',
  });
  root.querySelector('[data-cluster]')?.addEventListener('click', () => {
    ctx.navigate(`#/clusters?focus=${item.property_id}`);
  });
}

export const listingsPage = {
  async render(view, ctx) {
    if (ctx.query.agency_id) state.agencyId = ctx.query.agency_id;
    if (ctx.query.risk) state.risk = ctx.query.risk;
    if (ctx.query.property_id) state.propertyId = ctx.query.property_id;
    if (ctx.query.record_kind) state.recordKind = ctx.query.record_kind;
    if (ctx.query.provenance_status) state.provenanceStatus = ctx.query.provenance_status;
    let data;
    let agencies;
    try {
      [data, agencies] = await Promise.all([
        api.get('/api/listings', {
          q: state.q,
          agency_id: state.agencyId,
          status: state.status,
          risk: state.risk,
          property_id: state.propertyId,
          record_kind: state.recordKind,
          provenance_status: state.provenanceStatus,
          limit: 50,
        }),
        api.get('/api/agencies', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => listingsPage.render(view, ctx));
      return;
    }
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>挂牌明细 <span class="mono muted">· 原文 vs 归一</span></h2>
          <div style="display:flex;gap:8px;align-items:center">
            <span class="meta">${data.total} 条${state.propertyId ? ` · 簇 #${esc(state.propertyId)}` : ''} · 直达/转载可筛选</span>
            ${
              state.propertyId
                ? '<button class="btn sm ghost" id="clear-cluster">看全部挂牌</button>'
                : ''
            }
          </div>
        </div>
        <div class="panel-body">
          <div class="filter-bar" id="listing-filters">
            ${field({ label: '搜索', name: 'q', value: state.q, placeholder: '小区 / 编号 / 标题' })}
            ${field({ label: '中介', name: 'agency_id', value: state.agencyId, options: [{ value: '', label: '全部' }, ...agencies.items.map((item) => ({ value: item.id, label: item.name }))] })}
            ${field({ label: '挂牌状态', name: 'status', value: state.status, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.listingStatus).map(([value, label]) => ({ value, label }))] })}
            ${field({ label: '风险', name: 'risk', value: state.risk, options: [{ value: '', label: '全部' }, { value: 'bait', label: '钓鱼价' }, { value: 'stale', label: '僵尸挂牌' }, { value: 'none', label: '无标签' }] })}
            ${field({ label: '来源性质', name: 'record_kind', value: state.recordKind, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.recordKind).map(([value, label]) => ({ value, label }))] })}
            ${field({ label: '对齐状态', name: 'provenance_status', value: state.provenanceStatus, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.provenanceStatus).map(([value, label]) => ({ value, label }))] })}
          </div>
        </div>
        ${table({
          rows: data.items,
          columns: [
            { key: 'lid', label: '挂牌编号', className: 'mono' },
            { key: 'agency_name', label: '中介 / 渠道', render: (row) => `${esc(row.agency_name)}` },
            {
              key: 'record_kind',
              label: '来源',
              render: (row) => {
                const prov = row.provenance || {};
                const badge = `<span class="badge ${recordTone(prov.record_kind)}">${esc(prov.record_kind_label)}</span>`;
                const origin = prov.record_kind === 'repost'
                  ? `<div class="mono muted" style="font-size:var(--fs-xs)">原发布 ${esc(prov.origin_agency_name || prov.origin_agency_code || '—')} · ${esc(prov.origin_lid || '编号缺失')}</div>`
                  : '';
                return `${badge}${prov.is_quote ? ' <span class="badge info">报价</span>' : ''}${origin}`;
              },
            },
            { key: 'community', label: '小区', render: (row) => pairCell(row.community_raw, row.norm.community) },
            { key: 'area', label: '面积', render: (row) => pairCell(row.area_raw, row.norm.area === null ? '—' : `${row.norm.area}㎡`) },
            { key: 'price', label: '价格', render: (row) => pairCell(row.price_raw, row.norm.price ? wan(row.norm.price) : '—') },
            { key: 'floor', label: '楼层', render: (row) => pairCell(row.floor_raw, row.norm.floor ? `${row.norm.floor}/${row.norm.floor_total || '?'} 层` : '仅区间') },
            { key: 'status', label: '状态', render: (row) => `<span class="badge ${statusTone(row.status)}">${esc(row.status_label)}</span>` },
            {
              key: 'risk_tags',
              label: '风险',
              render: (row) =>
                (row.risk_tags || []).length
                  ? row.risk_tags.map((tag) => `<span class="badge ${tagTone(tag)}">${esc(ENUMS.riskTag[tag] || tag)}</span>`).join(' ')
                  : `<span class="muted">${row.property_risk_level ? esc(ENUMS.riskLevel[row.property_risk_level] || '') : '—'}</span>`,
            },
          ],
          empty: { title: '没有匹配的挂牌', hint: '换个中介或风险筛选再看。' },
        })}
      </section>`;

    const filters = view.querySelector('#listing-filters');
    filters?.addEventListener('change', () => {
      state.q = filters.querySelector('[name=q]').value.trim();
      state.agencyId = filters.querySelector('[name=agency_id]').value;
      state.status = filters.querySelector('[name=status]').value;
      state.risk = filters.querySelector('[name=risk]').value;
      state.recordKind = filters.querySelector('[name=record_kind]').value;
      state.provenanceStatus = filters.querySelector('[name=provenance_status]').value;
      state.propertyId = '';
      listingsPage.render(view, ctx);
    });
    filters?.querySelector('[name=q]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.q = event.target.value.trim();
        state.propertyId = '';
        listingsPage.render(view, ctx);
      }
    });
    view.querySelector('#clear-cluster')?.addEventListener('click', () => {
      state.propertyId = '';
      listingsPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openListing(view, ctx, item);
      });
    });
  },
};

export default listingsPage;
