import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  Button,
  buttonClasses,
  CapsLabel,
  cardClasses,
  EmptyRow,
  Input,
  Modal,
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { api } from '../lib/api'

interface ApiKeySummary {
  id: string
  label: string
  key_prefix: string
  last_used_at: string | null
  created_at: string
}

interface ApiKeyCreated extends ApiKeySummary {
  api_key: string
}

export default function Keys() {
  const [keys, setKeys] = useState<ApiKeySummary[]>([])
  const [label, setLabel] = useState('default')
  const [newKey, setNewKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<ApiKeySummary | null>(null)

  function load() {
    api
      .get<ApiKeySummary[]>('/keys')
      .then(setKeys)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load keys'))
  }

  useEffect(load, [])

  async function createKey() {
    setError(null)
    try {
      const created = await api.post<ApiKeyCreated>('/keys', { label })
      setNewKey(created.api_key)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create key')
    }
  }

  async function confirmRevoke() {
    if (!revokeTarget) return
    try {
      await api.delete(`/keys/${revokeTarget.id}`)
      setRevokeTarget(null)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke key')
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Account</CapsLabel>} title="API Keys" />

      {error && <p className="mb-6 text-sm font-medium text-red-deep">{error}</p>}

      {newKey && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mb-6`}>
          <p className="mb-2 font-bold">Copy your key now — it won&apos;t be shown again.</p>
          <pre className="overflow-auto rounded-control border border-sand bg-paper p-2 font-mono text-xs">{newKey}</pre>
        </div>
      )}

      <div className="mb-6 flex items-center gap-2">
        <Input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="label"
          className="max-w-xs"
        />
        <Button onClick={createKey}>Create key</Button>
      </div>

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>Label</Th>
              <Th>Prefix</Th>
              <Th>Last used</Th>
              <Th>Created</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 && <EmptyRow colSpan={5}>No API keys yet.</EmptyRow>}
            {keys.map((k) => (
              <Tr key={k.id}>
                <Td>{k.label}</Td>
                <Td mono>{k.key_prefix}…</Td>
                <Td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'never'}</Td>
                <Td>{new Date(k.created_at).toLocaleString()}</Td>
                <Td>
                  <Button variant="danger-ghost" size="sm" onClick={() => setRevokeTarget(k)}>
                    Revoke
                  </Button>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrapper>

      <Modal
        open={!!revokeTarget}
        onClose={() => setRevokeTarget(null)}
        title="Revoke API key?"
        actions={
          <>
            <Button variant="default" onClick={() => setRevokeTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmRevoke}>
              Revoke
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink/70">
          Requests using <span className="font-mono font-bold">{revokeTarget?.key_prefix}…</span>
          {' '}({revokeTarget?.label}) will stop working immediately.
        </p>
      </Modal>
    </AppShell>
  )
}
