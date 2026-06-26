import React, { useState, useEffect } from 'react'
import { Server, Cpu, Database, HardDrive } from 'lucide-react'
import { API_BASE } from '../config'

function Dashboard() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const fetchStatus = () => {
      fetch(`${API_BASE}/api/status`)
        .then((res) => {
          if (!res.ok) throw new Error('System status could not be fetched.')
          return res.json()
        })
        .then((data) => {
          setStatus(data)
          setError(null)
        })
        .catch((err) => {
          setError(err.message)
        })
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [])

  if (error) {
    return (
      <div className="card" style={{ borderColor: 'var(--status-rejected)' }}>
        <h3 style={{ color: 'var(--status-rejected)', marginBottom: '0.5rem' }}>Connection Error</h3>
        <p className="page-subtitle">Backend service is unreachable. Make sure the backend container is running.</p>
      </div>
    )
  }

  if (!status) {
    return (
      <div>
        <div className="page-header">
          <h1 className="page-title">Sistem Özeti</h1>
          <p className="page-subtitle">Sistem durum metrikleri yükleniyor...</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle">Real-time system health and Docker container orchestration monitoring.</p>
      </div>

      <div className="stats-grid">
        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Cpu size={16} color="var(--accent-cyan)" /> CPU Temperature
          </div>
          <div className="stat-value">{status.cpu_temp}</div>
        </div>

        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Database size={16} color="var(--accent-cyan)" /> Memory (RAM)
          </div>
          <div className="stat-value" style={{ fontSize: '1.6rem', marginTop: '0.4rem' }}>{status.memory}</div>
        </div>

        <div className="card">
          <div className="stat-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <HardDrive size={16} color="var(--accent-cyan)" /> Disk space
          </div>
          <div className="stat-value" style={{ fontSize: '1.4rem', marginTop: '0.5rem' }}>{status.disk}</div>
        </div>
      </div>

      <div className="containers-section">
        <h2 className="section-title">Active Docker Containers</h2>
        {status.containers.length === 0 ? (
          <p className="page-subtitle" style={{ fontStyle: 'italic' }}>No active containers found or Docker is offline.</p>
        ) : (
          <div className="containers-list">
            {status.containers.map((container, idx) => {
              const isUp = container.status.toLowerCase().includes('up')
              return (
                <div key={idx} className="container-card">
                  <div>
                    <div className="container-name">{container.name}</div>
                    <div className="container-status">{container.status}</div>
                  </div>
                  <span 
                    className={`badge ${isUp ? 'badge-resolved' : 'badge-failed'}`}
                    style={{ padding: '0.2rem 0.5rem' }}
                  >
                    {isUp ? 'Running' : 'Stopped'}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export default Dashboard
