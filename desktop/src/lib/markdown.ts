// Claude's replies as markdown. Everything goes through DOMPurify: model output is untrusted HTML.
import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({ gfm: true, breaks: true });

// inline code that names a file or folder becomes clickable (the page decides what a click does)
const FILE_EXT = /\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,8}$/;  // .md, .py, .mp4; not v1.2
export function looksLikePath(t: string): boolean {
  t = t.replace(/:\d+(:\d+)?$/, '');  // file.py:12(:3)
  if (!t || t.length > 300 || /[\n<>|*?"]/.test(t)) return false;
  if (/^(~[\/]|\/|[A-Za-z]:[\\/]|\\\\)/.test(t)) return !/\s{2}/.test(t);  // absolute: /x, ~/x, C:\x, \\server
  if (/\s/.test(t) || /^[a-z]+:\/\//i.test(t)) return false;  // relative paths: no spaces, not a URL
  return (/[\/\\]/.test(t) && !/^[.\/]+$/.test(t)) || FILE_EXT.test(t) && !/^\d/.test(t) && !/\(|\)/.test(t);
}
const esc = (t: string) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
marked.use({ renderer: { codespan({ text }) { return `<code${looksLikePath(text) ? ' class="path" title="Open"' : ''}>${esc(text)}</code>`; } } });
export const md = (text: string) => DOMPurify.sanitize(marked.parse(text, { async: false }) as string);
