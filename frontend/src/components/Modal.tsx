import type { ReactNode } from 'react'

export default function Modal({ title, onClose, children, footer }: {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(0,0,0,.6)',
      display: 'grid', placeItems: 'center', padding: 20, backdropFilter: 'blur(2px)',
    }}>
      <div className="pw-card" onClick={e => e.stopPropagation()} style={{ width: 520, maxWidth: '100%', maxHeight: '90vh', overflow: 'auto' }}>
        <div className="pw-card-head">
          <h3>{title}</h3>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={onClose}>✕</button>
        </div>
        <div className="pw-card-body pw-stack" style={{ gap: 14 }}>{children}</div>
        {footer && <div className="pw-card-head" style={{ borderBottom: 'none', borderTop: '1px solid var(--border)', justifyContent: 'flex-end' }}>{footer}</div>}
      </div>
    </div>
  )
}
