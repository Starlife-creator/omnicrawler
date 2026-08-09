// OmniCrawler 评估报告 — 图表脚本
(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var sevHigh = style.getPropertyValue('--sev-high').trim();
  var sevMed = style.getPropertyValue('--sev-med').trim();
  var sevLow = style.getPropertyValue('--sev-low').trim();

  // ---------- 图 1：维度成熟度评分雷达 ----------
  var radarEl = document.getElementById('chart-radar');
  if (radarEl) {
    var radar = echarts.init(radarEl, null, { renderer: 'svg' });
    radar.setOption({
      animation: false,
      tooltip: { trigger: 'item', appendToBody: true },
      radar: {
        indicator: [
          { name: '界面美化', max: 10 },
          { name: '用户交互', max: 10 },
          { name: 'GUI 性能', max: 10 },
          { name: 'GUI 代码质量', max: 10 },
          { name: '一致性', max: 10 },
          { name: '核心架构', max: 10 },
          { name: '安全性', max: 10 },
          { name: '工程化/CI', max: 10 }
        ],
        center: ['50%', '55%'],
        radius: '66%',
        splitNumber: 5,
        axisName: {
          color: ink,
          fontSize: 12,
          fontWeight: 600
        },
        splitLine: { lineStyle: { color: rule } },
        splitArea: { areaStyle: { color: [bg2, 'transparent'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      series: [{
        type: 'radar',
        data: [{
          value: [6.0, 5.5, 4.5, 4.5, 4.5, 7.5, 9.0, 8.0],
          name: '当前评分',
          symbol: 'circle',
          symbolSize: 7,
          lineStyle: { color: accent, width: 2 },
          itemStyle: { color: accent },
          areaStyle: { color: accent, opacity: 0.18 }
        }, {
          value: [10, 10, 10, 10, 10, 10, 10, 10],
          name: '目标（10 分）',
          symbol: 'none',
          lineStyle: { color: rule, width: 1, type: 'dashed' },
          areaStyle: { color: 'transparent' }
        }]
      }],
      legend: {
        data: ['当前评分', '目标（10 分）'],
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 },
        itemWidth: 16,
        itemHeight: 8
      }
    });
    window.addEventListener('resize', function () { radar.resize(); });
  }

  // ---------- 图 2：问题严重度分布 ----------
  var issueEl = document.getElementById('chart-issues');
  if (issueEl) {
    var issue = echarts.init(issueEl, null, { renderer: 'svg' });
    issue.setOption({
      animation: false,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true },
      legend: {
        data: ['高', '中', '低'],
        bottom: 0,
        textStyle: { color: muted, fontSize: 12 },
        itemWidth: 14,
        itemHeight: 8
      },
      grid: { left: '3%', right: '4%', bottom: '15%', top: '8%', containLabel: true },
      xAxis: {
        type: 'category',
        data: ['GUI 子系统', '核心架构', '安全性', '工程化/测试'],
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: ink, fontSize: 12, fontWeight: 600 },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: rule } },
        axisLabel: { color: muted, fontSize: 11 },
        axisLine: { show: false }
      },
      series: [
        {
          name: '高',
          type: 'bar',
          stack: 'total',
          data: [8, 2, 0, 1],
          itemStyle: { color: sevHigh },
          barWidth: '46%',
          label: { show: true, color: '#fff', fontSize: 11, fontWeight: 700 }
        },
        {
          name: '中',
          type: 'bar',
          stack: 'total',
          data: [20, 9, 0, 12],
          itemStyle: { color: sevMed },
          label: { show: true, color: '#fff', fontSize: 11, fontWeight: 700 }
        },
        {
          name: '低',
          type: 'bar',
          stack: 'total',
          data: [10, 9, 2, 13],
          itemStyle: { color: sevLow },
          label: { show: true, color: '#fff', fontSize: 11, fontWeight: 700 }
        }
      ]
    });
    window.addEventListener('resize', function () { issue.resize(); });
  }
})();
