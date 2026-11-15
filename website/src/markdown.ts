import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

interface MarkdownContext { repositoryId: number; repositoryPath: string; branch: string; filePath: string }
const slug = (text: string) => text.toLowerCase().trim().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-')

/** Repository text is untrusted: HTML off, explicit URL rules, then sanitize the final HTML. */
export function renderMarkdown(source: string, context: MarkdownContext): string {
  const md = new MarkdownIt({ html: false, linkify: true, typographer: false })
  const headings = new Map<string, number>()
  md.renderer.rules.heading_open = (tokens, index, options, _env, renderer) => {
    const name = slug(tokens[index + 1]?.content || 'section')
    const count = headings.get(name) || 0
    headings.set(name, count + 1)
    tokens[index]!.attrSet('id', `readme-${name}${count ? `-${count}` : ''}`)
    return renderer.renderToken(tokens, index, options)
  }
  function address(value: string, image: boolean): string {
    if (!value || /[\u0000-\u0020\\]/.test(value)) return ''
    if (value.startsWith('#')) {
      try { return image ? '' : `#readme-${slug(decodeURIComponent(value.slice(1)))}` } catch { return '' }
    }
    if (/^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith('//')) {
      try {
        const url = new URL(value, 'https://repository.invalid')
        return ['https:', 'http:', ...(image ? [] : ['mailto:'])].includes(url.protocol) ? url.href : ''
      } catch { return '' }
    }
    try {
      const base = context.filePath.split('/').slice(0, -1).map(encodeURIComponent).join('/')
      const target = new URL(value, `https://repository.invalid/${base ? `${base}/` : ''}`)
      const path = decodeURIComponent(target.pathname.slice(1)).replace(/\/+$/, '')
      const params = new URLSearchParams({ ref: context.branch, ...(path ? { path } : {}) })
      if (image) return `/api/repositories/${context.repositoryId}/image?${params}`
      const hash = target.hash ? `#readme-${slug(decodeURIComponent(target.hash.slice(1)))}` : ''
      return `${context.repositoryPath}?${params}${hash}`
    } catch { return '' }
  }
  md.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
    const token = tokens[index]!
    const href = address(String(token.attrGet('href') || ''), false)
    token.attrSet('href', href)
    if (/^(https?:|mailto:)/.test(href)) {
      token.attrSet('target', '_blank')
      token.attrSet('rel', 'noopener noreferrer')
    }
    return renderer.renderToken(tokens, index, options)
  }
  const renderImage = md.renderer.rules.image!
  md.renderer.rules.image = (tokens, index, options, env, renderer) => {
    const token = tokens[index]!
    const src = address(String(token.attrGet('src') || ''), true)
    if (!src) return md.utils.escapeHtml(token.content)
    token.attrSet('src', src)
    token.attrSet('loading', 'lazy')
    token.attrSet('referrerpolicy', 'no-referrer')
    return renderImage(tokens, index, options, env, renderer)
  }
  return DOMPurify.sanitize(md.render(source), {
    ALLOWED_TAGS: ['p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'strong', 'em', 's', 'a', 'img', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td'],
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'id', 'class', 'start', 'target', 'rel', 'loading', 'referrerpolicy'],
    ALLOW_DATA_ATTR: false,
  })
}
