/* ============================================================
   七猫风向标 · 风向标页（trend.html）
   数据：api/history.json（周期聚合）
        + api/boy-hot-date/market_summary.json（AI 风向）
        + api/{boy|girl}-hot-date/latest/all.json（赛道分布/关键词）
        + api/black-horses.json + api/authors.json + api/cross-board.json
   ============================================================ */
(function () {
  'use strict';

  var QM = window.QM;
  var echartsOK = typeof echarts !== 'undefined';

  var state = {
    period: 7,             // 7 / 14 / 30 / 0（0=全部）
    genreChannel: 'boy',   // 赛道分布频道
    history: null,         // history.json
    boardLatest: {}        // boy/girl → latest/all.json
  };

  var $ = function (id) { return document.getElementById(id); };
  var genreChart = null;

  /* ---------- 启动 ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    QM.initTheme();
    QM.bindThemeToggle(document.querySelector('.theme-toggle'));
    renderPeriodTabs();
    loadHistory();
    loadAiSummary();
    loadBoardLatest('boy');
    loadBoardLatest('girl');
    loadHorses();
    loadAuthors();
    loadEvergreen();
    bindChartEvents();
  });

  /* ---------- 周期 Tab ---------- */
  var PERIOD_OPTS = [
    { v: 7, name: '近 7 天' },
    { v: 14, name: '近 14 天' },
    { v: 30, name: '近 30 天' },
    { v: 0, name: '全部' }
  ];

  function renderPeriodTabs() {
    var box = $('periodTabs');
    box.innerHTML = '';
    PERIOD_OPTS.forEach(function (p) {
      var el = document.createElement('button');
      el.className = 'tab' + (state.period === p.v ? ' active' : '');
      el.type = 'button';
      el.textContent = p.name;
      el.addEventListener('click', function () {
        state.period = p.v;
        renderPeriodTabs();
        renderAggregation();
      });
      box.appendChild(el);
    });
  }

  /* ---------- history.json 周期聚合 ---------- */
  function loadHistory() {
    QM.fetchJSON(QM.apiPath('history.json'))
      .then(function (data) {
        state.history = data;
        var days = data && data.days_retained;
        $('footGen').textContent = data && data.generated_at ? ('历史数据构建于 ' + QM.formatDateTime(data.generated_at) + (days ? ' · 保留 ' + days + ' 天' : '')) : '';
        renderAggregation();
      })
      .catch(function (err) {
        console.warn('加载 history.json 失败:', err);
        $('riseList').innerHTML = '<li class="state-block" style="border:none;padding:12px 0;">历史数据暂不可用</li>';
        $('heatList').innerHTML = '';
      });
  }

  /** 聚合：周期内每本书 首末点排名差（正=上升）与热度增量 */
  function aggregate(days) {
    var books = (state.history && state.history.books) || {};
    var maxDate = '';
    Object.keys(books).forEach(function (id) {
      (books[id].points || []).forEach(function (p) {
        if (p.d > maxDate) { maxDate = p.d; }
      });
    });
    var cutoff = null;
    if (days && maxDate) {
      var t = new Date(maxDate + 'T00:00:00');
      t.setDate(t.getDate() - (days - 1));
      // 手动格式化，避免 toISOString 的 UTC 时区偏移
      cutoff = t.getFullYear() + '-' +
        String(t.getMonth() + 1).padStart(2, '0') + '-' +
        String(t.getDate()).padStart(2, '0');
    }
    var out = [];
    Object.keys(books).forEach(function (id) {
      var info = books[id];
      var pts = (info.points || []).filter(function (p) { return !cutoff || p.d >= cutoff; });
      if (pts.length < 2) { return; } // 周期内不足两点，无法计算变化
      var first = pts[0], last = pts[pts.length - 1];
      var rankDelta = first.r - last.r;          // 排名数字减小 = 上升
      var heatDelta = (last.h || 0) - (first.h || 0);
      var best = Infinity;
      pts.forEach(function (p) { if (p.r < best) { best = p.r; } });
      out.push({
        id: id,
        title: info.title || ('#' + id),
        author: info.author || '',
        rankDelta: rankDelta,
        heatDelta: heatDelta,
        best: best,
        boards: Object.keys(pts.reduce(function (m, p) { m[p.b] = 1; return m; }, {})).length
      });
    });
    return out;
  }

  function renderAggregation() {
    if (!state.history) { return; }
    var agg = aggregate(state.period);
    var label = PERIOD_OPTS.find(function (p) { return p.v === state.period; });
    $('periodMeta').innerHTML = '统计窗口：<span class="num">' + (state.period ? state.period : '全部') + '</span> 天' +
      (label ? '（' + label.name + '）' : '') + ' · 覆盖 <span class="num">' + agg.length + '</span> 本在榜书';

    var risers = agg.filter(function (x) { return x.rankDelta > 0; })
      .sort(function (a, b) { return b.rankDelta - a.rankDelta || b.heatDelta - a.heatDelta; }).slice(0, 10);
    var heaters = agg.filter(function (x) { return x.heatDelta > 0; })
      .sort(function (a, b) { return b.heatDelta - a.heatDelta; }).slice(0, 10);

    renderRankList($('riseList'), risers, 'rank');
    renderRankList($('heatList'), heaters, 'heat');
  }

  function renderRankList(ul, list, mode) {
    ul.innerHTML = '';
    if (!list.length) {
      ul.innerHTML = '<li style="padding:16px 0;color:var(--muted-foreground);font-size:var(--text-sm);border:none;">暂无数据（需多日快照积累）</li>';
      return;
    }
    list.forEach(function (x, i) {
      var li = document.createElement('li');
      var delta = mode === 'rank'
        ? '<span class="delta trend-up">↑' + x.rankDelta + '</span>'
        : '<span class="delta trend-up">+' + QM.formatCount(x.heatDelta) + '</span>';
      li.innerHTML =
        '<span class="idx">' + (i + 1) + '</span>' +
        '<span class="info">' +
          '<a class="t" href="' + QM.bookLink(x.id) + '">' + QM.escapeHTML(x.title) + '</a>' +
          '<span class="a">' + QM.escapeHTML(x.author || '') + (x.boards ? ' · ' + x.boards + ' 榜在榜' : '') + '</span>' +
        '</span>' + delta;
      ul.appendChild(li);
    });
  }

  /* ---------- AI 风向卡 ---------- */
  function loadAiSummary() {
    QM.fetchJSON(QM.apiPath('boy-hot-date/market_summary.json'))
      .then(function (data) {
        var ov = data && data.overview;
        var src = $('aiSource');
        if (!ov || !ov.text) {
          $('aiCard').textContent = '暂无全站风向分析。';
          src.textContent = '未生成';
          return;
        }
        $('aiCard').innerHTML = QM.miniMarkdown(ov.text);
        var isAI = ov.ai_source === 'ai';
        src.textContent = isAI ? 'AI 分析' : '规则统计';
        src.className = 'ai-source' + (isAI ? ' ai' : '');
        src.title = isAI ? '由大模型生成的趋势分析' : '未配置 AI 或调用失败，采用规则统计文案';
      })
      .catch(function (err) {
        console.warn('加载 market_summary.json 失败:', err);
        $('aiCard').textContent = 'AI 风向数据暂不可用。';
        $('aiSource').textContent = '加载失败';
      });
  }

  /* ---------- 赛道分布 + 关键词（大热日榜 latest/all.json） ---------- */
  function loadBoardLatest(ch) {
    QM.fetchJSON(QM.apiPath(ch + '-hot-date/latest/all.json'))
      .then(function (data) {
        state.boardLatest[ch] = data;
        if (ch === state.genreChannel) {
          renderGenreChannelTabs();
          renderGenreChart();
          renderWordCloud();
        }
      })
      .catch(function (err) {
        console.warn('加载 ' + ch + '-hot-date 榜单失败:', err);
        state.boardLatest[ch] = null;
        if (ch === state.genreChannel) { renderGenreChannelTabs(); }
      });
  }

  function renderGenreChannelTabs() {
    var box = $('genreChannel');
    box.innerHTML = '';
    [{ key: 'boy', name: '男生' }, { key: 'girl', name: '女生' }].forEach(function (c) {
      var ok = !!state.boardLatest[c.key];
      var el = document.createElement('button');
      el.className = 'chip' + (state.genreChannel === c.key ? ' active' : '');
      el.type = 'button';
      el.textContent = c.name;
      el.disabled = !ok;
      if (!ok) { el.title = '该榜单暂无数据'; }
      el.addEventListener('click', function () {
        state.genreChannel = c.key;
        renderGenreChannelTabs();
        renderGenreChart();
        renderWordCloud();
      });
      box.appendChild(el);
    });
  }

  function renderGenreChart() {
    var box = $('genreChart');
    var data = state.boardLatest[state.genreChannel];
    var cats = ((data && data.categories) || []).slice()
      .sort(function (a, b) { return (b.total_heat || 0) - (a.total_heat || 0); })
      .slice(0, 15)
      .reverse(); // 横向柱图自下而上

    if (!echartsOK || !cats.length) {
      box.innerHTML = '<div class="state-block" style="margin:0;border:none;">暂无赛道分布数据</div>';
      return;
    }
    if (!genreChart) { genreChart = echarts.init(box); }
    var C = QM.chartColors();
    genreChart.setOption({
      color: [C.primary],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: C.tooltipBg,
        borderColor: C.border,
        textStyle: { color: C.tooltipText, fontSize: 12 },
        formatter: function (params) {
          var p = params[0];
          var cat = cats[p.dataIndex] || {};
          return QM.escapeHTML(p.name) + '<br/>分类热度：<b>' + QM.formatCount(p.value) +
            '</b><br/>在榜书籍：' + (cat.count || 0) + ' 本';
        }
      },
      grid: { left: 8, right: 46, top: 10, bottom: 10, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: C.label, fontSize: 11, formatter: function (v) { return QM.formatCount(v); } },
        splitLine: { lineStyle: { color: C.split } }
      },
      yAxis: {
        type: 'category',
        data: cats.map(function (c) { return c.name; }),
        axisLabel: { color: C.foreground, fontSize: 12 },
        axisLine: { lineStyle: { color: C.split } },
        axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        data: cats.map(function (c) { return c.total_heat || 0; }),
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          color: C.label,
          fontSize: 11,
          fontFamily: 'JetBrains Mono, Consolas, monospace',
          formatter: function (p) { return QM.formatCount(p.value); }
        }
      }]
    });
  }

  function renderWordCloud() {
    var box = $('wordCloud');
    var data = state.boardLatest[state.genreChannel];
    var words = ((data && data.keywords) || []).slice(0, 40);
    box.innerHTML = '';
    if (!words.length) {
      box.innerHTML = '<span style="color:var(--muted-foreground);font-size:var(--text-sm);">暂无关键词统计数据</span>';
      return;
    }
    var maxC = Math.max.apply(null, words.map(function (w) { return w.count || 0; }));
    var minC = Math.min.apply(null, words.map(function (w) { return w.count || 0; }));
    var frag = document.createDocumentFragment();
    words.forEach(function (w) {
      var c = w.count || 0;
      // 按次数线性缩放字号 12~22px
      var ratio = maxC > minC ? (c - minC) / (maxC - minC) : 1;
      var size = Math.round(12 + ratio * 10);
      var el = document.createElement('span');
      el.className = 'wc-chip';
      el.style.fontSize = size + 'px';
      el.style.opacity = String(0.72 + ratio * 0.28);
      el.title = '出现 ' + c + ' 次';
      el.innerHTML = QM.escapeHTML(w.word) + '<span class="num">' + c + '</span>';
      frag.appendChild(el);
    });
    box.appendChild(frag);
  }

  /* ---------- 黑马榜 ---------- */
  function loadHorses() {
    QM.fetchJSON(QM.apiPath('black-horses.json'))
      .then(function (data) {
        var horses = (data && data.horses) || [];
        if (data && data.date) { $('horseDate').textContent = data.date + ' · 按黑马分降序 TOP' + horses.length; }
        var tbody = $('horseTable').querySelector('tbody');
        tbody.innerHTML = '';
        if (!horses.length) {
          $('horseEmpty').hidden = false;
          return;
        }
        horses.forEach(function (h, i) {
          var tr = document.createElement('tr');
          var rc = h.rank_change;
          var rcHtml = rc > 0 ? '<span class="num trend-up">↑' + rc + '</span>'
            : rc < 0 ? '<span class="num trend-down">↓' + (-rc) + '</span>'
            : '<span class="trend-flat">—</span>';
          var cover = QM.safeUrl(h.cover);
          var img = cover !== '#'
            ? '<img src="' + cover + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
            : '';
          tr.innerHTML =
            '<td class="num">' + (i + 1) + '</td>' +
            '<td><div class="table-book">' + img +
              '<div><a class="t" href="' + QM.bookLink(h.book_id) + '">' + QM.escapeHTML(h.title) + '</a>' +
              '<div class="a">第 ' + (h.rank || '-') + ' 名</div></div></div></td>' +
            '<td class="hide-sm">' + QM.escapeHTML(h.author || '-') + '</td>' +
            '<td class="hide-sm">' + QM.escapeHTML(h.minor || '-') + '</td>' +
            '<td class="hide-sm">' + QM.escapeHTML(h.board_name || h.board || '-') + '</td>' +
            '<td>' + rcHtml + '</td>' +
            '<td class="num trend-up">' + (h.heat_growth_pct != null ? '+' + h.heat_growth_pct + '%' : '-') + '</td>' +
            '<td class="num">' + (h.score != null ? Math.round(h.score) : '-') + '</td>';
          tr.addEventListener('click', function () {
            window.location.href = QM.bookLink(h.book_id);
          });
          tbody.appendChild(tr);
        });
      })
      .catch(function (err) {
        console.warn('加载 black-horses.json 失败:', err);
        $('horseEmpty').hidden = false;
        $('horseEmpty').textContent = '黑马数据暂不可用';
      });
  }

  /* ---------- 热门作者榜 ---------- */
  function loadAuthors() {
    QM.fetchJSON(QM.apiPath('authors.json'))
      .then(function (data) {
        var authors = ((data && data.authors) || []).slice(0, 15);
        var tbody = $('authorTable').querySelector('tbody');
        tbody.innerHTML = '';
        if (!authors.length) {
          tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted-foreground);">暂无作者统计数据</td></tr>';
          return;
        }
        authors.forEach(function (a, i) {
          var tr = document.createElement('tr');
          var link = QM.safeUrl(a.author_url);
          var name = link !== '#'
            ? '<a href="' + link + '" target="_blank" rel="noopener">' + QM.escapeHTML(a.author) + '</a>'
            : QM.escapeHTML(a.author || '-');
          var titles = (a.titles || []).slice(0, 2).join('、');
          tr.innerHTML =
            '<td class="num">' + (i + 1) + '</td>' +
            '<td>' + name + (titles ? '<div class="a" style="font-size:var(--text-2xs);color:var(--muted-foreground);">' + QM.escapeHTML(titles) + '</div>' : '') + '</td>' +
            '<td class="num">' + (a.books != null ? a.books : '-') + '</td>' +
            '<td class="num">' + (a.boards != null ? a.boards : '-') + '</td>' +
            '<td class="num">' + QM.formatCount(a.total_heat) + '</td>';
          tbody.appendChild(tr);
        });
      })
      .catch(function (err) {
        console.warn('加载 authors.json 失败:', err);
      });
  }

  /* ---------- 跨榜常青树 ---------- */
  function loadEvergreen() {
    QM.fetchJSON(QM.apiPath('cross-board.json'))
      .then(function (data) {
        var books = (((data && data.books) || []).slice())
          .sort(function (a, b) { return (b.boards_count || 0) - (a.boards_count || 0); })
          .slice(0, 10);
        var tbody = $('evergreenTable').querySelector('tbody');
        tbody.innerHTML = '';
        if (!books.length) {
          tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted-foreground);">暂无跨榜数据</td></tr>';
          return;
        }
        books.forEach(function (b, i) {
          var tr = document.createElement('tr');
          var names = (b.board_names || []).join('、');
          var cover = QM.safeUrl(b.cover);
          var img = cover !== '#'
            ? '<img src="' + cover + '" alt="" loading="lazy" referrerpolicy="no-referrer">'
            : '';
          tr.innerHTML =
            '<td class="num">' + (i + 1) + '</td>' +
            '<td><div class="table-book">' + img +
              '<div><a class="t" href="' + QM.bookLink(b.book_id) + '">' + QM.escapeHTML(b.title) + '</a>' +
              '<div class="a">' + QM.escapeHTML(b.minor || '') + '</div></div></div></td>' +
            '<td class="hide-sm">' + QM.escapeHTML(b.author || '-') + '</td>' +
            '<td><span class="mini-badge blue"><span class="num">' + (b.boards_count || 0) + '</span> 榜</span>' +
              (names ? '<div class="a" style="font-size:var(--text-2xs);color:var(--muted-foreground);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + QM.escapeHTML(names) + '</div>' : '') + '</td>' +
            '<td class="num hide-sm">#' + (b.best_rank || '-') + '</td>' +
            '<td class="num">' + QM.formatCount(b.total_heat) + '</td>';
          tr.addEventListener('click', function () {
            window.location.href = QM.bookLink(b.book_id);
          });
          tbody.appendChild(tr);
        });
      })
      .catch(function (err) {
        console.warn('加载 cross-board.json 失败:', err);
      });
  }

  /* ---------- 图表事件（resize / 主题切换重建） ---------- */
  function bindChartEvents() {
    var timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (genreChart) { genreChart.resize(); }
      }, 120);
    });
    document.addEventListener('qm-theme-change', function () {
      if (genreChart) { renderGenreChart(); }
    });
  }
})();
