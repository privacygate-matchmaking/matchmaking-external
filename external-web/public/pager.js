'use strict';
/* 목록 페이지 나누기: 노드 배열을 size 개씩 container 에 보여 주고, 아래에 1 2 3 … 페이지 버튼을 단다. */
window.Pager = (() => {
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = String(text); return n; };
  // 현재 페이지 주변만 번호로 보여 준다 (1 … 4 5 6 … 12)
  function numbers(page, total) {
    if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
    const set = new Set([1, total, page - 1, page, page + 1].filter(n => n >= 1 && n <= total));
    const list = [...set].sort((a, b) => a - b), out = [];
    list.forEach((n, i) => { if (i && n - list[i - 1] > 1) out.push('…'); out.push(n); });
    return out;
  }
  /**
   * mount(container, nodes, size, {nav, label, onPage}) → {go(page), page()}
   * nav: 페이지 버튼을 넣을 요소 (없으면 container 바로 뒤에 만든다)
   */
  function mount(container, nodes, size, opts = {}) {
    let page = 1;
    const total = Math.max(1, Math.ceil(nodes.length / size));
    let nav = opts.nav;
    if (!nav) { nav = el('nav', 'pager'); container.after(nav); }
    nav.setAttribute('aria-label', opts.label || '페이지');
    function go(n) {
      page = Math.min(Math.max(1, n), total);
      container.replaceChildren(...nodes.slice((page - 1) * size, page * size));
      nav.replaceChildren();
      nav.hidden = total <= 1;
      if (total <= 1) return;
      const prev = el('button', 'pager-step', '‹'); prev.type = 'button'; prev.disabled = page === 1;
      prev.setAttribute('aria-label', '이전 페이지'); prev.onclick = () => turn(page - 1);
      nav.append(prev);
      for (const n of numbers(page, total)) {
        if (n === '…') { nav.append(el('span', 'pager-gap', '…')); continue; }
        const b = el('button', 'pager-num' + (n === page ? ' current' : ''), n); b.type = 'button';
        b.setAttribute('aria-label', `${n}페이지`);
        if (n === page) b.setAttribute('aria-current', 'page');
        b.onclick = () => turn(n);
        nav.append(b);
      }
      const next = el('button', 'pager-step', '›'); next.type = 'button'; next.disabled = page === total;
      next.setAttribute('aria-label', '다음 페이지'); next.onclick = () => turn(page + 1);
      nav.append(next, el('span', 'pager-info', `${nodes.length}건 중 ${(page - 1) * size + 1}–${Math.min(page * size, nodes.length)}`));
      if (opts.onPage) opts.onPage(page);
    }
    function turn(n) { go(n); container.scrollIntoView({behavior: 'smooth', block: 'nearest'}); }
    go(1);
    return {go, page: () => page, total};
  }
  return {mount, numbers};
})();
