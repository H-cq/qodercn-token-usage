// Qoder CN Token 用量仪表盘（Canvas 版）
// 数据由 build_cn_token_dashboard.py 生成；Tokens 为 context_usage_ratio × 200000 推导，Credits 为实测。
import {
  BarChart,
  ChartComparisonGrid,
  ChartContainer,
  H1,
  LineChart,
  MetricsGrid,
  PieChart,
  ReportSection,
  ReportShell,
  Row,
  SendToChatButton,
  Stack,
  Table,
  Tag,
  Text,
  useCanvasState,
} from 'qoder/canvas';
import type { MetricItem, TableColumn } from 'qoder/canvas';

type DayPoint = {
  label: string;
  main: number;
  sub: number;
  trips: number;
  sessions: number;
  tokens: number;
  credits: number;
  share: number;
};

type DimRow = {
  name: string;
  trips: number;
  sessions: number;
  tokens: number;
  credits: number;
  share: number;
};

type Summary = {
  trips: number;
  subTrips: number;
  sessions: number;
  projects: number;
  days: number;
  tokens: number;
  subTokens: number;
  subShare: number;
  credits: number;
  origCredits: number;
  avgTrip: number;
  avgDay: number;
};

type RangeData = {
  id: string;
  label: string;
  summary: Summary;
  days: DayPoint[];
  models: DimRow[];
  projects: DimRow[];
};

type Payload = {
  generatedAt: string;
  meta: {
    files: number;
    records: number;
    sessions: number;
    projects: number;
    since: string;
    until: string;
    window: number;
    subTrips: number;
    subTokens: number;
    source: string;
  };
  ranges: RangeData[];
  footnotes: string[];
};

const DATA: Payload = __DATA__;

const fmtTokens = (n: number): string => {
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)} 亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)} 万`;
  return `${Math.round(n)}`;
};
const fmtInt = (n: number): string => n.toLocaleString('en-US');
const fmtCredits = (n: number): string =>
  n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmtPct = (n: number): string => `${(n * 100).toFixed(1)}%`;

const dimColumns: TableColumn<DimRow>[] = [
  { key: 'name', title: '名称', role: 'label', truncate: true },
  { key: 'trips', title: '往返', align: 'right', render: (row) => fmtInt(row.trips) },
  { key: 'sessions', title: '会话', align: 'right', render: (row) => fmtInt(row.sessions) },
  { key: 'tokens', title: 'Tokens（推导）', align: 'right', render: (row) => fmtInt(row.tokens) },
  { key: 'share', title: '占比', align: 'right', render: (row) => fmtPct(row.share) },
  { key: 'credits', title: 'Credits', align: 'right', render: (row) => fmtCredits(row.credits) },
];

const DIM_LABELS: [string, string][] = [
  ['day', '按日期'],
  ['model', '按模型'],
  ['project', '按项目'],
];

export default function CnTokenDashboard() {
  const [rangeId, setRangeId] = useCanvasState<string>('qoder-cn-token-range', '7');
  const [dim, setDim] = useCanvasState<string>('qoder-cn-token-dim', 'day');
  const r = DATA.ranges.find((x) => x.id === rangeId) ?? DATA.ranges[0];
  const s = r.summary;
  const dayCats = r.days.map((d) => d.label);
  const topProjects = r.projects.slice(0, 10);
  const rows: DimRow[] =
    dim === 'day'
      ? r.days
          .slice()
          .reverse()
          .map((d) => ({
            name: d.label,
            trips: d.trips,
            sessions: d.sessions,
            tokens: d.tokens,
            credits: d.credits,
            share: d.share,
          }))
      : dim === 'model'
        ? r.models
        : r.projects;

  const kpis: MetricItem[] = [
    {
      label: 'Tokens（推导）',
      value: fmtTokens(s.tokens),
      description: `精确 ${fmtInt(s.tokens)} · 子代理 ${fmtPct(s.subShare)}`,
    },
    {
      label: 'Credits（实测）',
      value: fmtCredits(s.credits),
      description: `原始 Credits ${fmtCredits(s.origCredits)}`,
    },
    {
      label: '往返次数',
      value: fmtInt(s.trips),
      description: `子代理 ${fmtInt(s.subTrips)} 次 · 单次均 ${fmtTokens(s.avgTrip)}`,
    },
    {
      label: '会话 / 项目',
      value: `${s.sessions} / ${s.projects}`,
      description: `覆盖 ${s.days} 个自然日`,
    },
    {
      label: '日均 Tokens',
      value: fmtTokens(s.avgDay),
      description: `按有记录的 ${s.days} 天计`,
    },
  ];

  return (
    <ReportShell width="wide" ariaLabel="Qoder CN Token 用量仪表盘">
      <Stack gap="sectionCompact">
        <Stack gap="component">
          <H1>Qoder CN Token 用量仪表盘</H1>
          <Text tone="secondary">
            桌面端会话转录聚合 · Credits 实测 · Tokens 推导（context_usage_ratio × {fmtInt(DATA.meta.window)}）
          </Text>
          <Row gap="inline" align="center" wrap>
            {DATA.ranges.map((x) => (
              <Tag key={x.id} active={x.id === r.id} onClick={() => setRangeId(x.id)}>
                {x.label}
              </Tag>
            ))}
          </Row>
          <MetricsGrid variant="header" columns={5} items={kpis} />
          <Text size="small" tone="secondary">
            输出与缓存 token 在 CN 桌面端转录中不记录，故不展示；Credits 为客户端上报真值。数据源：
            {DATA.meta.files} 个转录文件 · {fmtInt(DATA.meta.records)} 次模型往返。
          </Text>
        </Stack>

        <ReportSection title="每日消耗趋势" description="按北京时间自然日聚合" meta={r.label} divided>
          <Stack gap="sectionCompact">
            <ChartContainer
              title="每日 Token 用量（推导）"
              description="按来源堆叠：主线 + 子代理"
              caption="单位：tokens"
            >
              <BarChart
                stacked
                height={260}
                categories={dayCats}
                series={[
                  { name: '主线', data: r.days.map((d) => d.main) },
                  { name: '子代理', data: r.days.map((d) => d.sub) },
                ]}
                valueFormatter={fmtTokens}
                accessibilitySummary={`${r.label}每日 Token 用量堆叠柱状图`}
              />
            </ChartContainer>
            <ChartContainer
              title="每日 Credits（实测）"
              description="来自转录 usage.credits 客户端上报值"
              caption="单位：Credits"
            >
              <LineChart
                fill
                height={200}
                categories={dayCats}
                series={[{ name: 'Credits', data: r.days.map((d) => d.credits) }]}
                valueFormatter={fmtCredits}
                accessibilitySummary={`${r.label}每日 Credits 折线图`}
              />
            </ChartContainer>
          </Stack>
        </ReportSection>

        <ReportSection title="构成分布" divided>
          <ChartComparisonGrid plotHeight={260}>
            <ChartContainer title="模型分布" description="按 Tokens 合计；模型为 Qoder 档位标识" caption={r.label}>
              <PieChart
                donut
                centerLabel="合计"
                data={r.models.map((m) => ({ label: m.name, value: m.tokens }))}
                valueFormatter={fmtTokens}
                accessibilitySummary={`${r.label}各模型 Token 占比`}
              />
            </ChartContainer>
            <ChartContainer
              title={`项目排行 Top ${topProjects.length}`}
              description="按 Tokens 合计"
              caption={r.label}
            >
              <BarChart
                horizontal
                height={260}
                categories={topProjects.map((p) => p.name)}
                series={[{ name: 'Tokens', data: topProjects.map((p) => p.tokens) }]}
                valueFormatter={fmtTokens}
                accessibilitySummary={`${r.label}各项目 Token 消耗排行`}
              />
            </ChartContainer>
          </ChartComparisonGrid>
        </ReportSection>

        <ReportSection title="消费明细" description="Tokens 为推导值；Credits 为实测值" divided>
          <Stack gap="component">
            <Row gap="inline" align="center" wrap>
              {DIM_LABELS.map(([key, label]) => (
                <Tag key={key} active={dim === key} onClick={() => setDim(key)}>
                  {label}
                </Tag>
              ))}
            </Row>
            <Table columns={dimColumns} rows={rows} rowKey="name" density="compact" />
          </Stack>
        </ReportSection>

        <ReportSection title="口径与刷新" divided>
          <Stack gap="component">
            {DATA.footnotes.map((f, i) => (
              <Text key={i} size="small" tone="secondary">
                · {f}
              </Text>
            ))}
            <Row gap="component" align="center" wrap>
              <Text size="small" tone="secondary">
                数据快照：{DATA.generatedAt}
              </Text>
              <SendToChatButton
                variant="secondary"
                text="刷新 CN Token 用量仪表盘（重新生成数据）"
              >
                刷新数据
              </SendToChatButton>
            </Row>
          </Stack>
        </ReportSection>
      </Stack>
    </ReportShell>
  );
}
