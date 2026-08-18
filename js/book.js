/* ============================================================
   七猫风向标 · 书籍详情页（book.html?id={book_id}）
   数据：api/books/{book_id}.json
        { book_id,title,cover,author,intro,minor,status,word_count_text,
          latest:{在哪些榜、当前排名、热度}, history:points[{d,b,r,h}] }
   ============================================================ */
(function () {
  'use strict';

  var QM = window.QM;
  var echartsOK = typeof echarts !== 'undefined';
  var chart = null;
  var bookData = null;   // 缓存，主题切换时重建图表

  var $ = function (id) { return document.getElementById(id); };

  document.addEventListener('DOMContentLoaded', function () {
    QM.initTheme();
    QM.bindThemeToggle(document.querySelector('.theme-toggle'));

    var m = window.location.search.match(/[?&]id=(\d+)/);
    if (!m) {
      renderError('缺少书籍 ID', '请从榜单卡片或风向标页进入，例如 book.html?id=195958。');
      return;
    }
    loadBook(m[1]);
    bindChartEvents();
  });

  function loadBook(id) {
    QM.fetchJSON(QM.apiPath('books/' + id + '.json'))
      .then(function (data) {
        if (!data || !data.title) { throw new Error('empty book data'); }
        bookData = data;
        renderHero(data);
        renderDetail(data);
        renderChart(data);
        document.title = data.title + ' · 七猫风向标';
      })
      .catch(function (err) {
        console.warn('加载书籍数据失败:', err);
        renderError('未找到该书籍的数据', '可能该书尚未被当日快照收录，或 api/books/' + QM.escapeHTML(id) + '.json 尚未生成。');
      });
  }

  /* ---------- 头部信息 ---------- */
  function renderHero(b) {
    var cover = QM.safeUrl(b.cover);
    var coverHtml = cover !== '#'
      ? '<img src="' + cover + '" alt="' + QM.escapeHTML(b.title) + ' 封面" referrerpolicy="no-referrer">'
      : '<div class="cover-fallback">暂无封面</div>';

    /* 在榜徽章：latest 兼容数组或 {boards:[...]} 两种结构 */
    var latest = b.latest;
    var boards = Array.isArray(latest) ? latest
      : (latest && Array.isArray(latest.boards)) ? latest.boards
      : [];
    var badgeHtml = boards.length
      ? boards.map(function (x) {
          var name = x.board_name || x.name || x.board || '榜单';
          var rank = x.rank != null ? ' · #' + x.rank : '';
          var heat = x.heat ? ' · ' + QM.formatCount(x.heat) : '';
          return '<span class="board-chip">' + QM.escapeHTML(name) +
            '<span class="num">' + QM.escapeHTML(rank + heat) + '</span></span>';
        }).join('')
      : '<span class="mini-badge">当前未在任何榜单</span>';

    var stale = isStale(b);

    $('bookHero').innerHTML =
      '<div class="cover-wrap">' + coverHtml + '</div>' +
      '<div class="book-hero-info">' +
        '<h1 class="book-hero-title">' + QM.escapeHTML(b.title) + '</h1>' +
        '<div class="book-hero-meta">' +
          '<span>' + QM.escapeHTML(b.author || '佚名') + '</span><i class="dot">·</i>' +
          '<span>' + QM.escapeHTML(b.minor || b.major || '-') + '</span><i class="dot">·</i>' +
          '<span>' + QM.escapeHTML(b.status || '-') + '</span><i class="dot">·</i>' +
          '<span class="num">' + QM.escapeHTML(QM.formatWords(b)) + '</span>' +
          (b.url ? '<i class="dot">·</i><a href="' + QM.safeUrl(b.url) + '" target="_blank" rel="noopener">七猫书页 ↗</a>' : '') +
        '</div>' +
        '<div class="board-badges">' + badgeHtml + '</div>' +
        (stale ? '<div class="stale-warn">⏸ 停更嫌疑：最新章节更新于 ' + QM.escapeHTML(QM.formatDateTime(b.updated_at)) + '，距今超过 3 天</div>' : '') +
      '</div>';
  }

  /** 停更判断：优先数据自带 stale_update 字段，否则按 updated_at 推断 */
  function isStale(b) {
    if (b.stale_update) { return true; }
    if (!b.updated_at) { return false; }
    var t = new Date(String(b.updated_at).replace(' ', 'T'));
    if (isNaN(t.getTime())) { return false; }
    return (Date.now() - t.getTime()) > 3 * 24 * 3600 * 1000;
  }

  /* ---------- 简介与更新信息 ---------- */
  function renderDetail(b) {
    var rows = [
      ['作者', QM.escapeHTML(b.author || '-')],
      ['分类', QM.escapeHTML(b.minor || b.major || '-')],
      ['状态', QM.escapeHTML(b.status || '-')],
      ['字数', '<span class="num">' + QM.escapeHTML(QM.formatWords(b)) + '</span>'],
      ['最新章节', QM.escapeHTML((b.latest_chapter || '-').trim())],
      ['更新时间', '<span class="num">' + QM.escapeHTML(QM.formatDateTime(b.updated_at)) + '</span>']
    ];
    $('bookDetail').innerHTML =
      (b.intro ? '<p class="intro-full">' + QM.escapeHTML(b.intro) + '</p>' : '<p style="color:var(--muted-foreground);">暂无简介</p>') +
      '<dl class="kv-list" style="margin-top:var(--space-4);padding-top:var(--space-4);border-top:1px solid var(--border);">' +
        rows.map(function (r) { return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>'; }).join('') +
      '</dl>';
  }

  /* ---------- 双轴历史曲线 ---------- */
  /** 同日多榜点聚合：排名取最小（最好），热度取最大 */
  function groupPoints(points) {
    var map = {};
    (points || []).forEach(function (p) {
      if (!p.d) { return; }
      if (!map[p.d]) { map[p.d] = { d: p.d, r: p.r, h: p.h || 0 }; }
      else {
        if (p.r != null && (map[p.d].r == null || p.r < map[p.d].r)) { map[p.d].r = p.r; }
        if ((p.h || 0) > map[p.d].h) { map[p.d].h = p.h; }
      }
    });
    return Object.keys(map).sort().map(function (d) { return map[d]; });
  }

  function renderChart(b) {
    var box = $('historyChart');
    var pts = groupPoints(b.history || b.points);
    if (!echartsOK || pts.length < 2) {
      box.innerHTML = '<div class="state-block" style="margin:0;border:none;">' +
        (pts.length < 2 ? '历史数据不足（需至少两天快照才能绘制曲线）' : '图表库加载失败') + '</div>';
      return;
    }
    if (!chart) { chart = echarts.init(box); }

    var dates = pts.map(function (p) { return p.d.slice(5); }); // MM-DD
    var ranks = pts.map(function (p) { return p.r != null ? p.r : null; });
    var heats = pts.map(function (p) { return p.h > 0 ? +(p.h / 10000).toFixed(1) : null; }); // 万
    var C = QM.chartColors();

    chart.setOption({
      color: [C.primary, C.secondary],
      tooltip: {
        trigger: 'axis',
        backgroundColor: C.tooltipBg,
        borderColor: C.border,
        textStyle: { color: C.tooltipText, fontSize: 12 },
        formatter: function (params) {
          var html = params[0].axisValue;
          params.forEach(function (p) {
            var v = p.value == null ? '暂无' : p.value;
            if (p.seriesName === '热度') { v = p.value == null ? '暂无' : p.value + ' 万'; }
            html += '<br/>' + p.marker + p.seriesName + '：<b>' + v + '</b>';
          });
          return html;
        }
      },
      legend: {
        data: ['排名', '热度'],
        textStyle: { color: C.label, fontSize: 12 },
        top: 0
      },
      grid: { left: 8, right: 14, top: 34, bottom: 52, containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLabel: { color: C.label, fontSize: 11 },
        axisLine: { lineStyle: { color: C.split } }
      },
      yAxis: [
        {
          type: 'value',
          name: '排名',
          nameTextStyle: { color: C.label },
          inverse: true,
          min: 1,
          minInterval: 1,
          axisLabel: { color: C.label, fontSize: 11, formatter: function (v) { return '#' + v; } },
          splitLine: { lineStyle: { color: C.split } }
        },
        {
          type: 'value',
          name: '热度（万）',
          nameTextStyle: { color: C.label },
          scale: true,
          axisLabel: { color: C.label, fontSize: 11 },
          splitLine: { show: false }
        }
      ],
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', height: 18, bottom: 8, borderColor: C.split, textStyle: { color: C.label, fontSize: 10 } }
      ],
      series: [
        {
          name: '排名',
          type: 'line',
          yAxisIndex: 0,
          data: ranks,
          connectNulls: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 2.5 }
        },
        {
          name: '热度',
          type: 'line',
          yAxisIndex: 1,
          data: heats,
          connectNulls: true,
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 2, type: 'solid' },
          areaStyle: { opacity: 0.08 }
        }
      ]
    });
  }

  /* ---------- 错误态 ---------- */
  function renderError(title, desc) {
    $('bookMain').innerHTML =
      '<div class="state-block" style="margin:var(--space-8) 0;">' +
        '<span class="icon">📕</span><h3>' + QM.escapeHTML(title) + '</h3><p>' + QM.escapeHTML(desc) + '</p>' +
        '<a class="btn btn-primary" href="index.html">返回总览看板</a>' +
      '</div>';
  }

  /* ---------- resize / 主题切换 ---------- */
  function bindChartEvents() {
    var timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () { if (chart) { chart.resize(); } }, 120);
    });
    document.addEventListener('qm-theme-change', function () {
      if (bookData) { renderChart(bookData); }
    });
  }
})();
