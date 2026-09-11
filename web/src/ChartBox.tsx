import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ChartSpec } from "./api";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const PALETTE = ["#22d3ee", "#3b82f6", "#a78bfa", "#fcd34d", "#34d399"];

// 主题色取自 CSS 变量（.light 下自动切换）
const cssVar = (name: string, fallback: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

function buildOption(c: ChartSpec) {
  const light = document.documentElement.classList.contains("light");
  const fg = cssVar("--color-fg", "#dbe4f3");
  const muted = cssVar("--color-fg-muted", "#94a3b8");
  const edge = cssVar("--color-edge", "#1b2942");
  const ink = cssVar("--color-ink", "#05070d");
  const gridLine = light ? "rgba(15,23,42,0.08)" : "rgba(56,189,248,0.08)";

  const base = {
    color: PALETTE,
    backgroundColor: "transparent",
    textStyle: { color: fg, fontFamily: "JetBrains Mono, monospace" },
    tooltip: {
      trigger: "axis" as const,
      backgroundColor: light ? "#ffffff" : "#0a1122",
      borderColor: edge,
      textStyle: { color: fg, fontSize: 12 },
    },
    title: {
      text: c.title,
      left: "center",
      textStyle: { color: fg, fontSize: 13, fontWeight: 500 },
    },
    grid: { left: 44, right: 20, top: c.title ? 44 : 24, bottom: 30 },
    legend: { top: c.title ? 26 : 4, textStyle: { color: muted, fontSize: 11 } },
    xAxis: {
      type: "category" as const,
      data: c.x,
      axisLine: { lineStyle: { color: edge } },
      axisLabel: { color: muted, fontSize: 11 },
    },
    yAxis: {
      type: "value" as const,
      splitLine: { lineStyle: { color: gridLine } },
      axisLabel: { color: muted, fontSize: 11 },
    },
  };

  if (c.type === "pie") {
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "item" as const },
      grid: undefined,
      xAxis: undefined,
      yAxis: undefined,
      series: [
        {
          type: "pie",
          radius: ["32%", "68%"],
          center: ["50%", "54%"],
          data: c.x.map((name, i) => ({ name, value: c.series[0]?.data[i] ?? null })),
          itemStyle: { borderColor: ink, borderWidth: 2 },
          label: { color: muted, fontSize: 11 },
        },
      ],
    };
  }

  return {
    ...base,
    series: c.series.map((s) => ({
      type: c.type,
      name: s.name,
      data: s.data,
      smooth: c.type === "line",
      symbolSize: c.type === "line" ? 6 : undefined,
      barMaxWidth: 42,
      itemStyle: c.type === "bar" ? { borderRadius: [6, 6, 0, 0] } : undefined,
      areaStyle: c.type === "line" ? { opacity: 0.12 } : undefined,
    })),
  };
}

export default function ChartBox({ chart }: { chart: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const c = echarts.init(el);
    inst.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      c.dispose();
      inst.current = null;
    };
  }, []);

  // 图表数据或主题变化时重建 option
  useEffect(() => {
    const c = inst.current;
    if (!c) return;
    c.setOption(buildOption(chart), true);
    const mq = new MutationObserver(() => c.setOption(buildOption(chart), true));
    mq.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => mq.disconnect();
  }, [chart]);

  const download = () => {
    const c = inst.current;
    if (!c) return;
    const a = document.createElement("a");
    a.href = c.getDataURL({ pixelRatio: 2, backgroundColor: "transparent" });
    a.download = "chart.png";
    a.click();
  };

  return (
    <div>
      <div ref={ref} className="h-72 w-full" />
      <div className="flex justify-end px-1 pt-1">
        <button
          onClick={download}
          className="rounded-md border border-cyan-400/30 px-2.5 py-1 text-xs text-accent-fg transition hover:bg-cyan-400/10"
        >
          下载图表 PNG
        </button>
      </div>
    </div>
  );
}
