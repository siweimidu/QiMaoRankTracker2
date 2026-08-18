/* ============================================================
   七猫风向标 · 总览看板（index.html）
   数据：api/boards.json + api/{slug}/latest/all.json
         + api/status.json + api/cross-board.json
   ============================================================ */
(function () {
  'use strict';

  var QM = window.QM;

  /* 榜单类型与周期的固定顺序（与 boards_config 对应） */
  var TYPES = [
    { key: 'hot', name: '大热' },
    { key: 'new', name: '新书' },
    { key: 'over', name: '完结' },
    { key: 'collect', name: '收藏' },
    { key: 'update', name: '更新' }
  ];
  var PERIODS = [
    { key: 'date', name: '日榜' },
    { key: 'month', name: '月榜' }
  ];
  var CHANNELS = [
    { key: 'male', name: '男生频道' },
    { key: 'female', name: '女生频道' }
  ];

  /* ---------- 页面状态 ---------- */
  var state = {
    boards: null,            // boards.json 分组结果
    channel: 'male',
    type: 'hot',
    period: 'date',
    boardData: null,         // 当前 latest/all.json
    crossMap: {},            // book_id → cross-board 条目
    filters: { category: '全部', q: '', status: 'all', momentum: 'all' },
    loadSeq: 0               // 防止快速切换 Tab 时的竞态
  };

  var $ = function (id) { return document.getElementById(id); };
  var grid = $('boardGrid');

  /* ---------- 启动 ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    QM.initTheme();
    QM.bindThemeToggle(document.querySelector('.theme-toggle'));
    bindToolbar();
    loadStatus();
    loadCrossBoard();
    QM.loadBoards()
      .then(function (data) {
        state.boards = data;
        $('footGen').textContent = data.generated_at ? ('构建于 ' + QM.formatDateTime(data.generated_at)) : '';
        initSelectionFromUrl();
        renderChannelTabs();
        renderL2Tabs();
        loadActiveBoard();
      })
      .catch(function (err) {
        console.warn('加载 boards.json 失败:', err);
        renderError('榜单索引加载失败', '请确认 api/boards.json 已生成（先运行构建脚本），或稍后重试。');
      });
  });

  /* ---------- 状态条 ---------- */
  function loadStatus() {
    QM.fetchJSON(QM.apiPath('status.json'))
      .then(function (st) {
        $('stUpdated').textContent = QM.formatDateTime(st.generated_at);
        $('stScraped').textContent = QM.formatDateTime(st.last_scraped_at);
        $('stTotal').textContent = st.total_books != null ? st.total_books : '-';
        $('stNew').textContent = st.new_today != null ? st.new_today : '-';
        $('stHorse').textContent = st.horse_count != null ? st.horse_count : '-';
        $('statusRow').hidden = false;
        var missing = (st.missing || []).filter(Boolean);
        if (missing.length) {
          $('missingText').textContent = '缺档警告：' + missing.join('；');
          $('missingBar').hidden = false;
        }
      })
      .catch(function (err) {
        console.warn('加载 status.json 失败:', err);
        $('statusRow').hidden = true;
      });
  }

  /* ---------- 跨榜映射（在榜 N 榜徽章） ---------- */
  function loadCrossBoard() {
    QM.fetchJSON(QM.apiPath('cross-board.json'))
      .then(function (data) {
        var map = {};
        ((data && data.books) || []).forEach(function (b) {
          if (b.book_id != null) { map[b.book_id] = b; }
        });
        state.crossMap = map;
        if (state.boardData) { renderCards(); }
      })
      .catch(function (err) { console.warn('加载 cross-board.json 失败:', err); });
  }

  /* ---------- 初始选择（?board= 优先） ---------- */
  function initSelectionFromUrl() {
    var slug = QM.getBoardParam();
    if (slug) {
      var b = findBoardBySlug(slug);
      if (b) {
        state.channel = b.channel === 'female' ? 'female' : 'male';
        state.type = b.type || 'hot';
        state.period = b.period || 'date';
        return;
      }
    }
    // 默认：男生频道大热日榜；若不可用则寻找第一个可用组合
    if (!findBoard(state.channel, state.type, state.period)) {
      var picked = firstAvailable(state.channel);
      if (picked) { state.type = picked.type; state.period = picked.period; }
    }
  }

  function boardsOfChannel(ch) {
    return (ch === 'female' ? state.boards.female : state.boards.male) || [];
  }

  function findBoard(ch, type, period) {
    return boardsOfChannel(ch).find(function (b) {
      return b.type === type && b.period === period && isAvailable(b);
    }) || null;
  }

  function findBoardBySlug(slug) {
    return state.boards.all.find(function (b) { return b.slug === slug && isAvailable(b); }) || null;
  }

  function isAvailable(b) {
    return !!b && b.book_count > 0 && !!b.latest_date;
  }

  function firstAvailable(ch) {
    var list = boardsOfChannel(ch);
    for (var i = 0; i < TYPES.length; i++) {
      for (var j = 0; j < PERIODS.length; j++) {
        if (list.some(function (b) { return b.type === TYPES[i].key && b.period === PERIODS[j].key && isAvailable(b); })) {
          return { type: TYPES[i].key, period: PERIODS[j].key };
        }
      }
    }
    return null;
  }

  function activeBoard() {
    return findBoard(state.channel, state.type, state.period);
  }

  /* ---------- Tab 渲染 ---------- */
  function renderChannelTabs() {
    var box = $('channelTabs');
    box.innerHTML = '';
    CHANNELS.forEach(function (ch) {
      var count = boardsOfChannel(ch.key).filter(isAvailable).length;
      var el = document.createElement('button');
      el.className = 'tab' + (state.channel === ch.key ? ' active' : '');
      el.type = 'button';
      el.innerHTML = QM.escapeHTML(ch.name) + ' <span class="num">' + count + '</span>';
      el.addEventListener('click', function () {
        if (state.channel === ch.key) { return; }
        state.channel = ch.key;
        if (!findBoard(state.channel, state.type, state.period)) {
          // 尝试保持类型、切换周期；仍不行则回退到该频道第一个可用组合
          var alt = findBoard(state.channel, state.type, state.period === 'date' ? 'month' : 'date');
          if (alt) { state.period = alt.period; }
          else {
            var picked = firstAvailable(state.channel);
            if (picked) { state.type = picked.type; state.period = picked.period; }
          }
        }
        renderChannelTabs();
        renderL2Tabs();
        loadActiveBoard();
      });
      box.appendChild(el);
    });
  }

  function renderL2Tabs() {
    var typeBox = $('typeTabs');
    typeBox.innerHTML = '';
    TYPES.forEach(function (t) {
      var hasAny = PERIODS.some(function (p) {
        return findBoard(state.channel, t.key, p.key);
      });
      var el = document.createElement('button');
      el.className = 'tab' + (state.type === t.key ? ' active' : '') + (hasAny ? '' : ' disabled');
      el.type = 'button';
      el.textContent = t.name + '榜';
      if (!hasAny) { el.title = '该榜单暂无数据'; }
      else if (state.type !== t.key) {
        el.addEventListener('click', function () {
          state.type = t.key;
          if (!findBoard(state.channel, state.type, state.period)) {
            var alt = PERIODS.find(function (p) { return findBoard(state.channel, t.key, p.key); });
            if (alt) { state.period = alt.key; }
          }
          renderL2Tabs();
          loadActiveBoard();
        });
      } else {
        el.addEventListener('click', function () { /* 已选中 */ });
      }
      typeBox.appendChild(el);
    });

    var pBox = $('periodToggle');
    pBox.innerHTML = '';
    PERIODS.forEach(function (p) {
      var ok = !!findBoard(state.channel, state.type, p.key);
      var el = document.createElement('button');
      el.className = p.key === state.period ? 'active' : '';
      el.type = 'button';
      el.textContent = p.name;
      el.disabled = !ok;
      if (!ok) { el.title = '该周期暂无数据'; }
      el.addEventListener('click', function () {
        state.period = p.key;
        renderL2Tabs();
        loadActiveBoard();
      });
      pBox.appendChild(el);
    });
  }

  /* ---------- 榜单数据加载 ---------- */
  function loadActiveBoard() {
    var b = activeBoard();
    if (!b) {
      $('toolbar').hidden = true;
      $('boardMeta').textContent = '';
      renderEmpty('该频道暂无榜单数据', '数据尚未生成，请先运行抓取与构建脚本。');
      QM.syncBoardParam(null);
      return;
    }
    QM.syncBoardParam(b.slug);
    var seq = ++state.loadSeq;
    renderBoardMeta(b);
    renderSkeleton();
    QM.fetchJSON(QM.apiPath(b.slug + '/latest/all.json'))
      .then(function (data) {
        if (seq !== state.loadSeq) { return; } // 已切换到其它榜
        state.boardData = data;
        state.filters.category = '全部';
        renderChips();
        $('toolbar').hidden = false;
        renderCards();
      })
      .catch(function (err) {
        if (seq !== state.loadSeq) { return; }
        console.warn('加载榜单数据失败:', err);
        state.boardData = null;
        renderError('榜单数据加载失败', 'api/' + b.slug + '/latest/all.json 读取失败，可点击重试。', loadActiveBoard);
      });
  }

  function renderBoardMeta(b) {
    $('boardMeta').innerHTML =
      '<span>' + QM.escapeHTML(b.name || b.slug) + '</span>' +
      '<span>数据日期 <span class="num">' + QM.formatDate(b.latest_date) + '</span></span>' +
      '<span>在榜 <span class="num">' + (b.book_count != null ? b.book_count : '-') + '</span> 本</span>';
  }

  /* ---------- 分类 chips ---------- */
  function renderChips() {
    var box = $('catChips');
    var cats = ((state.boardData && state.boardData.categories) || []).slice()
      .sort(function (a, c) { return (c.count || 0) - (a.count || 0); });
    var frag = document.createDocumentFragment();

    var all = document.createElement('button');
    all.className = 'chip' + (state.filters.category === '全部' ? ' active' : '');
    all.type = 'button';
    all.innerHTML = '全部 <span class="num">' + ((state.boardData && state.boardData.books) || []).length + '</span>';
    all.addEventListener('click', function () {
      state.filters.category = '全部';
      refreshChipsActive();
      renderCards();
    });
    frag.appendChild(all);

    cats.forEach(function (c) {
      var el = document.createElement('button');
      el.className = 'chip' + (state.filters.category === c.name ? ' active' : '');
      el.type = 'button';
      el.title = '热度合计 ' + QM.formatCount(c.total_heat);
      el.innerHTML = QM.escapeHTML(c.name) + ' <span class="num">' + (c.count || 0) + '</span>';
      el.addEventListener('click', function () {
        state.filters.category = c.name;
        refreshChipsActive();
        renderCards();
      });
      frag.appendChild(el);
    });
    box.innerHTML = '';
    box.appendChild(frag);
  }

  function refreshChipsActive() {
    var chips = $('catChips').children;
    for (var i = 0; i < chips.length; i++) { chips[i].classList.remove('active'); }
    var active = null;
    var name = state.filters.category;
    for (var j = 0; j < chips.length; j++) {
      var txt = chips[j].textContent.replace(/\s*\d+$/, '').trim();
      if (txt === name) { active = chips[j]; break; }
    }
    if (active) { active.classList.add('active'); }
    else if (chips[0]) { chips[0].classList.add('active'); }
  }

  /* ---------- 工具行事件 ---------- */
  function bindToolbar() {
    $('searchInput').addEventListener('input', function () {
      state.filters.q = this.value.trim().toLowerCase();
      renderCards();
    });
    $('statusFilter').addEventListener('change', function () {
      state.filters.status = this.value;
      renderCards();
    });
    $('momentumFilter').addEventListener('change', function () {
      state.filters.momentum = this.value;
      renderCards();
    });
    $('csvBtn').addEventListener('click', exportCSV);
  }

  /* ---------- 过滤 ---------- */
  function filteredBooks() {
    var books = (state.boardData && state.boardData.books) || [];
    var f = state.filters;
    return books.filter(function (b) {
      if (f.category !== '全部' && (b.category || b.minor) !== f.category) { return false; }
      if (f.q) {
        var t = (b.title || '').toLowerCase() + ' ' + (b.author || '').toLowerCase();
        if (t.indexOf(f.q) === -1) { return false; }
      }
      if (f.status !== 'all' && b.status !== f.status) { return false; }
      if (f.momentum === 'up' && !(b.rank_change > 0)) { return false; }
      if (f.momentum === 'down' && !(b.rank_change < 0)) { return false; }
      if (f.momentum === 'new' && !b.is_new) { return false; }
      return true;
    });
  }

  /* ---------- 卡片渲染 ---------- */
  function renderCards() {
    if (!state.boardData) { return; }
    var list = filteredBooks();
    if (!list.length) {
      renderEmpty('没有符合筛选条件的书籍', '试试切换分类、清空搜索词或重置筛选。');
      return;
    }
    grid.innerHTML = '';
    var frag = document.createDocumentFragment();
    list.forEach(function (b) { frag.appendChild(cardHTML(b)); });
    grid.appendChild(frag);
  }

  function cardHTML(b) {
    var art = document.createElement('article');
    art.className = 'book-card';

    /* 排名变化：新上榜 > 升 > 降 > 持平 */
    var rc = b.rank_change;
    var rcHtml, rcClass;
    if (b.is_new) { rcHtml = 'NEW'; rcClass = 'new'; }
    else if (rc > 0) { rcHtml = '↑' + rc; rcClass = 'up'; }
    else if (rc < 0) { rcHtml = '↓' + (-rc); rcClass = 'down'; }
    else { rcHtml = '—'; rcClass = 'flat'; }

    var rankCls = b.rank === 1 ? ' top1' : b.rank === 2 ? ' top2' : b.rank === 3 ? ' top3' : '';

    var cover = QM.safeUrl(b.cover);
    var coverInner = cover !== '#'
      ? '<img src="' + cover + '" alt="' + QM.escapeHTML(b.title) + ' 封面" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML=\'<div class=&quot;cover-fallback&quot;>暂无封面</div>\'">'
      : '<div class="cover-fallback">暂无封面</div>';

    /* 热度变化 */
    var hc = QM.formatSignedCount(b.heat_change);
    var hcHtml = hc
      ? '<span class="heat-change ' + (b.heat_change > 0 ? 'up' : 'down') + '">' + hc + '</span>'
      : '';

    /* 徽章行 */
    var badges = '';
    if (b.badge) {
      badges += '<span class="mini-badge gold">🏅 ' + QM.escapeHTML(b.badge) + '</span>';
    }
    var bc = b.boards_count || (state.crossMap[b.book_id] || {}).boards_count || 0;
    if (bc >= 2) {
      badges += '<span class="mini-badge blue">榜 <span class="num">' + bc + '</span> 在榜</span>';
    }
    if (b.stale_update) {
      badges += '<span class="mini-badge warn" title="最新章节距今超过 3 天">⏸ 停更嫌疑</span>';
    }
    var pt = b.platform_trend;
    if (pt === 'rise') { badges += '<span class="mini-badge" style="color:var(--rise);border-color:var(--rise)">↗ 平台看涨</span>'; }
    else if (pt === 'drop') { badges += '<span class="mini-badge" style="color:var(--drop);border-color:var(--drop)">↘ 平台回落</span>'; }

    var cat = b.category || b.minor || '';

    art.innerHTML =
      '<div class="cover-wrap">' + coverInner +
        '<span class="rank-badge' + rankCls + ' num">' + (b.rank != null ? b.rank : '-') + '</span>' +
        '<span class="rank-change ' + rcClass + '">' + rcHtml + '</span>' +
      '</div>' +
      '<div class="book-body">' +
        '<h3 class="book-title"><a href="' + QM.bookLink(b.book_id) + '">' + QM.escapeHTML(b.title) + '</a></h3>' +
        '<div class="book-meta">' +
          '<span>' + QM.escapeHTML(b.author || '佚名') + '</span><i class="dot">·</i>' +
          '<span>' + QM.escapeHTML((b.major ? b.major + '·' : '') + cat) + '</span><i class="dot">·</i>' +
          '<span>' + QM.escapeHTML(b.status || '-') + '</span><i class="dot">·</i>' +
          '<span class="num">' + QM.escapeHTML(QM.formatWords(b)) + '</span>' +
        '</div>' +
        '<div class="book-heat-row">' +
          '<span class="heat-value num">' + QM.escapeHTML(QM.formatCount(b.heat)) + '<small>热度</small></span>' +
          hcHtml +
        '</div>' +
        (badges ? '<div class="badge-row">' + badges + '</div>' : '') +
        (b.intro ? '<p class="book-intro">' + QM.escapeHTML(b.intro) + '</p>' : '') +
      '</div>';
    return art;
  }

  /* ---------- 状态视图 ---------- */
  function renderSkeleton() {
    grid.innerHTML = '';
    for (var i = 0; i < 6; i++) {
      var d = document.createElement('div');
      d.className = 'skeleton-card';
      d.innerHTML = '<div class="sk sk-cover"></div><div class="sk-body"><div class="sk sk-line w60 h18"></div><div class="sk sk-line w80"></div><div class="sk sk-line w40"></div><div class="sk sk-line w60"></div></div>';
      grid.appendChild(d);
    }
  }

  function renderEmpty(title, desc) {
    grid.innerHTML =
      '<div class="state-block"><span class="icon">🔍</span><h3>' + QM.escapeHTML(title) + '</h3><p>' + QM.escapeHTML(desc) + '</p></div>';
  }

  function renderError(title, desc, retryFn) {
    grid.innerHTML =
      '<div class="state-block"><span class="icon">⚠️</span><h3>' + QM.escapeHTML(title) + '</h3><p>' + QM.escapeHTML(desc) + '</p>' +
      (retryFn ? '<button class="btn btn-primary" type="button">重试</button>' : '') +
      '</div>';
    if (retryFn) {
      var btn = grid.querySelector('button');
      if (btn) { btn.addEventListener('click', retryFn); }
    }
  }

  /* ---------- CSV 导出（当前视图） ---------- */
  function exportCSV() {
    var list = filteredBooks();
    if (!list.length) { return; }
    var b = activeBoard() || {};
    var headers = ['排名', '书名', '作者', '大类', '小类', '状态', '字数', '热度', '热度变化', '排名变化', '新上榜', '在榜榜数', '停更嫌疑', '书籍链接'];
    var rows = list.map(function (x) {
      var bc = x.boards_count || (state.crossMap[x.book_id] || {}).boards_count || 0;
      return [
        x.rank, x.title, x.author, x.major, x.minor, x.status, QM.formatWords(x),
        x.heat, x.heat_change != null ? x.heat_change : '',
        x.is_new ? 'NEW' : (x.rank_change != null ? x.rank_change : ''),
        x.is_new ? '是' : '', bc >= 2 ? bc : '', x.stale_update ? '是' : '',
        QM.safeUrl(x.url)
      ];
    });
    var date = (state.boardData && state.boardData.date) || '';
    QM.downloadCSV('qimao-' + (b.slug || 'board') + (date ? '-' + date : '') + '.csv', headers, rows);
  }
})();
