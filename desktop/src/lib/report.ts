// Bug reports: the app's own recent errors + each daemon's scrubbed log + versions, as one markdown report.
// Step 1 (no credentials): copy the report, open a pre-filled GitHub issue to paste it into.
import type { Machine } from './api';

export const REPORT_REPO = 'cyaninc-lab/switchboard';
const ring: string[] = [];
const SECRETS: [RegExp, string][] = [
  [/sk-ant-[A-Za-z0-9_-]{8,}/g, 'sk-ant-[redacted]'],
  [/(bearer\s+)[A-Za-z0-9._-]{12,}/gi, '$1[redacted]'],
  [/("?(?:token|secret|password|api_key|authorization)"?\s*[:=]\s*"?)[^"\s,}]{6,}/gi, '$1[redacted]'],
];
export const scrub = (t: string) => SECRETS.reduce((s, [rx, sub]) => s.replace(rx, sub), t);

function remember(level: string, args: unknown[]) {
  const text = args.map((a) => (a instanceof Error ? `${a.message}\n${a.stack ?? ''}` : typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
  ring.push(`${new Date().toISOString()} ${level} ${text}`);
  if (ring.length > 200) ring.shift();
}

/** Start collecting the app's own warnings and errors (call once). */
export function captureAppLog() {
  for (const level of ['error', 'warn'] as const) {
    const orig = console[level].bind(console);
    console[level] = (...a: unknown[]) => { remember(level.toUpperCase(), a); orig(...a); };
  }
  window.addEventListener('error', (e) => remember('ERROR', [e.error ?? e.message]));
  window.addEventListener('unhandledrejection', (e) => remember('ERROR', ['unhandled rejection', e.reason]));
}

type MachineInfo = { name: string; daemon?: string; sdk?: string; os?: string; arch?: string; error?: string; log?: string };

async function machineInfo(m: Machine, withLogs: boolean): Promise<MachineInfo> {
  const get = (p: string) => fetch(m.url.replace(/\/$/, '') + p, { headers: { Authorization: `Bearer ${m.token}` } }).then((r) => r.json());
  try {
    const info = await get('/v1/machine');
    return { ...info, log: withLogs ? (await get('/v1/logs?lines=200')).log : undefined };
  } catch (e) {
    return { name: m.name, error: String((e as Error).message) };
  }
}

export async function buildReport(description: string, machines: Machine[], withLogs: boolean, appVersion: string) {
  const infos = await Promise.all(machines.map((m) => machineInfo(m, withLogs)));
  const lines = [
    description.trim(),
    '',
    '### Environment',
    `- App: Switchboard ${appVersion} on ${navigator.userAgent}`,
    ...infos.map((i) => i.error ? `- ${i.name}: unreachable (${i.error})` : `- ${i.name}: daemon ${i.daemon}, Agent SDK ${i.sdk}, ${i.os} ${i.arch}`),
  ];
  if (withLogs) {
    lines.push('', '### App log', '<details><summary>last app warnings/errors</summary>', '', '```', ring.join('\n') || '(none)', '```', '</details>');
    for (const i of infos.filter((i) => i.log !== undefined)) {
      lines.push('', `### Daemon log: ${i.name}`, '<details><summary>last 200 lines</summary>', '', '```', i.log || '(empty)', '```', '</details>');
    }
  }
  return scrub(lines.join('\n'));
}

export function issueUrl(description: string) {
  const title = description.trim().split('\n')[0].slice(0, 90) || 'Bug report';
  const body = '<!-- Paste the report Switchboard copied to your clipboard below (Ctrl+V), then submit. -->\n\n';
  return `https://github.com/${REPORT_REPO}/issues/new?labels=bug&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}
