// Claude's replies as markdown. Everything goes through DOMPurify: model output is untrusted HTML.
import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({ gfm: true, breaks: true });
export const md = (text: string) => DOMPurify.sanitize(marked.parse(text, { async: false }) as string);
