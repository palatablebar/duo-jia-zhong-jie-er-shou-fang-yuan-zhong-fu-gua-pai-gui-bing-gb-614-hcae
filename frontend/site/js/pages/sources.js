import { api } from '../api.js';
import { ENUMS, provenanceTone, recordTone, wan } from '../config.js';
import { badge, drawer, emptyState, errorBox, errorMessage, esc, field, fmtTime, kv, table } from '../ui.js';

const state = { q: '', status: 'unresolved', reason: '' };

function openSource(item) {
  drawer({
    title: `${item.agency_name} 转载 · ${item.lid}`,
    subtitle: `${item.record_kind_label} · ${item.provenance_status_label}`,
    body: `${kv([
      ['转载渠道', `${esc(item.agency_name)} <span class="mono muted">${esc(item.agency_code)}</span>`],
      ['原发布中介（编号）', item.origin_agency_code ? `<span class="mono">${esc(item.origin_agency_code)}</span>` : '<span class="muted">缺失</span>'],
      ['原发布中介（台账名）', esc(item.origin_agency_name || '—')],
      ['原挂牌编号', item.origin_lid ? `<span class="mono">${esc(item.origin_lid)}</span>` : '<span class="muted">缺失</span>'],
      ['对齐状态', badge(provenanceTone(item.provenance_status), item.provenance_status_label)],
      ['待补齐原因', esc(item.provenance_reason_label || '—')],
      ['对齐到的直达记录', item.source_listing_id ? `<span class="mono">${esc(item.source_lid || '')}</span>` : '<span class="muted">未对齐</span>'],
      ['所属房源簇', item.property_id ? `<span class="mono">#${esc(item.property_id)} ${esc(item.property_community || '')} ${esc(item.property_layout || '')}</span>` : '<span class="muted">不进入估价清单</span>'],
      ['转载价格(元)', item.price_num ? `<span class="mono">${esc(wan(item.price_num))}</span>` : '—'],
      ['小区(原文)', esc(item.community_raw || '—')],
      ['更新时间', fmtTime(item.updated_at)],
    ])}
    <p class="note">${
      item.provenance_status === 'unresolved'
        ? '这条转载缺少可对照的原发布中介 / 原挂牌编号，已保留在来源台账，不进入银行估价清单；补齐后下一次归并重算会自动对齐并参与。'
        : '已对齐到直达记录，仅作佐证，不重复参与报价与中介计数。'
    }</p>`,
  });
}

export const sourcesPage = {
  async render(view, ctx) {
    let data;
    let overview;
    try {
      [data, overview] = await Promise.all([
        api.get('/api/sources', { q: state.q, provenance_status: state.status, reason: state.reason, limit: 100 }),
        api.get('/api/overview'),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => sourcesPage.render(view, ctx));
      return;
    }
    const unresolved = overview.unresolved_repost_total || 0;
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>来源台账 <span class="mono muted">· 转载与原发布</span></h2>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span class="meta">${data.total} 条 · 已对齐 ${esc(overview.linked_repost_total)} / 待补齐 ${esc(unresolved)}</span>
            <button class="btn sm ghost" id="to-unresolved">看全部待补齐</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="filter-bar" id="source-filters">
            ${field({ label: '搜索', name: 'q', value: state.q, placeholder: '转载编号 / 原挂牌编号 / 中介' })}
            ${field({ label: '对齐状态', name: 'provenance_status', value: state.status, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.provenanceStatus).filter(([value]) => value !== 'direct').map(([value, label]) => ({ value, label }))] })}
            ${field({ label: '待补齐原因', name: 'reason', value: state.reason, options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.provenanceReason).map(([value, label]) => ({ value, label }))] })}
          </div>
          <p class="note">来源台账记录每一条转载：原发布中介 + 原挂牌编号。能对上直达记录的只作佐证；对不上的留在这里待补齐，不进入银行估价清单，也不会在归并时丢失。</p>
        </div>
        ${table({
          rows: data.items,
          columns: [
            { key: 'lid', label: '转载记录', render: (row) => `<span class="mono">${esc(row.lid)}</span><div class="mono muted">${esc(row.agency_name)}</div>` },
            { key: 'origin_agency', label: '原发布中介', render: (row) => `${esc(row.origin_agency_name || '—')}${row.origin_agency_code ? `<div class="mono muted">${esc(row.origin_agency_code)}</div>` : ''}` },
            { key: 'origin_lid', label: '原挂牌编号', className: 'mono', render: (row) => esc(row.origin_lid || '缺失') },
            { key: 'record_kind', label: '来源性质', render: (row) => badge(recordTone(row.record_kind), row.record_kind_label) },
            { key: 'provenance_status', label: '对齐状态', render: (row) => badge(provenanceTone(row.provenance_status), row.provenance_status_label) },
            { key: 'provenance_reason_label', label: '待补齐原因', render: (row) => esc(row.provenance_reason_label || '—') },
            { key: 'source_lid', label: '对齐到的直达', render: (row) => (row.source_lid ? `<span class="mono">${esc(row.source_lid)}</span>` : '<span class="muted">—</span>') },
            { key: 'property_id', label: '所属簇', render: (row) => (row.property_id ? `<span class="mono">#${esc(row.property_id)}</span>` : '<span class="muted">不进清单</span>') },
          ],
          empty: { title: '这个筛选下没有转载记录', hint: '试试「全部」或先跑一次归并。' },
        })}
      </section>`;

    const filters = view.querySelector('#source-filters');
    filters?.addEventListener('change', () => {
      state.q = filters.querySelector('[name=q]').value.trim();
      state.status = filters.querySelector('[name=provenance_status]').value;
      state.reason = filters.querySelector('[name=reason]').value;
      sourcesPage.render(view, ctx);
    });
    filters?.querySelector('[name=q]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        state.q = event.target.value.trim();
        sourcesPage.render(view, ctx);
      }
    });
    view.querySelector('#to-unresolved')?.addEventListener('click', () => {
      state.status = 'unresolved';
      state.reason = '';
      sourcesPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openSource(item);
      });
    });
  },
};

export default sourcesPage;
