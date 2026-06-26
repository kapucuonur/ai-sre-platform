import React, { useState, useEffect } from 'react'
import { Play, ShieldAlert, Check, X, Clock, HelpCircle } from 'lucide-react'
import { API_BASE } from '../config'

function Incidents() {
  const [incidents, setIncidents] = useState([])
  const [loading, setLoading] = useState(true)
  const [processingId, setProcessingId] = useState(null)

  const fetchIncidents = () => {
    fetch(`${API_BASE}/api/incidents`)
      .then((res) => res.json())
      .then((data) => {
        setIncidents(data)
        setLoading(false)
      })
      .catch((err) => {
        console.error('Error fetching incidents:', err)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchIncidents()
    const interval = setInterval(fetchIncidents, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleAction = (incidentId, action) => {
    setProcessingId(incidentId)
    // Optimistic UI update
    setIncidents(prev => 
      prev.map(inc => inc.id === incidentId ? { ...inc, status: action === 'approve' ? 'approved' : 'rejected' } : inc)
    )

    fetch(`${API_BASE}/api/incidents/${incidentId}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    })
      .then((res) => {
        if (!res.ok) throw new Error('Action failed')
        return res.json()
      })
      .then(() => {
        fetchIncidents()
      })
      .catch((err) => {
        console.error(err)
        fetchIncidents()
      })
      .finally(() => {
        setProcessingId(null)
      })
  }

  const getStatusIcon = (status) => {
    switch (status) {
      case 'pending': return <Clock size={16} color="var(--status-pending)" />
      case 'approved': return <Clock size={16} color="var(--status-approved)" />
      case 'resolved': return <Check size={16} color="var(--status-resolved)" />
      case 'rejected': return <X size={16} color="var(--status-rejected)" />
      case 'failed': return <ShieldAlert size={16} color="var(--status-failed)" />
      default: return <HelpCircle size={16} />
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Incidents & Actions</h1>
        <p className="page-subtitle">Historical incident stream, AI SRE root-cause analysis, and self-healing action logs.</p>
      </div>

      {loading && incidents.length === 0 ? (
        <p className="page-subtitle">Incidents are loading...</p>
      ) : incidents.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <p className="page-subtitle" style={{ fontSize: '1.1rem', fontStyle: 'italic' }}>
            No incidents detected yet. Your system is fully operational and healthy. 🎉
          </p>
        </div>
      ) : (
        <div className="incidents-container">
          {incidents.map((incident) => (
            <div key={incident.id} className="card incident-card">
              <div className="incident-meta">
                <span className="incident-service">{incident.service}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <span className={`badge badge-${incident.status}`}>
                    {incident.status}
                  </span>
                  <span className="incident-time">
                    {new Date(incident.created_at).toLocaleString('tr-TR')}
                  </span>
                </div>
              </div>

              <div className="incident-title">
                {incident.title}
              </div>

              <div className="incident-content">
                <div className="incident-section-title">AI SRE Root-Cause Analysis</div>
                <p className="incident-text">{incident.ai_analysis}</p>
              </div>

              {incident.logs && (
                <div className="incident-content">
                  <div className="incident-section-title">Captured Error Logs</div>
                  <pre className="incident-code" style={{ fontSize: '0.8rem', maxHeight: '180px' }}>
                    {incident.logs}
                  </pre>
                </div>
              )}

              {incident.proposed_command && (
                <div className="incident-content">
                  <div className="incident-section-title">Proposed Recovery Action</div>
                  <div className="incident-code">{incident.proposed_command}</div>
                </div>
              )}

              {incident.action_output && (
                <div className="incident-content">
                  <div className="incident-section-title">Execution Console Output</div>
                  <pre className="incident-code" style={{ fontSize: '0.8rem', borderColor: 'rgba(16, 185, 129, 0.2)' }}>
                    {incident.action_output}
                  </pre>
                </div>
              )}

              {incident.status === 'pending' && (
                <div className="incident-actions">
                  <button 
                    disabled={processingId !== null}
                    onClick={() => handleAction(incident.id, 'approve')}
                    className="btn btn-primary"
                  >
                    <Check size={16} /> Approve & Execute
                  </button>
                  <button 
                    disabled={processingId !== null}
                    onClick={() => handleAction(incident.id, 'reject')}
                    className="btn btn-danger"
                  >
                    <X size={16} /> Reject Fix
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default Incidents
