import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import type { Reference } from './collaboration'

const referencePattern =
  /(?<![\w/#@\\])(?:https?:\/\/[^\s<>\[\]()]+|[a-z0-9][a-z0-9_-]*\/[a-z0-9][a-z0-9._-]*(?:#[1-9][0-9]*|@[0-9a-fA-F]{7,40})|#[1-9][0-9]*|@[a-z0-9][a-z0-9_-]*|[0-9a-fA-F]{7,40})(?![\w])/g

export function renderDiscussion(
  source: string,
  references: Reference[] = [],
  editable = false,
): string {
  const md = new MarkdownIt({ html: false, linkify: true, typographer: false })
  md.disable('text_join')
  const sourceLines = source.split('\n')
  const targets = new Map(references.map((reference) => [reference.raw, reference]))
  const escape = md.utils.escapeHtml
  md.core.ruler.after('linkify', 'discussion-links', (state) => {
    for (const block of state.tokens) {
      let links = 0
      for (const token of block.children || []) {
        if (token.type === 'link_open') links++
        if (token.type === 'link_close') links--
        token.meta = { ...token.meta, insideLink: links > 0, line: block.map?.[0] }
      }
    }
  })
  md.renderer.rules.text_special = (tokens, index) => escape(tokens[index]!.content)
  md.renderer.rules.text = (tokens, index) => {
    const token = tokens[index]!
    if (token.meta?.insideLink) return escape(token.content)
    let rendered = '',
      cursor = 0
    for (const match of token.content.matchAll(referencePattern)) {
      const target = targets.get(match[0])
      rendered += escape(token.content.slice(cursor, match.index))
      rendered +=
        target && target.url.startsWith('/repositories/')
          ? `<a href="${escape(target.url)}" class="discussion-reference" title="${escape(`${target.title}${target.state ? ` · ${target.state}` : ''}`)}">${escape(match[0])}</a>`
          : escape(match[0])
      cursor = match.index + match[0].length
    }
    rendered += escape(token.content.slice(cursor))
    if (
      index === 0 &&
      /^\[[ xX]\] /.test(token.content) &&
      typeof token.meta?.line === 'number' &&
      /^\s*(?:[-+*]|\d+[.)])\s+\[[ xX]\] /.test(sourceLines[token.meta.line] || '')
    ) {
      rendered = `<input type="checkbox" ${editable ? '' : 'disabled'} ${/^\[[xX]\]/.test(token.content) ? 'checked' : ''} data-task-line="${token.meta.line}" aria-label="任务完成状态"> ${rendered.slice(4)}`
    }
    return rendered
  }
  const link =
    md.renderer.rules.link_open ||
    ((tokens, index, options, _env, renderer) => renderer.renderToken(tokens, index, options))
  md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    const token = tokens[index]!,
      href = String(token.attrGet('href') || '')
    if (/^https?:\/\//.test(href)) {
      token.attrSet('target', '_blank')
      token.attrSet('rel', 'noopener noreferrer')
    } else if (!href.startsWith('/') && !href.startsWith('#') && !href.startsWith('mailto:'))
      token.attrSet('href', '')
    return link(tokens, index, options, env, renderer)
  }
  const renderImage = md.renderer.rules.image!
  md.renderer.rules.image = (tokens, index, options, env, renderer) => {
    const token = tokens[index]!,
      src = String(token.attrGet('src') || '')
    if (!/^https?:\/\//.test(src) && !/^\/api\/attachments\/\d+$/.test(src))
      return escape(token.content)
    token.attrSet('loading', 'lazy')
    token.attrSet('referrerpolicy', 'no-referrer')
    return renderImage(tokens, index, options, env, renderer)
  }
  return DOMPurify.sanitize(md.render(source), {
    ALLOWED_TAGS: [
      'p',
      'br',
      'hr',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'blockquote',
      'pre',
      'code',
      'strong',
      'em',
      's',
      'a',
      'img',
      'ul',
      'ol',
      'li',
      'table',
      'thead',
      'tbody',
      'tr',
      'th',
      'td',
      'input',
    ],
    ALLOWED_ATTR: [
      'href',
      'src',
      'alt',
      'title',
      'class',
      'start',
      'target',
      'rel',
      'loading',
      'referrerpolicy',
      'type',
      'checked',
      'disabled',
      'data-task-line',
      'aria-label',
    ],
    ALLOW_DATA_ATTR: false,
  })
}
