import React, { useState, useEffect } from 'react'
import { Calendar, Terminal, Clock, RefreshCw } from 'lucide-react'
import { API_BASE } from '../config'

function History() {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchHistory = () => {
    setLoading(true)
    fetch(`${API_BASE}/api/history`)
      .then((res) => res.json())
      .then((data) => {
        setHistory(data)
        setLoading(false)
      })
      .catch((err) => {
        console.error('Error fetching history:', err)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchHistory()
  }, [])

  const getDuration = (inc) => {
    if (inc.duration !== undefined && inc.duration !== null) {
      return `${Number(inc.duration).toFixed(1)}s`
    }
    if (!inc.created_at || !inc.updated_at || inc.status === 'pending') return '-'
    const start = new Date(inc.created_at)
    const end = new Date(inc.updated_at)
    const diffMs = end - start
    if (isNaN(diffMs) || diffMs < 0) return '0s'
    return `${(diffMs / 1000).toFixed(1)}s`
  }

  const formatTime = (isoString) => {
    if (!isoString) return '-'
    try {
      const d = new Date(isoString)
      return d.toLocaleString()
    } catch {
      return isoString
    }
  }

  const getStatusBadgeClass = (status) => {
    switch (status) {
      case 'resolved': return 'badge badge-resolved'
      case 'failed': return 'badge badge-failed'
      case 'approved': return 'badge badge-approved'
      case 'rejected': return 'badge badge-rejected'
      case 'pending': return 'badge badge-pending'
      case 'rolled_back': return 'badge badge-rejected'
      default: return 'badge'
    }
  }

  const handleRollback = (id) => {
    if (!window.confirm('Are you sure you want to rollback this fix? This will attempt to revert code changes applied to files.')) return
    
    fetch(`${API_BASE}/api/incidents/${id}/rollback`, {
      method: 'POST'
    })
      .then((res) => {
        if (!res.ok) throw new Error('Rollback failed')
        return res.json()
      })
      .then((data) => {
        alert('Rollback completed: ' + (data.details ? data.details.join(', ') : 'Success'))
        fetchHistory()
      })
      .catch((err) => {
        console.error('Error during rollback:', err)
        alert('Failed to execute rollback: ' + err.message)
      })
  }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="page-title">History</h1>
          <p className="page-subtitle">Log viewer for all past system incidents, alert responses, and autonomous healing runs.</p>
        </div>
        <button className="btn btn-secondary" onClick={fetchHistory} disabled={loading} style={{ height: 'fit-content' }}>
          <RefreshCw size={16} className={loading ? 'spin-animation' : ''} /> Refresh
        </button>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            Loading healing history logs...
          </div>
        ) : history.length === 0 ? (
          <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            No incident response or healing records found.
          </div>
        ) : (
          <div className="table-container">
            <table className="history-table">
              <thead>
                <tr>
                  <th><span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}><Calendar size={14} /> Date</span></th>
                  <th>Service</th>
                  <th>Incident / Alert</th>
                  <th><span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}><Terminal size={14} /> Applied Command</span></th>
                  <th>Status</th>
                  <th><span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}><Clock size={14} /> Duration</span></th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                      {formatTime(row.created_at)}
                    </td>
                    <td style={{ fontWeight: 600, color: 'var(--accent-cyan)', textTransform: 'uppercase', fontSize: '0.85rem' }}>
                      {row.service}
                    </td>
                    <td style={{ fontWeight: 500, color: '#fff' }}>
                      {row.title}
                    </td>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.85rem', color: '#38bdf8' }}>
                      {row.proposed_command ? <code>{row.proposed_command}</code> : <span style={{ color: 'var(--text-muted)' }}>None</span>}
                    </td>
                    <td>
                      <span className={getStatusBadgeClass(row.status)}>
                        {row.status}
                      </span>
                    </td>
                    <td style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', fontWeight: 500 }}>
                      {getDuration(row)}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {(row.status === 'resolved' || row.status === 'failed') && row.proposed_command && row.proposed_command.startsWith('[') ? (
                        <button 
                          className="btn btn-secondary" 
                          onClick={() => handleRollback(row.id)}
                          style={{ padding: '4px 8px', fontSize: '0.75rem', borderColor: '#ef4444', color: '#ef4444' }}
                        >
                          Rollback
                        </button>
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

export default History
