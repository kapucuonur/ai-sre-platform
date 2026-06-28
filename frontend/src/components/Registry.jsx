import React, { useState, useEffect } from 'react'
import { Database, Zap, ShieldAlert, Cpu, Terminal, Clock, CheckCircle, AlertTriangle } from 'lucide-react'
import { API_BASE } from '../config'

function Registry() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const fetchAnalytics = () => {
      fetch(`${API_BASE}/api/registry/analytics`)
        .then((res) => {
          if (!res.ok) throw new Error('Strategy Registry analytics could not be retrieved.')
          return res.json()
        })
        .then((data) => {
          setData(data)
          setError(null)
        })
        .catch((err) => {
          setError(err.message)
        })
    }

    fetchAnalytics()
    const interval = setInterval(fetchAnalytics, 5000)
    return () => clearInterval(interval)
  }, [])

  const formatTime = (isoString) => {
    if (!isoString) return 'Never'
    try {
      const d = new Date(isoString)
      return d.toLocaleString('tr-TR', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      })
    } catch (e) {
      return isoString
    }
  }

  if (error) {
    return (
      <div className="card" style={{ borderColor: 'var(--status-rejected)' }}>
        <h3 style={{ color: 'var(--status-rejected)', marginBottom: '0.5rem' }}>Connection Error</h3>
        <p className="page-subtitle">Backend service is unreachable. Make sure the SRE Platform backend is running.</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Otonom Hafıza Registry</h1>
          <p className="page-subtitle">Loading registry analytics and strategy tables...</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Otonom Hafıza Registry</h1>
        <p className="page-subtitle">Monitoring learned self-healing command paths and time-based decay metrics.</p>
      </div>

      {/* Analytics Stats Grid */}
      <div className="stats-grid" style={{ marginBottom: '2rem' }}>
        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Database size={16} color="var(--accent-cyan)" /> Registered Strategies
          </div>
          <div className="stat-value">{data.total_strategies}</div>
        </div>

        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Zap size={16} color="var(--accent-cyan)" /> Estimated Token Savings
          </div>
          <div className="stat-value" style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem' }}>
            {data.total_savings_tokens.toLocaleString()}
            <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', fontWeight: 500 }}>tokens</span>
          </div>
        </div>

        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <ShieldAlert size={16} color="var(--accent-cyan)" /> Blacklist Rate
          </div>
          <div className="stat-value" style={{ color: data.blacklist_rate > 50 ? 'var(--status-rejected)' : '#fff' }}>
            {data.blacklist_rate}%
          </div>
        </div>
      </div>

      {/* Strategies Table */}
      <div className="containers-section">
        <h2 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Terminal size={18} color="var(--accent-cyan)" /> Learned Strategy Registry Table
        </h2>

        {data.entries.length === 0 ? (
          <p className="page-subtitle" style={{ fontStyle: 'italic', marginTop: '1rem' }}>
            No strategies registered yet. The daemon will learn recovery steps automatically on next incident.
          </p>
        ) : (
          <div className="table-container">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Error Hash</th>
                  <th>Target Command</th>
                  <th>Weight</th>
                  <th style={{ textAlign: 'center' }}>Success Hits</th>
                  <th style={{ textAlign: 'center' }}>Failed Hits</th>
                  <th style={{ textAlign: 'center' }}>Status</th>
                  <th>Last Used</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map((row, idx) => {
                  const isActive = row.is_blacklisted === 0
                  return (
                    <tr key={idx} style={{ opacity: isActive ? 1 : 0.6 }}>
                      <td style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--accent-cyan)', fontSize: '0.85rem' }}>
                        {row.error_hash}
                      </td>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.85rem', color: '#e2e8f0' }}>
                        <code>{row.command}</code>
                      </td>
                      <td style={{ fontWeight: 700, color: row.weight > 0 ? '#10b981' : row.weight === 0 ? '#f59e0b' : '#ef4444' }}>
                        {row.weight}
                      </td>
                      <td style={{ textAlign: 'center', color: '#10b981', fontWeight: 600 }}>
                        {row.success_count}
                      </td>
                      <td style={{ textAlign: 'center', color: '#ef4444', fontWeight: 600 }}>
                        {row.fail_count}
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <span 
                          className={`badge ${isActive ? 'badge-resolved' : 'badge-failed'}`}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem', padding: '0.2rem 0.5rem' }}
                        >
                          {isActive ? <CheckCircle size={10} /> : <ShieldAlert size={10} />}
                          {isActive ? 'Active' : 'Blacklisted'}
                        </span>
                      </td>
                      <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                        {formatTime(row.last_used)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

export default Registry
