import { Badge, CapsLabel, cardClasses, CodeBlock, InlineCode, Table, TableWrapper, Td, Th, Tr } from './ui'

interface OpenApiParam {
  name: string
  in?: string
  required?: boolean
  description?: string
  schema?: { type?: string; example?: unknown }
}

interface OpenApiOperation {
  summary?: string
  description?: string
  tags?: string[]
  parameters?: OpenApiParam[]
}

export interface OpenApiSpec {
  info?: { title?: string; description?: string; version?: string }
  servers?: { url?: string }[]
  paths?: Record<string, { get?: OpenApiOperation }>
}

/**
 * Renders the LLM-generated OpenAPI spec as a readable reference in the Warm
 * Editorial style — the enriched prose (api description, endpoint summary,
 * per-parameter descriptions, tags) plus the practical endpoint/params/curl.
 * Replaces the previous Scalar embed.
 */
export default function ApiDocView({ spec, slug }: { spec: OpenApiSpec; slug: string }) {
  const info = spec.info ?? {}
  const pathKey = Object.keys(spec.paths ?? {})[0] ?? `/v1/run/${slug}`
  const op = spec.paths?.[pathKey]?.get ?? {}
  const server = spec.servers?.[0]?.url ?? 'http://localhost:8000'
  const params = op.parameters ?? []
  const query = params.length
    ? '?' + params.map((p) => `${p.name}=${p.schema?.example ?? ''}`).join('&')
    : ''
  const curl = `curl -H "X-API-Key: ab_..." "${server}${pathKey}${query}"`

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        {(op.tags ?? []).length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {(op.tags ?? []).map((t) => (
              <Badge key={t} variant="neutral">
                {t}
              </Badge>
            ))}
          </div>
        )}
        <h1 className="font-display text-display-sm">{info.title ?? slug}</h1>
        {info.description && <p className="max-w-2xl text-lg text-ink/80">{info.description}</p>}
      </header>

      <section className="space-y-3">
        <CapsLabel tone="muted">Endpoint</CapsLabel>
        <div className={`${cardClasses({ variant: 'quiet' })} flex flex-wrap items-center gap-3`}>
          <Badge variant="info">GET</Badge>
          <InlineCode>{pathKey}</InlineCode>
        </div>
        {op.summary && <p className="font-medium">{op.summary}</p>}
        {op.description && <p className="text-ink/80">{op.description}</p>}
      </section>

      <section className="space-y-3">
        <CapsLabel tone="muted">Authentication</CapsLabel>
        <p className="text-ink/80">
          Send your API key in the <InlineCode>X-API-Key</InlineCode> header with every request.
        </p>
      </section>

      <section className="space-y-3">
        <CapsLabel tone="muted">Parameters</CapsLabel>
        {params.length === 0 ? (
          <p className="text-ink/70">This API takes no parameters.</p>
        ) : (
          <TableWrapper>
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Required</Th>
                  <Th>Description</Th>
                </tr>
              </thead>
              <tbody>
                {params.map((p) => (
                  <Tr key={p.name}>
                    <Td mono>{p.name}</Td>
                    <Td>{p.schema?.type ?? 'string'}</Td>
                    <Td>{p.required ? 'yes' : 'no'}</Td>
                    <Td>{p.description}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrapper>
        )}
      </section>

      <section className="space-y-3">
        <CapsLabel tone="muted">Example request</CapsLabel>
        <CodeBlock lang="bash" code={curl} />
      </section>

      <section className="space-y-3">
        <CapsLabel tone="muted">Response</CapsLabel>
        <p className="text-ink/80">
          Returns <InlineCode>200</InlineCode> with a JSON body of the shape{' '}
          <InlineCode>{'{ "data": …, "meta": { … } }'}</InlineCode> — the extracted data is under{' '}
          <InlineCode>data</InlineCode>.
        </p>
      </section>
    </div>
  )
}
