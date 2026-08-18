/* ============================================================
   七猫风向标 · 公共工具（三页共用）
   依赖：无（纯原生 JS）
   ============================================================ */
(function () {
  'use strict';

  var QM = {};

  /* ---------- 路径工厂（全部相对路径，本地与 GitHub Pages 均可直接运行） ---------- */

  /** 拼接静态 API 相对路径：apiPath('boards.json') → 'api/boards.json' */
  QM.apiPath = function (file) {
    return 'api/' + String(file).replace(/^\/+/, '');
  };

  /** 读取当前 URL 的 ?board= 参数 */
  QM.getBoardParam = function () {
    var m = window.location.search.match(/[?&]board=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  };

  /** 将当前榜单 slug 同步到 URL（replaceState 不污染历史） */
  QM.syncBoardParam = function (slug) {
    try {
      var url = new URL(window.location.href);
      if (slug) { url.searchParams.set('board', slug); }
      else { url.searchParams.delete('board'); }
      window.history.replaceState(null, '', url);
    } catch (e) { /* 忽略老旧浏览器 */ }
  };

  /* ---------- 网络请求 ---------- */

  /**
   * fetchJSON：带超时与 1 次重试的 JSON 请求
   * @param {string} url 相对/绝对地址
   * @param {{timeout?:number, retries?:number}} [opts]
   */
  QM.fetchJSON = function (url, opts) {
    opts = opts || {};
    var timeout = opts.timeout || 12000;
    var retries = opts.retries == null ? 1 : opts.retries;

    function attempt(remain) {
      var ctrl = new AbortController();
      var timer = setTimeout(function () { ctrl.abort(); }, timeout);
      return fetch(url, { signal: ctrl.signal, cache: 'no-cache' })
        .then(function (res) {
          clearTimeout(timer);
          if (!res.ok) { throw new Error('HTTP ' + res.status); }
          return res.json();
        })
        .catch(function (err) {
          clearTimeout(timer);
          if (remain > 0) { return attempt(remain - 1); }
          throw err;
        });
    }
    return attempt(retries);
  };

  /**
   * loadBoards：读取 boards.json 并按频道分组
   * @returns {Promise<{generated_at:string, all:object[], male:object[], female:object[]}>}
   */
  QM.loadBoards = function () {
    return QM.fetchJSON(QM.apiPath('boards.json')).then(function (data) {
      var boards = (data && data.boards) || [];
      var group = { male: [], female: [], all: boards };
      boards.forEach(function (b) {
        if (b.channel === 'male') { group.male.push(b); }
        else if (b.channel === 'female') { group.female.push(b); }
      });
      return {
        generated_at: data && data.generated_at,
        all: boards,
        male: group.male,
        female: group.female
      };
    });
  };

  /* ---------- 主题（localStorage: qm-theme，默认跟随系统） ---------- */

  QM.THEME_KEY = 'qm-theme';

  /** 初始化主题（页面 head 已有内联防闪烁脚本，此处做系统变化监听） */
  QM.initTheme = function () {
    var media = window.matchMedia('(prefers-color-scheme: dark)');
    var handler = function () {
      var saved = null;
      try { saved = localStorage.getItem(QM.THEME_KEY); } catch (e) { /* 隐私模式 */ }
      if (!saved) { apply(media.matches); }
    };
    if (media.addEventListener) { media.addEventListener('change', handler); }
    else if (media.addListener) { media.addListener(handler); }
  };

  function apply(dark) {
    document.documentElement.classList.toggle('dark', !!dark);
  }

  QM.isDark = function () {
    return document.documentElement.classList.contains('dark');
  };

  /** 切换主题并广播 'qm-theme-change' 事件（图表页监听后重建配色） */
  QM.toggleTheme = function () {
    apply(!QM.isDark());
    try { localStorage.setItem(QM.THEME_KEY, QM.isDark() ? 'dark' : 'light'); } catch (e) { /* 忽略 */ }
    document.dispatchEvent(new CustomEvent('qm-theme-change', { detail: { dark: QM.isDark() } }));
  };

  /** 绑定主题切换按钮（class="theme-toggle"） */
  QM.bindThemeToggle = function (btn) {
    if (!btn) { return; }
    var refresh = function () { btn.textContent = QM.isDark() ? '☀️' : '🌙'; btn.title = QM.isDark() ? '切换到亮色' : '切换到暗色'; };
    refresh();
    btn.addEventListener('click', function () { QM.toggleTheme(); refresh(); });
    document.addEventListener('qm-theme-change', refresh);
  };

  /* ---------- 格式化 ---------- */

  /** 1362000 → "136.2万"；9500 → "9500"；空值 → "-" */
  QM.formatCount = function (n) {
    if (n == null || isNaN(n)) { return '-'; }
    n = Number(n);
    if (Math.abs(n) >= 10000) {
      var s = (Math.round((n / 10000) * 10) / 10).toFixed(1);
      if (/\.0$/.test(s)) { s = s.slice(0, -2); }
      return s + '万';
    }
    return String(n);
  };

  /** 带符号版本：21000 → "+2.1万"；-3000 → "-3000" */
  QM.formatSignedCount = function (n) {
    if (n == null || isNaN(n) || Number(n) === 0) { return null; }
    var sign = n > 0 ? '+' : '';
    return sign + QM.formatCount(n);
  };

  /** 字数显示：优先用字数文本，否则由数值换算 "xxx万字" */
  QM.formatWords = function (book) {
    if (!book) { return '-'; }
    if (book.word_count_text) { return book.word_count_text; }
    if (book.word_count) { return QM.formatCount(book.word_count) + '字'; }
    return '-';
  };

  /** 日期时间显示："2026-08-18T09:49:07" → "08-18 09:49" */
  QM.formatDateTime = function (s) {
    if (!s) { return '-'; }
    var m = String(s).match(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
    if (m) { return m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5]; }
    return String(s);
  };

  QM.formatDate = function (s) {
    if (!s) { return '-'; }
    return String(s).slice(0, 10);
  };

  /** HTML 转义（防 XSS） */
  QM.escapeHTML = function (s) {
    if (s == null) { return ''; }
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  /** 外链安全过滤：仅允许 http(s)，其余返回 '#' */
  QM.safeUrl = function (u) {
    return (typeof u === 'string' && /^https?:\/\//i.test(u)) ? u : '#';
  };

  /** 书籍详情页链接（book_id 仅允许数字） */
  QM.bookLink = function (id) {
    return /^\d+$/.test(String(id)) ? 'book.html?id=' + id : '#';
  };

  /**
   * miniMarkdown：极简 Markdown 渲染（先 escapeHTML 再替换，防 XSS）
   * 支持：## 标题、**粗体**、- 列表、换行
   */
  QM.miniMarkdown = function (text) {
    if (!text) { return ''; }
    var lines = QM.escapeHTML(text).split(/\r?\n/);
    var out = [];
    var inList = false;
    var inline = function (s) {
      return s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    };
    lines.forEach(function (raw) {
      var line = raw.trim();
      if (/^[-•]\s+/.test(line)) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + inline(line.replace(/^[-•]\s+/, '')) + '</li>');
        return;
      }
      if (inList) { out.push('</ul>'); inList = false; }
      if (!line) { return; }
      var hm = line.match(/^#{1,4}\s+(.*)$/);
      if (hm) { out.push('<h4>' + inline(hm[1]) + '</h4>'); }
      else { out.push('<p>' + inline(line) + '</p>'); }
    });
    if (inList) { out.push('</ul>'); }
    return out.join('');
  };

  /* ---------- CSV 导出 ---------- */

  /**
   * 数组转 CSV 字符串（带 BOM，Excel 中文不乱码）
   * @param {string[]} headers 表头
   * @param {Array<Array<*>>} rows 行数据
   */
  QM.toCSV = function (headers, rows) {
    function cell(v) {
      var s = v == null ? '' : String(v);
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }
    var lines = [headers.map(cell).join(',')];
    rows.forEach(function (r) { lines.push(r.map(cell).join(',')); });
    return '\uFEFF' + lines.join('\r\n');
  };

  /** 触发浏览器下载 CSV */
  QM.downloadCSV = function (filename, headers, rows) {
    var blob = new Blob([QM.toCSV(headers, rows)], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 800);
  };

  /* ---------- ECharts 主题辅助 ---------- */

  /** 从 CSS token 读取当前主题的图表配色 */
  QM.chartColors = function () {
    var cs = getComputedStyle(document.documentElement);
    var pick = function (name, fallback) {
      var v = cs.getPropertyValue(name).trim();
      return v || fallback;
    };
    return {
      primary: pick('--chart-1', '#0065fd'),
      secondary: pick('--chart-2', '#557fff'),
      label: pick('--chart-label', '#7f8d9f'),
      split: pick('--chart-split', '#e7eaef'),
      tooltipBg: pick('--chart-tooltip-bg', '#ffffff'),
      tooltipText: pick('--chart-tooltip-text', '#0e1115'),
      rise: pick('--rise', '#16a34a'),
      drop: pick('--drop', '#ef4444'),
      foreground: pick('--foreground', '#0e1115'),
      border: pick('--border', '#e7eaef')
    };
  };

  /** 挂载到全局 */
  window.QM = QM;
})();
